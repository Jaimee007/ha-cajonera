"""Alexa bridge for the cajonera: a thin Lambda in front of Home Assistant's `cajonera.voz` service.

Every order intent becomes one call to HA, and Alexa speaks the sentence HA returns. When HA needs one more
answer (how many, which drawer, which part) the Lambda keeps the Alexa session open and asks; the pending
question lives in the session attributes, never in the Lambda. If HA cannot be reached, a search is answered
read-only straight from InvenTree and a write says honestly whether it can have happened.

One file, standard library plus urllib3, so it can be pasted into the AWS console. Python 3.9+.
See alexa/README.md and docs/superpowers/specs/2026-11-25-alexa-bridge-design.md.
"""

from __future__ import annotations

import json
import logging
import os
import time

import urllib3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# One pool for every warm invocation: no new TLS handshake per request.
_http = urllib3.PoolManager()

HA_TIMEOUT = 4.0  # seconds the HA call may take; Alexa gives the whole skill 8
USER_AGENT = "MiAtlasAlexaSkill/1.0"  # Cloudflare answers 403 to Python's default User-Agent
NOT_WRITTEN = "No he podido hablar con Home Assistant, no he cambiado nada."
MAYBE_WRITTEN = "No sé si se ha registrado. Pregúntame cuánto queda antes de repetirlo."
DOWN = "No puedo consultar el inventario ahora mismo."

MAX_FALLOS = 2  # consecutive answers that did not fill the pending question
MAX_PREGUNTAS = 4  # questions asked for one order
MAX_OPCIONES = 20  # candidates kept in the session and sent to HA
MAX_READONLY_PARTS = 5  # parts the read-only fallback checks: each one costs a request

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


# ---- Logging: names and outcomes only. The event carries an access token; never log it.

def _log(intent: str, slots: list, outcome: str) -> None:
    logger.info("intent=%s slots=%s outcome=%s", intent, ",".join(sorted(slots)), outcome)


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
    value = ((slot or {}).get("value") or "").strip()
    if len(ids) == 1:
        return {"pieza_pk": ids[0]}
    if ids:
        # A shared word ("tornillos") resolves to several parts; the words the user said ("tornillos M3X8")
        # travel with them so Home Assistant can pick the one that was named.
        found = {"pieza_pks": ids[:MAX_OPCIONES]}
        if value:
            found["texto"] = value
        return found
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
        "pregunta": answer.get("frase") or "¿Cómo?",
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
        if not offered:
            # The question had no options (HA did not know the part yet): let HA see every candidate and ask.
            request["pieza_pks"] = narrowed[:MAX_OPCIONES]
            request.pop("pieza_pk", None)
            request.pop("texto", None)
            return request, None
        # Still ambiguous among the options offered: keep the survivors and ask again, locally. This is a
        # question, not a failure (the user did answer).
        survivors = [o for o in opciones if isinstance(o, dict) and o.get("pieza_pk") in narrowed]
        state["request"] = {**request, "pieza_pks": narrowed}
        state["opciones"] = survivors
        state["_otra_pregunta"] = True
        names = [o["nombre"] for o in survivors]
        if len(names) > 3:
            names = names[:3] + [f"{len(survivors) - 3} más"]
        return None, "Sigue habiendo varias: " + _join(names) + ". ¿Cuál?"
    return None, "¿Qué quieres hacer?"


def _join(items: list) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]


def handle_event(event: dict, call_ha, fallback_buscar, remaining_ms=8000) -> dict:
    """The whole skill for one Alexa request. `call_ha(payload, timeout)` and `fallback_buscar(request, timeout)`
    are injected so the logic runs without a network. `remaining_ms` is the time Alexa still gives the skill, as an
    int or as a callable that is read when it matters (after HA failed), not at the start."""
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

    pending = attrs.get("pendiente")

    if name in ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NoIntent", "AMAZON.NavigateHomeIntent"):
        return speak("Hasta luego.")  # an explicit wish to stop ends the question too
    if name == "AMAZON.HelpIntent":
        help_text = "Puedes decir: cuántos huevos quedan, añade seis huevos, he cogido dos tornillos, o actualiza huevos a doce."
        if pending:  # keep the question: help must not make the user start the order again
            return _ask(f"{help_text} {pending.get('pregunta') or ''}".strip(), pending)
        return speak(help_text, end=False)
    if name == "AMAZON.FallbackIntent":
        if pending:  # the answer was not understood: it counts as a failed one and the question stays
            fallos = pending.get("fallos", 0) + 1
            if fallos >= MAX_FALLOS:
                return speak(PANEL)
            return _ask(pending.get("pregunta") or "¿Cómo?", {**pending, "fallos": fallos})
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
            if state.pop("_otra_pregunta", False):
                # Narrowed but still ambiguous: a new question, not a failure.
                preguntas = state.get("preguntas", 0) + 1
                if preguntas > MAX_PREGUNTAS:
                    return speak(PANEL)
                return _ask(reask, {**state, "preguntas": preguntas, "fallos": 0, "pregunta": reask})
            fallos = state.get("fallos", 0) + 1
            if fallos >= MAX_FALLOS:
                return speak(PANEL)
            state = {**state, "fallos": fallos, "pregunta": reask or state.get("pregunta")}
            return _ask(reask or "¿Cómo?", state)
        state = {**state, "fallos": 0}
        return _dispatch(request, state, call_ha, fallback_buscar, remaining_ms)

    return speak("No tengo configurada esa operación.")


def _dispatch(request: dict, state: dict | None, call_ha, fallback_buscar, remaining_ms: int) -> dict:
    """Call HA and turn its answer (or its failure) into the Alexa response."""
    try:
        answer = call_ha(request, HA_TIMEOUT)
    except HAError as err:
        return _on_ha_error(err, request, fallback_buscar, remaining_ms)
    return _after_ha(answer, request, state)


