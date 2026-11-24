"""The cajonera services, free of Home Assistant so they can be tested.

`__init__.py` only registers these methods as HA services and forwards
notifications. Every public method answers within `deadline` seconds, because
the Alexa Lambda gives up after 4 s (spec section 4.4).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from .context import context_after_search
from .inventree import InvenTreeError
from .low_stock import LowStockReport, low_stock_report
from .models import Location
from .search import DOWN_PHRASE, drawer_contents, search
from .simulator import DrawerSimulator
from .sync import apply_sync, plan_sync
from .withdraw import ASK_QTY, MAYBE_DONE, Context, withdraw_by_stock, withdraw_by_text

_LOGGER = logging.getLogger(__name__)

DEADLINE = 3.5  # seconds; below the Lambda's 4 s budget


class Cajonera:
    def __init__(
        self,
        api,
        simulator: DrawerSimulator,
        deadline: float = DEADLINE,
        now: Callable[[], datetime] = datetime.now,
        on_notices: Callable[[list[str]], None] | None = None,
        on_change: Callable[[], None] | None = None,
    ):
        self.api = api
        self.simulator = simulator
        self.deadline = deadline
        self.now = now
        self.on_notices = on_notices or (lambda notices: None)
        self.on_change = on_change or (lambda: None)
        self.root_pk: int | None = None
        self.locations: dict[int, Location] = {}
        self.synced = False  # locations match the drawers and are loaded
        self._stock_marked = False  # every cabinet item is flagged keep-when-empty
        self.context: Context | None = None
        self.low = LowStockReport()
        self._sync_lock = asyncio.Lock()

    # Background work (also called inline, bounded by the deadline)

    async def sync(self) -> list[str]:
        """Create and rename InvenTree locations so they match the drawers.

        Raises InvenTreeError if InvenTree fails; `synced` stays False so the
        next tick retries.
        """
        async with self._sync_lock:
            if self.root_pk is None:
                self.root_pk = await self.api.ensure_root()
            current = await self.api.child_locations(self.root_pk)
            plan = plan_sync(self.simulator.drawers(), current)
            await apply_sync(self.api, self.root_pk, plan)
            self.locations = {loc.pk: loc for loc in await self.api.child_locations(self.root_pk)}
            self.synced = True
        if plan.notices:
            self.on_notices(plan.notices)
        return plan.notices

    async def mark_stock_keep_when_empty(self) -> None:
        """Stop InvenTree deleting a cabinet item when its last unit is withdrawn.

        Otherwise "no queda ninguno" would be indistinguishable from "never had any".
        Runs outside sync() and outside any deadline (from tick() or the manual sync):
        it is not needed to answer, and a cancellation must not cost sync its notices.
        `_stock_marked` becomes True only when the whole pass completes; on an
        InvenTreeError it is logged and the next tick retries. Withdrawals also
        mark an item before emptying it, so a gap here is never destructive.
        Stock outside the cabinet is never touched.
        """
        try:
            for pk in list(self.locations):
                for item in await self.api.stock_in_location(pk):
                    if item.delete_on_deplete:
                        await self.api.set_keep_when_empty(item.pk)
        except InvenTreeError as err:
            _LOGGER.warning("Could not mark cabinet stock as keep-when-empty: %s", err)
            return
        self._stock_marked = True

    async def refresh_low(self) -> None:
        try:
            self.low = await low_stock_report(self.api, self.locations)
        except InvenTreeError as err:
            _LOGGER.warning("Low-stock refresh failed: %s", err)
            return
        self.on_change()

    async def tick(self) -> None:
        """Periodic: retry a pending sync, else refresh locations; then low stock."""
        try:
            if not self.synced:
                await self.sync()
            else:
                self.locations = {loc.pk: loc for loc in await self.api.child_locations(self.root_pk)}
        except InvenTreeError as err:
            _LOGGER.warning("Cajonera refresh failed: %s", err)
            return
        if not self._stock_marked:
            await self.mark_stock_keep_when_empty()
        await self.refresh_low()

    async def _ready(self) -> bool:
        if self.synced:
            return True
        try:
            await self.sync()
        except InvenTreeError as err:
            _LOGGER.warning("InvenTree not ready: %s", err)
            return False
        return True

    async def _bounded(self, coro, on_timeout):
        try:
            return await asyncio.wait_for(coro, self.deadline)
        except asyncio.TimeoutError:
            return on_timeout

    # Services

    async def buscar(self, texto: str | None) -> dict:
        if not (texto or "").strip():
            return {"frase": "¿Qué quieres buscar?", "resultados": []}
        return await self._bounded(self._buscar(texto), {"frase": DOWN_PHRASE, "resultados": []})

    async def _buscar(self, texto: str) -> dict:
        if not await self._ready():
            return {"frase": DOWN_PHRASE, "resultados": []}
        result = await search(self.api, texto, self.locations)
        if result.phrase != DOWN_PHRASE:
            self.context = context_after_search(result, self.now())
        low = {h.location.address for h in result.hits if h.part.is_low}
        if result.addresses:
            self.simulator.search(result.addresses, low)
        return {
            "frase": result.phrase,
            "resultados": [
                {
                    "stock": h.stock.pk,
                    "pieza": h.part.name,
                    "cajon": h.location.name,
                    "direccion": h.location.address,
                    "cantidad": h.stock.quantity,
                    "bajo": h.part.is_low,
                }
                for h in result.hits
            ],
        }

    async def retirar(
        self,
        texto: str | None = None,
        cantidad: float | None = None,
        cajon: str | None = None,
        stock: int | None = None,
    ) -> dict:
        if cantidad is None or cantidad <= 0:
            return {"frase": ASK_QTY, "hecho": False}
        return await self._bounded(
            self._retirar(texto, cantidad, cajon, stock),
            # Past the deadline a removal may already be in flight: never say "failed".
            {"frase": MAYBE_DONE, "hecho": False},
        )

    async def _retirar(self, texto, cantidad, cajon, stock) -> dict:
        if not await self._ready():
            return {"frase": DOWN_PHRASE, "hecho": False}
        now = self.now()
        if stock is not None:
            result = await withdraw_by_stock(self.api, stock, cantidad, now, self.locations)
        else:
            result = await withdraw_by_text(self.api, texto, cantidad, cajon, self.context, now, self.locations)
        if result.context is not None:
            self.context = result.context
        if result.done:
            if result.address is not None:
                self.simulator.confirm(result.address, result.low)
            asyncio.get_running_loop().create_task(self.refresh_low())
        return {"frase": result.phrase, "hecho": result.done}

    async def contenido(self, direccion: int) -> dict:
        return await self._bounded(self._contenido(direccion), {"cajon": None, "piezas": [], "frase": DOWN_PHRASE})

    async def _contenido(self, direccion: int) -> dict:
        if not await self._ready():
            return {"cajon": None, "piezas": [], "frase": DOWN_PHRASE}
        location = next((loc for loc in self.locations.values() if loc.address == direccion), None)
        if location is None:
            return {"cajon": None, "piezas": [], "frase": "Ese cajón no está en el inventario."}
        try:
            hits = await drawer_contents(self.api, location)
        except InvenTreeError:
            return {"cajon": location.name, "piezas": [], "frase": DOWN_PHRASE}
        return {
            "cajon": location.name,
            "piezas": [
                {"stock": h.stock.pk, "pieza": h.part.name, "cantidad": h.stock.quantity, "bajo": h.part.is_low}
                for h in hits
            ],
        }

    async def sincronizar(self) -> dict:
        async def run():
            try:
                notices = await self.sync()
            except InvenTreeError:
                return {"avisos": [DOWN_PHRASE]}
            self._stock_marked = False
            await self.mark_stock_keep_when_empty()
            await self.refresh_low()
            return {"avisos": notices}

        # A manual sync may legitimately take longer than a voice answer.
        return await self._bounded(run(), {"avisos": ["La sincronización sigue en curso."]})
