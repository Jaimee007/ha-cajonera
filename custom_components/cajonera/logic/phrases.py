"""Spanish formatting helpers for the spoken and on-screen answers."""

from __future__ import annotations


def fmt_qty(q: float) -> str:
    """40.0 -> "40", 2.5 -> "2,5" (Spanish decimal comma)."""
    # Fixed precision (3 decimals): no exponents ("1e+06") and no float noise.
    text = f"{round(float(q), 3):.3f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    return text.replace(".", ",")


def join_es(items: list[str]) -> str:
    """["a", "b", "c"] -> "a, b y c"."""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " y " + items[-1]
