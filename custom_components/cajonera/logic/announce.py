"""The sentence spoken back when someone changes the stock by hand (the card), so an Echo can repeat it.

Pure function: it only needs what the operation already knows. The caller decides where the sentence goes.
"""

from __future__ import annotations

from .phrases import fmt_qty


def _tail(phrase: str) -> str:
    """The part of an operation's answer after "Hecho. " (what it adds to the lead sentence)."""
    return phrase[len("Hecho. "):] if phrase.startswith("Hecho. ") else phrase


def spoken_action(accion: str, part_name: str, drawer: str, quantity: float | None, phrase: str) -> str | None:
    """"He retirado 4 de Tornillo M3x8 del B3. Quedan 113." for a finished operation, or None for an unknown one."""
    tail = _tail(phrase)
    if accion == "retirar" and quantity is not None:
        return f"He retirado {fmt_qty(quantity)} de {part_name} del {drawer}. {tail}"
    if accion == "anadir" and quantity is not None:
        return f"He añadido {fmt_qty(quantity)} de {part_name} al {drawer}. {tail}"
    if accion == "actualizar" and quantity is not None:
        return f"He dejado {part_name} en {fmt_qty(quantity)} en el {drawer}. {tail}"
    if accion == "mover":
        # "He movido 3 del B1 al B3." -> the part goes after the quantity.
        return tail.replace(" del ", f" de {part_name} del ", 1) if " del " in tail else f"{tail} ({part_name})"
    return None
