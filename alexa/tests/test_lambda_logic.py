import pytest

from helpers import FakeHA, asks, done, ended_event, intent_event, is_open, launch_event, no_fallback, number, slot, text_of
from lambda_function import (
    MAX_FALLOS,
    MAX_PREGUNTAS,
    build_request,
    handle_event,
    resolve_cajon,
    resolve_producto,
    slot_number,
    slot_uno,
)

PANEL = "No consigo entenderlo. Hazlo desde el panel de la cajonera."


def run(event, ha):
    return handle_event(event, ha, no_fallback)


# ---- slots

def test_resolve_producto_one_id():
    assert resolve_producto(slot("producto", "huevos", [5])) == {"pieza_pk": 5}


def test_resolve_producto_several_ids_flattened_and_deduplicated():
    assert resolve_producto(slot("producto", "tornillos", [5, 6, 5, 7])) == {"pieza_pks": [5, 6, 7], "texto": "tornillos"}


def test_resolve_producto_caps_candidates_at_twenty():
    assert len(resolve_producto(slot("producto", "x", list(range(1, 40))))["pieza_pks"]) == 20


def test_resolve_producto_no_match_falls_back_to_text():
    assert resolve_producto(slot("producto", "arandelas raras", status="ER_SUCCESS_NO_MATCH")) == {"texto": "arandelas raras"}


def test_resolve_producto_raw_value_without_resolution():
    assert resolve_producto(slot("producto", "huevos")) == {"texto": "huevos"}


def test_resolve_producto_empty():
    assert resolve_producto(None) == {} and resolve_producto({"name": "producto"}) == {}


def test_resolve_cajon_prefers_resolved_id():
    s = {"name": "cajon", "value": "be tres", "resolutions": {"resolutionsPerAuthority": [
        {"status": {"code": "ER_SUCCESS_MATCH"}, "values": [{"value": {"name": "be tres", "id": "B3"}}]}]}}
    assert resolve_cajon(s) == "B3"
    assert resolve_cajon({"name": "cajon", "value": "c 2"}) == "C2"
    assert resolve_cajon(None) is None


def test_slot_number_and_uno():
    assert slot_number(number(50)) == 50.0
    assert slot_number(number("2,5")) == 2.5
    assert slot_number({"name": "cantidad"}) is None and slot_number(None) is None
    assert slot_uno(slot("uno", "un", ["un"])) is True and slot_uno(None) is False


# ---- building the request

def test_add_with_number_sends_cantidad():
    req = build_request("AnadirInventarioIntent", {"producto": slot("producto", "huevos", [5]), "cantidad": number(6)})
    assert req == {"accion": "anadir", "pieza_pk": 5, "cantidad": 6.0}


def test_add_with_uno_sends_uno_and_no_cantidad():
    req = build_request("AnadirInventarioIntent", {"producto": slot("producto", "huevos", [5]), "uno": slot("uno", "un", ["un"])})
    assert req == {"accion": "anadir", "pieza_pk": 5, "uno": True}


def test_add_without_either_sends_neither():
    """Review Focus 1: the Lambda never invents a quantity."""
    req = build_request("AnadirInventarioIntent", {"producto": slot("producto", "tornillos", [5, 6])})
    assert req == {"accion": "anadir", "pieza_pks": [5, 6], "texto": "tornillos"}
    assert "cantidad" not in req and "uno" not in req


@pytest.mark.parametrize("intent,accion", [
    ("ConsultarInventarioIntent", "buscar"), ("LocalizarInventarioIntent", "buscar"),
    ("RestarInventarioIntent", "retirar"), ("ActualizarInventarioIntent", "actualizar"),
])
def test_intent_to_accion(intent, accion):
    assert build_request(intent, {"producto": slot("producto", "huevos", [5])})["accion"] == accion


def test_other_intents_build_nothing():
    assert build_request("AMAZON.HelpIntent", {}) is None


# ---- conversation

def test_answer_with_a_done_result_ends_the_session():
    ha = FakeHA(done("Hecho. He retirado 2 de Huevos del B1. Quedan 10."))
    r = run(intent_event("RestarInventarioIntent", {"producto": slot("producto", "huevos", [5]), "cantidad": number(2)}), ha)
    assert text_of(r) == "Hecho. He retirado 2 de Huevos del B1. Quedan 10." and not is_open(r)
    assert ha.calls == [{"accion": "retirar", "pieza_pk": 5, "cantidad": 2.0}]


