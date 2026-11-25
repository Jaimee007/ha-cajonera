"""Stock withdrawal: "he cogido 3 tornillos M3" (spec section 4.3).

Golden rule: on any ambiguity nothing is written to InvenTree. A withdrawal
from the wrong part is worse than asking the user to repeat.

Ambiguity is decided per drawer, not per stock item: InvenTree often keeps
several stock items of one part in the same location, and the user only
knows the drawer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .inventree import InvenTreeError
from .models import Location, Part, StockItem
from .phrases import fmt_qty, join_es
from .search import DOWN_PHRASE, NO_MATCH_PHRASE, is_unplaced

CONTEXT_TTL = timedelta(minutes=2)
ASK_QTY = "Dime cuántas has cogido."
ASK_WHAT = "¿Qué has cogido?"
MAYBE_DONE = "No sé si se ha registrado. Pregúntame dónde están antes de repetirlo."


@dataclass(frozen=True)
class Context:
    """The last part (and drawer, if there was only one) the user dealt with."""

    part_pk: int
    location_pk: int | None
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


def _by_drawer(items: list[StockItem], locations: dict[int, Location]) -> dict[int, list[StockItem]]:
    """Stock items in the cajonera, grouped by location pk, in first-seen order."""
    out: dict[int, list[StockItem]] = {}
    for s in items:
        if s.location_pk in locations:
            out.setdefault(s.location_pk, []).append(s)
    return out


async def _remove(
    api, part: Part, location: Location, items: list[StockItem], quantity: float, now: datetime, locations, notes: str
) -> WithdrawResult:
    in_drawer = sum(s.quantity for s in items)
    if quantity > in_drawer:
        return WithdrawResult(f"Solo quedan {fmt_qty(in_drawer)} en {_where(location)}.")

    # Take from the largest items first, so one removal usually suffices.
    plan: list[tuple[StockItem, float]] = []
    pending = quantity
    for item in sorted(items, key=lambda s: s.quantity, reverse=True):
        if pending <= 0:
            break
        take = min(item.quantity, pending)
        if take > 0:
            plan.append((item, take))
            pending -= take

    # An item emptied by this withdrawal must survive it (InvenTree deletes it at 0 unless
    # told otherwise), or "no queda ninguno" could not be told from "never had any". This is
    # a write before the removal, so a failure here still means nothing was removed.
    try:
        for item, take in plan:
            if item.delete_on_deplete and take >= item.quantity:
                await api.set_keep_when_empty(item.pk)
    except InvenTreeError:
        return WithdrawResult(DOWN_PHRASE)

    removed_any = False
    try:
        for item, take in plan:
            await api.remove_stock(item.pk, take, notes)
            removed_any = True
    except InvenTreeError:
        # The POST may have been applied even if the answer never arrived (spec 4.4).
        # Saying "it failed" would invite a double withdrawal.
        return WithdrawResult(MAYBE_DONE, done=False, context=Context(part.pk, location.pk, now) if removed_any else None)

    left = in_drawer - quantity
    address = None if is_unplaced(location) else location.address
    context = Context(part.pk, location.pk, now)
    try:
        part = await api.get_part(part.pk)
        stock = await api.stock_for_part(part.pk)
    except InvenTreeError:
        # The withdrawal is done; only the extra detail is missing.
        return WithdrawResult(f"Hecho. Quedan {fmt_qty(left)}.", done=True, address=address, context=context)

    drawers = _by_drawer(stock, locations)
    if len(drawers) > 1:
        phrase = f"Hecho. Quedan {fmt_qty(left)} en {_where(location)}, {fmt_qty(part.total_stock)} en total"
    else:
        phrase = f"Hecho. Quedan {fmt_qty(left)}"
    if part.is_low:
        phrase += f", por debajo del mínimo de {fmt_qty(part.minimum_stock)}"
    return WithdrawResult(phrase + ".", done=True, address=address, low=part.is_low, context=context)


async def candidate_parts(api, text: str, locations: dict[int, Location]) -> list[Part]:
    """The parts a spoken or typed name can mean, narrowed the way a person would.

    An exact (case-insensitive) name wins on its own; otherwise only the parts that sit in a cabinet drawer
    count, so a look-alike that is in no drawer never makes a clear request ambiguous; if none is stocked,
    every match is returned.
    """
    parts = await api.search_parts(text)
    exact = [p for p in parts if p.name.casefold() == text.casefold()]
    if len(exact) == 1:
        return exact
    if len(parts) > 1:
        stocked = [p for p in parts if _by_drawer(await api.stock_for_part(p.pk), locations)]
        if stocked:
            return stocked
    return parts


async def _resolve_part(api, text: str, locations: dict[int, Location]) -> tuple[Part | None, str | None]:
    """The single part the text means, or a phrase explaining why there isn't one."""
    parts = await candidate_parts(api, text, locations)
    if not parts:
        return None, NO_MATCH_PHRASE
    if len(parts) > 1:
        return None, f"Hay varias piezas con ese nombre: {join_es([p.name for p in parts[:3]])}. ¿Cuál?"
    return parts[0], None


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
    drawer = (drawer or "").strip()
    live = context if context and now - context.at <= CONTEXT_TTL else None
    try:
        if not text:
            if live is None:
                return WithdrawResult(ASK_WHAT)
            part = await api.get_part(live.part_pk)
        else:
            part, why = await _resolve_part(api, text, locations)
            if part is None:
                return WithdrawResult(why)

        drawers = _by_drawer(await api.stock_for_part(part.pk), locations)
        if not drawers:
            return WithdrawResult(f"Hay {fmt_qty(part.total_stock)} de {part.name}, pero no está en ningún cajón.")
        where_all = join_es([f"en {_where(locations[pk])}" for pk in list(drawers)[:3]])

        chosen: int | None = None
        if drawer:
            # An explicit drawer is either honoured or refused, never ignored.
            chosen = next((pk for pk in drawers if locations[pk].name.upper() == drawer.upper()), None)
            if chosen is None:
                return WithdrawResult(f"No hay {part.name} en el {drawer.upper()}; hay {where_all}.")
        elif len(drawers) == 1:
            chosen = next(iter(drawers))
        elif live and live.part_pk == part.pk and live.location_pk in drawers:
            chosen = live.location_pk
        if chosen is None:
            return WithdrawResult(f"Hay {where_all}, ¿de cuál?")

        return await _remove(api, part, locations[chosen], drawers[chosen], quantity, now, locations, "cajonera: voz")
    except InvenTreeError:
        return WithdrawResult(DOWN_PHRASE)