def _on_ha_error(err: HAError, request: dict, fallback_buscar, remaining_ms) -> dict:
    """HA did not answer. A search is answered read-only from InvenTree; a write says honestly what may have happened,
    and never keeps a pending question."""
    if request.get("accion") == "buscar":
        left = (remaining_ms() if callable(remaining_ms) else remaining_ms) / 1000.0 - 1.0  # time left now, minus a 1 s margin
        if left < 0.5:
            return speak(DOWN)  # no time to look: answer something rather than let Alexa time out
        try:
            return speak(fallback_buscar(request, left))
        except RuntimeError:
            return speak(DOWN)
    return speak(MAYBE_WRITTEN if err.sent else NOT_WRITTEN)


# ---- Transports

def _env(name: str) -> str:
    return os.environ.get(name, "")


def ha_call(payload: dict, timeout: float) -> dict:
    """POST cajonera.voz and return its `service_response`. One request, never retried: a retried POST could write twice."""
    url = _env("HA_URL").rstrip("/") + "/api/services/cajonera/voz?return_response"
    try:
        response = _http.request(
            "POST",
            url,
            body=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + _env("HA_TOKEN"),
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            timeout=urllib3.Timeout(total=timeout),  # connect and read share ONE budget, so HA can never take 2 + 4 s
            retries=False,
        )
    except (urllib3.exceptions.ConnectTimeoutError, urllib3.exceptions.NewConnectionError):
        raise HAError("connect", sent=False)
    except urllib3.exceptions.MaxRetryError as err:
        # Raised for connection-level failures; the request never left unless the reason says it did.
        reason = getattr(err, "reason", None)
        raise HAError("connection", sent=isinstance(reason, (urllib3.exceptions.ReadTimeoutError, urllib3.exceptions.ProtocolError)))
    except urllib3.exceptions.HTTPError:
        raise HAError("transport", sent=True)
    if response.status >= 500:
        raise HAError(f"http {response.status}", sent=True)
    if response.status >= 400:
        raise HAError(f"http {response.status}", sent=False)
    try:
        envelope = json.loads(response.data.decode("utf-8"))
        answer = envelope["service_response"]
        answer["frase"]
    except (ValueError, KeyError, TypeError):
        raise HAError("bad envelope", sent=True)
    return answer


def _inventree(path: str, timeout: float) -> dict:
    headers = {"Authorization": "Token " + _env("INVENTREE_TOKEN"), "Accept": "application/json", "User-Agent": USER_AGENT}
    if _env("CF_ACCESS_CLIENT_ID") and _env("CF_ACCESS_CLIENT_SECRET"):
        headers["CF-Access-Client-Id"] = _env("CF_ACCESS_CLIENT_ID")
        headers["CF-Access-Client-Secret"] = _env("CF_ACCESS_CLIENT_SECRET")
    try:
        response = _http.request(
            "GET", _env("INVENTREE_URL").rstrip("/") + path, headers=headers,
            timeout=urllib3.Timeout(connect=min(2.0, timeout), read=timeout), retries=False,
        )
        if response.status != 200:
            raise RuntimeError(f"inventree http {response.status}")
        return json.loads(response.data.decode("utf-8"))
    except (urllib3.exceptions.HTTPError, ValueError) as err:
        raise RuntimeError("inventree unreachable") from err


def _rows(data) -> list:
    return data.get("results", []) if isinstance(data, dict) else (data or [])


def inventree_readonly(request: dict, timeout: float) -> str:
    """Answer "where is it" straight from InvenTree, read only, when Home Assistant is down. Lights nothing."""
    if request.get("pieza_pk") is not None:
        pks = [request["pieza_pk"]]
    elif request.get("pieza_pks"):
        pks = list(request["pieza_pks"])
    elif request.get("texto"):
        from urllib.parse import quote

        pks = [p["pk"] for p in _rows(_inventree("/api/part/?active=true&limit=10&search=" + quote(request["texto"]), timeout))]
    else:
        pks = []
    drawers: list = []
    deadline = time.monotonic() + timeout
    for pk in pks[:MAX_READONLY_PARTS]:
        left = deadline - time.monotonic()
        if left <= 0:
            raise RuntimeError("read-only lookup ran out of time")
        for item in _rows(_inventree(f"/api/stock/?part={pk}&location_detail=true&limit=50", left)):
            name = (item.get("location_detail") or {}).get("name")
            if name and float(item.get("quantity") or 0) > 0 and name not in drawers:
                drawers.append(name)
    if not drawers:
        return "No encuentro nada con ese nombre."
    where = "Está en el " + drawers[0] + "." if len(drawers) == 1 else "Hay " + " y ".join("en el " + d for d in drawers[:3]) + "."
    return where + " Ahora no puedo encender el cajón."


def lambda_handler(event, context):
    """AWS entry point."""
    request = event.get("request", {})
    intent = (request.get("intent") or {})
    remaining = (lambda: context.get_remaining_time_in_millis()) if context is not None else (lambda: 8000)
    try:
        response = handle_event(event, ha_call, inventree_readonly, remaining_ms=remaining)
        outcome = "ok"
    except Exception as err:  # an unexpected bug must still answer Alexa, never go silent
        # Only the exception TYPE: its message and traceback can carry a URL, a header or a token.
        logger.error("unexpected error type=%s", type(err).__name__)
        response = speak("Ha ocurrido un error. Inténtalo de nuevo.")
        outcome = "error"
    _log(intent.get("name") or request.get("type", "?"), list((intent.get("slots") or {}).keys()), outcome)
    return response
