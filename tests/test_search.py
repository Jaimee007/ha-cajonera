from logic.models import Location, Part, StockItem
from logic.search import Hit, SearchResult, drawer_contents, search
from fakes import FakeInventory

B3 = Location(11, "B3", 17)
C1 = Location(12, "C1", 18)
B4 = Location(13, "B4", 19)
UNPLACED = Location(14, "Cajón 20 (sin posición)", 20)
LOCS = {loc.pk: loc for loc in (B3, C1, B4, UNPLACED)}

M3x8 = Part(5, "Tornillo M3x8", 20, 40)


def inv(parts, stock):
    return FakeInventory(parts, stock, list(LOCS.values()))


async def test_blank_text_does_not_call_inventree():
    fake = inv([M3x8], [])
    fake.fail = True
    r = await search(fake, "   ", LOCS)
    assert r == SearchResult("¿Qué quieres buscar?", [], [], [])


async def test_no_match():
    r = await search(inv([M3x8], []), "arandela", LOCS)
    assert r.phrase == "No encuentro nada con ese nombre."
    assert r.addresses == []


async def test_one_part_one_drawer():
    r = await search(inv([M3x8], [StockItem(9, 5, "Tornillo M3x8", 40.0, 11)]), "m3", LOCS)
    assert r.phrase == "Están en el B3. Quedan 40."
    assert r.addresses == [17]
    assert r.part_pks == [5]
    assert r.hits == [Hit(M3x8, StockItem(9, 5, "Tornillo M3x8", 40.0, 11), B3)]


async def test_one_part_several_drawers():
    part = Part(5, "Tornillo M3x8", 0, 55)
    stock = [StockItem(9, 5, "Tornillo M3x8", 40.0, 11), StockItem(10, 5, "Tornillo M3x8", 15.0, 12)]
    r = await search(inv([part], stock), "m3", LOCS)
    assert r.phrase == "Hay en el B3 y en el C1, 55 en total."
    assert r.addresses == [17, 18]


async def test_several_parts_names_first_three():
    parts = [Part(5, "Tornillo M3x8", 0, 1), Part(6, "Tornillo M3x12", 0, 1),
             Part(7, "Tornillo M3x20", 0, 1), Part(8, "Tornillo M3x30", 0, 1)]
    stock = [StockItem(9, 5, "", 1, 11), StockItem(10, 6, "", 1, 13), StockItem(11, 7, "", 1, 12),
             StockItem(12, 8, "", 1, 12)]
    r = await search(inv(parts, stock), "tornillo m3", LOCS)
    assert r.phrase == "Tornillo M3x8 en el B3, Tornillo M3x12 en el B4 y Tornillo M3x20 en el C1."
    assert r.addresses == [17, 19, 18]


async def test_low_part_adds_warning():
    part = Part(5, "Tornillo M3x8", 20, 5)
    r = await search(inv([part], [StockItem(9, 5, "Tornillo M3x8", 5.0, 11)]), "m3", LOCS)
    assert r.phrase == "Están en el B3. Quedan 5. Ojo, quedan pocos: 5, y el mínimo es 20."


async def test_empty_stock_still_lights_the_drawer():
    part = Part(5, "Tornillo M3x8", 0, 0)
    r = await search(inv([part], [StockItem(9, 5, "Tornillo M3x8", 0.0, 11)]), "m3", LOCS)
    assert r.phrase == "Tenías Tornillo M3x8 en el B3, pero no queda ninguno."
    assert r.addresses == [17]


async def test_stock_outside_cajonera_is_ignored():
    stock = [StockItem(9, 5, "Tornillo M3x8", 30.0, None), StockItem(10, 5, "Tornillo M3x8", 10.0, 99)]
    r = await search(inv([M3x8], stock), "m3", LOCS)
    assert r.phrase == "Hay 40 de Tornillo M3x8, pero no está en ningún cajón."
    assert r.addresses == []