def test_question_keeps_session_open_with_reprompt_and_stores_state():
    ha = FakeHA(asks("cantidad", "¿Cuántos?", pieza_pk=5))
    r = run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "tornillos", [5])}), ha)
    assert text_of(r) == "¿Cuántos?" and is_open(r)
    assert r["response"]["reprompt"]["outputSpeech"]["text"]
    st = r["sessionAttributes"]["pendiente"]
    assert st["falta"] == "cantidad" and st["request"]["accion"] == "anadir" and st["request"]["pieza_pk"] == 5
    assert st["preguntas"] == 1 and st["fallos"] == 0


def test_quantity_answer_fills_the_field_and_ends_on_success():
    ha = FakeHA(asks("cantidad", "¿Cuántos?", pieza_pk=5), done("Hecho. He añadido 50."))
    first = run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "tornillos", [5])}), ha)
    r = run(intent_event("RespuestaCantidadIntent", {"cantidad": number(50)}, attrs=first["sessionAttributes"]), ha)
    assert text_of(r) == "Hecho. He añadido 50." and not is_open(r)
    assert ha.calls[1] == {"accion": "anadir", "pieza_pk": 5, "cantidad": 50.0}


def test_drawer_answer_fills_the_field():
    ha = FakeHA(asks("cajon", "Hay en el B1 y en el B3, ¿en cuál?", pieza_pk=5, opciones=["B1", "B3"]), done("Hecho."))
    first = run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "huevos", [5]), "cantidad": number(3)}), ha)
    r = run(intent_event("RespuestaCajonIntent", {"cajon": {"name": "cajon", "value": "b 3"}}, attrs=first["sessionAttributes"]), ha)
    assert not is_open(r)
    assert ha.calls[1] == {"accion": "anadir", "pieza_pk": 5, "cantidad": 3.0, "cajon": "B3"}


def test_pieza_answer_is_intersected_with_stored_candidates():
    """Review Focus 4: a shared keyword in the answer narrows the candidates, it does not start over."""
    opciones = [{"pieza_pk": 5, "nombre": "Tornillo M3x8"}, {"pieza_pk": 6, "nombre": "Tornillo M3x12"}]
    ha = FakeHA(asks("pieza", "Hay Tornillo M3x8 y Tornillo M3x12. ¿Cuál?", opciones=opciones), done("Hecho."))
    first = run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "tornillos", [5, 6, 9]), "cantidad": number(5)}), ha)
    # "m3" matches both options (and a part that was never offered): still ambiguous, ask again without calling HA
    r = run(intent_event("RespuestaPiezaIntent", {"producto": slot("producto", "m3", [5, 6, 20])}, attrs=first["sessionAttributes"]), ha)
    assert is_open(r) and r["sessionAttributes"]["pendiente"]["falta"] == "pieza" and len(ha.calls) == 1
    assert "Tornillo M3x8" in text_of(r) and "Tornillo M3x12" in text_of(r)
    # "ocho" narrows to one of the offered parts: HA is called with that part only
    r = run(intent_event("RespuestaPiezaIntent", {"producto": slot("producto", "ocho", [5, 77])}, attrs=first["sessionAttributes"]), ha)
    assert not is_open(r)
    assert ha.calls[1]["pieza_pk"] == 5 and "pieza_pks" not in ha.calls[1]


def test_answer_outside_the_options_fails():
    opciones = [{"pieza_pk": 5, "nombre": "A"}, {"pieza_pk": 6, "nombre": "B"}]
    ha = FakeHA(asks("pieza", "¿Cuál?", opciones=opciones))
    first = run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "x", [5, 6])}), ha)
    r = run(intent_event("RespuestaPiezaIntent", {"producto": slot("producto", "otra", [99])}, attrs=first["sessionAttributes"]), ha)
    assert is_open(r) and r["sessionAttributes"]["pendiente"]["fallos"] == 1
    assert len(ha.calls) == 1  # HA was not asked again


def test_two_failed_answers_send_the_user_to_the_panel():
    ha = FakeHA(asks("cantidad", "¿Cuántos?", pieza_pk=5))
    r = run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "huevos", [5])}), ha)
    for _ in range(MAX_FALLOS - 1):
        r = run(intent_event("RespuestaCantidadIntent", {}, attrs=r["sessionAttributes"]), ha)
        assert is_open(r)
    r = run(intent_event("RespuestaCantidadIntent", {}, attrs=r["sessionAttributes"]), ha)
    assert text_of(r) == PANEL and not is_open(r)


