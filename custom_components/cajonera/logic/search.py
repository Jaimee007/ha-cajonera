"""Inventory search and its spoken answer (spec section 4.2).

The same function serves Alexa and the HA panel; only how the phrase is shown
differs, and that is the caller's business.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace

from .inventree import InvenTreeError
from .models import Location, Part, StockItem
from .phrases import fmt_qty, join_es

MAX_LIT = 10  # drawers lit at once (spec section 5.4 of 01)
DOWN_PHRASE = "No puedo consultar el inventario ahora mismo."
NO_MATCH_PHRASE = "No encuentro nada con ese nombre."


@dataclass(frozen=True)
class Hit:
    part: Part
    stock: StockItem
    location: Location


@dataclass(frozen=True)
class SearchResult:
    phrase: str
    hits: list[Hit] = field(default_factory=list)
    addresses: list[int] = field(default_factory=list)  # drawers to light, at most MAX_LIT
    part_pks: list[int] = field(default_factory=list)  # parts that are in some drawer


def is_unplaced(location: Location) -> bool:
    """A drawer location renamed to "Cajón NN (sin posición)" by the sync."""
    return location.name.startswith("Cajón ")


def _where(location: Location) -> str:
    if is_unplaced(location):
        return f"el cajón {location.address}"
    return f"el {location.name}"


def _unique(items):
    seen = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def _by_drawer_phrase(by_part: list[tuple[Part, list[Hit]]]) -> str:
    """Several parts answered drawer by drawer: "En el B3 están A, B y C. En el A2 está D."

    A person looks in a drawer, so the drawer comes first and its parts are listed once, however many parts share it.
    A part that sits in two drawers appears in both. At most three drawers and three parts per drawer are named."""
    drawers: dict[int, tuple[Location, list[str]]] = {}
    for part, hits in by_part:
        for hit in hits:
            _, names = drawers.setdefault(hit.location.pk, (hit.location, []))
            if part.name not in names:
                names.append(part.name)
    clauses = []
    for location, names in list(drawers.values())[:3]:
        shown = names[:3] + ([f"{len(names) - 3} más"] if len(names) > 3 else [])
        verb = "está" if len(names) == 1 else "están"
        clauses.append(f"En {_where(location)} {verb} {join_es(shown)}.")
    if len(drawers) > 3:
        clauses.append(f"Y en {len(drawers) - 3} cajones más.")
    return " ".join(clauses)


def _low_suffix(part: Part) -> str:
    if not part.is_low:
        return ""
    return f" Ojo, quedan pocos: {fmt_qty(part.total_stock)}, y el mínimo es {fmt_qty(part.minimum_stock)}."


def _one_part_phrase(part: Part, hits: list[Hit]) -> str:
    placed = [h for h in hits if not is_unplaced(h.location)]
    if not placed:
        loc = hits[0].location
        return f"Está en el cajón {loc.address}, que ahora no está conectado."
    drawers = _unique(h.location for h in placed)
    if len(drawers) == 1:
        qty = sum(h.stock.quantity for h in placed)
        if qty <= 0:
            return f"Tenías {part.name} en {_where(drawers[0])}, pero no queda ninguno."
        return f"Están en {_where(drawers[0])}. Quedan {fmt_qty(qty)}." + _low_suffix(part)
    names = [f"en {_where(d)}" for d in drawers[:3]]
    if len(drawers) > 3:
        names.append(f"en {len(drawers) - 3} cajones más")
    return f"Hay {join_es(names)}, {fmt_qty(part.total_stock)} en total." + _low_suffix(part)


def group_hits(hits: list[Hit]) -> list[Hit]:
    """One hit per part per drawer: the system counts per drawer, never per stock item.

    The quantity is the sum of the drawer's items of that part. `stock` is the pk of the largest of them
    (ties: lowest pk), kept so callers that still look at it have a real item to point at. A part whose
    drawer total is 0 keeps its hit: it keeps its place in the drawer.
    """
    groups: dict[tuple[int, int], list[Hit]] = {}
    for hit in hits:
        groups.setdefault((hit.part.pk, hit.location.pk), []).append(hit)
    out = []
    for group in groups.values():
        main = min(group, key=lambda h: (-h.stock.quantity, h.stock.pk))
        total = sum(h.stock.quantity for h in group)
        out.append(main if len(group) == 1 else replace(main, stock=replace(main.stock, quantity=total)))
    return out


async def search(api, text: str, locations: dict[int, Location]) -> SearchResult:
    """Find `text` in InvenTree and say which drawers hold it.

    `locations` maps location pk -> Location and holds only the children of
    "Cajonera"; stock anywhere else is not in a drawer.
    """
    text = (text or "").strip()
    if not text:
        return SearchResult("¿Qué quieres buscar?")
    try:
        parts = await api.search_parts(text)
    except InvenTreeError:
        return SearchResult(DOWN_PHRASE)
    return await search_parts_result(api, parts, locations)


async def search_parts_result(
    api, parts: list[Part], locations: dict[int, Location], outside: dict[int, str] | None = None
) -> SearchResult:
    """Say where the given parts are. `outside` maps InvenTree location pk -> name for stock that is not in a
    cabinet drawer: when no part has stock in a drawer, the answer names those places instead."""
    if not parts:
        return SearchResult(NO_MATCH_PHRASE)
    try:
        stocks = await asyncio.gather(*(api.stock_for_part(p.pk) for p in parts))
    except InvenTreeError:
        return SearchResult(DOWN_PHRASE)

    by_part: list[tuple[Part, list[Hit]]] = []
    for part, items in zip(parts, stocks):
        hits = group_hits([Hit(part, s, locations[s.location_pk]) for s in items if s.location_pk in locations])
        if hits:
            by_part.append((part, hits))

    if not by_part:
        first = parts[0]
        if outside:
            places = []
            for items in stocks:
                for s in items:
                    name = outside.get(s.location_pk)
                    if name and name not in places:
                        places.append(name)
            if places:
                total = sum(p.total_stock for p in parts)
                return SearchResult(f"Está en {join_es(places)}. Quedan {fmt_qty(total)}.")
        return SearchResult(f"Hay {fmt_qty(first.total_stock)} de {first.name}, pero no está en ningún cajón.")

    all_hits = [h for _, hits in by_part for h in hits]
    lit = _unique(h.location.address for h in all_hits if not is_unplaced(h.location))

    if len(by_part) == 1:
        part, hits = by_part[0]
        phrase = _one_part_phrase(part, hits)
    else:
        phrase = _by_drawer_phrase(by_part)

    if len(lit) > MAX_LIT:
        phrase += f" Hay {len(lit)} cajones; enciendo los {MAX_LIT} primeros."

    return SearchResult(phrase, all_hits, lit[:MAX_LIT], [part.pk for part, _ in by_part])


async def drawer_contents(api, location: Location) -> list[Hit]:
    """Everything stored in one drawer, for the panel: one hit per part, sorted by part name."""
    items = await api.stock_in_location(location.pk)
    parts = await asyncio.gather(*(api.get_part(pk) for pk in _unique(s.part_pk for s in items)))
    by_pk = {p.pk: p for p in parts}
    hits = group_hits([Hit(by_pk[s.part_pk], s, location) for s in items])
    return sorted(hits, key=lambda h: (h.part.name.casefold(), h.part.pk))
