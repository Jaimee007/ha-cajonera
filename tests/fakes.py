"""In-memory stand-in for InvenTreeClient, with the same async methods."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from logic.inventree import InvenTreeError
from logic.models import Category, Location, Part, StockItem
from logic.naming import ROOT_LOCATION


class FakeInventory:
    ROOT_PK = 1

    def __init__(
        self,
        parts: list[Part] = (),
        stock: list[StockItem] = (),
        locations: list[Location] = (),
        categories: list[Category] = (),
    ):
        self.parts = {p.pk: p for p in parts}
        self.stock = {s.pk: s for s in stock}
        self.locations = {loc.pk: loc for loc in locations}
        self.category_list = {c.pk: c for c in categories}
        self.fail = False
        self.fail_on: set[str] = set()  # method names that raise InvenTreeError
        self.delay = 0.0  # seconds every call waits, to simulate a slow InvenTree
        self.search_calls = 0
        self.removed: list[tuple[int, float]] = []
        self.renamed: list[tuple[int, str]] = []
        self.created: list[Location] = []
        self.added: list[tuple[int, float]] = []
        self.counted: list[tuple[int, float]] = []
        self.transferred: list[tuple[int, float, int]] = []
        self.merged: list[tuple[list[int], int]] = []
        self.created_parts: list[Part] = []
        self.created_categories: list[Category] = []
        self.patched: list[tuple[int, dict]] = []
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

    def _set_quantity(self, stock_pk: int, quantity: float) -> None:
        """Change an item's quantity, keeping the part total in step. Like InvenTree,
        an item that reaches 0 is deleted when `delete_on_deplete` is True."""
        item = self.stock[stock_pk]
        part = self.parts[item.part_pk]
        self.parts[part.pk] = replace(part, total_stock=part.total_stock + quantity - item.quantity)
        if quantity <= 0 and item.delete_on_deplete:
            del self.stock[stock_pk]
        else:
            self.stock[stock_pk] = replace(item, quantity=quantity)

    async def remove_stock(self, stock_pk: int, quantity: float, notes: str) -> None:
        await self._io("remove_stock")
        self._set_quantity(stock_pk, self.stock[stock_pk].quantity - quantity)
        self.removed.append((stock_pk, quantity))
        self.last_notes = notes

    async def add_stock(self, stock_pk: int, quantity: float, notes: str) -> None:
        await self._io("add_stock")
        self._set_quantity(stock_pk, self.stock[stock_pk].quantity + quantity)
        self.added.append((stock_pk, quantity))
        self.last_notes = notes

    async def count_stock(self, stock_pk: int, quantity: float, notes: str) -> None:
        await self._io("count_stock")
        self._set_quantity(stock_pk, quantity)
        self.counted.append((stock_pk, quantity))
        self.last_notes = notes

    async def transfer_stock(self, stock_pk: int, quantity: float, location_pk: int, notes: str) -> None:
        await self._io("transfer_stock")
        item = self.stock[stock_pk]
        if quantity >= item.quantity:
            self.stock[stock_pk] = replace(item, location_pk=location_pk)
        else:
            # A partial transfer splits the item: the remainder stays, a new item moves.
            self.stock[stock_pk] = replace(item, quantity=item.quantity - quantity)
            self._next_pk += 1
            self.stock[self._next_pk] = replace(item, pk=self._next_pk, quantity=quantity, location_pk=location_pk)
        self.transferred.append((stock_pk, quantity, location_pk))
        self.last_notes = notes

    async def merge_stock(self, stock_pks: list[int], location_pk: int, notes: str) -> None:
        await self._io("merge_stock")
        first = self.stock[stock_pks[0]]
        total = sum(self.stock[pk].quantity for pk in stock_pks)
        for pk in stock_pks[1:]:
            del self.stock[pk]
        self.stock[first.pk] = replace(first, quantity=total, location_pk=location_pk)
        self.merged.append((list(stock_pks), location_pk))
        self.last_notes = notes

    async def create_stock(self, part_pk: int, location_pk: int, quantity: float) -> StockItem:
        await self._io("create_stock")
        self._next_pk += 1
        part = self.parts[part_pk]
        item = StockItem(self._next_pk, part_pk, part.name, quantity, location_pk, delete_on_deplete=False)
        self.stock[item.pk] = item
        self.parts[part_pk] = replace(part, total_stock=part.total_stock + quantity)
        return item

    async def set_keep_when_empty(self, stock_pk: int) -> None:
        await self._io("set_keep_when_empty")
        self.stock[stock_pk] = replace(self.stock[stock_pk], delete_on_deplete=False)
        self.patched.append((stock_pk, {"delete_on_deplete": False}))

    async def stock_in_category(self, category_pk: int, root_pk: int) -> list[StockItem]:
        await self._io("stock_in_category")
        return [s for s in self.stock.values() if self.parts[s.part_pk].category_pk == category_pk]

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
        await self._io("create_part")
        self._next_pk += 1
        part = Part(self._next_pk, name, minimum, max(quantity, 0), ipn, category_pk, description, keywords)
        self.parts[part.pk] = part
        if quantity > 0:
            # InvenTree's initial_stock uses the model default (delete_on_deplete=True),
            # so callers must run set_keep_when_empty themselves. Quantity 0 adds no stock.
            self._next_pk += 1
            self.stock[self._next_pk] = StockItem(self._next_pk, part.pk, name, quantity, location_pk)
        # The real POST /api/part/ response is not annotated: it reports total 0.
        returned = replace(part, total_stock=0.0)
        self.created_parts.append(returned)
        return returned

    async def update_part(self, pk: int, **fields) -> None:
        await self._io("update_part")
        mapping = {"IPN": "ipn", "category": "category_pk", "minimum_stock": "minimum_stock"}
        self.parts[pk] = replace(self.parts[pk], **{mapping.get(k, k): v for k, v in fields.items()})
        self.patched.append((pk, dict(fields)))

    async def categories(self) -> list[Category]:
        await self._io("categories")
        return list(self.category_list.values())

    async def create_category(self, name: str, parent_pk: int | None = None) -> Category:
        await self._io("create_category")
        self._next_pk += 1
        parent = self.category_list.get(parent_pk)
        path = f"{parent.path}/{name}" if parent else name
        cat = Category(self._next_pk, name, path)
        self.category_list[cat.pk] = cat
        self.created_categories.append(cat)
        return cat

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