async def test_unplaced_drawer_is_named_but_not_lit():
    r = await search(inv([M3x8], [StockItem(9, 5, "Tornillo M3x8", 40.0, 14)]), "m3", LOCS)
    assert r.phrase == "Está en el cajón 20, que ahora no está conectado."
    assert r.addresses == []


async def test_caps_lit_drawers_at_10():
    locs = {100 + i: Location(100 + i, f"A{i + 1}", i) for i in range(14)}
    part = Part(5, "Resistencia", 0, 14)
    stock = [StockItem(200 + i, 5, "Resistencia", 1.0, 100 + i) for i in range(14)]
    r = await search(FakeInventory([part], stock, list(locs.values())), "resistencia", locs)
    assert r.addresses == list(range(10))
    assert r.phrase.endswith(" Hay 14 cajones; enciendo los 10 primeros.")


async def test_inventree_down():
    fake = inv([M3x8], [])
    fake.fail = True
    r = await search(fake, "m3", LOCS)
    assert r.phrase == "No puedo consultar el inventario ahora mismo."
    assert r.addresses == []


async def test_decimal_quantities_use_comma():
    part = Part(5, "Cable", 0, 2.5)
    r = await search(inv([part], [StockItem(9, 5, "Cable", 2.5, 11)]), "cable", LOCS)
    assert r.phrase == "Están en el B3. Quedan 2,5."


async def test_drawer_contents_lists_hits():
    stock = [StockItem(9, 5, "Tornillo M3x8", 40.0, 11), StockItem(10, 5, "Tornillo M3x8", 3.0, 12)]
    hits = await drawer_contents(inv([M3x8], stock), B3)
    assert hits == [Hit(M3x8, StockItem(9, 5, "Tornillo M3x8", 40.0, 11), B3)]


async def test_drawer_contents_one_row_per_part_summing_items():
    # A kept-at-zero item next to a hand-made one: one part, one row, the sum.
    stock = [StockItem(2, 5, "Tornillo M3x8", 0.0, 11), StockItem(6, 5, "Tornillo M3x8", 12.0, 11)]
    hits = await drawer_contents(inv([M3x8], stock), B3)
    assert hits == [Hit(M3x8, StockItem(6, 5, "Tornillo M3x8", 12.0, 11), B3)]


async def test_drawer_contents_stock_is_largest_item_ties_lowest_pk():
    stock = [StockItem(9, 5, "T", 3.0, 11), StockItem(4, 5, "T", 3.0, 11), StockItem(7, 5, "T", 1.0, 11)]
    hits = await drawer_contents(inv([M3x8], stock), B3)
    assert [(h.stock.pk, h.stock.quantity) for h in hits] == [(4, 7.0)]


async def test_drawer_contents_two_parts_two_rows_sorted_by_name():
    parts = [Part(5, "tornillo", 0, 1), Part(6, "Arandela", 0, 1)]
    stock = [StockItem(9, 5, "", 1.0, 11), StockItem(10, 6, "", 1.0, 11)]
    hits = await drawer_contents(inv(parts, stock), B3)
    assert [h.part.name for h in hits] == ["Arandela", "tornillo"]


async def test_drawer_contents_kept_at_zero_part_keeps_its_row():
    hits = await drawer_contents(inv([M3x8], [StockItem(2, 5, "T", 0.0, 11)]), B3)
    assert [(h.stock.pk, h.stock.quantity) for h in hits] == [(2, 0.0)]


async def test_search_groups_items_of_one_part_per_drawer():
    part = Part(5, "Tornillo M3x8", 0, 17)
    stock = [StockItem(2, 5, "", 0.0, 11), StockItem(6, 5, "", 12.0, 11), StockItem(7, 5, "", 5.0, 12)]
    r = await search(inv([part], stock), "m3", LOCS)
    assert [(h.location.name, h.stock.pk, h.stock.quantity) for h in r.hits] == [("B3", 6, 12.0), ("C1", 7, 5.0)]
    assert r.phrase == "Hay en el B3 y en el C1, 17 en total."
