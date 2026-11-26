"""Matching a spoken name against part names: "tornillos M3X8", "m tres por ocho", "eme tres equis ocho"."""

import pytest

from logic.models import Part
from logic.names import narrow_by_text, normalize, words_to_digits


def p(pk, name):
    return Part(pk, name, 0.0, 1.0)


M3X8 = p(13, "Tornillo M3x8")
M3X12 = p(14, "Tornillo M3x12")
M4X10 = p(15, "Tornillo M4x10")
M3 = p(2, "tornillo m3")
SCREWS = [M3X8, M3X12, M4X10, M3]


@pytest.mark.parametrize("spoken,expected", [
    ("tornillos M3X8", "tornillom3x8"),
    ("Tornillo M3x8", "tornillom3x8"),
    ("tornillos m 3 x 8", "tornillom3x8"),
    ("tornillos m tres por ocho", "tornillom3x8"),
    ("tornillo eme tres equis ocho", "tornillom3x8"),
    ("tornillo m tres ocho", "tornillom38"),
    ("Resistencia 4k7", "resistencia4k7"),
    ("resistencia cuatro k siete", "resistencia4k7"),
    ("condensador cien nf", "condensador100nf"),
    ("Estaño", "estano"),
])
def test_normalize(spoken, expected):
    assert normalize(spoken) == expected


@pytest.mark.parametrize("words,digits", [
    ("tres", "3"), ("ocho", "8"), ("doce", "12"), ("veintiuno", "21"), ("treinta y dos", "32"),
    ("cien", "100"), ("ciento uno", "101"), ("doscientos veinte", "220"), ("mil", "1000"),
    ("dos mil treinta y dos", "2032"), ("cuatrocientos setenta", "470"),
])
def test_words_to_digits_numbers(words, digits):
    assert words_to_digits(words.split()) == [digits]


def test_two_consecutive_units_are_two_numbers():
    """"m tres ocho" is m, 3, 8 — not m11."""
    assert words_to_digits("m tres ocho".split()) == ["m", "3", "8"]


def test_words_around_numbers_are_kept():
    assert words_to_digits("cable usb c de tres metros".split()) == ["cable", "usb", "c", "de", "3", "metros"]


# ---- narrowing the candidates Alexa returned for a shared word

def names(parts):
    return [x.name for x in parts]


def test_exact_spoken_name_wins_among_alexas_candidates():
    """The real case: Alexa heard "tornillos M3X8" and returned four screws."""
    assert names(narrow_by_text(SCREWS, "tornillos M3X8")) == ["Tornillo M3x8"]


def test_part_number_alone_narrows():
    assert names(narrow_by_text(SCREWS, "m3x8")) == ["Tornillo M3x8"]


def test_spelled_out_name_narrows():
    assert names(narrow_by_text(SCREWS, "tornillo m tres por doce")) == ["Tornillo M3x12"]
    assert names(narrow_by_text(SCREWS, "tornillos eme cuatro equis diez")) == ["Tornillo M4x10"]


def test_the_shorter_name_is_not_swallowed_by_the_longer_one():
    """"tornillo m3" is a part of its own: saying exactly that must not pick M3x8 too."""
    assert names(narrow_by_text(SCREWS, "tornillo m3")) == ["tornillo m3"]


def test_a_generic_word_keeps_every_candidate():
    assert names(narrow_by_text(SCREWS, "tornillos")) == names(SCREWS)


def test_nothing_matching_keeps_every_candidate():
    """Never drop to an empty list: Alexa's candidates are better than nothing, and HA then asks."""
    assert names(narrow_by_text(SCREWS, "arandelas")) == names(SCREWS)


def test_empty_text_keeps_every_candidate():
    assert names(narrow_by_text(SCREWS, "")) == names(SCREWS) and names(narrow_by_text(SCREWS, None)) == names(SCREWS)


def test_ambiguous_fragment_keeps_every_part_that_contains_it():
    parts = [p(1, "Tornillo M3x8"), p(2, "Tornillo M3x80"), p(3, "Tuerca M3")]
    assert names(narrow_by_text(parts, "m3x8")) == ["Tornillo M3x8", "Tornillo M3x80"]


def test_parts_named_is_empty_when_nothing_matches():
    from logic.names import parts_named

    assert parts_named(SCREWS, "arandelas") == [] and parts_named(SCREWS, "") == [] and parts_named(SCREWS, None) == []
    assert [x.name for x in parts_named(SCREWS, "tornillo m tres por doce")] == ["Tornillo M3x12"]
