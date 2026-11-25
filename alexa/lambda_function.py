"""Alexa bridge for the cajonera: a thin Lambda in front of Home Assistant's `cajonera.voz` service.

Every order intent becomes one call to HA, and Alexa speaks the sentence HA returns. When HA needs one more
answer (how many, which drawer, which part) the Lambda keeps the Alexa session open and asks; the pending
question lives in the session attributes, never in the Lambda. If HA cannot be reached, a search is answered
read-only straight from InvenTree and a write says honestly whether it can have happened.

One file, standard library plus urllib3, so it can be pasted into the AWS console. Python 3.9+.
See alexa/README.md and docs/superpowers/specs/2026-11-25-alexa-bridge-design.md.
"""

from __future__ import annotations

MAX_FALLOS = 2  # consecutive answers that did not fill the pending question
MAX_PREGUNTAS = 4  # questions asked for one order
MAX_OPCIONES = 20  # candidates kept in the session and sent to HA

PANEL = "No consigo entenderlo. Hazlo desde el panel de la cajonera."

ORDER_INTENTS = {
    "ConsultarInventarioIntent": "buscar",
    "LocalizarInventarioIntent": "buscar",
    "AnadirInventarioIntent": "anadir",
    "RestarInventarioIntent": "retirar",
    "ActualizarInventarioIntent": "actualizar",
}
ANSWER_INTENTS = {
    "RespuestaCajonIntent": "cajon",
    "RespuestaCantidadIntent": "cantidad",
    "RespuestaPiezaIntent": "pieza",
}


class HAError(Exception):
    """Home Assistant could not answer. `sent` is False when the request certainly never reached it."""

    def __init__(self, message: str = "", sent: bool = True):
        super().__init__(message)
        self.sent = sent


# ---- Slots

def _resolved_ids(slot: dict | None) -> list:
    """Every id a custom slot resolved to, across authorities, de-duplicated and in order."""
    ids: list = []
    if not slot:
        return ids
    for authority in (slot.get("resolutions") or {}).get("resolutionsPerAuthority", []):
        if (authority.get("status") or {}).get("code") != "ER_SUCCESS_MATCH":
            continue
        for entry in authority.get("values", []):
            raw = (entry.get("value") or {}).get("id")
            if raw is None or raw in ids:
                continue
            ids.append(raw)
    return ids


def resolve_producto(slot: dict | None) -> dict:
    """`{"pieza_pk": n}`, `{"pieza_pks": [...]}` (a shared keyword), `{"texto": "..."}` or `{}`."""
    ids = []
    for raw in _resolved_ids(slot):
        try:
            number = int(raw)
        except (TypeError, ValueError):
            continue
        if number not in ids:
            ids.append(number)
    if len(ids) == 1:
        return {"pieza_pk": ids[0]}
    if ids:
        return {"pieza_pks": ids[:MAX_OPCIONES]}
    value = ((slot or {}).get("value") or "").strip()
    return {"texto": value} if value else {}


def resolve_cajon(slot: dict | None) -> str | None:
    ids = _resolved_ids(slot)
    if ids:
        return str(ids[0]).upper()
    value = ((slot or {}).get("value") or "").replace(" ", "").upper()
    return value or None


def slot_number(slot: dict | None) -> float | None:
    value = (slot or {}).get("value")
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def slot_uno(slot: dict | None) -> bool:
    return bool(_resolved_ids(slot)) or bool(((slot or {}).get("value") or "").strip().lower() in ("un", "una"))


def build_request(intent_name: str, slots: dict) -> dict | None:
    """The `cajonera.voz` payload for an order intent, or None for any other intent. Never invents a quantity."""
    accion = ORDER_INTENTS.get(intent_name)
    if accion is None:
        return None
    request: dict = {"accion": accion}
    request.update(resolve_producto(slots.get("producto")))
    if accion in ("anadir", "retirar", "actualizar"):
        cantidad = slot_number(slots.get("cantidad"))
        if cantidad is not None:
            request["cantidad"] = cantidad
        elif accion in ("anadir", "retirar") and slot_uno(slots.get("uno")):
            request["uno"] = True
    return request


# ---- Responses

def speak(text: str, *, end: bool = True, reprompt: str | None = None, attrs: dict | None = None) -> dict:
    response: dict = {"outputSpeech": {"type": "PlainText", "text": text}, "shouldEndSession": end}
    if not end:
        response["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": reprompt or text}}
    out: dict = {"version": "1.0", "response": response}
    if attrs:
        out["sessionAttributes"] = attrs
    return out


# ---- The conversation

def _ask(frase: str, pending: dict) -> dict:
    return speak(frase, end=False, attrs={"pendiente": pending})


def _after_ha(answer: dict, request: dict, state: dict | None) -> dict:
    """Turn HA's answer into an Alexa response, carrying the question counters forward."""
    pregunta = answer.get("pregunta")
    if not pregunta:
        return speak(answer.get("frase") or "Hecho.")
    preguntas = (state or {}).get("preguntas", 0) + 1
    if preguntas > MAX_PREGUNTAS:
        return speak(PANEL)
    request = dict(request)
    if pregunta.get("pieza_pk") is not None:
        request["pieza_pk"] = pregunta["pieza_pk"]
        request.pop("pieza_pks", None)
        request.pop("texto", None)
    pending = {
        "request": request,
        "falta": pregunta.get("falta"),
        "opciones": list(pregunta.get("opciones") or [])[:MAX_OPCIONES],
        "fallos": (state or {}).get("fallos", 0),
        "preguntas": preguntas,
    }
    return _ask(answer.get("frase") or "¿Cómo?", pending)


