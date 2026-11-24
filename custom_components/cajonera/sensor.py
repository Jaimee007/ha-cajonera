"""Two sensors the panel card draws from: the drawer map and the low-stock list."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DOMAIN, SIGNAL_UPDATE, CajoneraHub


async def async_setup_platform(
    hass: HomeAssistant, config, async_add_entities: AddEntitiesCallback, discovery_info=None
) -> None:
    if discovery_info is None:
        return
    hub: CajoneraHub = hass.data[DOMAIN]
    async_add_entities([MapSensor(hub), LowStockSensor(hub)])


class _CajoneraSensor(SensorEntity):
    _attr_should_poll = False

    def __init__(self, hub: CajoneraHub):
        self.hub = hub

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self._update))

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()


class MapSensor(_CajoneraSensor):
    _attr_name = "Cajonera mapa"
    _attr_unique_id = "cajonera_mapa"
    _attr_icon = "mdi:file-cabinet"
    _unrecorded_attributes = frozenset({"cajones"})

    @property
    def native_value(self) -> int:
        return len(self.hub.simulator.drawers())

    @property
    def extra_state_attributes(self) -> dict:
        low = self.hub.low.addresses
        return {
            "cajones": [
                {
                    "direccion": s.drawer.address,
                    "nombre": None if s.drawer.error else s.name,
                    "x": s.drawer.x,
                    "y": s.drawer.y,
                    "ancho": s.drawer.width,
                    "alto": s.drawer.height,
                    "modo": s.mode,
                    "rgb": list(s.rgb),
                    "abierto": s.open,
                    "bajo": s.drawer.address in low,
                    "error": s.drawer.error,
                }
                for s in self.hub.simulator.states()
            ]
        }


class LowStockSensor(_CajoneraSensor):
    _attr_name = "Cajonera existencias bajas"
    _attr_unique_id = "cajonera_existencias_bajas"
    _attr_icon = "mdi:package-variant-remove"
    _unrecorded_attributes = frozenset({"piezas"})

    @property
    def native_value(self) -> int:
        return len(self.hub.low.parts)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "piezas": [
                {
                    "pieza": lp.part.name,
                    "total": lp.part.total_stock,
                    "minimo": lp.part.minimum_stock,
                    "cajones": lp.drawers,
                }
                for lp in self.hub.low.parts
            ]
        }