def test_four_questions_send_the_user_to_the_panel():
    ha = FakeHA(asks("cantidad", "¿Cuántos?", pieza_pk=5))  # HA keeps asking although each answer is understood
    r = run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "huevos", [5])}), ha)
    answers = 0
    while is_open(r) and answers < 10:
        r = run(intent_event("RespuestaCantidadIntent", {"cantidad": number(0)}, attrs=r["sessionAttributes"]), ha)
        answers += 1
    assert text_of(r) == PANEL and answers <= MAX_PREGUNTAS


def test_a_filled_answer_resets_the_failure_count():
    ha = FakeHA(asks("cantidad", "¿Cuántos?", pieza_pk=5), asks("cajon", "¿En cuál?", pieza_pk=5, opciones=["B1", "B3"]))
    r = run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "huevos", [5])}), ha)
    r = run(intent_event("RespuestaCantidadIntent", {}, attrs=r["sessionAttributes"]), ha)  # one failure
    assert r["sessionAttributes"]["pendiente"]["fallos"] == 1
    r = run(intent_event("RespuestaCantidadIntent", {"cantidad": number(4)}, attrs=r["sessionAttributes"]), ha)
    assert r["sessionAttributes"]["pendiente"]["fallos"] == 0 and r["sessionAttributes"]["pendiente"]["falta"] == "cajon"


def test_new_order_replaces_pending_state():
    ha = FakeHA(asks("cantidad", "¿Cuántos?", pieza_pk=5), done("Están en el B1. Quedan 12."))
    first = run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "huevos", [5])}), ha)
    r = run(intent_event("LocalizarInventarioIntent", {"producto": slot("producto", "tuercas", [8])}, attrs=first["sessionAttributes"]), ha)
    assert not is_open(r) and "pendiente" not in r.get("sessionAttributes", {})
    assert ha.calls[1] == {"accion": "buscar", "pieza_pk": 8}


def test_answer_without_pending_state_asks_what_to_do():
    r = run(intent_event("RespuestaCajonIntent", {"cajon": {"name": "cajon", "value": "b 3"}}), FakeHA(done()))
    assert text_of(r) == "¿Qué quieres hacer?" and is_open(r)


def test_launch_help_stop_fallback_wording():
    ha = FakeHA(done())
    assert text_of(run(launch_event(), ha)).startswith("Bienvenido al inventario.") and is_open(run(launch_event(), ha))
    assert run(ended_event(), ha) == {"version": "1.0"}
    for name in ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NoIntent", "AMAZON.NavigateHomeIntent"):
        r = run(intent_event(name), ha)
        assert text_of(r) == "Hasta luego." and not is_open(r)
    assert is_open(run(intent_event("AMAZON.HelpIntent"), ha)) and is_open(run(intent_event("AMAZON.FallbackIntent"), ha))
    assert ha.calls == []


def test_every_open_response_has_a_reprompt():
    ha = FakeHA(asks("cantidad", "¿Cuántos?", pieza_pk=5))
    responses = [
        run(launch_event(), ha),
        run(intent_event("AMAZON.HelpIntent"), ha),
        run(intent_event("AMAZON.FallbackIntent"), ha),
        run(intent_event("RespuestaCajonIntent", {}), ha),
        run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "huevos", [5])}), ha),
    ]
    for r in responses:
        assert is_open(r) and r["response"]["reprompt"]["outputSpeech"]["text"]


def test_the_fifth_question_is_never_asked():
    """Four questions are allowed for one order; the fifth turn is the hand-off to the panel."""
    ha = FakeHA(asks("cantidad", "¿Cuántos?", pieza_pk=5))
    r = run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "huevos", [5])}), ha)
    asked = 1
    while is_open(r):
        r = run(intent_event("RespuestaCantidadIntent", {"cantidad": number(0)}, attrs=r["sessionAttributes"]), ha)
        if is_open(r):
            asked += 1
    assert asked == MAX_PREGUNTAS and text_of(r) == PANEL


# ---- Final review I3: Help and Fallback do not throw a pending question away

def test_help_and_fallback_keep_the_pending_question():
    ha = FakeHA(asks("pieza", "¿De qué producto?"))
    first = run(intent_event("AnadirInventarioIntent", {"cantidad": number(3)}), ha)
    pending = first["sessionAttributes"]["pendiente"]
    for name in ("AMAZON.HelpIntent", "AMAZON.FallbackIntent"):
        r = run(intent_event(name, attrs=first["sessionAttributes"]), ha)
        assert is_open(r) and r["sessionAttributes"]["pendiente"]["falta"] == "pieza"
        assert "¿De qué producto?" in text_of(r)
    assert pending["fallos"] == 0


