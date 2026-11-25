import pytest

from logic.models import Location, Part, StockItem
from logic.stock_ops import MAYBE_STOCK, OpResult, add_to_drawer, count_drawer, move_between
from logic.search import DOWN_PHRASE
from fakes import FakeInventory

B1 = Location(11, "B1", 17)
B3 = Location(12, "B3", 18)
LOCS = {B1.pk: B1, B3.pk: B3}
INVALID = "Dime una cantidad válida."
UNKNOWN = "Ese cajón no está en el inventario."


def fake_with(items, minimum=0.0):
    total = sum(i.quantity for i in items)
    return FakeInventory([Part(5, "Tornillo M3x8", minimum, total)], items, list(LOCS.values()))


def item(pk, qty, loc=11, keep=False):
    return StockItem(pk, 5, "Tornillo M3x8", float(qty), loc, delete_on_deplete=not keep)


def one_in_b1(qty=40, **kw):
    return fake_with([item(9, qty)], **kw)


async def test_add_to_existing_drawer_stock():
    fake = one_in_b1()
    r = await add_to_drawer(fake, 5, 17, 10, LOCS)
    assert r == OpResult("Hecho. Ahora hay 50 en el B1.", done=True, address=17, low=False, part_pk=5, location_pk=11)
    assert fake.added == [(9, 10)]
    assert fake.last_notes == "cajonera: añadir"


async def test_add_goes_to_the_largest_item_and_keeps_it():
    fake = fake_with([item(9, 5), item(10, 40)])
    r = await add_to_drawer(fake, 5, 17, 10, LOCS)
    assert r.phrase == "Hecho. Ahora hay 55 en el B1."
    assert fake.added == [(10, 10)]
    assert fake.merged == []
    assert fake.stock[10].delete_on_deplete is False


async def test_add_creates_stock_in_empty_drawer():
    fake = one_in_b1()
    r = await add_to_drawer(fake, 5, 18, 7, LOCS)
    assert r.phrase == "Hecho. Ahora hay 7 en el B3." and r.done
    created = [s for s in fake.stock.values() if s.location_pk == 12]
    assert len(created) == 1 and created[0].quantity == 7 and created[0].delete_on_deplete is False
    assert fake.added == []


async def test_count_merges_items_first():
    fake = fake_with([item(9, 40), item(10, 5)])
    r = await count_drawer(fake, 5, 17, 47, LOCS)
    assert r.phrase == "Hecho. El B1 tiene ahora 47." and r.done
    assert fake.merged == [([9, 10], 11)]
    assert fake.counted == [(9, 47)]
    assert [s.quantity for s in fake.stock.values() if s.location_pk == 11] == [47]
    assert fake.last_notes == "cajonera: recuento"


async def test_count_to_zero_keeps_item():
    fake = fake_with([item(9, 40)])
    r = await count_drawer(fake, 5, 17, 0, LOCS)
    assert r.phrase == "Hecho. El B1 tiene ahora 0." and r.done
    assert 9 in fake.stock and fake.stock[9].quantity == 0
    assert fake.stock[9].delete_on_deplete is False


async def test_count_in_empty_drawer_creates_stock():
    fake = one_in_b1()
    r = await count_drawer(fake, 5, 18, 6, LOCS)
    assert r.phrase == "Hecho. El B3 tiene ahora 6." and r.done
    assert [s.quantity for s in fake.stock.values() if s.location_pk == 12] == [6]


async def test_move_all_and_partial():
    fake = one_in_b1()
    r = await move_between(fake, 5, 17, 18, None, LOCS)
    assert r == OpResult("Hecho. He movido 40 del B1 al B3.", done=True, address=18, low=False, part_pk=5, location_pk=12)
    assert fake.transferred == [(9, 40, 12)]
    assert fake.stock[9].location_pk == 12
    assert fake.last_notes == "cajonera: mover"

    fake = one_in_b1()
    r = await move_between(fake, 5, 17, 18, 15, LOCS)
    assert r.phrase == "Hecho. He movido 15 del B1 al B3." and r.done
    assert fake.transferred == [(9, 15, 12)]
    assert fake.stock[9].quantity == 25 and fake.stock[9].delete_on_deplete is False


