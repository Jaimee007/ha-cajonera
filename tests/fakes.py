"""In-memory stand-in for InvenTreeClient, with the same async methods."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from logic.inventree import InvenTreeError
from logic.models import Location, Part, StockItem
from logic.naming import ROOT_LOCATION


class FakeInventory:
    ROOT_PK = 1

    def __init__(self, parts: list[Part] = (), stock: list[StockItem] = (), locations: list[Location] = ()):
        self.parts = {p.pk: p for p in parts}
        self.stock = {s.pk: s for s in stock}
        self.locations = {loc.pk: loc for loc in locations}
        self.fail = False
        self.fail_on: set[str] = set()  # method names that raise InvenTreeError
        self.delay = 0.0  # seconds every call waits, to simulate a slow InvenTree
        self.search_calls = 0
        self.removed: list[tuple[int, float]] = []
        self.renamed: list[tuple[int, str]] = []
        self.created: list[Location] = []
        self._next_pk = 1000

    async def _io(self, name: str):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail or name in self.fail_on:
            raise InvenTreeError(f"fake InvenTree failed in {name}")

    async def search_parts(self, text: str, limit: int = 10) -> list[Part]:
        await self._io("search_parts")
        self.search_calls += 1
        words = text.lower().split()
        hits = [p for p in self.parts.values() if all(w in p.name.lower() for w in words)]
        return hits[:limit]

    async def get_part(self, pk: int) -> Part:
        await self._io("get_part")
        return self.parts[pk]

    async def low_stock_parts(self) -> list[Part]:
        await self._io("low_stock_parts")
        return [p for p in self.parts.values() if p.is_low]

    async def stock_for_part(self, part_pk: int) -> list[StockItem]:
        await self._io("stock_for_part")
        return [s for s in self.stock.values() if s.part_pk == part_pk]

    async def stock_in_location(self, location_pk: int) -> list[StockItem]:
        await self._io("stock_in_location")
        return [s for s in self.stock.values() if s.location_pk == location_pk]

    async def get_stock(self, pk: int) -> StockItem:
        await self._io("get_stock")
        return self.stock[pk]

    async def remove_stock(self, stock_pk: int, quantity: float, notes: str) -> None:
        await self._io("remove_stock")
        item = self.stock[stock_pk]
        self.stock[stock_pk] = replace(item, quantity=item.quantity - quantity)
        part = self.parts[item.part_pk]
        self.parts[part.pk] = replace(part, total_stock=part.total_stock - quantity)
        self.removed.append((stock_pk, quantity))
        self.last_notes = notes

    async def ensure_root(self) -> int:
        await self._io("ensure_root")
        return self.ROOT_PK

    async def child_locations(self, parent_pk: int) -> list[Location]:
        await self._io("child_locations")
        return list(self.locations.values())

    async def create_location(self, parent_pk: int, name: str, address: int) -> Location:
        await self._io("create_location")
        self._next_pk += 1
        loc = Location(self._next_pk, name, address)
        self.locations[loc.pk] = loc
        self.created.append(loc)
        return loc

    async def set_location_address(self, pk: int, address: int) -> None:
        await self._io("set_location_address")
        self.locations[pk] = replace(self.locations[pk], address=address)

    async def rename_location(self, pk: int, name: str) -> None:
        await self._io("rename_location")
        self.locations[pk] = replace(self.locations[pk], name=name)
        self.renamed.append((pk, name))


assert ROOT_LOCATION  # keep the import meaningful for readers: the fake's root is "Cajonera"
