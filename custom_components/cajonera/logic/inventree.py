"""Minimal async client for the InvenTree REST API.

Only what the cajonera needs. Every failure (HTTP error, timeout, connection
problem) surfaces as InvenTreeError so callers can answer "No puedo consultar
el inventario ahora mismo." instead of crashing.

Field names (total_in_stock, low_stock, ...) were taken from the InvenTree API
docs; if an installed version differs, adapt the mapping here only.
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .models import Category, Location, Part, StockItem
from .naming import ROOT_LOCATION


def _metadata_path(location_pk: int) -> str:
    # Generic metadata endpoint. The older /api/stock/location/<pk>/metadata/ answers
    # 301 on InvenTree 1.x, and a PATCH that follows a 301 turns into a GET, so the
    # address would silently never be saved.
    return f"/api/metadata/stocklocation/{location_pk}/"


class InvenTreeError(Exception):
    """InvenTree could not be reached or answered with an error."""


def _results(data: Any) -> list[dict]:
    # List endpoints answer a plain list, or {"results": [...]} when paginated.
    if isinstance(data, dict):
        return data.get("results", [])
    return data


def _mapped(fn):
    """Turn an unexpected payload shape into InvenTreeError instead of KeyError/TypeError."""

    def wrapper(d):
        try:
            return fn(d)
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            raise InvenTreeError(f"unexpected InvenTree payload: {err!r}") from err

    return wrapper


@_mapped
def _part(d: dict) -> Part:
    total = d.get("total_in_stock", d.get("in_stock", 0))
    return Part(
        pk=d["pk"],
        name=d["name"],
        minimum_stock=float(d.get("minimum_stock") or 0),
        total_stock=float(total or 0),
        ipn=d.get("IPN") or "",
        category_pk=d.get("category"),
        description=d.get("description") or "",
        keywords=d.get("keywords") or "",
    )


@_mapped
def _stock(d: dict) -> StockItem:
    detail = d.get("part_detail") or {}
    return StockItem(
        pk=d["pk"],
        part_pk=d["part"],
        part_name=detail.get("name", ""),
        quantity=float(d.get("quantity") or 0),
        location_pk=d.get("location"),
        delete_on_deplete=bool(d.get("delete_on_deplete", True)),
    )


@_mapped
def _category(d: dict) -> Category:
    return Category(pk=d["pk"], name=d["name"], path=d.get("pathstring") or d["name"])


_PART_UPDATE_FIELDS = frozenset({"name", "description", "category", "keywords", "IPN", "minimum_stock"})


class InvenTreeClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, token: str, timeout: float = 3.0):
        self._session = session
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Token {token}", "Accept": "application/json"}
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def _request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> Any:
        try:
            async with self._session.request(
                method,
                self._base + path,
                params=params,
                json=json,
                headers=self._headers,
                timeout=self._timeout,
            ) as resp:
                if resp.status >= 400:
                    raise InvenTreeError(f"{method} {path}: HTTP {resp.status}")
                if resp.status == 204:
                    return None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            # ValueError: a 200 with a non-JSON body (login page, proxy error page).
            raise InvenTreeError(f"{method} {path}: {err!r}") from err

    # Parts

    async def search_parts(self, text: str, limit: int = 10) -> list[Part]:
        data = await self._request("GET", "/api/part/", params={"search": text, "limit": limit, "active": "true"})
        return [_part(d) for d in _results(data)]

    async def get_part(self, pk: int) -> Part:
        return _part(await self._request("GET", f"/api/part/{pk}/"))

    async def low_stock_parts(self) -> list[Part]:
        data = await self._request("GET", "/api/part/", params={"low_stock": "true", "active": "true"})
        return [_part(d) for d in _results(data)]

    async def create_part(
        self,
        name: str,
        category_pk: int,
        location_pk: int,
        quantity: float,
        ipn: str = "",
        description: str = "",
        keywords: str = "",
        minimum: float = 0,
    ) -> Part:
        data = await self._request(
            "POST",
            "/api/part/",
            json={
                "name": name,
                "category": category_pk,
                "IPN": ipn,
                "description": description,
                "keywords": keywords,
                "minimum_stock": minimum,
                "component": True,
                "active": True,
                "initial_stock": {"quantity": quantity, "location": location_pk},
            },
        )
        return _part(data)

    async def update_part(self, pk: int, **fields: Any) -> None:
        unknown = set(fields) - _PART_UPDATE_FIELDS
        if unknown:
            raise ValueError(f"cannot update part fields: {sorted(unknown)}")
        await self._request("PATCH", f"/api/part/{pk}/", json=fields)

    # Categories

    async def categories(self) -> list[Category]:
        data = await self._request("GET", "/api/part/category/")
        return [_category(d) for d in _results(data)]

    async def create_category(self, name: str, parent_pk: int | None = None) -> Category:
        body: dict[str, Any] = {"name": name}
        if parent_pk is not None:
            body["parent"] = parent_pk
        return _category(await self._request("POST", "/api/part/category/", json=body))

    # Stock

    async def stock_for_part(self, part_pk: int) -> list[StockItem]:
        data = await self._request("GET", "/api/stock/", params={"part": part_pk, "part_detail": "true"})
        return [_stock(d) for d in _results(data)]

    async def stock_in_location(self, location_pk: int) -> list[StockItem]:
        data = await self._request(
            "GET", "/api/stock/", params={"location": location_pk, "cascade": "false", "part_detail": "true"}
        )
        return [_stock(d) for d in _results(data)]

    async def get_stock(self, pk: int) -> StockItem:
        return _stock(await self._request("GET", f"/api/stock/{pk}/", params={"part_detail": "true"}))

    async def remove_stock(self, stock_pk: int, quantity: float, notes: str) -> None:
        await self._request(
            "POST", "/api/stock/remove/", json={"items": [{"pk": stock_pk, "quantity": quantity}], "notes": notes}
        )

    async def add_stock(self, stock_pk: int, quantity: float, notes: str) -> None:
        await self._request(
            "POST", "/api/stock/add/", json={"items": [{"pk": stock_pk, "quantity": quantity}], "notes": notes}
        )

    async def count_stock(self, stock_pk: int, quantity: float, notes: str) -> None:
        await self._request(
            "POST", "/api/stock/count/", json={"items": [{"pk": stock_pk, "quantity": quantity}], "notes": notes}
        )

    async def transfer_stock(self, stock_pk: int, quantity: float, location_pk: int, notes: str) -> None:
        await self._request(
            "POST",
            "/api/stock/transfer/",
            json={"items": [{"pk": stock_pk, "quantity": quantity}], "location": location_pk, "notes": notes},
        )

    async def merge_stock(self, stock_pks: list[int], location_pk: int, notes: str) -> None:
        await self._request(
            "POST",
            "/api/stock/merge/",
            json={
                "items": [{"item": pk} for pk in stock_pks],
                "location": location_pk,
                "notes": notes,
                "allow_mismatched_suppliers": True,
                "allow_mismatched_status": True,
            },
        )

    async def create_stock(self, part_pk: int, location_pk: int, quantity: float) -> StockItem:
        data = await self._request(
            "POST",
            "/api/stock/",
            json={"part": part_pk, "location": location_pk, "quantity": quantity, "delete_on_deplete": False},
        )
        return _stock(data)

    async def set_keep_when_empty(self, stock_pk: int) -> None:
        await self._request("PATCH", f"/api/stock/{stock_pk}/", json={"delete_on_deplete": False})

    async def stock_in_category(self, category_pk: int, root_pk: int) -> list[StockItem]:
        # `part_category`, not `category`: InvenTree 1.7.3 answers HTTP 400 to the latter.
        data = await self._request(
            "GET",
            "/api/stock/",
            params={"part_category": category_pk, "location": root_pk, "cascade": "true", "part_detail": "true"},
        )
        return [_stock(d) for d in _results(data)]

    # Locations

    async def ensure_root(self) -> int:
        data = await self._request("GET", "/api/stock/location/", params={"name": ROOT_LOCATION})
        # Filter client-side: the parent=null filter is not reliable across versions.
        for d in _results(data):
            if d.get("name") == ROOT_LOCATION and d.get("parent") is None:
                return d["pk"]
        created = await self._request(
            "POST",
            "/api/stock/location/",
            json={"name": ROOT_LOCATION, "description": "Cajonera modular (gestionada por Home Assistant)"},
        )
        return created["pk"]

    async def child_locations(self, parent_pk: int) -> list[Location]:
        data = await self._request("GET", "/api/stock/location/", params={"parent": parent_pk})
        rows = _results(data)
        metas = await asyncio.gather(
            *(self._request("GET", _metadata_path(d["pk"])) for d in rows)
        )
        out = []
        for d, meta in zip(rows, metas):
            address = ((meta or {}).get("metadata") or {}).get("cajonera", {}).get("direccion")
            out.append(Location(d["pk"], d["name"], address))
        return out

    async def create_location(self, parent_pk: int, name: str, address: int) -> Location:
        created = await self._request("POST", "/api/stock/location/", json={"name": name, "parent": parent_pk})
        await self.set_location_address(created["pk"], address)
        return Location(created["pk"], name, address)

    async def set_location_address(self, pk: int, address: int) -> None:
        await self._request("PATCH", _metadata_path(pk), json={"metadata": {"cajonera": {"direccion": address}}})

    async def rename_location(self, pk: int, name: str) -> None:
        await self._request("PATCH", f"/api/stock/location/{pk}/", json={"name": name})
