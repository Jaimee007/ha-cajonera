"""Cajonera: modular drawer cabinet backed by InvenTree.

Home Assistant glue only. All behaviour lives in `logic/` (pure Python, tested
with pytest): `logic/service.py` implements every service, and this module
just registers them, forwards notifications and runs the periodic refresh.
Until the controller firmware exists, a DrawerSimulator plays its part.
See docs/09_gestion_inventario.md.
"""

from __future__ import annotations

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

from .logic.inventree import InvenTreeClient
from .logic.models import Drawer
from .logic.service import Cajonera
from .logic.simulator import DrawerSimulator

DOMAIN = "cajonera"
SIGNAL_UPDATE = f"{DOMAIN}_update"
REFRESH_INTERVAL = timedelta(minutes=5)

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


def _unique_addresses(drawers: list[dict]) -> list[dict]:
    seen = [d["address"] for d in drawers]
    dupes = sorted({a for a in seen if seen.count(a) > 1})
    if dupes:
        raise vol.Invalid(f"simulated_drawers: repeated address {dupes}")
    return drawers


CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required("inventree_url"): cv.url,
                vol.Required("inventree_token"): cv.string,
                vol.Optional("simulated_drawers", default=[]): vol.All([DRAWER_SCHEMA], _unique_addresses),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

ORIGIN = vol.In(["alexa", "panel"])
ADDRESS = vol.All(vol.Coerce(int), vol.Range(min=0, max=31))
OPTIONAL_TEXT = vol.Any(None, cv.string)  # an unfilled Alexa slot arrives as None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    conf = config[DOMAIN]
    api = InvenTreeClient(async_get_clientsession(hass), conf["inventree_url"], conf["inventree_token"])
    drawers = [
        Drawer(d["address"], d.get("x"), d.get("y"), d["width"], d["height"], d.get("error"))
        for d in conf["simulated_drawers"]
    ]

    @callback
    def on_change() -> None:
        async_dispatcher_send(hass, SIGNAL_UPDATE)

    @callback
    def on_notices(notices: list[str]) -> None:
        persistent_notification.async_create(
            hass, "\n".join(f"- {n}" for n in notices), title="Cajonera", notification_id="cajonera_sync"
        )

    simulator = DrawerSimulator(drawers)
    cajonera = Cajonera(api, simulator, now=dt_util.now, on_notices=on_notices, on_change=on_change)
    simulator.add_listener(on_change)
    hass.data[DOMAIN] = cajonera

    async def tick(_now=None) -> None:
        await cajonera.tick()

    # InvenTree may be down at start-up: never block or fail setup on it.
    hass.async_create_background_task(tick(), "cajonera initial sync")
    async_track_time_interval(hass, tick, REFRESH_INTERVAL)

    _register_services(hass, cajonera)
    hass.async_create_task(async_load_platform(hass, "sensor", DOMAIN, {}, config))
    return True


def _register_services(hass: HomeAssistant, cajonera: Cajonera) -> None:
    async def buscar(call: ServiceCall) -> ServiceResponse:
        return await cajonera.buscar(call.data.get("texto"))

    async def retirar(call: ServiceCall) -> ServiceResponse:
        return await cajonera.retirar(
            texto=call.data.get("texto"),
            cantidad=call.data.get("cantidad"),
            cajon=call.data.get("cajon"),
            stock=call.data.get("stock"),
        )

    async def contenido(call: ServiceCall) -> ServiceResponse:
        return await cajonera.contenido(call.data["direccion"])

    async def identificar(call: ServiceCall) -> None:
        cajonera.simulator.identify(call.data["direccion"])

    async def sincronizar(call: ServiceCall) -> ServiceResponse:
        return await cajonera.sincronizar()

    async def simular_apertura(call: ServiceCall) -> None:
        cajonera.simulator.set_open(call.data["direccion"], call.data["abierto"])

    hass.services.async_register(
        DOMAIN,
        "inventario_buscar",
        buscar,
        schema=vol.Schema({vol.Optional("texto"): OPTIONAL_TEXT, vol.Optional("origen", default="panel"): ORIGIN}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "inventario_retirar",
        retirar,
        schema=vol.Schema(
            {
                vol.Optional("texto"): OPTIONAL_TEXT,
                vol.Optional("cantidad"): vol.Any(None, vol.Coerce(float)),
                vol.Optional("cajon"): OPTIONAL_TEXT,
                vol.Optional("stock"): vol.Any(None, vol.Coerce(int)),
                vol.Optional("origen", default="panel"): ORIGIN,
            }
        ),
        # A write action: plain scripts may call it without asking for the answer.
        supports_response=SupportsResponse.OPTIONAL,
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
