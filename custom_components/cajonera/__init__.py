"""Cajonera: modular drawer cabinet backed by InvenTree.

Home Assistant glue only. All behaviour lives in `logic/` (pure Python, tested
with pytest): `logic/service.py` implements every service, and this module
just registers them, forwards notifications and runs the periodic refresh.
Until the controller firmware exists, a DrawerSimulator plays its part.
See docs/09_gestion_inventario.md.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
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
FRONTEND_URL = "/cajonera_frontend"
INTEGRATION_DIR = Path(__file__).parent

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
ID = vol.Coerce(int)
QUANTITY = vol.Any(None, vol.Coerce(float))
# `pieza_editar` fields; each is optional and only the ones given are changed.
EDIT_FIELDS = {
    "nombre": OPTIONAL_TEXT,
    "descripcion": OPTIONAL_TEXT,
    "categoria": vol.Any(None, ID),
    "palabras_clave": OPTIONAL_TEXT,
    "ipn": OPTIONAL_TEXT,
    "minimo": QUANTITY,
}


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
    await _register_card(hass)
    hass.async_create_task(async_load_platform(hass, "sensor", DOMAIN, {}, config))
    return True


def _read_version() -> str:
    return json.loads((INTEGRATION_DIR / "manifest.json").read_text(encoding="utf-8"))["version"]


async def _register_card(hass: HomeAssistant) -> None:
    """Serve the Lovelace card from this integration so no manual resource is needed."""
    version = await hass.async_add_executor_job(_read_version)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL, str(INTEGRATION_DIR / "frontend"), cache_headers=False)]
    )
    # The version in the query string busts the browser cache on every release.
    add_extra_js_url(hass, f"{FRONTEND_URL}/cajonera-card.js?v={version}")


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

    async def sugerir(call: ServiceCall) -> ServiceResponse:
        return await cajonera.sugerir(call.data.get("texto"))

    async def pieza(call: ServiceCall) -> ServiceResponse:
        return await cajonera.pieza(call.data["pieza_pk"])

    async def anadir(call: ServiceCall) -> ServiceResponse:
        return await cajonera.anadir(call.data["pieza_pk"], call.data["direccion"], call.data.get("cantidad"))

    async def actualizar(call: ServiceCall) -> ServiceResponse:
        return await cajonera.actualizar(call.data["pieza_pk"], call.data["direccion"], call.data.get("cantidad"))

    async def mover(call: ServiceCall) -> ServiceResponse:
        return await cajonera.mover(call.data["pieza_pk"], call.data["desde"], call.data["hasta"], call.data.get("cantidad"))

    async def categorias(call: ServiceCall) -> ServiceResponse:
        return await cajonera.categorias()

    async def categoria_crear(call: ServiceCall) -> ServiceResponse:
        return await cajonera.categoria_crear(call.data.get("nombre"), call.data.get("padre"))

    async def sugerir_cajon(call: ServiceCall) -> ServiceResponse:
        return await cajonera.sugerir_cajon(call.data["categoria"])

    async def pieza_crear(call: ServiceCall) -> ServiceResponse:
        return await cajonera.pieza_crear(
            call.data.get("nombre"),
            call.data["categoria"],
            call.data["direccion"],
            call.data.get("cantidad"),
            ipn=call.data.get("ipn"),
            descripcion=call.data.get("descripcion"),
            palabras_clave=call.data.get("palabras_clave"),
            minimo=call.data.get("minimo"),
        )

    async def pieza_editar(call: ServiceCall) -> ServiceResponse:
        # Only the keys the caller provided are changed.
        changes = {key: call.data[key] for key in EDIT_FIELDS if key in call.data}
        return await cajonera.pieza_editar(call.data["pieza_pk"], **changes)

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

    # Inventory card
    hass.services.async_register(
        DOMAIN,
        "sugerir",
        sugerir,
        schema=vol.Schema({vol.Optional("texto"): OPTIONAL_TEXT}),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "pieza",
        pieza,
        schema=vol.Schema({vol.Required("pieza_pk"): ID}),
        supports_response=SupportsResponse.ONLY,
    )
    stock_fields = {vol.Required("pieza_pk"): ID, vol.Required("direccion"): ADDRESS, vol.Optional("cantidad"): QUANTITY}
    hass.services.async_register(
        DOMAIN,
        "inventario_anadir",
        anadir,
        schema=vol.Schema(stock_fields),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "inventario_actualizar",
        actualizar,
        schema=vol.Schema(stock_fields),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "inventario_mover",
        mover,
        schema=vol.Schema(
            {
                vol.Required("pieza_pk"): ID,
                vol.Required("desde"): ADDRESS,
                vol.Required("hasta"): ADDRESS,
                vol.Optional("cantidad"): QUANTITY,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, "categorias", categorias, supports_response=SupportsResponse.ONLY)
    hass.services.async_register(
        DOMAIN,
        "categoria_crear",
        categoria_crear,
        schema=vol.Schema({vol.Optional("nombre"): OPTIONAL_TEXT, vol.Optional("padre"): vol.Any(None, ID)}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "sugerir_cajon",
        sugerir_cajon,
        schema=vol.Schema({vol.Required("categoria"): ID}),
        # It lights a drawer, but the card calls it for the answer.
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "pieza_crear",
        pieza_crear,
        schema=vol.Schema(
            {
                vol.Optional("nombre"): OPTIONAL_TEXT,
                vol.Required("categoria"): ID,
                vol.Required("direccion"): ADDRESS,
                vol.Optional("cantidad"): QUANTITY,
                vol.Optional("ipn"): OPTIONAL_TEXT,
                vol.Optional("descripcion"): OPTIONAL_TEXT,
                vol.Optional("palabras_clave"): OPTIONAL_TEXT,
                vol.Optional("minimo"): QUANTITY,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "pieza_editar",
        pieza_editar,
        schema=vol.Schema({vol.Required("pieza_pk"): ID, **{vol.Optional(k): v for k, v in EDIT_FIELDS.items()}}),
        supports_response=SupportsResponse.OPTIONAL,
    )
