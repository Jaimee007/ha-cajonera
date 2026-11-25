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

from .catalog import (
    NAME_REQUIRED,
    create_category,
    create_part,
    edit_part,
    list_categories,
    part_sheet,
    suggest,
    suggest_drawer,
)
from .context import context_after_search
from .inventree import InvenTreeError
from .low_stock import LowStockReport, low_stock_report
from .models import Location
from .search import DOWN_PHRASE, MAX_LIT, drawer_contents, search
from .simulator import DrawerSimulator
from .stock_ops import INVALID_QTY, MAYBE_STOCK, OpResult, add_to_drawer, count_drawer, move_between
from .sync import apply_sync, plan_sync
from .voice import VoiceResult, voz as voice_order
from .withdraw import ASK_QTY, MAYBE_DONE, Context, withdraw_by_part, withdraw_by_stock, withdraw_by_text

_LOGGER = logging.getLogger(__name__)

DEADLINE = 3.5  # seconds; below the Lambda's 4 s budget
# An idempotent write (a PATCH, a find-or-create) can be repeated safely, so it says nothing about quantities.
MAYBE_SAVED = "No sé si se ha guardado. Revisa antes de repetirlo."


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
        self._tasks: set[asyncio.Task] = set()  # background work; a reference keeps it from being collected
        self._read_refresh: asyncio.Task | None = None  # the low-stock refresh a read started, while it runs

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

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _refresh_low_after_read(self) -> None:
        """A read just saw fresh InvenTree data, so the low-stock report may be stale (someone edited
        InvenTree by hand). Refresh it in the background; one at a time, a read during a refresh adds none."""
        if self._read_refresh is not None and not self._read_refresh.done():
            return
        self._read_refresh = self._spawn(self.refresh_low())

    async def drain(self) -> None:
        """Wait for the background work in flight (for tests)."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

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
            self._refresh_low_after_read()
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
                    "pieza_pk": h.part.pk,
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
        pieza_pk: int | None = None,
    ) -> dict:
        if cantidad is None or cantidad <= 0:
            return {"frase": ASK_QTY, "hecho": False}
        return await self._bounded(
            self._retirar(texto, cantidad, cajon, stock, pieza_pk),
            # Past the deadline a removal may already be in flight: never say "failed".
            {"frase": MAYBE_DONE, "hecho": False},
        )

    async def _retirar(self, texto, cantidad, cajon, stock, pieza_pk=None) -> dict:
        if not await self._ready():
            return {"frase": DOWN_PHRASE, "hecho": False}
        now = self.now()
        if stock is not None:
            result = await withdraw_by_stock(self.api, stock, cantidad, now, self.locations)
        elif pieza_pk is not None:
            result = await withdraw_by_part(self.api, pieza_pk, cantidad, cajon, now, self.locations)
        else:
            result = await withdraw_by_text(self.api, texto, cantidad, cajon, self.context, now, self.locations)
        if result.context is not None:
            self.context = result.context
        if result.done:
            if result.address is not None:
                self.simulator.confirm(result.address, result.low)
            self._spawn(self.refresh_low())
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
        self._refresh_low_after_read()
        return {
            "cajon": location.name,
            "piezas": [
                {"stock": h.stock.pk, "pieza_pk": h.part.pk, "pieza": h.part.name, "cantidad": h.stock.quantity, "bajo": h.part.is_low}
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

    # Inventory card: suggestions, part sheet, stock writes and catalog

    async def sugerir(self, texto: str | None) -> dict:
        return await self._bounded(self._sugerir(texto), {"sugerencias": []})

    async def _sugerir(self, texto: str | None) -> dict:
        if not await self._ready():
            return {"sugerencias": []}
        # This only answers: the card lights the drawers with iluminar_busqueda when it accepts the answer.
        return {"sugerencias": await suggest(self.api, texto or "", self.locations)}

    def iluminar_busqueda(self, direcciones: list[int], bajos: list[int]) -> None:
        """Light the drawers of the suggestions the card shows; an empty list puts every search light out.

        Unlike `sugerir`, which only answers, this is the card's explicit order, given when it accepts an
        answer, so a late answer can never light drawers for text the user has moved on from. The context
        is not touched: nothing was taken or chosen yet.
        """
        real = {d.address for d in self.simulator.drawers()}
        addresses = [a for a in dict.fromkeys(direcciones) if a in real][:MAX_LIT]
        self.simulator.search(addresses, set(bajos) & set(addresses))

    async def pieza(self, pieza_pk: int) -> dict:
        return await self._bounded(self._pieza(pieza_pk), {"frase": DOWN_PHRASE})

    async def _pieza(self, pieza_pk: int) -> dict:
        if not await self._ready():
            return {"frase": DOWN_PHRASE}
        sheet = await part_sheet(self.api, pieza_pk, self.locations)
        if "frase" in sheet:
            return sheet
        self._refresh_low_after_read()
        addresses = [row["direccion"] for row in sheet["cajones"]]
        location_pk = None
        if len(addresses) == 1:
            location_pk = next((pk for pk, loc in self.locations.items() if loc.address == addresses[0]), None)
        self.context = Context(pieza_pk, location_pk, self.now())
        # A part in no drawer must not leave the previous part's lights on.
        self.simulator.search(addresses, set(addresses) if sheet["bajo"] else set())
        return sheet

    async def anadir(self, pieza_pk: int, direccion: int, cantidad: float | None) -> dict:
        if cantidad is None or cantidad <= 0:
            return {"frase": INVALID_QTY, "hecho": False}
        return await self._write(lambda: add_to_drawer(self.api, pieza_pk, direccion, cantidad, self.locations))

    async def actualizar(self, pieza_pk: int, direccion: int, cantidad: float | None) -> dict:
        if cantidad is None or cantidad < 0:
            return {"frase": INVALID_QTY, "hecho": False}
        return await self._write(lambda: count_drawer(self.api, pieza_pk, direccion, cantidad, self.locations))

    async def mover(self, pieza_pk: int, desde: int, hasta: int, cantidad: float | None = None) -> dict:
        if cantidad is not None and cantidad <= 0:
            return {"frase": INVALID_QTY, "hecho": False}
        return await self._write(lambda: move_between(self.api, pieza_pk, desde, hasta, cantidad, self.locations))

    async def pieza_crear(
        self,
        nombre: str | None,
        categoria: int,
        direccion: int,
        cantidad: float | None,
        ipn: str = "",
        descripcion: str = "",
        palabras_clave: str = "",
        minimo: float = 0,
    ) -> dict:
        if not (nombre or "").strip():
            return {"frase": NAME_REQUIRED, "hecho": False, "pieza_pk": None}
        return await self._write(
            lambda: create_part(
                self.api,
                nombre,
                categoria,
                direccion,
                cantidad,
                self.locations,
                ipn=ipn or "",
                description=descripcion or "",
                keywords=palabras_clave or "",
                minimum=minimo or 0,
            ),
            with_part_pk=True,
        )

    async def pieza_editar(self, pieza_pk: int, **changes) -> dict:
        # None means "not provided" (an unfilled slot); an empty string clears a field.
        changes = {key: value for key, value in changes.items() if value is not None}
        return await self._write(lambda: edit_part(self.api, pieza_pk, changes), maybe=MAYBE_SAVED)

    async def _write(self, op, with_part_pk: bool = False, maybe: str = MAYBE_STOCK) -> dict:
        """Run a stock or catalog write under the deadline and apply its side effects."""
        timeout = {"frase": maybe, "hecho": False}
        if with_part_pk:
            timeout["pieza_pk"] = None
        return await self._bounded(self._write_inner(op, with_part_pk), timeout)

    async def _write_inner(self, op, with_part_pk: bool) -> dict:
        if not await self._ready():
            answer = {"frase": DOWN_PHRASE, "hecho": False}
            if with_part_pk:
                answer["pieza_pk"] = None
            return answer
        result: OpResult = await op()
        if result.done:
            if result.address is not None:
                self.simulator.confirm(result.address, result.low)
            if result.part_pk is not None and result.location_pk is not None:
                self.context = Context(result.part_pk, result.location_pk, self.now())
            self._spawn(self.refresh_low())
        answer = {"frase": result.phrase, "hecho": result.done}
        if with_part_pk:
            answer["pieza_pk"] = result.part_pk
        return answer

    async def voz(
        self,
        accion: str,
        pieza_pk: int | None = None,
        pieza_pks: list[int] | None = None,
        texto: str | None = None,
        cantidad: float | None = None,
        cajon: str | None = None,
        uno: bool = False,
    ) -> dict:
        """One spoken order from the Alexa bridge: the card's rules, answered as a sentence (and maybe a question)."""
        down = {"frase": DOWN_PHRASE, "hecho": False, "pregunta": None}
        # Past the deadline a write may already be in flight: never say it failed.
        timeout = down if accion == "buscar" else {"frase": MAYBE_STOCK, "hecho": False, "pregunta": None}
        return await self._bounded(
            self._voz(accion, pieza_pk, pieza_pks, texto, cantidad, cajon, uno, down), timeout
        )

    async def _voz(self, accion, pieza_pk, pieza_pks, texto, cantidad, cajon, uno, down) -> dict:
        if not await self._ready():
            return down
        result: VoiceResult = await voice_order(
            self.api, self.locations, self.context, self.now(), accion=accion, pieza_pk=pieza_pk,
            pieza_pks=pieza_pks, texto=texto, cantidad=cantidad, cajon=cajon, uno=uno,
        )
        if result.iluminar:
            self.simulator.search(result.iluminar, set(result.bajos) & set(result.iluminar))
        if result.confirmar is not None:
            self.simulator.confirm(*result.confirmar)
        if result.contexto is not None or result.sustituir_contexto:
            self.context = result.contexto
        if result.refrescar_bajo:
            self._spawn(self.refresh_low())
        pregunta = None
        if result.pregunta is not None:
            pregunta = {
                "falta": result.pregunta.falta,
                "pieza_pk": result.pregunta.pieza_pk,
                "opciones": result.pregunta.opciones,
            }
        return {"frase": result.frase, "hecho": result.hecho, "pregunta": pregunta}

    async def categorias(self) -> dict:
        return await self._bounded(self._categorias(), {"categorias": []})

    async def _categorias(self) -> dict:
        return {"categorias": await list_categories(self.api)}

    async def categoria_crear(self, nombre: str | None, padre: int | None = None) -> dict:
        row = await self._bounded(create_category(self.api, nombre, padre), {"frase": MAYBE_SAVED})
        return row if "frase" in row else {"categoria": row}

    async def sugerir_cajon(self, categoria: int) -> dict:
        down = {"direccion": None, "cajon": None, "motivo": DOWN_PHRASE}
        return await self._bounded(self._sugerir_cajon(categoria, down), down)

    async def _sugerir_cajon(self, categoria: int, down: dict) -> dict:
        if not await self._ready():
            return down
        # Only an answer: the card lights the drawer when it accepts the answer (guardar_aqui),
        # so a late suggestion can never light a drawer the user has moved away from.
        return await suggest_drawer(self.api, categoria, self.root_pk, self.simulator.drawers(), self.locations)

    def guardar_aqui(self, direccion: int) -> None:
        """Move the "store it here" light to the drawer the user chose instead of the suggested one."""
        # An unknown address is ignored: lighting nothing must not end a search in progress.
        if any(d.address == direccion for d in self.simulator.drawers()):
            self.simulator.store_here(direccion)

    def guardar_apagar(self) -> None:
        """Put out the "store it here" light; a search in progress is left alone."""
        self.simulator.clear_store()

    def seleccionar(self, direccion: int) -> None:
        """Light only the drawer tapped on the card (30 s); every other order light goes out."""
        # An unknown address is ignored: lighting nothing must not end a search in progress.
        if any(d.address == direccion for d in self.simulator.drawers()):
            self.simulator.select(direccion)

    def seleccion_apagar(self) -> None:
        """Put out the selection light; a search or store-here in progress is left alone."""
        self.simulator.clear_selection()
