"""The rules of `cajonera.voz`: what Alexa may ask, refuse and do (spec: 2026-11-25-alexa-bridge-design.md).

Pure Python, no Home Assistant. Every function returns data; `Cajonera.voz` applies the lights, the confirm
flash and the context. The rules are the card's rules, so voice and panel cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .inventree import InvenTreeError
from .models import Location, Part
from .phrases import fmt_qty, join_es
from .stock_ops import add_to_drawer, count_drawer
from .search import DOWN_PHRASE, MAX_LIT, NO_MATCH_PHRASE, is_unplaced, search_parts_result
from .withdraw import CONTEXT_TTL, Context, candidate_parts, withdraw_by_part

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
    sustituir_contexto: bool = False  # True: replace the context with `contexto` even when it is None (a search does)
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
                return VoiceResult(NO_MATCH_PHRASE, sustituir_contexto=True)
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
        sustituir_contexto=True,  # every search replaces the context: an older part must never be withdrawn by mistake
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


# ---- Questions and refusals for the writes

@dataclass(frozen=True)
class Listo:
    """Everything known: the write can be done."""

    part: Part
    location: Location
    cantidad: float


def _drawers_of(items, locations: dict[int, Location]) -> list[Location]:
    """The cabinet drawers (not unplaced) a part's stock sits in, by name."""
    pks = {s.location_pk for s in items if s.location_pk in locations and not is_unplaced(locations[s.location_pk])}
    return sorted((locations[pk] for pk in pks), key=lambda loc: _name_order(loc.name))


def _name_order(name: str) -> tuple:
    letters = "".join(c for c in name if c.isalpha())
    digits = "".join(c for c in name if c.isdigit())
    return (len(letters), letters, int(digits) if digits else 0)


def _all_drawers(locations: dict[int, Location]) -> list[Location]:
    return sorted((loc for loc in locations.values() if not is_unplaced(loc)), key=lambda loc: _name_order(loc.name))


def _names(locations: list[Location]) -> str:
    """"en el B1", "en el B1 y en el B3", more than three: "…, … y N más" (the preposition is part of each name)."""
    shown = [f"en el {loc.name}" for loc in locations[:3]]
    if len(locations) > 3:
        shown.append(f"{len(locations) - 3} más")
    return join_es(shown)


def _pregunta_cajon(part: Part, frase: str, options: list[Location], light: bool = False) -> VoiceResult:
    return VoiceResult(
        frase,
        pregunta=Pregunta("cajon", part.pk, [loc.name for loc in options][:MAX_CANDIDATES]),
        iluminar=[loc.address for loc in options][:MAX_LIT] if light else [],
    )


async def plan_voice(
    api,
    locations: dict[int, Location],
    context: Context | None,
    now: datetime,
    *,
    accion: str,
    pieza_pk: int | None = None,
    pieza_pks: list[int] | None = None,
    texto: str | None = None,
    cantidad: float | None = None,
    cajon: str | None = None,
    uno: bool = False,
) -> VoiceResult | Listo:
    """Decide what a spoken write needs: a question, a refusal, or the go-ahead. Nothing is written here."""
    try:
        return await _plan(api, locations, context, now, accion, pieza_pk, pieza_pks, texto, cantidad, cajon, uno)
    except InvenTreeError:
        return VoiceResult(DOWN_PHRASE)


