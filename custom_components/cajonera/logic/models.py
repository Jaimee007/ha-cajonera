"""Plain data shared by the cajonera logic. No Home Assistant imports."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Drawer:
    """A drawer as the controller reports it.

    `x`/`y` are None when the drawer has no position (no cable path to the
    base). `error` is one of "duplicada", "fuera_reticula", "pareja" or None.
    A double drawer is a single Drawer whose address is the primary module's.
    """

    address: int
    x: int | None
    y: int | None
    width: int = 1
    height: int = 1
    error: str | None = None


@dataclass(frozen=True)
class Location:
    """An InvenTree stock location. `address` comes from its metadata."""

    pk: int
    name: str
    address: int | None


@dataclass(frozen=True)
class Part:
    pk: int
    name: str
    minimum_stock: float
    total_stock: float
    ipn: str = ""
    category_pk: int | None = None
    description: str = ""
    keywords: str = ""

    @property
    def is_low(self) -> bool:
        # A part without a minimum is never low (spec section 6).
        return self.minimum_stock > 0 and self.total_stock < self.minimum_stock


@dataclass(frozen=True)
class StockItem:
    pk: int
    part_pk: int
    part_name: str
    quantity: float
    location_pk: int | None
    delete_on_deplete: bool = True


@dataclass(frozen=True)
class Category:
    """A part category. `path` is the full path string, e.g. "Ferreteria/Tornillos"."""

    pk: int
    name: str
    path: str
