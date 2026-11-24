from datetime import datetime

from logic.context import context_after_search
from logic.models import Location, Part, StockItem
from logic.search import Hit, SearchResult
from logic.withdraw import Context

NOW = datetime(2026, 11, 24, 12, 0, 0)
B3 = Location(11, "B3", 17)
C1 = Location(12, "C1", 18)
M3 = Part(5, "M3", 0, 40)


def test_single_part_single_drawer_sets_context():
    r = SearchResult("x", [Hit(M3, StockItem(9, 5, "M3", 40, 11), B3)], [17], [5])
    assert context_after_search(r, NOW) == Context(5, 11, NOW)


def test_one_part_in_two_drawers_keeps_the_part_without_a_drawer_guess():
    hits = [Hit(M3, StockItem(9, 5, "M3", 20, 11), B3), Hit(M3, StockItem(10, 5, "M3", 20, 12), C1)]
    assert context_after_search(SearchResult("x", hits, [17, 18], [5]), NOW) == Context(5, None, NOW)


def test_any_other_search_clears_context():
    """C2: a later search that is not one clear part must not leave the old part alive."""
    assert context_after_search(SearchResult("No encuentro nada con ese nombre."), NOW) is None
    assert context_after_search(SearchResult("x", [], [], [5, 6]), NOW) is None