async def withdraw_by_part(
    api,
    part_pk: int,
    quantity: float | None,
    drawer: str | None,
    now: datetime,
    locations: dict[int, Location],
    notes: str = "cajonera: panel",
) -> WithdrawResult:
    """Withdrawal of a known part: no name resolution, same drawer rules as the voice route.

    `notes` is what InvenTree's stock history records ("cajonera: panel" or "cajonera: voz")."""
    if _bad_quantity(quantity):
        return WithdrawResult(ASK_QTY)
    drawer = (drawer or "").strip()
    try:
        part = await api.get_part(part_pk)
        drawers = _by_drawer(await api.stock_for_part(part.pk), locations)
        if not drawers:
            return WithdrawResult(f"Hay {fmt_qty(part.total_stock)} de {part.name}, pero no está en ningún cajón.")
        where_all = join_es([f"en {_where(locations[pk])}" for pk in list(drawers)[:3]])
        if drawer:
            # An explicit drawer is either honoured or refused, never ignored.
            chosen = next((pk for pk in drawers if locations[pk].name.upper() == drawer.upper()), None)
            if chosen is None:
                return WithdrawResult(f"No hay {part.name} en el {drawer.upper()}; hay {where_all}.")
        elif len(drawers) == 1:
            chosen = next(iter(drawers))
        else:
            return WithdrawResult(f"Hay {where_all}, ¿de cuál?")
        return await _remove(api, part, locations[chosen], drawers[chosen], quantity, now, locations, notes)
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
        part = await api.get_part(item.part_pk)
    except InvenTreeError:
        return WithdrawResult(DOWN_PHRASE)
    return await _remove(api, part, locations[item.location_pk], [item], quantity, now, locations, "cajonera: panel")
