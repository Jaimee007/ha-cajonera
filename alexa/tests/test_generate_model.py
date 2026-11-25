import copy
import json
import re
from pathlib import Path

import pytest

import generate_model as gm

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "model_template.json"


@pytest.fixture
def template():
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def lm(model):
    return model["interactionModel"]["languageModel"]


def intents(model):
    return {i["name"]: i for i in lm(model)["intents"]}


def values(model, type_name):
    return next(t for t in lm(model)["types"] if t["name"] == type_name)["values"]


PARTS = [
    {"pk": 5, "name": "Tornillo M3x8", "keywords": "tornillos, tornillo, m3, m3 por 8"},
    {"pk": 6, "name": "Tornillo M3x12", "keywords": "tornillos, tornillo, m3, m3 por 12"},
    {"pk": 7, "name": "Resistencia 4k7", "keywords": "resistencias, resistencia, 4k7, cuatro con siete"},
    {"pk": 8, "name": "Huevos", "keywords": ""},
]


# ---- the template

def test_template_keeps_invocation_name_and_five_intents(template):
    assert lm(template)["invocationName"] == "mi atlas"
    names = set(intents(template))
    assert {"ConsultarInventarioIntent", "AnadirInventarioIntent", "RestarInventarioIntent",
            "ActualizarInventarioIntent", "LocalizarInventarioIntent"} <= names
    assert {"RespuestaCajonIntent", "RespuestaCantidadIntent", "RespuestaPiezaIntent"} <= names


def test_every_utterance_slot_exists_in_its_intent(template):
    for intent in intents(template).values():
        declared = {s["name"] for s in intent.get("slots", [])}
        for sample in intent["samples"]:
            used = set(re.findall(r"\{(\w+)\}", sample))
            assert used <= declared, (intent["name"], sample, used - declared)


def test_no_utterance_is_shared_between_intents(template):
    seen = {}
    for intent in intents(template).values():
        for sample in intent["samples"]:
            assert sample not in seen, (sample, seen.get(sample), intent["name"])
            seen[sample] = intent["name"]


def test_utterances_have_no_duplicates_inside_an_intent(template):
    for intent in intents(template).values():
        assert len(intent["samples"]) == len(set(intent["samples"])), intent["name"]


def test_risky_utterances_are_gone(template):
    actualizar = intents(template)["ActualizarInventarioIntent"]["samples"]
    for risky in ("hay {cantidad} {producto}", "quedan {cantidad} {producto}", "tengo {cantidad} {producto}",
                  "hay {cantidad} {producto} en total", "quedan {cantidad} {producto} en total"):
        assert risky not in actualizar
    assert "{producto}" not in intents(template)["ConsultarInventarioIntent"]["samples"]


def test_utterances_use_accents(template):
    all_samples = " ".join(s for i in intents(template).values() for s in i["samples"])
    for word in ("añade", "cuántos", "dónde", "cajón"):
        assert word in all_samples
    for unaccented in ("anade ", "cuantos ", "donde "):
        assert unaccented not in all_samples


def test_answer_intents_use_carrier_phrases(template):
    caj = intents(template)["RespuestaCajonIntent"]["samples"]
    assert "{cajon}" not in caj and any(s.startswith("en el") for s in caj)
    pieza = intents(template)["RespuestaPiezaIntent"]["samples"]
    assert "{producto}" not in pieza
    assert "{cantidad}" in intents(template)["RespuestaCantidadIntent"]["samples"]  # a bare number is allowed there


def test_anadir_and_restar_carry_both_quantity_forms(template):
    for name in ("AnadirInventarioIntent", "RestarInventarioIntent"):
        samples = intents(template)[name]["samples"]
        assert any("{cantidad}" in s for s in samples) and any("{uno}" in s for s in samples)
        assert {s["name"] for s in intents(template)[name]["slots"]} == {"producto", "cantidad", "uno"}


# ---- the generator

def test_part_value_has_id_and_synonyms(template):
    model, warnings = gm.build_model(template, PARTS, ["A1", "B3"])
    value = next(v for v in values(model, "TipoProducto") if v["id"] == "7")
    assert value["name"]["value"] == "resistencia cuatro k siete"
    syn = value["name"]["synonyms"]
    assert "resistencias" in syn and "cuatro k siete" in syn and "cuatro con siete" in syn
    assert value["name"]["value"] not in syn
    assert len(syn) == len(set(syn))


def test_shared_keyword_stays_on_every_part(template):
    """Review Focus 4: both M3 screws keep "tornillos" so Alexa returns both and the Lambda can ask."""
    model, _ = gm.build_model(template, PARTS, [])
    by_id = {v["id"]: v for v in values(model, "TipoProducto")}
    for pk in ("5", "6"):
        assert "tornillos" in by_id[pk]["name"]["synonyms"]


def test_digits_are_spelled_out_in_synonyms(template):
    model, _ = gm.build_model(template, PARTS, [])
    syn = next(v for v in values(model, "TipoProducto") if v["id"] == "5")["name"]["synonyms"]
    assert "m tres por ocho" in syn
    for v in values(model, "TipoProducto"):
        for form in [v["name"]["value"]] + v["name"].get("synonyms", []):
            assert all(c.isalpha() or c == " " for c in form), form


def test_plural_variants_are_added_for_single_words(template):
    model, _ = gm.build_model(template, PARTS, [])
    huevos = next(v for v in values(model, "TipoProducto") if v["id"] == "8")
    assert huevos["name"]["value"] == "huevos" and "huevo" in huevos["name"]["synonyms"]


def test_part_without_spoken_form_is_omitted_with_warning(template):
    parts = PARTS + [{"pk": 99, "name": "!!!", "keywords": "???"}]
    model, warnings = gm.build_model(template, parts, [])
    assert all(v["id"] != "99" for v in values(model, "TipoProducto"))
    assert any("99" in w for w in warnings)


