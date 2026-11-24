"""Spanish formatting helpers for the spoken and on-screen answers."""

from __future__ import annotations


def fmt_qty(q: float) -> str:
    """40.0 -> "40", 2.5 -> "2,5" (Spanish decimal comma)."""
    q = float(q)
    if q.is_integer():
        return str(int(q))
    return f"{q:g}".replace(".", ",")


def join_es(items: list[str]) -> str:
    """["a", "b", "c"] -> "a, b y c"."""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " y " + items[-1]
