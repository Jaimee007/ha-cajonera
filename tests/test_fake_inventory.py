from fakes import FakeInventory
from logic.models import Location, Part, StockItem


def _inv(**item_kwargs):
    return FakeInventory(
        parts=[Part(5, "M3", 0.0, 10.0, category_pk=8)],
        stock=[StockItem(9, 5, "M3", 10.0, 11, **item_kwargs), StockItem(10, 5, "M3", 0.0, 12)],
        locations=[Location(11, "A1", 1), Location(12, "A2", 2)],
    )


async def test_remove_to_zero_deletes_item_when_delete_on_deplete():
    inv = _inv()
    await inv.remove_stock(9, 10, "n")
    assert 9 not in inv.stock
    assert inv.parts[5].total_stock == 0


async def test_remove_to_zero_keeps_item_when_flag_is_off():
    inv = _inv(delete_on_deplete=False)
    await inv.remove_stock(9, 10, "n")
    assert inv.stock[9].quantity == 0


async def test_count_to_zero_deletes_and_merge_sums_into_first():
    inv = _inv()
    await inv.count_stock(9, 0, "n")
    assert 9 not in inv.stock and inv.counted == [(9, 0)]
    inv = _inv()
    await inv.add_stock(10, 5, "n")
    await inv.merge_stock([9, 10], 11, "n")
    assert inv.stock[9].quantity == 15 and 10 not in inv.stock
    assert inv.merged == [([9, 10], 11)]


async def test_create_part_and_category_are_recorded():
    inv = _inv()
    cat = await inv.create_category("Tuercas")
    part = await inv.create_part("M5", cat.pk, 11, 3, ipn="X")
    assert inv.created_categories == [cat] and inv.created_parts == [part]
    assert [s.part_pk for s in await inv.stock_in_category(cat.pk, 1)] == [part.pk]
