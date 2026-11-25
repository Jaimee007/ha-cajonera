"""Simulated drawers standing in for the ESP32 controller.

Until the firmware exists, this object owns what the controller will own: the
LED state of every drawer and the 30 s search timer (spec section 7.5). It
follows the priority of spec section 7.3: an open drawer beats any order.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from .models import Drawer
from .naming import drawer_name

COLORS = {
    "reposo": (0, 0, 0),
    "buscado": (0, 0, 255),
    "buscado_bajo": (255, 120, 0),  # the panel shows it alternating with blue
    "encontrado": (0, 255, 0),
    "confirmar_ok": (0, 255, 0),
    "confirmar_bajo": (255, 120, 0),
    "abierto": (255, 255, 255),
    "identificar": (255, 0, 255),
    "guardar": (255, 0, 255),  # "store it here": the drawer to put something into
}


@dataclass(frozen=True)
class DrawerState:
    drawer: Drawer
    name: str | None
    mode: str
    rgb: tuple[int, int, int]
    open: bool


class _Slot:
    def __init__(self, drawer: Drawer):
        self.drawer = drawer
        self.open = False
        self.base = "reposo"  # order layer: reposo / buscado / buscado_bajo / guardar
        self.overlay: str | None = None  # short-lived: encontrado, confirmar_*, identificar
        self.overlay_timer: asyncio.TimerHandle | None = None

    @property
    def mode(self) -> str:
        if self.overlay == "encontrado":
            return "encontrado"
        if self.open:
            return "abierto"
        return self.overlay or self.base


class DrawerSimulator:
    def __init__(
        self,
        drawers: list[Drawer],
        search_seconds: float = 30,
        found_seconds: float = 2,
        flash_seconds: float = 1,
        identify_seconds: float = 3,
    ):
        self._drawers = list(drawers)
        self._slots = {d.address: _Slot(d) for d in drawers}
        self._search_seconds = search_seconds
        self._found_seconds = found_seconds
        self._flash_seconds = flash_seconds
        self._identify_seconds = identify_seconds
        self._search_timer: asyncio.TimerHandle | None = None
        self._store_timer: asyncio.TimerHandle | None = None
        self._listeners: list[Callable[[], None]] = []

    # Reading

    def drawers(self) -> list[Drawer]:
        return list(self._drawers)

    def states(self) -> list[DrawerState]:
        out = []
        for d in self._drawers:
            slot = self._slots[d.address]
            name = drawer_name(d.x, d.y) if d.x is not None and d.y is not None else None
            out.append(DrawerState(d, name, slot.mode, COLORS[slot.mode], slot.open))
        return out

    def add_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    # Orders

    def search(self, addresses: list[int], low: set[int]) -> None:
        """Start a search, replacing any search (or store-here) in progress."""
        self._cancel_search_timer()
        self._cancel_store_timer()
        for address, slot in self._slots.items():
            if address in addresses:
                slot.base = "buscado_bajo" if address in low else "buscado"
            else:
                slot.base = "reposo"
        if any(a in self._slots for a in addresses):
            self._search_timer = asyncio.get_running_loop().call_later(self._search_seconds, self._end_search)
        self._notify()

    def store_here(self, address: int, seconds: float = 30) -> None:
        """Light one drawer to say "put it here", replacing any search or earlier store-here."""
        self._cancel_search_timer()
        self._cancel_store_timer()
        known = address in self._slots
        for a, slot in self._slots.items():
            slot.base = "guardar" if a == address else "reposo"
        if known:
            self._store_timer = asyncio.get_running_loop().call_later(seconds, self._end_store)
        self._notify()

    def clear_store(self) -> None:
        """Put out the "store it here" light. A search in progress is not touched."""
        if any(slot.base == "guardar" for slot in self._slots.values()):
            self._end_store()

    def confirm(self, address: int, low: bool) -> None:
        self._overlay(address, "confirmar_bajo" if low else "confirmar_ok", self._flash_seconds)

    def identify(self, address: int) -> None:
        self._overlay(address, "identificar", self._identify_seconds)

    def set_open(self, address: int, is_open: bool) -> None:
        slot = self._slots.get(address)
        if slot is None or slot.open == is_open:
            return
        slot.open = is_open
        if is_open and slot.base == "guardar":
            # Opened to put something in: no green, nothing was being searched.
            self._end_store(notify=False)
        elif is_open and slot.base != "reposo":
            # Found: green for a moment, and the whole search ends.
            self._end_search(notify=False)
            self._overlay(address, "encontrado", self._found_seconds, notify=False)
        elif is_open:
            self._clear_overlay(slot)
        self._notify()

    # Internals

    def _overlay(self, address: int, mode: str, seconds: float, notify: bool = True) -> None:
        slot = self._slots.get(address)
        if slot is None:
            return
        self._clear_overlay(slot)
        slot.overlay = mode
        slot.overlay_timer = asyncio.get_running_loop().call_later(seconds, self._overlay_done, slot)
        if notify:
            self._notify()

    def _overlay_done(self, slot: _Slot) -> None:
        slot.overlay = None
        slot.overlay_timer = None
        self._notify()

    @staticmethod
    def _clear_overlay(slot: _Slot) -> None:
        if slot.overlay_timer:
            slot.overlay_timer.cancel()
        slot.overlay = None
        slot.overlay_timer = None

    def _cancel_search_timer(self) -> None:
        if self._search_timer:
            self._search_timer.cancel()
            self._search_timer = None

    def _cancel_store_timer(self) -> None:
        if self._store_timer:
            self._store_timer.cancel()
            self._store_timer = None

    def _end_store(self, notify: bool = True) -> None:
        self._cancel_store_timer()
        for slot in self._slots.values():
            if slot.base == "guardar":
                slot.base = "reposo"
        if notify:
            self._notify()

    def _end_search(self, notify: bool = True) -> None:
        self._cancel_search_timer()
        for slot in self._slots.values():
            slot.base = "reposo"
        if notify:
            self._notify()

    def _notify(self) -> None:
        for cb in self._listeners:
            cb()
