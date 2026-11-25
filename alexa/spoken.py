"""Spanish spoken forms of part names, keywords and drawer names, for the interaction-model generator.

Alexa matches a slot value against what the user SAID, and digits are said as words ("m3" -> "m tres"), so every
value and synonym must be letters and spaces only. Pure functions, no dependencies. Not used by the Lambda.
"""

from __future__ import annotations

import re

_UNITS = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce",
          "trece", "catorce", "quince", "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno",
          "veintidós", "veintitrés", "veinticuatro", "veinticinco", "veintiséis", "veintisiete", "veintiocho",
          "veintinueve"]
_TENS = {30: "treinta", 40: "cuarenta", 50: "cincuenta", 60: "sesenta", 70: "setenta", 80: "ochenta", 90: "noventa"}
_HUNDREDS = {200: "doscientos", 300: "trescientos", 400: "cuatrocientos", 500: "quinientos", 600: "seiscientos",
             700: "setecientos", 800: "ochocientos", 900: "novecientos"}


def _below_hundred(n: int, apocope: bool = False) -> str:
    if n < 30:
        word = _UNITS[n]
        if apocope and n == 1:
            return "un"
        if apocope and n == 21:
            return "veintiún"
        return word
    tens, unit = divmod(n, 10)
    word = _TENS[tens * 10]
    if unit:
        word += " y " + ("un" if apocope and unit == 1 else _UNITS[unit])
    return word


def _below_thousand(n: int, apocope: bool = False) -> str:
    if n < 100:
        return _below_hundred(n, apocope)
    if n == 100:
        return "cien"
    hundreds, rest = divmod(n, 100)
    head = "ciento" if hundreds == 1 else _HUNDREDS[hundreds * 100]
    return head + (" " + _below_hundred(rest, apocope) if rest else "")


def number_to_spanish(n: int) -> str:
    """0..999 999 as Spanish words: 21 -> "veintiuno", 21000 -> "veintiún mil", 100 -> "cien"."""
    if not isinstance(n, int) or n < 0 or n > 999_999:
        raise ValueError(f"cannot spell {n!r}")
    if n < 1000:
        return _below_thousand(n)
    thousands, rest = divmod(n, 1000)
    head = "mil" if thousands == 1 else _below_thousand(thousands, apocope=True) + " mil"
    return head + (" " + _below_thousand(rest) if rest else "")


_LATIN = frozenset("abcdefghijklmnopqrstuvwxyzáéíóúüñ")


def clean(text: str) -> str:
    """Lowercase; every character that is not a Latin letter, a digit or a space becomes a space; spaces collapsed."""
    # Only Latin letters (with Spanish accents), digits and spaces survive: Greek "Ω" or "µ" cannot be spoken as is.
    text = "".join(c if (c == " " or c.isdigit() or c in _LATIN) else " " for c in (text or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _spell_digits(cleaned: str) -> str | None:
    """Replace each digit run by its Spanish words; None if a run is too long to spell. Letters stuck to digits
    are separated ("m3x8" -> "m tres x ocho")."""
    out: list[str] = []
    for token in re.findall(r"\d+|[^\W\d_]+", cleaned):
        if token.isdigit():
            if len(token) > 6:
                return None
            out.append(number_to_spanish(int(token)))
        else:
            out.append(token)
    return " ".join(out)


def spoken_forms(text: str) -> list[str]:
    """How a person says `text`: the cleaned form (if it has no digit) and the spelled-out form. Letters and spaces only."""
    cleaned = clean(text)
    if not cleaned:
        return []
    forms: list[str] = []
    if not any(c.isdigit() for c in cleaned):
        forms.append(cleaned)
    else:
        spelled = _spell_digits(cleaned)
        if spelled:
            forms.append(spelled)
    out: list[str] = []
    for form in forms:
        if form and all(c.isalpha() or c == " " for c in form) and form not in out:
            out.append(form)
    return out


def plural_variants(form: str) -> list[str]:
    """Plural or singular of a SINGLE word: add "s" after a vowel, drop it after "as"/"os". Nothing else is guessed."""
    if not form or " " in form:
        return []
    if form.endswith(("as", "os")):
        return [form[:-1]]
    if form[-1] in "aeiouáéíóú":
        return [form + "s"]
    return []


_LETTER_NAMES = {"a": ["a"], "b": ["be", "b"], "c": ["ce", "c"], "d": ["de", "d"], "e": ["e"], "f": ["efe", "f"],
                 "g": ["ge", "g"], "h": ["hache", "h"], "i": ["i"], "j": ["jota", "j"]}
_LETTER_EXTRA = {"b": ["ve"]}  # "b" and "v" sound the same in Spanish: Alexa often hears "ve"


def drawer_synonyms(name: str) -> list[str]:
    """How a drawer such as "B3" is said: "be tres", "b tres", "ve tres". Rows after "j" and odd names give []."""
    m = re.fullmatch(r"([A-Za-z])(\d{1,3})", (name or "").strip())
    if not m:
        return []
    letter, number = m.group(1).lower(), int(m.group(2))
    if letter not in _LETTER_NAMES:
        return []
    spoken_number = number_to_spanish(number)
    return [f"{word} {spoken_number}" for word in _LETTER_NAMES[letter] + _LETTER_EXTRA.get(letter, [])]