def test_drawer_values(template):
    model, warnings = gm.build_model(template, PARTS, ["A1", "B3", "K1"])
    cajones = {v["id"]: v for v in values(model, "TipoCajon")}
    assert set(cajones) == {"A1", "B3"}
    assert cajones["B3"]["name"]["value"] == "be tres" and "ve tres" in cajones["B3"]["name"]["synonyms"]
    assert any("K1" in w for w in warnings)


def test_uno_una_type_is_kept(template):
    model, _ = gm.build_model(template, PARTS, [])
    assert [v["name"]["value"] for v in values(model, "UnoUna")] == ["un", "una"]


def test_template_is_not_modified(template):
    before = copy.deepcopy(template)
    gm.build_model(template, PARTS, ["A1"])
    assert template == before


def test_warns_above_500_and_fails_above_20000(template):
    many = [{"pk": i, "name": f"Pieza {gm.number_word(i)}", "keywords": ""} for i in range(1, 502)]
    _, warnings = gm.build_model(template, many, [])
    assert any("500" in w for w in warnings)
    with pytest.raises(ValueError):
        gm.build_model(template, [{"pk": i, "name": f"p{gm.number_word(i % 999_999 + 1)}", "keywords": ""} for i in range(1, 20_002)], [])


def test_diff_summary(template):
    old, _ = gm.build_model(template, PARTS[:2], ["A1"])
    new_parts = [dict(PARTS[0], name="Tornillo M3x8 inox")] + PARTS[2:]
    new, _ = gm.build_model(template, new_parts, ["A1", "B3"])
    lines = gm.diff_summary(old, new)
    text = "\n".join(lines)
    assert "+ 2 parts" in text and "- 1 part" in text and "~ 1 renamed" in text
    assert gm.diff_summary(None, new) == ["no previous model"]


# ---- reading InvenTree

class FakeResponse:
    def __init__(self, body, status=200):
        self.status = status
        self.data = json.dumps(body).encode("utf-8")


class FakeOpener:
    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def __call__(self, url, headers):
        self.requests.append((url, headers))
        for fragment, body in self.pages:
            if fragment in url:
                return FakeResponse(body)
        return FakeResponse({}, 404)


def test_fetch_inventory_follows_pagination():
    opener = FakeOpener([
        ("offset=2", {"results": [{"pk": 3, "name": "C", "keywords": ""}], "next": None}),
        ("/api/part/", {"results": [{"pk": 1, "name": "A", "keywords": "x"}, {"pk": 2, "name": "B", "keywords": None}],
                        "next": "https://inv.example/api/part/?limit=2&offset=2"}),
        ("/api/stock/location/", {"results": [{"pk": 10, "name": "B3", "parent": 1}, {"pk": 11, "name": "Nevera", "parent": 4},
                                              {"pk": 1, "name": "Cajonera", "parent": None}]}),
    ])
    parts, drawers = gm.fetch_inventory("https://inv.example", "tok", opener=opener)
    assert [p["pk"] for p in parts] == [1, 2, 3] and parts[1]["keywords"] == ""
    assert drawers == ["B3"]
    ua = opener.requests[0][1]["User-Agent"]
    assert ua and "python" not in ua.lower() and "urllib" not in ua.lower()
    assert opener.requests[0][1]["Authorization"] == "Token tok"


def test_fetch_inventory_without_cajonera_root_has_no_drawers():
    opener = FakeOpener([("/api/part/", {"results": [], "next": None}), ("/api/stock/location/", {"results": []})])
    assert gm.fetch_inventory("https://inv.example", "tok", opener=opener) == ([], [])


# ---- the command line

def test_cli_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env.local"
    env.write_text("INVENTREE_URL=https://inv.example\nINVENTREE_TOKEN=SECRET-TOKEN\n", encoding="utf-8")
    out = tmp_path / "model.json"
    monkeypatch.setattr(gm, "fetch_inventory", lambda url, token, opener=None: (PARTS, ["A1", "B3"]))
    code = gm.main(["--env", str(env), "--template", str(TEMPLATE_PATH), "--out", str(out), "--dry-run"])
    assert code == 0 and not out.exists()
    printed = capsys.readouterr().out
    assert "no previous model" in printed and "SECRET-TOKEN" not in printed


def test_cli_writes_the_model_and_shows_the_diff(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env.local"
    env.write_text("INVENTREE_URL=https://inv.example\nINVENTREE_TOKEN=T\n", encoding="utf-8")
    out = tmp_path / "model.json"
    monkeypatch.setattr(gm, "fetch_inventory", lambda url, token, opener=None: (PARTS, ["A1"]))
    assert gm.main(["--env", str(env), "--template", str(TEMPLATE_PATH), "--out", str(out)]) == 0
    first = json.loads(out.read_text(encoding="utf-8"))
    assert len(values(first, "TipoProducto")) == 4
    monkeypatch.setattr(gm, "fetch_inventory", lambda url, token, opener=None: (PARTS[:2], ["A1"]))
    capsys.readouterr()
    gm.main(["--env", str(env), "--template", str(TEMPLATE_PATH), "--out", str(out)])
    assert "- 2 parts" in capsys.readouterr().out


def test_cli_missing_env_values_fail_cleanly(tmp_path, capsys):
    env = tmp_path / ".env.local"
    env.write_text("INVENTREE_URL=https://inv.example\n", encoding="utf-8")
    assert gm.main(["--env", str(env), "--template", str(TEMPLATE_PATH), "--out", str(tmp_path / "m.json")]) == 1
    assert "INVENTREE_TOKEN" in capsys.readouterr().err
