import json
import logging

import pytest
import urllib3

import lambda_function as lf
from helpers import FakeHA, asks, done, intent_event, is_open, no_fallback, number, slot, text_of

NOT_WRITTEN = "No he podido hablar con Home Assistant, no he cambiado nada."
MAYBE = "No sé si se ha registrado. Pregúntame cuánto queda antes de repetirlo."
DOWN = "No puedo consultar el inventario ahora mismo."


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self.data = json.dumps(body if body is not None else {}).encode("utf-8")


class FakeHttp:
    """Stands in for urllib3.PoolManager: records requests and answers (or raises) from a queue."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def request(self, method, url, **kw):
        self.requests.append((method, url, kw))
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("HA_URL", "https://ha.example.test/")
    monkeypatch.setenv("HA_TOKEN", "HA-SECRET-TOKEN")
    monkeypatch.setenv("INVENTREE_URL", "https://inv.example.test/")
    monkeypatch.setenv("INVENTREE_TOKEN", "INV-SECRET-TOKEN")
    monkeypatch.delenv("CF_ACCESS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)


def use_http(monkeypatch, http):
    monkeypatch.setattr(lf, "_http", http)
    return http


ENVELOPE = {"changed_states": [], "service_response": {"frase": "Están en el B1. Quedan 12.", "hecho": False, "pregunta": None}}


# ---- the HA call

def test_ha_call_posts_expected_request(monkeypatch):
    http = use_http(monkeypatch, FakeHttp(FakeResponse(200, ENVELOPE)))
    lf.ha_call({"accion": "buscar", "pieza_pk": 5}, 4.0)
    method, url, kw = http.requests[0]
    assert method == "POST" and url == "https://ha.example.test/api/services/cajonera/voz?return_response"
    assert kw["headers"]["Authorization"] == "Bearer HA-SECRET-TOKEN"
    assert kw["headers"]["Content-Type"] == "application/json"
    assert json.loads(kw["body"]) == {"accion": "buscar", "pieza_pk": 5}
    assert kw["retries"] is False


def test_ha_call_returns_service_response(monkeypatch):
    use_http(monkeypatch, FakeHttp(FakeResponse(200, ENVELOPE)))
    assert lf.ha_call({"accion": "buscar"}, 4.0) == ENVELOPE["service_response"]


@pytest.mark.parametrize("failure,sent", [
    (urllib3.exceptions.ConnectTimeoutError(None, "u", "t"), False),
    (urllib3.exceptions.NewConnectionError(None, "refused"), False),
    (urllib3.exceptions.MaxRetryError(None, "u", reason=urllib3.exceptions.NewConnectionError(None, "dns")), False),
    (FakeResponse(401, {}), False),
    (FakeResponse(404, {}), False),
    (urllib3.exceptions.ReadTimeoutError(None, "u", "t"), True),
    (urllib3.exceptions.ProtocolError("dropped"), True),
    (FakeResponse(500, {}), True),
    (FakeResponse(502, {}), True),
])
def test_ha_error_sent_flag_per_failure_kind(monkeypatch, failure, sent):
    use_http(monkeypatch, FakeHttp(failure))
    with pytest.raises(lf.HAError) as err:
        lf.ha_call({"accion": "retirar"}, 4.0)
    assert err.value.sent is sent


def test_ha_call_bad_envelope_counts_as_sent(monkeypatch):
    use_http(monkeypatch, FakeHttp(FakeResponse(200, {"unexpected": True})))
    with pytest.raises(lf.HAError) as err:
        lf.ha_call({"accion": "buscar"}, 4.0)
    assert err.value.sent is True


def test_post_is_never_retried(monkeypatch):
    """Review Focus 3: exactly one request goes out, whatever fails."""
    http = use_http(monkeypatch, FakeHttp(urllib3.exceptions.ReadTimeoutError(None, "u", "t")))
    with pytest.raises(lf.HAError):
        lf.ha_call({"accion": "retirar"}, 4.0)
    assert len(http.requests) == 1


# ---- wording after a failure

WRITE = intent_event("RestarInventarioIntent", {"producto": slot("producto", "huevos", [5]), "cantidad": number(2)})
SEARCH = intent_event("LocalizarInventarioIntent", {"producto": slot("producto", "huevos", [5])})


def test_write_failure_wording_depends_on_sent():
    """Review Focus 3."""
    for sent, wording in ((False, NOT_WRITTEN), (True, MAYBE)):
        r = lf.handle_event(WRITE, FakeHA(lf.HAError("x", sent=sent)), no_fallback)
        assert text_of(r) == wording and not is_open(r)


def test_failed_write_never_keeps_a_pending_question():
    r = lf.handle_event(WRITE, FakeHA(lf.HAError("x", sent=True)), no_fallback)
    assert "sessionAttributes" not in r


def test_buscar_falls_back_to_inventree_read_only():
    seen = {}

    def fallback(request, timeout):
        seen["request"] = request
        return "Está en el B3. Ahora no puedo encender el cajón."

    r = lf.handle_event(SEARCH, FakeHA(lf.HAError("x", sent=True)), fallback)
    assert text_of(r) == "Está en el B3. Ahora no puedo encender el cajón." and not is_open(r)
    assert seen["request"] == {"accion": "buscar", "pieza_pk": 5}


def test_fallback_deadline_uses_remaining_time():
    seen = {}

    def fallback(request, timeout):
        seen["timeout"] = timeout
        return "x"

    # remaining_ms is what Alexa still gives the skill WHEN HA has failed: 1 s is kept as a margin
    lf.handle_event(SEARCH, FakeHA(lf.HAError("x", sent=True)), fallback, remaining_ms=8000)
    assert seen["timeout"] == pytest.approx(7.0)
    lf.handle_event(SEARCH, FakeHA(lf.HAError("x", sent=True)), fallback, remaining_ms=3000)
    assert seen["timeout"] == pytest.approx(2.0)


def test_fallback_failure_says_down():
    def failing(request, timeout):
        raise RuntimeError("inventree down")

    r = lf.handle_event(SEARCH, FakeHA(lf.HAError("x", sent=False)), failing)
    assert text_of(r) == DOWN and not is_open(r)


# ---- the read-only InvenTree lookup

def inv_http(monkeypatch, pages):
    """pages: url fragment -> body, answered in order of request."""
    class Http(FakeHttp):
        def request(self, method, url, **kw):
            self.requests.append((method, url, kw))
            for fragment, body in pages.items():
                if fragment in url:
                    return FakeResponse(200, body)
            return FakeResponse(404, {})

    return use_http(monkeypatch, Http(FakeResponse(200, {})))


def test_inventree_readonly_one_drawer(monkeypatch):
    inv_http(monkeypatch, {
        "/api/part/5/": {"pk": 5, "name": "Huevos"},
        "/api/stock/?part=5": {"results": [{"pk": 9, "quantity": 12, "location": 1, "location_detail": {"name": "B1"}}]},
    })
    assert lf.inventree_readonly({"accion": "buscar", "pieza_pk": 5}, 3.0) == "Está en el B1. Ahora no puedo encender el cajón."


def test_inventree_readonly_several_drawers(monkeypatch):
    inv_http(monkeypatch, {
        "/api/part/5/": {"pk": 5, "name": "Huevos"},
        "/api/stock/?part=5": {"results": [
            {"pk": 9, "quantity": 7, "location": 1, "location_detail": {"name": "B1"}},
            {"pk": 10, "quantity": 5, "location": 2, "location_detail": {"name": "B3"}}]},
    })
    assert lf.inventree_readonly({"accion": "buscar", "pieza_pk": 5}, 3.0) == "Hay en el B1 y en el B3. Ahora no puedo encender el cajón."


def test_inventree_readonly_by_text(monkeypatch):
    inv_http(monkeypatch, {
        "/api/part/?": {"results": [{"pk": 5, "name": "Huevos"}]},
        "/api/stock/?part=5": {"results": [{"pk": 9, "quantity": 12, "location": 1, "location_detail": {"name": "B1"}}]},
    })
    assert lf.inventree_readonly({"accion": "buscar", "texto": "huevos"}, 3.0).startswith("Está en el B1.")


def test_inventree_readonly_nothing_found(monkeypatch):
    inv_http(monkeypatch, {"/api/part/?": {"results": []}})
    assert lf.inventree_readonly({"accion": "buscar", "texto": "arandela"}, 3.0) == "No encuentro nada con ese nombre."


def test_inventree_readonly_failure_raises(monkeypatch):
    use_http(monkeypatch, FakeHttp(urllib3.exceptions.ReadTimeoutError(None, "u", "t")))
    with pytest.raises(RuntimeError):
        lf.inventree_readonly({"accion": "buscar", "pieza_pk": 5}, 3.0)


def test_cloudflare_headers_only_when_both_env_vars_set(monkeypatch):
    http = inv_http(monkeypatch, {"/api/part/5/": {"pk": 5, "name": "Huevos"}, "/api/stock/?part=5": {"results": []}})
    lf.inventree_readonly({"accion": "buscar", "pieza_pk": 5}, 3.0)
    assert "CF-Access-Client-Id" not in http.requests[0][2]["headers"]
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "id")
    lf.inventree_readonly({"accion": "buscar", "pieza_pk": 5}, 3.0)
    assert "CF-Access-Client-Id" not in http.requests[-1][2]["headers"]  # one of the two is not enough
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "secret")
    lf.inventree_readonly({"accion": "buscar", "pieza_pk": 5}, 3.0)
    headers = http.requests[-1][2]["headers"]
    assert headers["CF-Access-Client-Id"] == "id" and headers["CF-Access-Client-Secret"] == "secret"


def test_user_agent_is_not_pythons_default(monkeypatch):
    http = inv_http(monkeypatch, {"/api/part/5/": {"pk": 5, "name": "Huevos"}, "/api/stock/?part=5": {"results": []}})
    lf.inventree_readonly({"accion": "buscar", "pieza_pk": 5}, 3.0)
    ua = http.requests[0][2]["headers"]["User-Agent"]
    assert ua and "python" not in ua.lower() and "urllib" not in ua.lower()
    assert http.requests[0][2]["headers"]["Authorization"] == "Token INV-SECRET-TOKEN"


# ---- logging

def test_logs_never_contain_the_event_or_tokens(monkeypatch, caplog, capsys):
    """Review Focus 5: nothing sensitive is logged on any path, failures included."""
    caplog.set_level(logging.DEBUG)
    event_token = "EVENT-ACCESS-TOKEN-XYZ"
    paths = [
        (WRITE, FakeHA(done("Hecho."))),
        (WRITE, FakeHA(lf.HAError("boom HA-SECRET-TOKEN", sent=True))),
        (WRITE, FakeHA(lf.HAError("x", sent=False))),
        (SEARCH, FakeHA(lf.HAError("x", sent=True))),
        (intent_event("AnadirInventarioIntent", {"producto": slot("producto", "huevos", [5])}), FakeHA(asks("cantidad", "¿Cuántos?", pieza_pk=5))),
    ]
    for event, ha in paths:
        event["context"]["System"]["apiAccessToken"] = event_token
        lf.handle_event(event, ha, lambda r, t: (_ for _ in ()).throw(RuntimeError("INV-SECRET-TOKEN")))
    http = use_http(monkeypatch, FakeHttp(urllib3.exceptions.ReadTimeoutError(None, "https://ha.example.test", "t")))
    ctx = type("Ctx", (), {"get_remaining_time_in_millis": lambda self: 8000})()
    ev = intent_event("RestarInventarioIntent", {"producto": slot("producto", "huevos", [5]), "cantidad": number(2)}, token=event_token)
    lf.lambda_handler(ev, ctx)
    captured = capsys.readouterr()
    everything = caplog.text + captured.out + captured.err
    for secret in (event_token, "HA-SECRET-TOKEN", "INV-SECRET-TOKEN", "apiAccessToken", "Bearer "):
        assert secret not in everything, secret
    assert len(http.requests) == 1  # and the write was not retried


def test_logging_helper_only_writes_names_and_outcome(caplog):
    caplog.set_level(logging.INFO)
    lf._log("RestarInventarioIntent", ["producto", "cantidad"], "done")
    assert caplog.records and "RestarInventarioIntent" in caplog.text and "producto" in caplog.text and "done" in caplog.text


# ---- end to end through the AWS entry point

def test_lambda_handler_end_to_end_with_fake_transport(monkeypatch):
    use_http(monkeypatch, FakeHttp(FakeResponse(200, {"changed_states": [], "service_response": {
        "frase": "Hecho. He retirado 2 de Huevos del B1. Quedan 10.", "hecho": True, "pregunta": None}})))
    ctx = type("Ctx", (), {"get_remaining_time_in_millis": lambda self: 8000})()
    r = lf.lambda_handler(WRITE, ctx)
    assert text_of(r) == "Hecho. He retirado 2 de Huevos del B1. Quedan 10." and not is_open(r)


def test_unexpected_error_logs_only_the_exception_type(monkeypatch, caplog):
    """An exception message can carry a URL or a token: only its type is logged."""
    caplog.set_level(logging.DEBUG)

    def boom(*a, **k):
        raise ValueError("secret=HA-SECRET-TOKEN https://ha.example.test/?token=ABC")

    monkeypatch.setattr(lf, "handle_event", boom)
    ctx = type("Ctx", (), {"get_remaining_time_in_millis": lambda self: 8000})()
    r = lf.lambda_handler(WRITE, ctx)
    assert text_of(r) == "Ha ocurrido un error. Inténtalo de nuevo."
    assert "ValueError" in caplog.text
    assert "HA-SECRET-TOKEN" not in caplog.text and "token=ABC" not in caplog.text


# ---- Final review I2: the 8 s budget is enforced where it is spent

def test_ha_call_uses_a_total_budget_not_connect_plus_read(monkeypatch):
    http = use_http(monkeypatch, FakeHttp(FakeResponse(200, ENVELOPE)))
    lf.ha_call({"accion": "buscar"}, 4.0)
    timeout = http.requests[0][2]["timeout"]
    assert timeout.total == 4.0  # one budget for connect AND read: HA can never take 2 s + 4 s


def test_fallback_deadline_is_computed_after_the_ha_call_fails():
    """The time left is read when HA has failed, not at the start: it is a callable the Lambda re-reads."""
    clock = {"left": 8000}
    seen = {}

    def slow_ha(payload, timeout):
        clock["left"] = 3200  # HA hung for 4.8 s
        raise lf.HAError("slow", sent=True)

    def fallback(request, timeout):
        seen["timeout"] = timeout
        return "x"

    lf.handle_event(SEARCH, slow_ha, fallback, remaining_ms=lambda: clock["left"])
    assert seen["timeout"] == pytest.approx(2.2)  # 3.2 s were left when HA failed, minus the 1 s margin
    lf.handle_event(SEARCH, slow_ha_with(clock, 6000), fallback, remaining_ms=lambda: clock["left"])
    assert seen["timeout"] == pytest.approx(5.0)


def slow_ha_with(clock, left):
    def call(payload, timeout):
        clock["left"] = left
        raise lf.HAError("x", sent=True)

    return call


def test_fallback_gives_up_when_there_is_no_time_left():
    def fallback(request, timeout):
        raise AssertionError("must not be called with no time")

    r = lf.handle_event(SEARCH, slow_ha_with({"left": 0}, 900), fallback, remaining_ms=lambda: 900)
    assert text_of(r) == DOWN and not is_open(r)


def test_readonly_lookup_caps_the_parts_it_checks(monkeypatch):
    calls = []

    class Http(FakeHttp):
        def request(self, method, url, **kw):
            calls.append(url)
            return FakeResponse(200, {"results": []})

    use_http(monkeypatch, Http(FakeResponse(200, {})))
    lf.inventree_readonly({"accion": "buscar", "pieza_pks": list(range(1, 21))}, 3.0)
    assert len(calls) <= lf.MAX_READONLY_PARTS


def test_readonly_lookup_respects_its_own_deadline(monkeypatch):
    import time

    class Slow(FakeHttp):
        def request(self, method, url, **kw):
            time.sleep(0.3)
            return FakeResponse(200, {"results": [{"pk": 9, "quantity": 1, "location": 1, "location_detail": {"name": "B1"}}]})

    use_http(monkeypatch, Slow(FakeResponse(200, {})))
    started = time.monotonic()
    with pytest.raises(RuntimeError):  # it stops when its budget is spent instead of asking for every part
        lf.inventree_readonly({"accion": "buscar", "pieza_pks": [1, 2, 3, 4, 5]}, 0.5)
    assert time.monotonic() - started < 1.2  # near its budget, not 5 x its timeout
