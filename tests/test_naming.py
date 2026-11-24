import pytest

from logic.models import Part
from logic.naming import ROOT_LOCATION, drawer_name, temp_name, unplaced_name


@pytest.mark.parametrize(
    "x,y,name",
    [(0, 0, "A1"), (3, 0, "A4"), (1, 2, "C2"), (0, 25, "Z1"), (0, 26, "AA1"), (0, 27, "AB1")],
)
def test_drawer_name(x, y, name):
    assert drawer_name(x, y) == name


@pytest.mark.parametrize("x,y", [(-1, 0), (0, -1)])
def test_outside_grid_has_no_name(x, y):
    assert drawer_name(x, y) is None


def test_unplaced_name():
    assert unplaced_name(17) == "Cajón 17 (sin posición)"


def test_temp_name():
    assert temp_name(17) == "_dir17"


def test_root_location():
    assert ROOT_LOCATION == "Cajonera"


def test_part_is_low():
    assert Part(1, "M3", minimum_stock=20, total_stock=17).is_low
    assert not Part(1, "M3", minimum_stock=20, total_stock=20).is_low
    assert not Part(1, "M3", minimum_stock=0, total_stock=0).is_low
