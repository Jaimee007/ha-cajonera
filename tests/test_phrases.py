import pytest

from logic.phrases import fmt_qty, join_es


@pytest.mark.parametrize("q,s", [(40.0, "40"), (40, "40"), (2.5, "2,5"), (0.0, "0"), (0.25, "0,25")])
def test_fmt_qty(q, s):
    assert fmt_qty(q) == s


def test_join_es():
    assert join_es(["el B3"]) == "el B3"
    assert join_es(["el B3", "el C1"]) == "el B3 y el C1"
    assert join_es(["a", "b", "c"]) == "a, b y c"


@pytest.mark.parametrize("q,s", [(1_000_000.5, "1000000,5"), (0.1 + 0.2, "0,3"), (2_000_000.0, "2000000"), (0.0001, "0")])
def test_fmt_qty_never_uses_exponents_and_rounds(q, s):
    """M4: spoken quantities must not read '1e+06' or float noise."""
    assert fmt_qty(q) == s
