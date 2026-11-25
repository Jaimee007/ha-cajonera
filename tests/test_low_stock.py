from logic.low_stock import LowPart, LowStockReport, low_stock_report
from logic.models import Location, Part, StockItem
from fakes import FakeInventory

B3 = Location(11, "B3", 17)
C1 = Location(12, "C1", 18)
LOCS = {B3.pk: B3, C1.pk: C1}


async def test_report_lists_low_parts_with_their_drawers():
    low = Part(5, "Tornillo M3x8", 20, 15)
    fake = FakeInventory([low], [StockItem(9, 5, "", 10, 11), StockItem(10, 5, "", 5, 12)], list(LOCS.values()))
    report = await low_stock_report(fake, LOCS)
    assert report == LowStockReport([LowPart(low, ["B3", "C1"])], {17, 18})


async def test_part_outside_cajonera_is_listed_without_drawers():
    low = Part(5, "Tornillo M3x8", 20, 15)
    fake = FakeInventory([low], [StockItem(9, 5, "", 15, None)], list(LOCS.values()))
    report = await low_stock_report(fake, LOCS)
    assert report == LowStockReport([LowPart(low, [])], set())


async def test_ignores_parts_that_are_not_low():
    class LyingApi(FakeInventory):
        async def low_stock_parts(self):
            return list(self.parts.values())  # the filter returned everything

    fake = LyingApi([Part(5, "Ok", 20, 40), Part(6, "Sin mínimo", 0, 0)], [], [])
    assert await low_stock_report(fake, LOCS) == LowStockReport([], set())
