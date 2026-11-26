"""Shows every voice order in Home Assistant's Logbook (Settings > Logbook, or the History panel)."""

from __future__ import annotations

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

DOMAIN = "cajonera"
EVENT_ORDER = "cajonera_orden"


@callback
def async_describe_events(hass: HomeAssistant, async_describe_event) -> None:
    """Describe the `cajonera_orden` event as a Logbook line."""

    @callback
    def describe(event: Event) -> dict:
        data = event.data
        order = data.get("accion", "?")
        done = "hecho" if data.get("hecho") else "sin cambios"
        return {
            LOGBOOK_ENTRY_NAME: "Cajonera (voz)",
            LOGBOOK_ENTRY_MESSAGE: f"{order} ({done}): {data.get('frase', '')}",
        }

    async_describe_event(DOMAIN, EVENT_ORDER, describe)