async def test_move_merges_source_items_first():
    fake = fake_with([item(9, 40), item(10, 5)])
    r = await move_between(fake, 5, 17, 18, None, LOCS)
    assert r.phrase == "Hecho. He movido 45 del B1 al B3."
    assert fake.merged == [([9, 10], 11)]
    assert fake.transferred == [(9, 45, 12)]


async def test_move_same_drawer_refused():
    fake = one_in_b1()
    r = await move_between(fake, 5, 17, 17, 5, LOCS)
    assert r.phrase == "El origen y el destino son el mismo cajón." and not r.done
    assert fake.transferred == [] and fake.merged == [] and fake.patched == []


async def test_move_nothing_in_source_refused():
    fake = one_in_b1()
    r = await move_between(fake, 5, 18, 17, 1, LOCS)
    assert r.phrase == "No hay Tornillo M3x8 en el B3." and not r.done
    assert fake.transferred == []


async def test_move_more_than_source_refused():
    fake = one_in_b1()
    r = await move_between(fake, 5, 17, 18, 41, LOCS)
    assert r.phrase == "Solo hay 40 en el B1." and not r.done
    assert fake.transferred == [] and fake.merged == [] and fake.patched == []


async def test_move_unknown_drawer_refused():
    fake = one_in_b1()
    for src, dst in [(99, 18), (17, 99)]:
        r = await move_between(fake, 5, src, dst, 1, LOCS)
        assert r.phrase == UNKNOWN and not r.done
    assert fake.transferred == [] and fake.patched == []


async def test_add_and_count_unknown_drawer_refused():
    fake = one_in_b1()
    assert (await add_to_drawer(fake, 5, 99, 1, LOCS)).phrase == UNKNOWN
    assert (await count_drawer(fake, 5, 99, 1, LOCS)).phrase == UNKNOWN
    assert fake.added == [] and fake.counted == []


@pytest.mark.parametrize("quantity", [0, -1, None])
async def test_invalid_quantities_add(quantity):
    fake = one_in_b1()
    r = await add_to_drawer(fake, 5, 17, quantity, LOCS)
    assert r.phrase == INVALID and not r.done
    assert fake.added == [] and fake.patched == []


@pytest.mark.parametrize("quantity", [0, -1])
async def test_invalid_quantities_move(quantity):
    fake = one_in_b1()
    r = await move_between(fake, 5, 17, 18, quantity, LOCS)
    assert r.phrase == INVALID and not r.done
    assert fake.transferred == [] and fake.patched == []


@pytest.mark.parametrize("quantity", [-1, None])
async def test_invalid_quantities_count(quantity):
    fake = one_in_b1()
    r = await count_drawer(fake, 5, 17, quantity, LOCS)
    assert r.phrase == INVALID and not r.done
    assert fake.counted == [] and fake.merged == [] and fake.patched == []


async def test_count_accepts_zero():
    fake = one_in_b1()
    assert (await count_drawer(fake, 5, 17, 0, LOCS)).done


@pytest.mark.parametrize(
    "op",
    [
        lambda f: add_to_drawer(f, 5, 17, 1, LOCS),
        lambda f: count_drawer(f, 5, 17, 1, LOCS),
        lambda f: move_between(f, 5, 17, 18, 1, LOCS),
    ],
)
async def test_failure_before_write_says_down(op):
    fake = one_in_b1()
    fake.fail_on = {"stock_for_part"}
    r = await op(fake)
    assert r.phrase == DOWN_PHRASE and not r.done
    assert fake.added == fake.counted == fake.transferred == fake.merged == []


async def test_failure_after_write_says_maybe():
    fake = fake_with([item(9, 40), item(10, 5)])
    fake.fail_on = {"count_stock"}
    r = await count_drawer(fake, 5, 17, 47, LOCS)
    assert r.phrase == MAYBE_STOCK and not r.done
    assert fake.merged == [([9, 10], 11)]
    assert MAYBE_STOCK == "No sé si se ha registrado. Revisa la cantidad antes de repetirlo."


