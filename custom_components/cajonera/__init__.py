"""Cajonera: modular drawer cabinet backed by InvenTree.

Home Assistant glue only. All behaviour lives in `logic/` (pure Python, tested
with pytest); this module wires it to HA services, sensors and timers.
Until the controller firmware exists, a DrawerSimulator plays its part.
See docs/09_gestion_inventario.md.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .logic.inventree import InvenTreeClient, InvenTreeError
from .logic.low_stock import LowStockReport, low_stock_report
from .logic.models import Drawer, Location
from .logic.search import DOWN_PHRASE, drawer_contents, search
from .logic.simulator import DrawerSimulator
from .logic.sync import apply_sync, plan_sync
from .logic.withdraw import Context, withdraw_by_stock, withdraw_by_text

_LOGGER = logging.getLogger(__name__)

DOMAIN = "cajonera"
SIGNAL_UPDATE = f"{DOMAIN}_update"
LOW_STOCK_INTERVAL = timedelta(minutes=5)

DRAWER_SCHEMA = vol.Schema(
    {
        vol.Required("address"): vol.All(vol.Coerce(int), vol.Range(min=0, max=31)),
        vol.Optional("x"): vol.Any(None, vol.Coerce(int)),
        vol.Optional("y"): vol.Any(None, vol.Coerce(int)),
        vol.Optional("width", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=2)),
        vol.Optional("height", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=2)),
        vol.Optional("error"): vol.In(["duplicada", "fuera_reticula", "pareja"]),
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("inventree_url"): cv.url,
                vol.Required("inventree_token"): cv.string,
                vol.Optional("simulated_drawers", default=[]): [DRAWER_SCHEMA],
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

ORIGIN = vol.In(["alexa", "panel"])
ADDRESS = vol.All(vol.Coerce(int), vol.Range(min=0, max=31))


class CajoneraHub:
    """State shared by services and sensors. Kept in hass.data[DOMAIN]."""

    def __init__(self, hass: HomeAssistant, api: InvenTreeClient, simulator: DrawerSimulator):
        self.hass = hass
        self.api = api
        self.simulator = simulator
        self.root_pk: int | None = None
        self.locations: dict[int, Location] = {}  # location pk -> drawer location
        self.context: Context | None = None
        self.low = LowStockReport()
        simulator.add_listener(self.notify)

    @callback
    def notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    async def ensure_ready(self) -> bool:
        """Make sure the root location is known and synced. False if InvenTree is down."""
        if self.root_pk is not None:
            return True
        try:
            self.root_pk = await self.api.ensure_root()
        except InvenTreeError as err:
            _LOGGER.warning("InvenTree not reachable: %s", err)
            return False
        await self.sync()
        return True

    async def refresh_locations(self) -> None:
        self.locations = {loc.pk: loc for loc in await self.api.child_locations(self.root_pk)}

    async def refresh_low(self) -> None:
        try:
            self.low = await low_stock_report(self.api, self.locations)
        except InvenTreeError as err:
            _LOGGER.warning("Low-stock refresh failed: %s", err)
            return
        self.notify()

    async def sync(self) -> list[str]:
        """Create and rename InvenTree locations so they match the drawers."""
        if self.root_pk is None:
            return [DOWN_PHRASE] if not await self.ensure_ready() else []
        try:
            await self.refresh_locations()
            plan = plan_sync(self.simulator.drawers(), list(self.locations.values()))
            await apply_sync(self.api, self.root_pk, plan)
            await self.refresh_locations()
        except InvenTreeError as err:
            _LOGGER.warning("Location sync failed: %s", err)
            return [DOWN_PHRASE]
        if plan.notices:
            persistent_notification.async_create(
                self.hass,
                "\n".join(f"- {n}" for n in plan.notices),
                title="Cajonera",
                notification_id="cajonera_sync",
            )
        await self.refresh_low()
        return plan.notices

    def location_for_address(self, address: int) -> Location | None:
        return next((loc for loc in self.locations.values() if loc.address == address), None)

    async def tick(self, _now=None) -> None:
        """Periodic refresh: locations (cheap) and the low-stock report."""
        if not await self.ensure_ready():
            return
        try:
            await self.refresh_locations()
        except InvenTreeError as err:
            _LOGGER.warning("Location refresh failed: %s", err)
            return
        await self.refresh_low()


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    conf = config[DOMAIN]
    api = InvenTreeClient(async_get_clientsession(hass), conf["inventree_url"], conf["inventree_token"])
    drawers = [
        Drawer(d["address"], d.get("x"), d.get("y"), d["width"], d["height"], d.get("error"))
        for d in conf["simulated_drawers"]
    ]
    hub = CajoneraHub(hass, api, DrawerSimulator(drawers))
    hass.data[DOMAIN] = hub

    # InvenTree may be down at start-up: never block or fail setup on it.
    hass.async_create_task(hub.tick())
    async_track_time_interval(hass, hub.tick, LOW_STOCK_INTERVAL)

    _register_services(hass, hub)
    hass.async_create_task(async_load_platform(hass, "sensor", DOMAIN, {}, config))
    return True


def _register_services(hass: HomeAssistant, hub: CajoneraHub) -> None:
    async def buscar(call: ServiceCall) -> ServiceResponse:
        if not await hub.ensure_ready():
            return {"frase": DOWN_PHRASE, "resultados": []}
        result = await search(hub.api, call.data["texto"], hub.locations)
        # S1: a searched drawer is "low" when the part searched for is low.
        low = {h.location.address for h in result.hits if h.part.is_low}
        if result.addresses:
            hub.simulator.search(result.addresses, low)
        placed = {h.location.pk for h in result.hits if h.location.address in result.addresses}
        if len(result.part_pks) == 1 and len(placed) == 1:
            hub.context = Context(result.part_pks[0], next(iter(placed)), dt_util.now())
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

    async def retirar(call: ServiceCall) -> ServiceResponse:
        if not await hub.ensure_ready():
            return {"frase": DOWN_PHRASE, "hecho": False}
        now = dt_util.now()
        if call.data.get("stock") is not None:
            result = await withdraw_by_stock(hub.api, call.data["stock"], call.data.get("cantidad"), now, hub.locations)
        else:
            result = await withdraw_by_text(
                hub.api,
                call.data.get("texto"),
                call.data.get("cantidad"),
                call.data.get("cajon"),
                hub.context,
                now,
                hub.locations,
            )
        if result.done:
            hub.context = result.context
            if result.address is not None:
                hub.simulator.confirm(result.address, result.low)
            hass.async_create_task(hub.refresh_low())
        return {"frase": result.phrase, "hecho": result.done}

    async def contenido(call: ServiceCall) -> ServiceResponse:
        location = hub.location_for_address(call.data["direccion"])
        if location is None:
            return {"cajon": None, "piezas": []}
        try:
            hits = await drawer_contents(hub.api, location)
        except InvenTreeError:
            return {"cajon": location.name, "piezas": [], "frase": DOWN_PHRASE}
        return {
            "cajon": location.name,
            "piezas": [
                {"stock": h.stock.pk, "pieza": h.part.name, "cantidad": h.stock.quantity, "bajo": h.part.is_low}
                for h in hits
            ],
        }

    async def identificar(call: ServiceCall) -> None:
        hub.simulator.identify(call.data["direccion"])

    async def sincronizar(call: ServiceCall) -> ServiceResponse:
        return {"avisos": await hub.sync()}

    async def simular_apertura(call: ServiceCall) -> None:
        hub.simulator.set_open(call.data["direccion"], call.data["abierto"])

    hass.services.async_register(
        DOMAIN,
        "inventario_buscar",
        buscar,
        schema=vol.Schema({vol.Required("texto"): cv.string, vol.Optional("origen", default="panel"): ORIGIN}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "inventario_retirar",
        retirar,
        schema=vol.Schema(
            {
                vol.Optional("texto"): cv.string,
                vol.Optional("cantidad"): vol.Coerce(float),
                vol.Optional("cajon"): cv.string,
                vol.Optional("stock"): vol.Coerce(int),
                vol.Optional("origen", default="panel"): ORIGIN,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "contenido_cajon",
        contenido,
        schema=vol.Schema({vol.Required("direccion"): ADDRESS}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(DOMAIN, "identificar", identificar, schema=vol.Schema({vol.Required("direccion"): ADDRESS}))
    for name in ("sincronizar_ubicaciones", "redescubrir"):
        # With the simulator there is nothing to rediscover: both just sync.
        hass.services.async_register(DOMAIN, name, sincronizar, supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN,
        "simular_apertura",
        simular_apertura,
        schema=vol.Schema({vol.Required("direccion"): ADDRESS, vol.Required("abierto"): cv.boolean}),
    )
