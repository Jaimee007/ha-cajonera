from datetime import datetime, timedelta

import pytest

from logic.models import Location, Part, StockItem
from logic.withdraw import CONTEXT_TTL, Context, withdraw_by_part, withdraw_by_stock, withdraw_by_text
from fakes import FakeInventory

NOW = datetime(2026, 11, 24, 12, 0, 0)
B3 = Location(11, "B3", 17)
C1 = Location(12, "C1", 18)
LOCS = {B3.pk: B3, C1.pk: C1}


def one_drawer(qty=40, minimum=20):
    return FakeInventory([Part(5, "Tornillo M3x8", minimum, qty)],
                         [StockItem(9, 5, "Tornillo M3x8", float(qty), 11)], list(LOCS.values()))


def two_drawers():
    return FakeInventory([Part(5, "Tornillo M3x8", 40, 30)],
                         [StockItem(9, 5, "Tornillo M3x8", 20.0, 11), StockItem(10, 5, "Tornillo M3x8", 10.0, 12)],
                         list(LOCS.values()))


async def call(fake, text="m3", quantity=3, drawer=None, context=None):
    return await withdraw_by_text(fake, text, quantity, drawer, context, NOW, LOCS)


def test_context_ttl_is_two_minutes():
    assert CONTEXT_TTL == timedelta(minutes=2)


@pytest.mark.parametrize("quantity", [0, -2, None])
async def test_zero_or_negative_quantity_writes_nothing(quantity):
    fake = one_drawer()
    r = await call(fake, quantity=quantity)
    assert r.phrase == "Dime cuántas has cogido."
    assert not r.done and fake.removed == []


async def test_no_text_without_context_asks():
    r = await call(one_drawer(), text=None)
    assert r.phrase == "¿Qué has cogido?"


async def test_uses_context_within_two_minutes():
    fake = one_drawer()
    ctx = Context(5, 11, NOW - timedelta(seconds=90))
    r = await call(fake, text=None, context=ctx)
    assert fake.removed == [(9, 3)]
    assert r.phrase == "Hecho. Quedan 37."


async def test_expired_context_asks():
    ctx = Context(5, 11, NOW - timedelta(seconds=121))
    r = await call(one_drawer(), text=None, context=ctx)
    assert r.phrase == "¿Qué has cogido?"


async def test_several_parts_asks_which():
    fake = FakeInventory([Part(5, "Tornillo M3x8", 0, 1), Part(6, "Tornillo M3x12", 0, 1),
                          Part(7, "Tornillo M3x20", 0, 1), Part(8, "Tornillo M3x30", 0, 1)],
                         [StockItem(9 + i, 5 + i, "", 1, 11) for i in range(4)], list(LOCS.values()))
    r = await call(fake, text="tornillo m3")
    assert r.phrase == "Hay varias piezas con ese nombre: Tornillo M3x8, Tornillo M3x12 y Tornillo M3x20. ¿Cuál?"
    assert fake.removed == []


async def test_no_part():
    r = await call(one_drawer(), text="arandela")
    assert r.phrase == "No encuentro nada con ese nombre."


async def test_ambiguous_drawer_writes_nothing():
    fake = two_drawers()
    r = await call(fake)
    assert r.phrase == "Hay en el B3 y en el C1, ¿de cuál?"
    assert fake.removed == []


async def test_drawer_name_resolves_ambiguity():
    fake = two_drawers()
    r = await call(fake, drawer="c1")
    assert fake.removed == [(10, 3)]
    assert r.phrase == "Hecho. Quedan 7 en el C1, 27 en total, por debajo del mínimo de 40."
    assert r.low and r.address == 18


async def test_context_drawer_resolves_ambiguity():
    fake = two_drawers()
    r = await call(fake, context=Context(5, 11, NOW - timedelta(seconds=10)))
    assert fake.removed == [(9, 3)]
    assert r.phrase == "Hecho. Quedan 17 en el B3, 27 en total, por debajo del mínimo de 40."


async def test_context_of_another_part_does_not_resolve():
    fake = two_drawers()
    r = await call(fake, context=Context(99, 11, NOW - timedelta(seconds=10)))
    assert r.phrase == "Hay en el B3 y en el C1, ¿de cuál?"


async def test_more_than_available():
    fake = one_drawer(qty=2)
    r = await call(fake)
    assert r.phrase == "Solo quedan 2 en el B3."
    assert fake.removed == []


async def test_low_after_withdraw():
    fake = one_drawer(qty=20)
    r = await call(fake)
    assert r.phrase == "Hecho. Quedan 17, por debajo del mínimo de 20."
    assert r.done and r.low and r.address == 17
    assert r.context == Context(5, 11, NOW)
    assert fake.last_notes == "cajonera: voz"