async def _plan(api, locations, context, now, accion, pieza_pk, pieza_pks, texto, cantidad, cajon, uno):
    # 1. The part.
    fresh = context if context and now - context.at <= CONTEXT_TTL else None
    if pieza_pk is None and not pieza_pks and not (texto or "").strip() and accion == "retirar" and fresh:
        pieza_pk = fresh.part_pk
    parts = await resolve_parts(api, locations, pieza_pk=pieza_pk, pieza_pks=pieza_pks, texto=texto)
    if not parts:
        if (texto or "").strip() or pieza_pk is not None or pieza_pks:
            return VoiceResult(NO_MATCH_PHRASE)
        return VoiceResult("¿Qué has cogido?" if accion == "retirar" else "¿De qué producto?",
                           pregunta=Pregunta("pieza", None, []))
    if len(parts) > 1:
        names = [p.name for p in parts[:3]]
        if len(parts) > 3:
            names.append(f"{len(parts) - 3} más")
        return VoiceResult(
            f"Hay {join_es(names)}. ¿Cuál?",
            pregunta=Pregunta("pieza", None, [{"pieza_pk": p.pk, "nombre": p.name} for p in parts[:MAX_CANDIDATES]]),
        )
    part = parts[0]

    # 2. The quantity: never assumed. "un/una" is the only way to say one without a number.
    if uno and cantidad is None:
        cantidad = 1
    low_bound_ok = cantidad is not None and (cantidad >= 0 if accion == "actualizar" else cantidad > 0)
    if not low_bound_ok:
        return VoiceResult("¿A cuántos?" if accion == "actualizar" else "¿Cuántos?", pregunta=Pregunta("cantidad", part.pk, []))

    # 3. The drawer.
    items = await api.stock_for_part(part.pk)
    holds = _drawers_of(items, locations)
    everything = _all_drawers(locations)
    wanted = (cajon or "").strip()
    if wanted:
        target = next((loc for loc in everything if loc.name.casefold() == wanted.casefold()), None)
        if target is None:
            options = holds or everything
            return _pregunta_cajon(part, f"No conozco el cajón {wanted.upper()}. ¿En cuál?", options)
        if holds and target.pk not in {loc.pk for loc in holds}:
            if accion == "anadir":
                return VoiceResult(
                    f"{part.name} no está en el {target.name}; está {_names(holds)}. Para ponerla ahí, hazlo desde el panel."
                )
            return _pregunta_cajon(
                part, f"No hay {part.name} en el {target.name}; hay {_names(holds)}. ¿En cuál?", holds
            )
        return Listo(part, target, cantidad)
    if len(holds) == 1:
        return Listo(part, holds[0], cantidad)
    if len(holds) > 1:
        if accion == "retirar" and fresh and fresh.part_pk == part.pk and fresh.location_pk in {loc.pk for loc in holds}:
            return Listo(part, locations[fresh.location_pk], cantidad)
        return _pregunta_cajon(part, f"Hay {_names(holds)}, ¿en cuál?", holds, light=True)
    # The part is in no cabinet drawer.
    if accion == "anadir":
        return _pregunta_cajon(part, "¿En qué cajón la guardo?", everything)
    return VoiceResult(f"Hay {fmt_qty(part.total_stock)} de {part.name}, pero no está en ningún cajón.")


# ---- Execution

VOICE_NOTE = "cajonera: voz"


def _strip_hecho(phrase: str) -> str:
    return phrase[len("Hecho. "):] if phrase.startswith("Hecho. ") else phrase


async def voz(
    api,
    locations: dict[int, Location],
    context: Context | None,
    now: datetime,
    *,
    accion: str,
    pieza_pk: int | None = None,
    pieza_pks: list[int] | None = None,
    texto: str | None = None,
    cantidad: float | None = None,
    cajon: str | None = None,
    uno: bool = False,
) -> VoiceResult:
    """One spoken order: search, or a write that is only done when nothing is missing or ambiguous."""
    if accion == "buscar":
        return await voz_buscar(api, locations, context, now, pieza_pk=pieza_pk, pieza_pks=pieza_pks, texto=texto)
    if accion not in ("anadir", "retirar", "actualizar"):
        return VoiceResult("No sé hacer eso.")
    plan = await plan_voice(
        api, locations, context, now, accion=accion, pieza_pk=pieza_pk, pieza_pks=pieza_pks, texto=texto,
        cantidad=cantidad, cajon=cajon, uno=uno,
    )
    if isinstance(plan, VoiceResult):
        return plan
    part, location, qty = plan.part, plan.location, plan.cantidad
    if accion == "retirar":
        done = await withdraw_by_part(api, part.pk, qty, location.name, now, locations, notes=VOICE_NOTE)
        ok, text, address, low = done.done, done.phrase, done.address, done.low
        lead = f"Hecho. He retirado {fmt_qty(qty)} de {part.name} del {location.name}."
    else:
        if accion == "anadir":
            op = await add_to_drawer(api, part.pk, location.address, qty, locations, notes=VOICE_NOTE)
            lead = f"Hecho. He añadido {fmt_qty(qty)} de {part.name} al {location.name}."
        else:
            op = await count_drawer(api, part.pk, location.address, qty, locations, notes=VOICE_NOTE)
            lead = f"Hecho. He dejado {part.name} en {fmt_qty(qty)} en el {location.name}."
        ok, text, address, low = op.done, op.phrase, op.address, op.low
    if not ok:
        return VoiceResult(text)  # a refusal or a failure: nothing was recorded, or maybe it was (the wording says)
    return VoiceResult(
        f"{lead} {_strip_hecho(text)}",
        hecho=True,
        confirmar=(address, low) if address is not None else None,
        contexto=Context(part.pk, location.pk, now),
        refrescar_bajo=True,
    )