async def test_failure_in_add_transfer_and_create_says_maybe():
    fake = one_in_b1()
    fake.fail_on = {"add_stock"}
    assert (await add_to_drawer(fake, 5, 17, 1, LOCS)).phrase == MAYBE_STOCK
    fake = one_in_b1()
    fake.fail_on = {"transfer_stock"}
    assert (await move_between(fake, 5, 17, 18, 1, LOCS)).phrase == MAYBE_STOCK
    fake = one_in_b1()
    fake.fail_on = {"create_stock"}
    assert (await add_to_drawer(fake, 5, 18, 1, LOCS)).phrase == MAYBE_STOCK


async def test_keep_flag_failure_before_any_quantity_write_is_plain_down():
    fake = one_in_b1()
    fake.fail_on = {"set_keep_when_empty"}
    r = await count_drawer(fake, 5, 17, 3, LOCS)
    assert r.phrase == DOWN_PHRASE and fake.counted == []


async def test_get_part_failure_after_write_still_done():
    fake = one_in_b1()
    orig = fake.get_part

    async def flaky(pk):
        if fake.added:
            fake.fail_on = {"get_part"}
        return await orig(pk)

    fake.get_part = flaky
    r = await add_to_drawer(fake, 5, 17, 10, LOCS)
    assert r.done and r.low is False and r.phrase == "Hecho. Ahora hay 50 en el B1."


async def test_low_flag_after_add():
    fake = one_in_b1(minimum=100)
    r = await add_to_drawer(fake, 5, 17, 10, LOCS)
    assert r.done and r.low is True
    fake = one_in_b1(minimum=20)
    assert (await add_to_drawer(fake, 5, 17, 10, LOCS)).low is False


async def test_move_into_a_drawer_that_already_has_the_part_leaves_one_item():
    """A partial move used to leave two stock items in the destination (33 + 7 in B1, 7 new in B3)."""
    fake = fake_with([item(9, 40), item(10, 5, loc=12)])
    r = await move_between(fake, 5, 17, 18, 7, LOCS)
    assert r.done and r.phrase == "Hecho. He movido 7 del B1 al B3."
    in_b3 = [s for s in fake.stock.values() if s.location_pk == 12]
    assert len(in_b3) == 1 and in_b3[0].quantity == 12
    in_b1 = [s for s in fake.stock.values() if s.location_pk == 11]
    assert len(in_b1) == 1 and in_b1[0].quantity == 33


async def test_move_back_and_forth_never_accumulates_items():
    """The reported case: move 7 away, then move 7 back."""
    fake = one_in_b1()
    await move_between(fake, 5, 17, 18, 7, LOCS)
    await move_between(fake, 5, 18, 17, 7, LOCS)
    for pk in (11, 12):
        assert len([s for s in fake.stock.values() if s.location_pk == pk and s.quantity > 0]) <= 1
    in_b1 = [s for s in fake.stock.values() if s.location_pk == 11]
    assert len(in_b1) == 1 and in_b1[0].quantity == 40


async def test_move_into_empty_destination_needs_no_merge():
    fake = one_in_b1()
    await move_between(fake, 5, 17, 18, 7, LOCS)
    assert fake.merged == []


async def test_merge_failure_after_the_transfer_still_reports_done():
    """The stock was moved; a failing tidy-up must not look like a failed move (spec 4.4 wording)."""
    fake = fake_with([item(9, 40), item(10, 5, loc=12)])
    fake.fail_on = {"merge_stock"}
    r = await move_between(fake, 5, 17, 18, 7, LOCS)
    assert r.done and "He movido 7 del B1 al B3" in r.phrase
    assert fake.transferred  # the transfer happened


async def test_moving_everything_leaves_a_kept_item_at_zero_in_source():
    fake = fake_with([item(9, 40), item(10, 5, loc=12)])
    await move_between(fake, 5, 17, 18, None, LOCS)
    in_b3 = [s for s in fake.stock.values() if s.location_pk == 12]
    assert len(in_b3) == 1 and in_b3[0].quantity == 45