async def test_stock_outside_cajonera_is_not_a_drawer():
    fake = FakeInventory([Part(5, "Tornillo M3x8", 0, 40)], [StockItem(9, 5, "Tornillo M3x8", 40.0, None)], [])
    r = await call(fake)
    assert r.phrase == "Hay 40 de Tornillo M3x8, pero no está en ningún cajón."
    assert fake.removed == []


async def test_withdraw_by_stock_never_asks():
    fake = two_drawers()
    r = await withdraw_by_stock(fake, 9, 3, NOW, LOCS)
    assert r.phrase == "Hecho. Quedan 17 en el B3, 27 en total, por debajo del mínimo de 40."
    assert fake.removed == [(9, 3)] and fake.last_notes == "cajonera: panel"


async def test_withdraw_by_stock_rejects_bad_quantity():
    fake = one_drawer()
    r = await withdraw_by_stock(fake, 9, 0, NOW, LOCS)
    assert r.phrase == "Dime cuántas has cogido." and fake.removed == []


async def test_inventree_down_on_withdraw():
    fake = one_drawer()
    fake.fail = True
    r = await call(fake)
    assert r.phrase == "No puedo consultar el inventario ahora mismo."
    assert not r.done


# Regressions from the final review


async def test_failure_after_remove_still_reports_done():
    """C1: the stock was decremented; a failing follow-up must not look like a failure."""
    fake = one_drawer()
    fake.fail_on = {"get_part"}
    original = fake.stock_for_part

    calls = {"n": 0}

    async def stock_for_part(pk):
        calls["n"] += 1
        if calls["n"] > 1:  # the follow-up read after remove_stock
            from logic.inventree import InvenTreeError
            raise InvenTreeError("slow")
        return await original(pk)

    fake.stock_for_part = stock_for_part
    r = await call(fake)
    assert fake.removed == [(9, 3)]
    assert r.done
    assert r.phrase == "Hecho. Quedan 37."


async def test_remove_itself_failing_says_maybe_registered():
    """C1 / spec 4.4: a failed or timed-out POST may have been applied."""
    fake = one_drawer()
    fake.fail_on = {"remove_stock"}
    r = await call(fake)
    assert not r.done
    assert r.phrase == "No sé si se ha registrado. Pregúntame dónde están antes de repetirlo."


async def test_two_items_in_one_drawer_are_one_drawer():
    """I3: several stock items of one part in the same drawer are not ambiguous."""
    fake = FakeInventory([Part(5, "Tornillo M3x8", 0, 15)],
                         [StockItem(9, 5, "Tornillo M3x8", 5.0, 11), StockItem(10, 5, "Tornillo M3x8", 10.0, 11)],
                         list(LOCS.values()))
    r = await call(fake, quantity=8)
    assert r.done
    assert sum(q for _, q in fake.removed) == 8
    assert r.phrase == "Hecho. Quedan 7."


async def test_more_than_drawer_total_uses_drawer_total():
    fake = FakeInventory([Part(5, "Tornillo M3x8", 0, 15)],
                         [StockItem(9, 5, "Tornillo M3x8", 5.0, 11), StockItem(10, 5, "Tornillo M3x8", 10.0, 11)],
                         list(LOCS.values()))
    r = await call(fake, quantity=20)
    assert r.phrase == "Solo quedan 15 en el B3."
    assert fake.removed == []


async def test_exact_name_wins_over_substring_matches():
    """I4: saying the exact name picks that part even if others contain it."""
    fake = FakeInventory([Part(5, "Tornillo M3x8", 0, 40), Part(6, "Tornillo M3x80", 0, 10)],
                         [StockItem(9, 5, "", 40.0, 11), StockItem(10, 6, "", 10.0, 12)], list(LOCS.values()))
    r = await call(fake, text="tornillo m3x8")
    assert fake.removed == [(9, 3)] and r.done


async def test_parts_outside_cajonera_do_not_make_it_ambiguous():
    """I4: withdrawal ignores matches that are in no drawer, like search does."""
    fake = FakeInventory([Part(5, "Tornillo M3x8", 0, 40), Part(6, "Tornillo M3x8 inox", 0, 10)],
                         [StockItem(9, 5, "", 40.0, 11), StockItem(10, 6, "", 10.0, None)], list(LOCS.values()))
    r = await call(fake, text="m3x8")
    assert fake.removed == [(9, 3)] and r.done


async def test_named_drawer_without_the_part_refuses():
    """I5: an explicit drawer that does not hold the part writes nothing."""
    fake = one_drawer()
    r = await call(fake, drawer="C1")
    assert r.phrase == "No hay Tornillo M3x8 en el C1; hay en el B3."
    assert fake.removed == []


async def test_misheard_drawer_does_not_fall_back_to_context():
    fake = two_drawers()
    r = await call(fake, drawer="C2", context=Context(5, 11, NOW - timedelta(seconds=10)))
    assert r.phrase == "No hay Tornillo M3x8 en el C2; hay en el B3 y en el C1."
    assert fake.removed == []


