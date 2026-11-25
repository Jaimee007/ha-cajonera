import pytest

from spoken import clean, drawer_synonyms, number_to_spanish, plural_variants, spoken_forms


@pytest.mark.parametrize("n,words", [
    (0, "cero"), (1, "uno"), (2, "dos"), (8, "ocho"), (10, "diez"), (15, "quince"), (16, "dieciséis"),
    (20, "veinte"), (21, "veintiuno"), (22, "veintidós"), (30, "treinta"), (31, "treinta y uno"),
    (99, "noventa y nueve"), (100, "cien"), (101, "ciento uno"), (200, "doscientos"), (500, "quinientos"),
    (999, "novecientos noventa y nueve"), (1000, "mil"), (1001, "mil uno"), (2000, "dos mil"),
    (21000, "veintiún mil"), (21001, "veintiún mil uno"), (100000, "cien mil"), (101000, "ciento un mil"),
    (999999, "novecientos noventa y nueve mil novecientos noventa y nueve"),
])
def test_number_to_spanish_known_values(n, words):
    assert number_to_spanish(n) == words


@pytest.mark.parametrize("n", [-1, 1_000_000, 10**9])
def test_number_out_of_range(n):
    with pytest.raises(ValueError):
        number_to_spanish(n)


def test_clean_removes_symbols_and_keeps_accents():
    assert clean("Tornillo M3x8 (inox) / 100Ω!") == "tornillo m3x8 inox 100"
    assert clean("  Estaño   Ñandú  ") == "estaño ñandú"
    assert clean("USB-C") == "usb c"


def test_spoken_forms_plain_words():
    assert spoken_forms("Huevos") == ["huevos"]
    assert spoken_forms("hilo de estaño") == ["hilo de estaño"]


def test_spoken_forms_spells_digits():
    forms = spoken_forms("m3 por 8")
    assert forms == ["m tres por ocho"]
    assert "tornillo m tres x ocho" in spoken_forms("Tornillo M3x8")
    assert spoken_forms("4k7") == ["cuatro k siete"]
    assert spoken_forms("Resistencia 100 ohmios") == ["resistencia cien ohmios"]


def test_spoken_forms_have_only_letters_and_spaces():
    for text in ("Condensador 470uF", "LED RGB", "Pila CR2032", "10k", "M3x12", "USB-C"):
        for form in spoken_forms(text):
            assert all(c.isalpha() or c == " " for c in form), (text, form)


def test_spoken_forms_drops_unspeakable():
    assert spoken_forms("1234567") == []  # a digit run above 999 999 cannot be spelled
    assert spoken_forms("!!!") == []
    assert spoken_forms("") == []


def test_spoken_forms_are_deduplicated_and_ordered():
    assert spoken_forms("M3") == ["m tres"]
    assert spoken_forms("tuerca") == ["tuerca"]


def test_plural_variants_rules():
    assert plural_variants("tornillo") == ["tornillos"]
    assert plural_variants("tornillos") == ["tornillo"]
    assert plural_variants("resistencias") == ["resistencia"]
    assert plural_variants("condensador") == []  # ends in a consonant: nothing is guessed
    assert plural_variants("led") == []
    assert plural_variants("cable plano") == []  # more than one word


def test_drawer_synonyms_b3():
    assert drawer_synonyms("B3") == ["be tres", "b tres", "ve tres"]


def test_drawer_synonyms_other_letters_and_numbers():
    assert drawer_synonyms("A1") == ["a uno"]
    assert drawer_synonyms("C12") == ["ce doce", "c doce"]


def test_drawer_synonyms_beyond_j_are_empty():
    assert drawer_synonyms("K1") == []
    assert drawer_synonyms("") == [] and drawer_synonyms("Cajón 7") == []