def test_fallback_counts_as_a_failed_answer_and_ends_at_the_limit():
    ha = FakeHA(asks("cantidad", "¿Cuántos?", pieza_pk=5))
    r = run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "huevos", [5])}), ha)
    for _ in range(MAX_FALLOS - 1):
        r = run(intent_event("AMAZON.FallbackIntent", attrs=r["sessionAttributes"]), ha)
        assert is_open(r)
    r = run(intent_event("AMAZON.FallbackIntent", attrs=r["sessionAttributes"]), ha)
    assert text_of(r) == PANEL and not is_open(r)


def test_stop_cancel_and_no_still_end_a_pending_question():
    ha = FakeHA(asks("cantidad", "¿Cuántos?", pieza_pk=5))
    first = run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "huevos", [5])}), ha)
    for name in ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NoIntent"):
        r = run(intent_event(name, attrs=first["sessionAttributes"]), ha)
        assert not is_open(r) and "sessionAttributes" not in r


def test_help_without_a_pending_question_is_unchanged():
    r = run(intent_event("AMAZON.HelpIntent"), FakeHA(done()))
    assert is_open(r) and "sessionAttributes" not in r


# ---- Final review I4: a part question with no options and an ambiguous answer

def test_ambiguous_answer_to_an_open_part_question_asks_ha_with_the_candidates():
    ha = FakeHA(asks("pieza", "¿De qué producto?"), asks("pieza", "Hay A y B. ¿Cuál?", opciones=[
        {"pieza_pk": 5, "nombre": "A"}, {"pieza_pk": 6, "nombre": "B"}]))
    first = run(intent_event("AnadirInventarioIntent", {"cantidad": number(3)}), ha)
    r = run(intent_event("RespuestaPiezaIntent", {"producto": slot("producto", "tornillos", [5, 6])}, attrs=first["sessionAttributes"]), ha)
    assert ha.calls[1] == {"accion": "anadir", "cantidad": 3.0, "pieza_pks": [5, 6]}
    assert text_of(r) == "Hay A y B. ¿Cuál?" and r["sessionAttributes"]["pendiente"]["fallos"] == 0


def test_still_ambiguous_after_narrowing_is_a_question_not_a_failure():
    """The executor's Task 5 ruling: counted as a question; never "Sigue habiendo varias: ."."""
    opciones = [{"pieza_pk": 5, "nombre": "Tornillo M3x8"}, {"pieza_pk": 6, "nombre": "Tornillo M3x12"}, {"pieza_pk": 7, "nombre": "Tornillo M4"}]
    ha = FakeHA(asks("pieza", "Hay tres. ¿Cuál?", opciones=opciones))
    first = run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "tornillos", [5, 6, 7]), "cantidad": number(2)}), ha)
    r = run(intent_event("RespuestaPiezaIntent", {"producto": slot("producto", "m tres", [5, 6, 99])}, attrs=first["sessionAttributes"]), ha)
    st = r["sessionAttributes"]["pendiente"]
    assert st["fallos"] == 0 and st["preguntas"] == 2
    assert "Tornillo M3x8" in text_of(r) and "Tornillo M3x12" in text_of(r) and "Tornillo M4" not in text_of(r)
    assert ": ." not in text_of(r)


# ---- The words the user said travel with the candidates Alexa resolved

def test_resolved_candidates_carry_the_spoken_text():
    """Alexa resolves "tornillos M3X8" to four screws; HA needs the words to pick the right one."""
    req = build_request("AnadirInventarioIntent", {"producto": slot("producto", "tornillos M3X8", [13, 14, 15, 2]), "cantidad": number(3)})
    assert req == {"accion": "anadir", "pieza_pks": [13, 14, 15, 2], "texto": "tornillos M3X8", "cantidad": 3.0}


def test_one_resolved_part_does_not_need_the_text():
    req = build_request("AnadirInventarioIntent", {"producto": slot("producto", "huevos", [5]), "cantidad": number(3)})
    assert req == {"accion": "anadir", "pieza_pk": 5, "cantidad": 3.0}


def test_text_survives_the_questions_so_the_answer_can_still_be_narrowed():
    ha = FakeHA(asks("cantidad", "¿Cuántos?", pieza_pk=13))
    r = run(intent_event("AnadirInventarioIntent", {"producto": slot("producto", "tornillos M3X8", [13, 14, 15, 2])}), ha)
    assert ha.calls[0]["texto"] == "tornillos M3X8"
    assert r["sessionAttributes"]["pendiente"]["request"]["pieza_pk"] == 13  # HA resolved it: the candidates are dropped
    assert "pieza_pks" not in r["sessionAttributes"]["pendiente"]["request"]
