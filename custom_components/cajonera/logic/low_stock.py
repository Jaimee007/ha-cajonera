"""Which parts are below their minimum, and in which drawers (spec section 6)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .models import Location, Part


@dataclass(frozen=True)
class LowPart:
    part: Part
    drawers: list[str]  # names of the cajonera drawers that hold it


@dataclass(frozen=True)
class LowStockReport:
    parts: list[LowPart] = field(default_factory=list)
    addresses: set[int] = field(default_factory=set)  # drawers to paint red in the panel


async def low_stock_report(api, locations: dict[int, Location]) -> LowStockReport:
    # Re-check is_low ourselves: the API filter is a hint, the rule is ours.
    parts = [p for p in await api.low_stock_parts() if p.is_low]
    stocks = await asyncio.gather(*(api.stock_for_part(p.pk) for p in parts))
    out, addresses = [], set()
    for part, items in zip(parts, stocks):
        locs = []
        for s in items:
            loc = locations.get(s.location_pk)
            if loc and loc not in locs:
                locs.append(loc)
        out.append(LowPart(part, [loc.name for loc in locs]))
        addresses |= {loc.address for loc in locs}
    return LowStockReport(out, addresses)