async def test_withdraw_last_unit_marks_item_first():
    fake = one_drawer(qty=4)
    assert fake.stock[9].delete_on_deplete is True
    r = await call(fake, quantity=4)
    assert r.done
    assert 9 in fake.stock and fake.stock[9].quantity == 0
    assert (9, {"delete_on_deplete": False}) in fake.patched


async def test_partial_withdraw_does_not_touch_the_flag():
    fake = one_drawer(qty=4)
    await call(fake, quantity=3)
    assert fake.patched == []


async def test_marking_failure_before_last_unit_removes_nothing():
    fake = one_drawer(qty=4)
    fake.fail_on = {"set_keep_when_empty"}
    r = await call(fake, quantity=4)
    assert r.phrase == "No puedo consultar el inventario ahora mismo."
    assert not r.done and fake.removed == []


# Withdrawal by part id (the card's part sheet)

def look_alikes(n=12):
    parts = [Part(100 + i, f"Tornillo M3x{8 + i}0" if i else "Tornillo M3x8", 0, 5) for i in range(n)]
    stock = [StockItem(200 + i, 100 + i, "", 5.0, 11) for i in range(n)]
    return FakeInventory(parts, stock, list(LOCS.values()))


async def test_withdraw_by_part_works_past_the_name_search_limit():
    fake = look_alikes(12)
    last = fake.parts[111]
    r = await withdraw_by_part(fake, last.pk, 2, None, NOW, LOCS)
    assert r.done and fake.removed == [(211, 2)]
    assert r.phrase == "Hecho. Quedan 3."
    assert fake.last_notes == "cajonera: panel"
    assert r.context == Context(111, 11, NOW)


async def test_text_route_dead_ends_where_part_route_works():
    fake = look_alikes(12)
    text = "tornillo m3"
    r = await withdraw_by_text(fake, text, 1, None, None, NOW, LOCS)
    assert not r.done and fake.removed == []


async def test_withdraw_by_part_distinguishes_same_name_parts():
    fake = FakeInventory(
        [Part(5, "Tuerca M4", 0, 4), Part(6, "Tuerca M4", 0, 9)],
        [StockItem(9, 5, "", 4.0, 11), StockItem(10, 6, "", 9.0, 12)],
        list(LOCS.values()),
    )
    r = await withdraw_by_part(fake, 6, 3, None, NOW, LOCS)
    assert r.done and fake.removed == [(10, 3)]
    assert r.address == 18 and r.phrase == "Hecho. Quedan 6."


async def test_withdraw_by_part_refuses_a_wrong_drawer_and_writes_nothing():
    fake = two_drawers()
    r = await withdraw_by_part(fake, 5, 3, "A1", NOW, LOCS)
    assert r.phrase == "No hay Tornillo M3x8 en el A1; hay en el B3 y en el C1."
    assert not r.done and fake.removed == []


async def test_withdraw_by_part_needs_a_drawer_when_there_are_several():
    fake = two_drawers()
    r = await withdraw_by_part(fake, 5, 3, None, NOW, LOCS)
    assert r.phrase == "Hay en el B3 y en el C1, ¿de cuál?"
    assert fake.removed == []
    r = await withdraw_by_part(fake, 5, 3, "c1", NOW, LOCS)
    assert r.done and fake.removed == [(10, 3)]
    assert r.phrase == "Hecho. Quedan 7 en el C1, 27 en total, por debajo del mínimo de 40."


async def test_withdraw_by_part_single_drawer_is_automatic_and_keeps_empty_item():
    fake = one_drawer(qty=2, minimum=0)
    r = await withdraw_by_part(fake, 5, 2, None, NOW, LOCS)
    assert r.done and fake.stock[9].quantity == 0 and fake.stock[9].delete_on_deplete is False


async def test_withdraw_by_part_edge_cases():
    fake = one_drawer()
    assert (await withdraw_by_part(fake, 5, 0, None, NOW, LOCS)).phrase == "Dime cuántas has cogido."
    assert (await withdraw_by_part(fake, 5, 99, None, NOW, LOCS)).phrase == "Solo quedan 40 en el B3."
    out = FakeInventory([Part(5, "Tornillo M3x8", 0, 40)], [StockItem(9, 5, "", 40.0, None)], [])
    r = await withdraw_by_part(out, 5, 1, None, NOW, LOCS)
    assert r.phrase == "Hay 40 de Tornillo M3x8, pero no está en ningún cajón." and out.removed == []
    fake.fail = True
    assert (await withdraw_by_part(fake, 5, 1, None, NOW, LOCS)).phrase == "No puedo consultar el inventario ahora mismo."
    assert fake.removed == []
