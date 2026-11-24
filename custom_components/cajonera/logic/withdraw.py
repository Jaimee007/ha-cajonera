"""Stock withdrawal: "he cogido 3 tornillos M3" (spec section 4.3).

Golden rule: on any ambiguity nothing is written to InvenTree. A withdrawal
from the wrong part is worse than asking the user to repeat.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from .inventree import InvenTreeError
from .models import Location, Part, StockItem
from .phrases import fmt_qty, join_es
from .search import DOWN_PHRASE, NO_MATCH_PHRASE, is_unplaced

CONTEXT_TTL = timedelta(minutes=2)
ASK_QTY = "Dime cuántas has cogido."
ASK_WHAT = "¿Qué has cogido?"


@dataclass(frozen=True)
class Context:
    """The last part and drawer the user dealt with, for "he cogido 3"."""

    part_pk: int
    location_pk: int
    at: datetime


@dataclass(frozen=True)
class WithdrawResult:
    phrase: str
    done: bool = False
    address: int | None = None
    low: bool = False
    context: Context | None = None


def _where(location: Location) -> str:
    return f"el cajón {location.address}" if is_unplaced(location) else f"el {location.name}"


def _bad_quantity(quantity) -> bool:
    return quantity is None or quantity <= 0


async def _remove(api, item: StockItem, quantity: float, now: datetime, locations, notes: str) -> WithdrawResult:
    location = locations[item.location_pk]
    if quantity > item.quantity:
        return WithdrawResult(f"Solo quedan {fmt_qty(item.quantity)} en {_where(location)}.")
    await api.remove_stock(item.pk, quantity, notes)
    part, stock = await asyncio.gather(api.get_part(item.part_pk), api.stock_for_part(item.part_pk))
    left = item.quantity - quantity
    in_drawers = {s.location_pk for s in stock if s.location_pk in locations}
    if len(in_drawers) > 1:
        phrase = f"Hecho. Quedan {fmt_qty(left)} en {_where(location)}, {fmt_qty(part.total_stock)} en total"
    else:
        phrase = f"Hecho. Quedan {fmt_qty(left)}"
    if part.is_low:
        phrase += f", por debajo del mínimo de {fmt_qty(part.minimum_stock)}"
    return WithdrawResult(
        phrase + ".",
        done=True,
        address=None if is_unplaced(location) else location.address,
        low=part.is_low,
        context=Context(part.pk, location.pk, now),
    )


async def withdraw_by_text(
    api,
    text: str | None,
    quantity: float | None,
    drawer: str | None,
    context: Context | None,
    now: datetime,
    locations: dict[int, Location],
) -> WithdrawResult:
    """Voice withdrawal. Rules are applied in order; the first that fits wins."""
    if _bad_quantity(quantity):
        return WithdrawResult(ASK_QTY)
    text = (text or "").strip()
    live_context = context if context and now - context.at <= CONTEXT_TTL else None
    try:
        if not text:
            if live_context is None:
                return WithdrawResult(ASK_WHAT)
            part: Part = await api.get_part(live_context.part_pk)
        else:
            parts = await api.search_parts(text)
            if len(parts) > 1:
                names = join_es([p.name for p in parts[:3]])
                return WithdrawResult(f"Hay varias piezas con ese nombre: {names}. ¿Cuál?")
            if not parts:
                return WithdrawResult(NO_MATCH_PHRASE)
            part = parts[0]

        items = [s for s in await api.stock_for_part(part.pk) if s.location_pk in locations]
        if not items:
            return WithdrawResult(f"Hay {fmt_qty(part.total_stock)} de {part.name}, pero no está en ningún cajón.")

        chosen = None
        if len(items) == 1:
            chosen = items[0]
        if chosen is None and drawer:
            wanted = drawer.strip().upper()
            chosen = next((s for s in items if locations[s.location_pk].name.upper() == wanted), None)
        if chosen is None and live_context and live_context.part_pk == part.pk:
            chosen = next((s for s in items if s.location_pk == live_context.location_pk), None)
        if chosen is None:
            where = join_es([f"en {_where(locations[s.location_pk])}" for s in items[:3]])
            return WithdrawResult(f"Hay {where}, ¿de cuál?")

        return await _remove(api, chosen, quantity, now, locations, "cajonera: voz")
    except InvenTreeError:
        return WithdrawResult(DOWN_PHRASE)


async def withdraw_by_stock(
    api, stock_pk: int, quantity: float, now: datetime, locations: dict[int, Location]
) -> WithdrawResult:
    """Panel withdrawal: the stock item is exact, so it never asks "¿de cuál?"."""
    if _bad_quantity(quantity):
        return WithdrawResult(ASK_QTY)
    try:
        item = await api.get_stock(stock_pk)
        if item.location_pk not in locations:
            return WithdrawResult("Esa existencia no está en ningún cajón.")
        return await _remove(api, item, quantity, now, locations, "cajonera: panel")
    except InvenTreeError:
        return WithdrawResult(DOWN_PHRASE)