def _fill(state: dict, intent_name: str, slots: dict) -> tuple[dict | None, str | None]:
    """The request with the pending field filled by the user's answer, or (None, phrase to re-ask)."""
    falta = state.get("falta")
    request = dict(state["request"])
    opciones = state.get("opciones") or []
    if falta == "cantidad":
        cantidad = slot_number(slots.get("cantidad"))
        if cantidad is None:
            return None, "¿Cuántos?"
        request["cantidad"] = cantidad
        request.pop("uno", None)
        return request, None
    if falta == "cajon":
        cajon = resolve_cajon(slots.get("cajon"))
        valid = {str(o).upper() for o in opciones}
        if not cajon or (valid and cajon not in valid):
            return None, "¿En qué cajón?"
        request["cajon"] = cajon
        return request, None
    if falta == "pieza":
        resolved = resolve_producto(slots.get("producto"))
        offered = [o["pieza_pk"] for o in opciones if isinstance(o, dict) and "pieza_pk" in o]
        if "pieza_pk" in resolved:
            ids = [resolved["pieza_pk"]]
        else:
            ids = list(resolved.get("pieza_pks", []))
        narrowed = [i for i in ids if i in offered] if offered else ids
        if not narrowed:
            return None, "¿Cuál?"
        if len(narrowed) == 1:
            request["pieza_pk"] = narrowed[0]
            request.pop("pieza_pks", None)
            request.pop("texto", None)
            return request, None
        # Still ambiguous: keep only the candidates that survived and ask again.
        state["request"] = {**request, "pieza_pks": narrowed}
        state["opciones"] = [o for o in opciones if isinstance(o, dict) and o.get("pieza_pk") in narrowed]
        names = [o["nombre"] for o in state["opciones"]]
        return None, "Sigue habiendo varias: " + ", ".join(names[:3]) + ". ¿Cuál?"
    return None, "¿Qué quieres hacer?"


def handle_event(event: dict, call_ha, fallback_buscar, remaining_ms: int = 8000) -> dict:
    """The whole skill for one Alexa request. `call_ha(payload, timeout)` and `fallback_buscar(request, timeout)`
    are injected so the logic runs without a network."""
    request_data = event.get("request", {})
    kind = request_data.get("type")
    attrs = (event.get("session") or {}).get("attributes") or {}

    if kind == "LaunchRequest":
        return speak(
            "Bienvenido al inventario. ¿Qué quieres consultar o modificar?",
            end=False,
            reprompt="Dime qué producto quieres consultar o modificar.",
        )
    if kind == "SessionEndedRequest":
        return {"version": "1.0"}
    if kind != "IntentRequest":
        return speak("No tengo configurada esa operación.")

    intent = request_data.get("intent", {})
    name = intent.get("name")
    slots = intent.get("slots") or {}

    if name in ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NoIntent", "AMAZON.NavigateHomeIntent"):
        return speak("Hasta luego.")
    if name == "AMAZON.HelpIntent":
        return speak(
            "Puedes decir: cuántos huevos quedan, añade seis huevos, he cogido dos tornillos, o actualiza huevos a doce.",
            end=False,
        )
    if name == "AMAZON.FallbackIntent":
        return speak("No te he entendido. Prueba a decir: añade seis huevos o dónde están los tornillos.", end=False)

    if name in ORDER_INTENTS:
        request = build_request(name, slots)
        return _dispatch(request, None, call_ha, fallback_buscar, remaining_ms)

    if name in ANSWER_INTENTS:
        state = attrs.get("pendiente")
        if not state:
            return speak("¿Qué quieres hacer?", end=False)
        request, reask = _fill(state, name, slots)
        if request is None:
            fallos = state.get("fallos", 0) + 1
            if fallos >= MAX_FALLOS:
                return speak(PANEL)
            state = {**state, "fallos": fallos}
            return _ask(reask or "¿Cómo?", state)
        state = {**state, "fallos": 0}
        return _dispatch(request, state, call_ha, fallback_buscar, remaining_ms)

    return speak("No tengo configurada esa operación.")


def _dispatch(request: dict, state: dict | None, call_ha, fallback_buscar, remaining_ms: int) -> dict:
    """Call HA and turn its answer (or its failure) into the Alexa response. Failure handling: see transports."""
    try:
        answer = call_ha(request, 4.0)
    except HAError as err:
        return _on_ha_error(err, request, fallback_buscar, remaining_ms)
    return _after_ha(answer, request, state)


def _on_ha_error(err: HAError, request: dict, fallback_buscar, remaining_ms: int) -> dict:
    # Replaced with the real wording and the read-only fallback in the transports step.
    return speak("No puedo consultar el inventario ahora mismo.")
