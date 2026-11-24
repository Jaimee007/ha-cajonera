from datetime import datetime, timedelta

import pytest

from logic.models import Location, Part, StockItem
from logic.withdraw import CONTEXT_TTL, Context, withdraw_by_stock, withdraw_by_text
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
                         [StockItem(9, 5, "", 1, 11)], list(LOCS.values()))
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
