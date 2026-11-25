"""Canned Alexa events and a recording fake for the HA call."""

from __future__ import annotations


def slot(name: str, value: str | None = None, ids: list | None = None, status: str = "ER_SUCCESS_MATCH") -> dict:
    """An Alexa slot. `ids` are the resolved value ids (several = a shared synonym)."""
    s: dict = {"name": name}
    if value is not None:
        s["value"] = value
    if ids:
        s["resolutions"] = {
            "resolutionsPerAuthority": [
                {
                    "authority": "amzn1.er-authority.test",
                    "status": {"code": status},
                    "values": [{"value": {"name": f"v{i}", "id": str(i)}} for i in ids],
                }
            ]
        }
    elif status == "ER_SUCCESS_NO_MATCH" and value is not None:
        s["resolutions"] = {"resolutionsPerAuthority": [{"authority": "a", "status": {"code": status}}]}
    return s


def number(value) -> dict:
    return {"name": "cantidad", "value": str(value)}


def intent_event(name: str, slots: dict | None = None, attrs: dict | None = None, token: str = "SECRET-ACCESS-TOKEN") -> dict:
    return {
        "version": "1.0",
        "session": {"new": False, "sessionId": "s1", "attributes": attrs or {}},
        "context": {"System": {"apiAccessToken": token}},
        "request": {"type": "IntentRequest", "intent": {"name": name, "slots": dict(slots or {})}},
    }


def launch_event() -> dict:
    return {"version": "1.0", "session": {"new": True, "attributes": {}}, "request": {"type": "LaunchRequest"}}


def ended_event() -> dict:
    return {"version": "1.0", "session": {"attributes": {}}, "request": {"type": "SessionEndedRequest"}}


class FakeHA:
    """Records every payload sent to `cajonera.voz` and answers from a queue (the last answer repeats)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[dict] = []

    def __call__(self, payload: dict, timeout: float) -> dict:
        self.calls.append(payload)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def done(frase: str = "Hecho.") -> dict:
    return {"frase": frase, "hecho": True, "pregunta": None}


def asks(falta: str, frase: str, pieza_pk: int | None = None, opciones: list | None = None) -> dict:
    return {"frase": frase, "hecho": False, "pregunta": {"falta": falta, "pieza_pk": pieza_pk, "opciones": opciones or []}}


def no_fallback(request: dict, timeout: float) -> str:
    raise RuntimeError("fallback not expected")


def text_of(response: dict) -> str:
    return response["response"]["outputSpeech"]["text"]


def is_open(response: dict) -> bool:
    return not response["response"]["shouldEndSession"]
