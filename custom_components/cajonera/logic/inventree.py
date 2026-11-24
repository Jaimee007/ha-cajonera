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

from .models import Location, Part, StockItem
from .naming import ROOT_LOCATION


class InvenTreeError(Exception):
    """InvenTree could not be reached or answered with an error."""


def _results(data: Any) -> list[dict]:
    # List endpoints answer a plain list, or {"results": [...]} when paginated.
    if isinstance(data, dict):
        return data.get("results", [])
    return data


def _part(d: dict) -> Part:
    total = d.get("total_in_stock", d.get("in_stock", 0))
    return Part(
        pk=d["pk"],
        name=d["name"],
        minimum_stock=float(d.get("minimum_stock") or 0),
        total_stock=float(total or 0),
    )


def _stock(d: dict) -> StockItem:
    detail = d.get("part_detail") or {}
    return StockItem(
        pk=d["pk"],
        part_pk=d["part"],
        part_name=detail.get("name", ""),
        quantity=float(d.get("quantity") or 0),
        location_pk=d.get("location"),
    )


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
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
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
            *(self._request("GET", f"/api/stock/location/{d['pk']}/metadata/") for d in rows)
        )
        out = []
        for d, meta in zip(rows, metas):
            address = ((meta or {}).get("metadata") or {}).get("cajonera", {}).get("direccion")
            out.append(Location(d["pk"], d["name"], address))
        return out

    async def create_location(self, parent_pk: int, name: str, address: int) -> Location:
        created = await self._request("POST", "/api/stock/location/", json={"name": name, "parent": parent_pk})
        await self._request(
            "PATCH",
            f"/api/stock/location/{created['pk']}/metadata/",
            json={"metadata": {"cajonera": {"direccion": address}}},
        )
        return Location(created["pk"], name, address)

    async def rename_location(self, pk: int, name: str) -> None:
        await self._request("PATCH", f"/api/stock/location/{pk}/", json={"name": name})
