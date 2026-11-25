"""Catalog for the inventory card: suggestions, part sheet, create and edit.

`locations` maps location pk -> Location and holds only the cabinet's drawers;
stock anywhere else is "outside the cabinet" ("fuera"). Unplaced drawers are
still drawers (their stock counts), but they are never suggested as a place to
put something new.

Every function answers InvenTree being down in the shape it normally returns.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter

from .inventree import InvenTreeError
from .models import Drawer, Location, Part, StockItem
from .phrases import fmt_qty
from .search import DOWN_PHRASE, is_unplaced
from .stock_ops import INVALID_QTY, MAYBE_STOCK, UNKNOWN_DRAWER, OpResult, _find, _finish

MAX_SUGGESTIONS = 8
MIN_SUGGEST_CHARS = 2
NAME_REQUIRED = "Dime un nombre para la pieza."
CATEGORY_NAME_REQUIRED = "Dime un nombre para la categoría."
CHOOSE_DRAWER = "Elige un cajón."
FIRST_EMPTY = "Primer cajón vacío."
_DUPLICATE_SEARCH_LIMIT = 50  # enough that an exact name is not pushed out of the results

_EDIT_FIELDS = {
    "nombre": "name",
    "descripcion": "description",
    "categoria": "category",
    "palabras_clave": "keywords",
    "ipn": "IPN",
}

_NAME = re.compile(r"^([A-Za-z]+)(\d+)$")


def _name_key(name: str) -> tuple:
    """Sort key for drawer names: A1 < A2 < A10 < B1 < ... < Z1 < AA1. Odd names go last."""
    m = _NAME.match(name)
    if not m:
        return (1, 0, "", 0, name)
    letters = m.group(1).upper()
    return (0, len(letters), letters, int(m.group(2)), name)


def _drawer_totals(items: list[StockItem], locations: dict[int, Location]) -> dict[int, float]:
    """Quantity per cabinet location pk. Kept-at-zero items still count: the part lives there."""
    totals: dict[int, float] = {}
    for s in items:
        if s.location_pk in locations:
            totals[s.location_pk] = totals.get(s.location_pk, 0.0) + s.quantity
    return totals


async def suggest(api, text: str, locations: dict[int, Location]) -> list[dict]:
    text = (text or "").strip()
    if len(text) < MIN_SUGGEST_CHARS:
        return []
    try:
        parts = await api.search_parts(text, limit=MAX_SUGGESTIONS)
        parts = parts[:MAX_SUGGESTIONS]
        stocks = await asyncio.gather(*(api.stock_for_part(p.pk) for p in parts))
    except InvenTreeError:
        return []
    rows = []
    for part, items in zip(parts, stocks):
        totals = _drawer_totals(items, locations)
        names = sorted({locations[pk].name for pk in totals}, key=_name_key)
        # What the card lights while typing: only drawers that exist on the cabinet.
        addresses = sorted({locations[pk].address for pk in totals if not is_unplaced(locations[pk])})
        rows.append(
            {
                "pieza_pk": part.pk,
                "nombre": part.name,
                "ipn": part.ipn,
                "categoria_pk": part.category_pk,
                "total": part.total_stock,
                "cajones": names,
                "direcciones": addresses,
                "bajo": part.is_low,
            }
        )
    return rows


async def part_sheet(api, part_pk: int, locations: dict[int, Location]) -> dict:
    try:
        part, items = await asyncio.gather(api.get_part(part_pk), api.stock_for_part(part_pk))
    except InvenTreeError:
        return {"frase": DOWN_PHRASE}
    totals = _drawer_totals(items, locations)
    drawers = sorted(
        ({"cajon": locations[pk].name, "direccion": locations[pk].address, "cantidad": qty} for pk, qty in totals.items()),
        key=lambda d: (-d["cantidad"], _name_key(d["cajon"])),
    )
    outside = sum(s.quantity for s in items if s.location_pk not in locations and s.quantity > 0)
    return {
        "pieza_pk": part.pk,
        "nombre": part.name,
        "descripcion": part.description,
        "categoria_pk": part.category_pk,
        "palabras_clave": part.keywords,
        "ipn": part.ipn,
        "minimo": part.minimum_stock,
        "total": part.total_stock,
        "bajo": part.is_low,
        "cajones": drawers,
        "fuera": outside,
    }


async def suggest_drawer(
    api, category_pk: int, root_pk: int, drawers: list[Drawer], locations: dict[int, Location]
) -> dict:
    """Where to put a new part: the drawer with most of its category, else the first empty one."""
    physical = {d.address for d in drawers}
    candidates = [
        loc for loc in locations.values() if not is_unplaced(loc) and loc.address in physical
    ]
    try:
        in_category = await api.stock_in_category(category_pk, root_pk)
        counts = Counter(s.location_pk for s in in_category)
        best = [(-counts[loc.pk], loc.address, loc) for loc in candidates if counts[loc.pk] > 0]
        if best:
            loc = min(best, key=lambda b: b[:2])[2]
            return {
                "direccion": loc.address,
                "cajon": loc.name,
                "motivo": f"Ya hay piezas de esta categoría en el {loc.name}.",
            }
        contents = await asyncio.gather(*(api.stock_in_location(loc.pk) for loc in candidates))
    except InvenTreeError:
        return {"direccion": None, "cajon": None, "motivo": DOWN_PHRASE}
    empty = [loc for loc, items in zip(candidates, contents) if not any(s.quantity > 0 for s in items)]
    if empty:
        loc = min(empty, key=lambda loc: _name_key(loc.name))
        return {"direccion": loc.address, "cajon": loc.name, "motivo": FIRST_EMPTY}
    return {"direccion": None, "cajon": None, "motivo": CHOOSE_DRAWER}


async def create_part(
    api,
    name: str,
    category_pk: int,
    address: int,
    quantity: float,
    locations: dict[int, Location],
    ipn: str = "",
    description: str = "",
    keywords: str = "",
    minimum: float = 0,
) -> OpResult:
    name = (name or "").strip()
    if not name:
        return OpResult(NAME_REQUIRED)
    try:
        similar = await api.search_parts(name, limit=_DUPLICATE_SEARCH_LIMIT)
    except InvenTreeError:
        return OpResult(DOWN_PHRASE)
    existing = next((p for p in similar if p.name.strip().casefold() == name.casefold()), None)
    if existing is not None:
        return OpResult(f"Ya existe «{existing.name}». Ábrela para añadir.", part_pk=existing.pk)
    if quantity is None or quantity <= 0:
        return OpResult(INVALID_QTY)
    location = _find(locations, address)
    if location is None:
        return OpResult(UNKNOWN_DRAWER)

    started = False
    try:
        started = True
        part = await api.create_part(
            name, category_pk, location.pk, quantity, ipn=ipn, description=description, keywords=keywords, minimum=minimum
        )
        # InvenTree deletes a stock item that reaches 0; the drawer must keep its place for the part.
        for item in await api.stock_for_part(part.pk):
            if item.location_pk == location.pk and item.delete_on_deplete:
                await api.set_keep_when_empty(item.pk)
        phrase = f"Hecho. He creado {name} con {fmt_qty(quantity)} en el {location.name}."
        return await _finish(api, phrase, part.pk, location)
    except InvenTreeError:
        return OpResult(MAYBE_STOCK if started else DOWN_PHRASE)


async def edit_part(api, part_pk: int, changes: dict) -> OpResult:
    fields: dict = {}
    for key, value in changes.items():
        if key in _EDIT_FIELDS:
            fields[_EDIT_FIELDS[key]] = value
        elif key == "minimo":
            try:
                fields["minimum_stock"] = float(value)
            except (TypeError, ValueError):
                return OpResult(INVALID_QTY)
            if fields["minimum_stock"] < 0:
                return OpResult(INVALID_QTY)
        # Unknown keys are ignored on purpose.
    if "name" in fields:
        fields["name"] = (fields["name"] or "").strip()
        if not fields["name"]:
            return OpResult(NAME_REQUIRED)
    try:
        if fields:
            # A single PATCH, and repeating an edit is harmless: a failure is plain "down".
            await api.update_part(part_pk, **fields)
    except InvenTreeError:
        return OpResult(DOWN_PHRASE)
    return OpResult("Hecho. He guardado los cambios.", done=True, part_pk=part_pk)


def _category_row(category) -> dict:
    return {"pk": category.pk, "nombre": category.name, "ruta": category.path}


async def list_categories(api) -> list[dict]:
    try:
        categories = await api.categories()
    except InvenTreeError:
        return []
    return sorted((_category_row(c) for c in categories), key=lambda r: r["ruta"].casefold())


def _parent_path(path: str) -> str:
    return path.rpartition("/")[0]


async def create_category(api, name: str, parent_pk: int | None) -> dict:
    name = (name or "").strip()
    if not name:
        return {"frase": CATEGORY_NAME_REQUIRED}
    try:
        categories = await api.categories()
        parent = next((c for c in categories if c.pk == parent_pk), None) if parent_pk is not None else None
        if parent_pk is None or parent is not None:
            parent_path = parent.path if parent else ""
            for c in categories:
                if c.name.casefold() == name.casefold() and _parent_path(c.path) == parent_path:
                    return _category_row(c)
        return _category_row(await api.create_category(name, parent_pk))
    except InvenTreeError:
        return {"frase": DOWN_PHRASE}
