"""Matching what a person said against part names.

Alexa transcribes "m3x8" as "M3X8", "m tres por ocho" or "eme tres equis ocho", and a part is called "Tornillo M3x8".
Both sides are brought to the same plain form (lowercase, no accents, no spaces, numbers as digits, the spoken
"por"/"equis" as an x) and compared as text. Pure functions, no Home Assistant imports.
"""

from __future__ import annotations

import re
import unicodedata

from .models import Part

_UNITS = {"cero": 0, "uno": 1, "un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
          "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
          "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiuno": 21,
          "veintiun": 21, "veintiuna": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
          "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29}
_TENS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90}
_HUNDREDS = {"cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300, "cuatrocientos": 400,
             "quinientos": 500, "seiscientos": 600, "setecientos": 700, "ochocientos": 800, "novecientos": 900}
_LETTERS = {"eme": "m", "ene": "n", "efe": "f", "ele": "l", "ese": "s", "equis": "x", "ce": "c", "be": "b", "ve": "v",
            "de": None}  # "de" is a word ("cable de tres metros"), not the letter d: it is left alone
_SPOKEN_X = {"por", "equis"}  # "m tres por ocho" and "m tres equis ocho" both mean "m3x8"


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _is_number_word(word: str) -> bool:
    return word in _UNITS or word in _TENS or word in _HUNDREDS or word in ("mil", "y")


def _spell(words: list[str]) -> int | None:
    """The value of a run of number words ("dos mil treinta y dos" -> 2032), or None if it is not one number."""
    if not words or words == ["y"] or words[0] == "y" or words[-1] == "y":
        return None
    total, current = 0, 0
    for word in words:
        if word == "y":
            if not (current % 100 >= 30 and current % 10 == 0):
                return None  # "y" only joins tens and units: "treinta y dos"
            continue
        if word == "mil":
            total += (current or 1) * 1000
            current = 0
        elif word in _HUNDREDS:
            if current % 1000 >= 100:
                return None
            current += _HUNDREDS[word]
        elif word in _TENS:
            if current % 100:
                return None
            current += _TENS[word]
        elif word in _UNITS:
            value = _UNITS[word]
            if current % 100 and not (current % 100 >= 30 and current % 10 == 0 and value < 10):
                return None  # two unit words in a row are two numbers ("tres ocho" is 3, 8)
            current += value
        else:
            return None
    return total + current


def words_to_digits(words: list[str]) -> list[str]:
    """Replace every run of number words by digits and keep every other word. "m tres ocho" stays m, 3, 8."""
    out: list[str] = []
    i = 0
    while i < len(words):
        if not _is_number_word(words[i]) or words[i] == "y":
            out.append(words[i])
            i += 1
            continue
        # The longest run that is still ONE number ("treinta y dos", "dos mil treinta y dos").
        end, best = i, None
        for j in range(i + 1, len(words) + 1):
            if not _is_number_word(words[j - 1]):
                break
            value = _spell(words[i:j])
            if value is not None:
                end, best = j, value
        if best is None:
            out.append(words[i])
            i += 1
        else:
            out.append(str(best))
            i = end
    return out


def normalize(text: str) -> str:
    """A plain comparable form: "tornillos M3X8", "tornillo m tres por ocho" -> "tornillom3x8"."""
    text = _strip_accents((text or "").lower())
    text = re.sub(r"[^a-z0-9ñ ]+", " ", text)
    words = text.split()
    words = [("x" if w in _SPOKEN_X else (_LETTERS[w] if _LETTERS.get(w) else w)) for w in words]
    words = words_to_digits(words)
    plain = "".join(words)
    # Plural of the first word ("tornillos m3x8" and "tornillo m3x8" are the same name).
    return re.sub(r"^([a-z]{3,})s(?=[a-z]?\d|$)", r"\1", plain) if words and words[0].endswith("s") and len(words) > 1 else plain


def parts_named(parts: list[Part], text: str | None) -> list[Part]:
    """The parts whose name contains what was said (an exact name first). Empty when nothing matches: for a text
    search, no match means "no such part"."""
    wanted = normalize(text or "")
    if not wanted:
        return []
    exact = [p for p in parts if normalize(p.name) == wanted]
    return exact or [p for p in parts if wanted in normalize(p.name)]


def narrow_by_text(parts: list[Part], text: str | None) -> list[Part]:
    """From the candidates Alexa resolved for a shared word, the ones the spoken text really names.

    An exact match of the whole name wins; otherwise the parts whose name contains what was said; if nothing is
    left, or the text says nothing more than the shared word, every candidate stays (the caller then asks).
    """
    wanted = normalize(text or "")
    if not wanted or not parts:
        return list(parts)
    exact = [p for p in parts if normalize(p.name) == wanted]
    if exact:
        return exact
    containing = [p for p in parts if wanted in normalize(p.name)]
    return containing if containing else list(parts)
