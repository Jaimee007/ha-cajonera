"""Drawer-level stock operations for the inventory card: add, count and move.

The user only knows the drawer, so every operation works on "the part's stock
in that drawer", however many InvenTree stock items that is. Count and move
first merge them into one item, so a count sets the drawer's real total and a
move never leaves a stray piece behind.

Like the withdrawal, an error after the first write is reported as "maybe
registered": the POST may have been applied even if the answer never arrived.
"""

from __future__ import annotations

from dataclasses import dataclass

from .inventree import InvenTreeError
from .models import Location, StockItem
from .phrases import fmt_qty
from .search import DOWN_PHRASE

INVALID_QTY = "Dime una cantidad válida."
UNKNOWN_DRAWER = "Ese cajón no está en el inventario."
SAME_DRAWER = "El origen y el destino son el mismo cajón."
MAYBE_STOCK = "No sé si se ha registrado. Revisa la cantidad antes de repetirlo."


@dataclass(frozen=True)
class OpResult:
    phrase: str
    done: bool = False
    address: int | None = None
    low: bool = False
    part_pk: int | None = None
    location_pk: int | None = None


class _Writes:
    """Tracks whether anything may already have been written to InvenTree."""

    def __init__(self) -> None:
        self.started = False


def _find(locations: dict[int, Location], address) -> Location | None:
    return next((loc for loc in locations.values() if loc.address == address), None)


async def _items_in(api, part_pk: int, location: Location) -> list[StockItem]:
    stock = await api.stock_for_part(part_pk)
    # Largest first: the largest item is the one that survives a merge.
    return sorted((s for s in stock if s.location_pk == location.pk), key=lambda s: s.quantity, reverse=True)


async def _single_item(api, items: list[StockItem], location: Location, writes: _Writes) -> StockItem:
    """Merge the drawer's items into one and make sure it survives reaching 0."""
    item = items[0]
    if len(items) > 1:
        writes.started = True
        await api.merge_stock([s.pk for s in items], location.pk, "cajonera: fusionar")
        item = await api.get_stock(item.pk)
    if item.delete_on_deplete:
        # InvenTree deletes a stock item that reaches 0; the drawer must keep its place for the part.
        await api.set_keep_when_empty(item.pk)
    return item


async def _tidy_drawer(api, part_pk: int, location: Location) -> None:
    """Merge the part's stock items in a drawer into one (the largest survives)."""
    items = await _items_in(api, part_pk, location)
    if len(items) > 1:
        await api.merge_stock([s.pk for s in items], location.pk, "cajonera: fusionar")
        survivor = await api.get_stock(items[0].pk)
        if survivor.delete_on_deplete:
            await api.set_keep_when_empty(survivor.pk)


async def _finish(api, phrase: str, part_pk: int, location: Location) -> OpResult:
    low = False
    try:
        low = (await api.get_part(part_pk)).is_low
    except InvenTreeError:
        # The write is done; only the "running low" detail is missing.
        pass
    return OpResult(phrase, done=True, address=location.address, low=low, part_pk=part_pk, location_pk=location.pk)


async def add_to_drawer(
    api, part_pk: int, address: int, quantity: float, locations: dict[int, Location]
) -> OpResult:
    if quantity is None or quantity <= 0:
        return OpResult(INVALID_QTY)
    location = _find(locations, address)
    if location is None:
        return OpResult(UNKNOWN_DRAWER)

    writes = _Writes()
    try:
        items = await _items_in(api, part_pk, location)
        if items:
            item = items[0]
            if item.delete_on_deplete:
                await api.set_keep_when_empty(item.pk)
            writes.started = True
            await api.add_stock(item.pk, quantity, "cajonera: añadir")
        else:
            writes.started = True
            await api.create_stock(part_pk, location.pk, quantity)
        total = sum(s.quantity for s in items) + quantity
        return await _finish(api, f"Hecho. Ahora hay {fmt_qty(total)} en el {location.name}.", part_pk, location)
    except InvenTreeError:
        return OpResult(MAYBE_STOCK if writes.started else DOWN_PHRASE)


async def count_drawer(api, part_pk: int, address: int, quantity: float, locations) -> OpResult:
    if quantity is None or quantity < 0:
        return OpResult(INVALID_QTY)
    location = _find(locations, address)
    if location is None:
        return OpResult(UNKNOWN_DRAWER)

    writes = _Writes()
    try:
        items = await _items_in(api, part_pk, location)
        if not items:
            if quantity > 0:
                writes.started = True
                await api.create_stock(part_pk, location.pk, quantity)
        else:
            item = await _single_item(api, items, location, writes)
            writes.started = True
            await api.count_stock(item.pk, quantity, "cajonera: recuento")
        return await _finish(api, f"Hecho. El {location.name} tiene ahora {fmt_qty(quantity)}.", part_pk, location)
    except InvenTreeError:
        return OpResult(MAYBE_STOCK if writes.started else DOWN_PHRASE)


async def move_between(
    api, part_pk: int, from_address: int, to_address: int, quantity: float | None, locations
) -> OpResult:
    if quantity is not None and quantity <= 0:
        return OpResult(INVALID_QTY)
    source = _find(locations, from_address)
    target = _find(locations, to_address)
    if source is None or target is None:
        return OpResult(UNKNOWN_DRAWER)
    if source.pk == target.pk:
        return OpResult(SAME_DRAWER)

    writes = _Writes()
    try:
        items = await _items_in(api, part_pk, source)
        total = sum(s.quantity for s in items)
        if total <= 0:
            name = (await api.get_part(part_pk)).name
            return OpResult(f"No hay {name} en el {source.name}.")
        if quantity is not None and quantity > total:
            return OpResult(f"Solo hay {fmt_qty(total)} en el {source.name}.")

        moved = total if quantity is None else quantity
        item = await _single_item(api, items, source, writes)
        writes.started = True
        await api.transfer_stock(item.pk, moved, target.pk, "cajonera: mover")
        phrase = f"Hecho. He movido {fmt_qty(moved)} del {source.name} al {target.name}."
        # A partial transfer creates a new stock item in the target, next to any the part
        # already has there. Tidy up so the drawer keeps one item per part. The move is already
        # done, so a failure here must not turn it into a failure.
        try:
            await _tidy_drawer(api, part_pk, target)
        except InvenTreeError:
            pass
        return await _finish(api, phrase, part_pk, target)
    except InvenTreeError:
        return OpResult(MAYBE_STOCK if writes.started else DOWN_PHRASE)
