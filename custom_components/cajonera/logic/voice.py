"""The rules of `cajonera.voz`: what Alexa may ask, refuse and do (spec: 2026-11-25-alexa-bridge-design.md).

Pure Python, no Home Assistant. Every function returns data; `Cajonera.voz` applies the lights, the confirm
flash and the context. The rules are the card's rules, so voice and panel cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .inventree import InvenTreeError
from .models import Location, Part
from .search import DOWN_PHRASE, MAX_LIT, NO_MATCH_PHRASE, is_unplaced, search_parts_result
from .withdraw import Context, candidate_parts

MAX_CANDIDATES = 20  # parts Alexa may send or HA may offer in one question


@dataclass(frozen=True)
class Pregunta:
    """One more answer HA needs. `falta` is "cantidad", "cajon" or "pieza"."""

    falta: str
    pieza_pk: int | None = None
    opciones: list = field(default_factory=list)


@dataclass(frozen=True)
class VoiceResult:
    frase: str
    hecho: bool = False
    pregunta: Pregunta | None = None
    # Side effects, as data, for `Cajonera.voz` to apply:
    iluminar: list[int] = field(default_factory=list)  # drawer addresses to light in search mode
    bajos: set[int] = field(default_factory=set)  # the lit drawers whose part is running low
    confirmar: tuple[int, bool] | None = None  # (address, low) for the confirm flash
    contexto: Context | None = None  # what "he cogido 3" refers to next
    refrescar_bajo: bool = False  # refresh the low-stock report in the background


async def resolve_parts(
    api, locations: dict[int, Location], *, pieza_pk: int | None, pieza_pks: list[int] | None, texto: str | None
) -> list[Part]:
    """The parts the request names: an id, the candidates Alexa resolved from a shared keyword, or a spoken name."""
    if pieza_pk is not None:
        return [await api.get_part(pieza_pk)]
    if pieza_pks:
        return [await api.get_part(pk) for pk in pieza_pks[:MAX_CANDIDATES]]
    if (texto or "").strip():
        return await candidate_parts(api, texto.strip(), locations)
    return []


async def voz_buscar(
    api,
    locations: dict[int, Location],
    context: Context | None,
    now: datetime,
    *,
    pieza_pk: int | None = None,
    pieza_pks: list[int] | None = None,
    texto: str | None = None,
) -> VoiceResult:
    """"Where is it": every matching part is searched at once and its drawers are lit, with no question."""
    try:
        parts = await resolve_parts(api, locations, pieza_pk=pieza_pk, pieza_pks=pieza_pks, texto=texto)
        if not parts:
            if (texto or "").strip() or pieza_pk is not None or pieza_pks:
                return VoiceResult(NO_MATCH_PHRASE)
            return VoiceResult("¿Qué quieres buscar?")
        outside = await _outside_names(api, parts, locations)
        result = await search_parts_result(api, parts, locations, outside=outside)
    except InvenTreeError:
        return VoiceResult(DOWN_PHRASE)
    if result.phrase == DOWN_PHRASE:
        return VoiceResult(DOWN_PHRASE)
    low = {h.location.address for h in result.hits if h.part.is_low and not is_unplaced(h.location)}
    contexto = None
    if len(result.part_pks) == 1:
        drawers = {h.location.pk for h in result.hits if h.part.pk == result.part_pks[0]}
        contexto = Context(result.part_pks[0], next(iter(drawers)) if len(drawers) == 1 else None, now)
    return VoiceResult(
        result.phrase,
        iluminar=result.addresses[:MAX_LIT],
        bajos=low,
        contexto=contexto,
        refrescar_bajo=True,
    )


async def _outside_names(api, parts: list[Part], locations: dict[int, Location]) -> dict[int, str]:
    """Names of the InvenTree locations that hold these parts but are not cabinet drawers (the fridge, say)."""
    names: dict[int, str] = {}
    for part in parts:
        for item in await api.stock_for_part(part.pk):
            pk = item.location_pk
            if pk is not None and pk not in locations and pk not in names:
                names[pk] = (await api.get_location(pk)).name
    return names
