"""Service-level behaviour, kept free of Home Assistant in logic/service.py."""

import asyncio
from datetime import datetime

from logic.models import Drawer, Location, Part, StockItem
from logic.service import Cajonera
from logic.simulator import DrawerSimulator
from fakes import FakeInventory

NOW = datetime(2026, 11, 24, 12, 0, 0)


def make(fake, drawers=(Drawer(0, 0, 0), Drawer(17, 0, 1)), deadline=0.3):
    return Cajonera(fake, DrawerSimulator(list(drawers)), deadline=deadline, now=lambda: NOW)


def stocked():
    return FakeInventory([Part(5, "Tornillo M3x8", 20, 40)], [StockItem(9, 5, "Tornillo M3x8", 40.0, 1001)], [])


async def test_first_call_syncs_and_answers():
    fake = stocked()
    caj = make(fake)
    # first sync creates A1 (pk 1001) and B1 (pk 1002); the stock sits in A1
    r = await caj.buscar("m3")
    assert [loc.name for loc in fake.created] == ["A1", "B1"]
    assert r["frase"] == "Están en el A1. Quedan 40."


async def test_blank_text_and_bad_quantity_answer_without_inventree():
    fake = stocked()
    fake.fail = True
    caj = make(fake)
    assert (await caj.buscar("  "))["frase"] == "¿Qué quieres buscar?"
    assert (await caj.buscar(None))["frase"] == "¿Qué quieres buscar?"
    assert (await caj.retirar(texto="m3", cantidad=0))["frase"] == "Dime cuántas has cogido."


async def test_inventree_down_answers_down_phrase():
    fake = stocked()
    fake.fail = True
    caj = make(fake)
    assert (await caj.buscar("m3"))["frase"] == "No puedo consultar el inventario ahora mismo."
    assert (await caj.retirar(texto="m3", cantidad=1))["frase"] == "No puedo consultar el inventario ahora mismo."


async def test_slow_inventree_answers_within_deadline():
    fake = stocked()
    caj = make(fake, deadline=0.2)
    await caj.sync()
    fake.delay = 0.5
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    r = await caj.buscar("m3")
    assert loop.time() - t0 < 0.4
    assert r["frase"] == "No puedo consultar el inventario ahora mismo."


async def test_slow_withdraw_says_maybe_registered():
    fake = stocked()
    caj = make(fake, deadline=0.2)
    await caj.sync()
    orig = fake.remove_stock

    async def slow_remove(*a):
        await asyncio.sleep(0.5)
        await orig(*a)

    fake.remove_stock = slow_remove
    r = await caj.retirar(texto="m3", cantidad=1)
    assert r["frase"] == "No sé si se ha registrado. Pregúntame dónde están antes de repetirlo."
    assert r["hecho"] is False


async def test_failed_sync_is_retried_and_reported_as_down():
    """I2: until locations are loaded, answers are 'down', not 'no está en ningún cajón'."""
    fake = stocked()
    fake.fail_on = {"child_locations"}
    caj = make(fake)
    assert (await caj.buscar("m3"))["frase"] == "No puedo consultar el inventario ahora mismo."
    fake.fail_on = set()
    await caj.tick()
    assert caj.synced
    assert (await caj.buscar("m3"))["frase"] == "Están en el A1. Quedan 40."


async def test_sync_button_returns_notices_on_first_run():
    """M3: the first manual sync reports what it created."""
    caj = make(stocked())
    assert (await caj.sincronizar())["avisos"] == ["Cajón 0 añadido como A1.", "Cajón 17 añadido como B1."]


async def test_search_then_other_search_clears_context():
    """C2 at the service level."""
    fake = FakeInventory([Part(5, "Tornillo M3x8", 0, 40), Part(6, "Resistencia 1k", 0, 5), Part(7, "Resistencia 10k", 0, 5)],
                         [StockItem(9, 5, "", 40.0, 1001), StockItem(10, 6, "", 5.0, 1002), StockItem(11, 7, "", 5.0, 1002)], [])
    caj = make(fake)
    await caj.buscar("m3")
    await caj.buscar("resistencia")
    r = await caj.retirar(cantidad=3)
    assert r["frase"] == "¿Qué has cogido?"
    assert fake.removed == []


async def test_contents_when_down():
    fake = stocked()
    fake.fail = True
    r = await make(fake).contenido(0)
    assert r["frase"] == "No puedo consultar el inventario ahora mismo."


async def test_low_search_lights_low_mode():
    fake = FakeInventory([Part(5, "Tornillo M3x8", 20, 5)], [StockItem(9, 5, "", 5.0, 1002)], [])
    caj = make(fake)
    await caj.buscar("m3")
    state = next(s for s in caj.simulator.states() if s.drawer.address == 17)
    assert state.mode == "buscado_bajo"


async def test_withdraw_confirms_on_the_drawer():
    caj = make(stocked())
    r = await caj.retirar(texto="m3", cantidad=1)
    assert r["hecho"]
    state = next(s for s in caj.simulator.states() if s.drawer.address == 0)
    assert state.mode == "confirmar_ok"


async def test_sync_marks_cabinet_stock_keep_when_empty():
    # The first sync creates A1 (pk 1001) and B1 (pk 1002); stock 12 sits outside the cabinet.
    fake = FakeInventory(
        [Part(5, "Tornillo M3x8", 0, 40), Part(6, "Huevos", 0, 4)],
        [StockItem(9, 5, "", 30.0, 1001), StockItem(10, 5, "", 10.0, 1002), StockItem(12, 6, "", 4.0, 555)],
        [],
    )
    caj = make(fake)
    await caj.sync()
    assert sorted(pk for pk, _ in fake.patched) == [9, 10]
    assert fake.stock[9].delete_on_deplete is False
    assert fake.stock[12].delete_on_deplete is True  # outside the cabinet: untouched
    # Already-marked stock is not patched again by the next sync.
    fake.patched.clear()
    await caj.sync()
    assert fake.patched == []


async def test_sync_marking_failure_is_swallowed_and_retried_next_sync():
    fake = stocked()
    fake.fail_on = {"set_keep_when_empty"}
    caj = make(fake)
    await caj.sync()  # does not raise
    assert caj.synced
    assert fake.stock[9].delete_on_deplete is True
    fake.fail_on = set()
    await caj.sync()
    assert fake.stock[9].delete_on_deplete is False


async def test_withdraw_last_unit_keeps_item_and_search_says_none_left():
    # The first sync creates A1 (pk 1001) and B1 (pk 1002).
    fake = FakeInventory([Part(6, "Huevos", 0, 4)], [StockItem(10, 6, "", 4.0, 1002)], [])
    caj = make(fake)
    await caj.sync()
    r = await caj.retirar(texto="huevos", cantidad=4)
    assert r["hecho"]
    assert 10 in fake.stock and fake.stock[10].quantity == 0
    assert (await caj.buscar("huevos"))["frase"] == "Tenías Huevos en el B1, pero no queda ninguno."
