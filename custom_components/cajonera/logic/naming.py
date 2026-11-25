"""Drawer names in the A1/B3 grid (spec section 3.1).

The base is the bottom-left drawer at (0, 0) = "A1". Rows are letters counted
upwards, columns are numbers counted rightwards.
"""

from __future__ import annotations

ROOT_LOCATION = "Cajonera"


def _row_letters(y: int) -> str:
    # Spreadsheet-style bijective base 26: 0 -> A, 25 -> Z, 26 -> AA.
    letters = ""
    n = y + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def drawer_name(x: int, y: int) -> str | None:
    """Name for grid position (x, y), or None if it lies outside the grid."""
    if x < 0 or y < 0:
        return None
    return f"{_row_letters(y)}{x + 1}"


def unplaced_name(address: int) -> str:
    """Location name for a drawer that is disconnected or has no position."""
    return f"Cajón {address} (sin posición)"


def temp_name(address: int) -> str:
    """Intermediate name used while renaming, so no two locations collide."""
    return f"_dir{address}"
