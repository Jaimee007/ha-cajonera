"""Service-level behaviour, kept free of Home Assistant in logic/service.py."""

import asyncio
from datetime import datetime

from logic.models import Category, Drawer, Location, Part, StockItem
from logic.service import Cajonera
from logic.simulator import DrawerSimulator
from logic.withdraw import Context
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


async def test_sync_notices_survive_without_marking():
    """Marking is not part of sync(): it can neither delay nor cancel the notices."""
    fake = stocked()
    seen = []
    caj = Cajonera(fake, DrawerSimulator([Drawer(0, 0, 0), Drawer(17, 0, 1)]), deadline=0.3, now=lambda: NOW,
                   on_notices=seen.append)
    notices = await caj.sync()
    assert notices == ["Cajón 0 añadido como A1.", "Cajón 17 añadido como B1."]
    assert seen == [notices]
    assert fake.patched == []
    assert fake.stock[9].delete_on_deplete is True


async def test_tick_marks_stock_and_retries_after_failure():
    # The first sync creates A1 (pk 1001) and B1 (pk 1002); stock 12 sits outside the cabinet.
    fake = FakeInventory(
        [Part(5, "Tornillo M3x8", 0, 40), Part(6, "Huevos", 0, 4)],
        [StockItem(9, 5, "", 30.0, 1001), StockItem(10, 5, "", 10.0, 1002), StockItem(12, 6, "", 4.0, 555)],
        [],
    )
    fake.fail_on = {"set_keep_when_empty"}
    caj = make(fake)
    await caj.tick()
    assert caj.synced and caj._stock_marked is False
    assert fake.stock[9].delete_on_deplete is True
    fake.fail_on = set()
    await caj.tick()
    assert caj._stock_marked is True
    assert sorted(pk for pk, _ in fake.patched) == [9, 10]
    assert fake.stock[12].delete_on_deplete is True  # outside the cabinet: untouched
    fake.patched.clear()
    await caj.tick()  # done: no more passes
    assert fake.patched == []


async def test_ready_does_not_mark_stock():
    fake = stocked()
    caj = make(fake)
    r = await caj.buscar("m3")
    assert r["frase"] == "Están en el A1. Quedan 40."
    assert fake.patched == [] and caj._stock_marked is False


async def test_manual_sync_remarks_stock_after_sync():
    fake = stocked()
    caj = make(fake)
    await caj.tick()
    assert caj._stock_marked
    fake.stock[9] = StockItem(9, 5, "Tornillo M3x8", 40.0, 1001)  # a new unmarked item appears
    r = await caj.sincronizar()
    assert r["avisos"] == []
    assert caj._stock_marked and fake.stock[9].delete_on_deplete is False


async def test_withdraw_last_unit_keeps_item_and_search_says_none_left():
    # The first sync creates A1 (pk 1001) and B1 (pk 1002); the item is still unmarked.
    fake = FakeInventory([Part(6, "Huevos", 0, 4)], [StockItem(10, 6, "", 4.0, 1002)], [])
    caj = make(fake)
    await caj.sync()
    r = await caj.retirar(texto="huevos", cantidad=4)
    assert r["hecho"]
    assert 10 in fake.stock and fake.stock[10].quantity == 0
    assert (await caj.buscar("huevos"))["frase"] == "Tenías Huevos en el B1, pero no queda ninguno."


def _modes(caj):
    return {s.drawer.address: s.mode for s in caj.simulator.states()}


async def test_sugerir_has_no_side_effects():
    caj = make(stocked())
    r = await caj.sugerir("m3")
    assert [s["nombre"] for s in r["sugerencias"]] == ["Tornillo M3x8"]
    assert r["sugerencias"][0]["direcciones"] == [0]
    assert set(_modes(caj).values()) == {"reposo"}
    assert caj.context is None


async def test_iluminar_busqueda_lights_drawers_and_marks_low_ones():
    caj = make(stocked())
    await caj.sync()
    caj.iluminar_busqueda([0, 17], [17])
    assert _modes(caj) == {0: "buscado", 17: "buscado_bajo"}


async def test_iluminar_busqueda_ignores_unknown_addresses_and_duplicates():
    caj = make(stocked())
    await caj.sync()
    caj.iluminar_busqueda([30, 0, 0], [30, 0])
    assert _modes(caj) == {0: "buscado_bajo", 17: "reposo"}
    caj.iluminar_busqueda([30], [])  # nothing real to light: everything goes out
    assert set(_modes(caj).values()) == {"reposo"}


async def test_iluminar_busqueda_lights_at_most_ten_drawers():
    drawers = [Drawer(a, a, 0) for a in range(12)]
    caj = make(stocked(), drawers=drawers)
    caj.iluminar_busqueda(list(range(12)), [])
    lit = [a for a, mode in _modes(caj).items() if mode == "buscado"]
    assert lit == list(range(10))


async def test_iluminar_busqueda_empty_list_puts_every_search_light_out():
    caj = make(stocked())
    await caj.sync()
    caj.iluminar_busqueda([0, 17], [])
    caj.iluminar_busqueda([], [])
    assert set(_modes(caj).values()) == {"reposo"}
    assert caj.simulator._search_timer is None


async def test_iluminar_busqueda_leaves_the_context_alone():
    caj = make(stocked())
    await caj.pieza(5)
    before = caj.context
    assert before is not None
    caj.iluminar_busqueda([17], [])
    assert _modes(caj) == {0: "reposo", 17: "buscado"}
    assert caj.context == before
    caj.iluminar_busqueda([], [])
    assert caj.context == before


async def test_pieza_lights_drawers_and_sets_context():
    caj = make(stocked())
    sheet = await caj.pieza(5)
    assert sheet["nombre"] == "Tornillo M3x8"
    assert _modes(caj) == {0: "buscado", 17: "reposo"}
    assert caj.context == Context(5, 1001, NOW)


async def test_pieza_with_two_drawers_has_no_location_in_context():
    fake = FakeInventory([Part(5, "Tornillo M3x8", 50, 40)],
                         [StockItem(9, 5, "", 25.0, 1001), StockItem(10, 5, "", 15.0, 1002)], [])
    caj = make(fake)
    await caj.pieza(5)
    assert _modes(caj) == {0: "buscado_bajo", 17: "buscado_bajo"}
    assert caj.context == Context(5, None, NOW)


async def test_pieza_down_changes_nothing():
    fake = stocked()
    fake.fail = True
    caj = make(fake)
    sheet = await caj.pieza(5)
    assert sheet == {"frase": "No puedo consultar el inventario ahora mismo."}
    assert set(_modes(caj).values()) == {"reposo"}
    assert caj.context is None


async def test_anadir_confirms_on_drawer():
    fake = stocked()
    caj = make(fake)
    r = await caj.anadir(5, 0, 10)
    assert r["hecho"] and "50" in r["frase"]
    assert fake.added == [(9, 10)]
    assert _modes(caj)[0] == "confirmar_ok"
    assert caj.context == Context(5, 1001, NOW)


async def test_bad_quantity_answers_without_inventree():
    fake = stocked()
    fake.fail = True
    caj = make(fake)
    assert await caj.anadir(5, 0, 0) == {"frase": "Dime una cantidad válida.", "hecho": False}
    assert await caj.anadir(5, 0, None) == {"frase": "Dime una cantidad válida.", "hecho": False}
    assert await caj.actualizar(5, 0, -1) == {"frase": "Dime una cantidad válida.", "hecho": False}
    assert await caj.mover(5, 0, 17, 0) == {"frase": "Dime una cantidad válida.", "hecho": False}
    assert not caj.synced


async def test_actualizar_and_mover_set_context_to_the_destination():
    fake = stocked()
    caj = make(fake)
    r = await caj.actualizar(5, 0, 30)
    assert r["hecho"] and fake.counted == [(9, 30)]
    r = await caj.mover(5, 0, 17)
    assert r["hecho"]
    assert _modes(caj)[17] == "confirmar_ok"
    assert caj.context == Context(5, 1002, NOW)


async def test_write_timeout_says_maybe_stock():
    fake = stocked()
    caj = make(fake, deadline=0.2)
    await caj.sync()
    orig = fake.add_stock

    async def slow_add(*a):
        await asyncio.sleep(0.5)
        await orig(*a)

    fake.add_stock = slow_add
    expected = {"frase": "No sé si se ha registrado. Revisa la cantidad antes de repetirlo.", "hecho": False}
    assert await caj.anadir(5, 0, 1) == expected
    orig_create = fake.create_part

    async def slow_create(*a, **kw):
        await asyncio.sleep(0.5)
        return await orig_create(*a, **kw)

    fake.create_part = slow_create
    assert await caj.pieza_crear("Nueva", 3, 0, 1) == {**expected, "pieza_pk": None}
    assert (await caj.pieza_crear("  ", 3, 0, 1))["frase"] == "Dime un nombre para la pieza."


async def test_idempotent_write_timeouts_do_not_talk_about_quantity():
    expected = "No sé si se ha guardado. Revisa antes de repetirlo."
    fake = FakeInventory([Part(5, "Tornillo M3x8", 0, 40)], [StockItem(9, 5, "", 40.0, 1001)], [],
                         [Category(3, "Tornillos", "Tornillos")])
    caj = make(fake, deadline=0.2)
    await caj.sync()
    fake.delay = 0.5
    assert (await caj.pieza_editar(5, nombre="Otro")) == {"frase": expected, "hecho": False}
    assert (await caj.categoria_crear("Nuevas")) == {"frase": expected}


async def test_write_failure_changes_no_light_or_context():
    fake = stocked()
    caj = make(fake)
    await caj.sync()
    fake.fail_on = {"add_stock"}
    r = await caj.anadir(5, 0, 1)
    assert not r["hecho"]
    assert set(_modes(caj).values()) == {"reposo"}
    assert caj.context is None


async def test_sugerir_cajon_only_answers_and_lights_nothing():
    fake = FakeInventory([Part(5, "Tornillo M3x8", 0, 40)], [StockItem(9, 5, "", 40.0, 1001)], [])
    fake.parts[5] = Part(5, "Tornillo M3x8", 0, 40, category_pk=3)
    caj = make(fake)
    r = await caj.sugerir_cajon(3)
    assert r["direccion"] == 0 and r["cajon"] == "A1"
    assert set(_modes(caj).values()) == {"reposo"}


async def test_sugerir_cajon_leaves_a_search_and_a_tapped_light_alone():
    fake = FakeInventory([Part(5, "Tornillo M3x8", 0, 40)], [StockItem(9, 5, "", 40.0, 1001)], [])
    fake.parts[5] = Part(5, "Tornillo M3x8", 0, 40, category_pk=3)
    caj = make(fake)
    await caj.sync()
    caj.guardar_aqui(17)  # the user tapped B1 while the suggestion was in flight
    assert (await caj.sugerir_cajon(3))["direccion"] == 0
    assert _modes(caj) == {0: "reposo", 17: "guardar"}


async def test_guardar_apagar_clears_only_the_store_light():
    caj = make(stocked())
    await caj.sync()
    caj.guardar_apagar()  # nothing lit: no-op
    assert set(_modes(caj).values()) == {"reposo"}
    caj.guardar_aqui(17)
    caj.guardar_apagar()
    assert set(_modes(caj).values()) == {"reposo"}
    await caj.buscar("m3")
    assert _modes(caj) == {0: "buscado", 17: "reposo"}
    caj.guardar_apagar()  # a search in progress is not ours to clear
    assert _modes(caj) == {0: "buscado", 17: "reposo"}


async def test_guardar_aqui_moves_the_magenta_light():
    fake = FakeInventory([Part(5, "Tornillo M3x8", 0, 40)], [StockItem(9, 5, "", 40.0, 1001)], [])
    fake.parts[5] = Part(5, "Tornillo M3x8", 0, 40, category_pk=3)
    caj = make(fake)
    assert (await caj.sugerir_cajon(3))["direccion"] == 0
    caj.guardar_aqui(0)
    assert _modes(caj) == {0: "guardar", 17: "reposo"}
    caj.guardar_aqui(17)
    assert _modes(caj) == {0: "reposo", 17: "guardar"}
    caj.guardar_aqui(30)  # unknown address: ignored
    assert _modes(caj) == {0: "reposo", 17: "guardar"}


async def test_sugerir_cajon_down_lights_nothing():
    fake = stocked()
    fake.fail = True
    caj = make(fake)
    r = await caj.sugerir_cajon(3)
    assert r == {"direccion": None, "cajon": None, "motivo": "No puedo consultar el inventario ahora mismo."}
    assert set(_modes(caj).values()) == {"reposo"}


async def test_pieza_crear_then_sugerir_finds_it():
    fake = FakeInventory([], [], [], [Category(3, "Tornillos", "Tornillos")])
    caj = make(fake)
    r = await caj.pieza_crear("Tuerca M4", 3, 17, 25, ipn="T-M4", minimo=5)
    assert r["hecho"] and r["pieza_pk"] is not None
    assert _modes(caj)[17] == "confirmar_ok"
    assert caj.context == Context(r["pieza_pk"], 1002, NOW)
    found = (await caj.sugerir("tuerca"))["sugerencias"]
    assert [(s["pieza_pk"], s["cajones"]) for s in found] == [(r["pieza_pk"], ["B1"])]
    assert fake.created_parts[0].minimum_stock == 5


async def test_pieza_editar_passes_only_given_keys_and_has_no_drawer():
    fake = stocked()
    caj = make(fake)
    r = await caj.pieza_editar(5, nombre="Tornillo M3x10", minimo=7)
    assert r["hecho"]
    assert fake.patched == [(5, {"name": "Tornillo M3x10", "minimum_stock": 7.0})]
    assert set(_modes(caj).values()) == {"reposo"}
    assert caj.context is None


async def test_categorias_and_categoria_crear():
    fake = FakeInventory([], [], [], [Category(3, "Tornillos", "Tornillos")])
    caj = make(fake)
    assert (await caj.categorias())["categorias"] == [{"pk": 3, "nombre": "Tornillos", "ruta": "Tornillos"}]
    r = await caj.categoria_crear("Métricos", 3)
    assert r["categoria"]["ruta"] == "Tornillos/Métricos"
    assert (await caj.categoria_crear("  "))["frase"] == "Dime un nombre para la categoría."
    fake.fail = True
    assert (await caj.categorias()) == {"categorias": []}
    assert (await caj.sugerir("tuerca")) == {"sugerencias": []}


# Part ids for the card (final review I1, I3, minor 7)

def look_alike_inventory(n=12):
    parts = [Part(100 + i, f"Tornillo M3x{8 + i}", 0, 5) for i in range(n)]
    stock = [StockItem(200 + i, 100 + i, "", 5.0, 1001) for i in range(n)]
    return FakeInventory(parts, stock, [])


async def test_retirar_by_pieza_pk_works_among_twelve_look_alikes():
    fake = look_alike_inventory()
    caj = make(fake)
    r = await caj.retirar(texto="Tornillo M3x", cantidad=1, cajon="A1")
    assert not r["hecho"] and fake.removed == []  # the text route cannot tell them apart
    r = await caj.retirar(cantidad=2, cajon="A1", pieza_pk=111)
    assert r["hecho"] and fake.removed == [(211, 2)]
    assert _modes(caj)[0] == "confirmar_ok"
    assert caj.context == Context(111, 1001, NOW)


async def test_retirar_by_pieza_pk_distinguishes_same_name_parts():
    fake = FakeInventory([Part(5, "Tuerca M4", 0, 4), Part(6, "Tuerca M4", 0, 9)],
                         [StockItem(9, 5, "", 4.0, 1001), StockItem(10, 6, "", 9.0, 1001)], [])
    caj = make(fake)
    r = await caj.retirar(cantidad=3, pieza_pk=6)
    assert r["hecho"] and fake.removed == [(10, 3)]


async def test_retirar_by_pieza_pk_wrong_drawer_writes_nothing():
    fake = stocked()
    caj = make(fake)
    r = await caj.retirar(cantidad=1, cajon="B1", pieza_pk=5)
    assert r == {"frase": "No hay Tornillo M3x8 en el B1; hay en el A1.", "hecho": False}
    assert fake.removed == []
    assert set(_modes(caj).values()) == {"reposo"}


async def test_retirar_bad_quantity_with_pieza_pk_still_asks():
    assert await make(stocked()).retirar(cantidad=0, pieza_pk=5) == {"frase": "Dime cuántas has cogido.", "hecho": False}


async def test_buscar_and_contenido_rows_carry_pieza_pk():
    caj = make(stocked())
    r = await caj.buscar("m3")
    assert [row["pieza_pk"] for row in r["resultados"]] == [5]
    c = await caj.contenido(0)
    assert [row["pieza_pk"] for row in c["piezas"]] == [5]


async def test_pieza_in_no_drawer_clears_old_lights_and_keeps_context():
    fake = FakeInventory([Part(5, "Tornillo M3x8", 0, 40), Part(6, "Cinta", 0, 3)],
                         [StockItem(9, 5, "", 40.0, 1001), StockItem(10, 6, "", 3.0, None)], [])
    caj = make(fake)
    await caj.pieza(5)
    assert _modes(caj) == {0: "buscado", 17: "reposo"}
    sheet = await caj.pieza(6)
    assert sheet["cajones"] == []
    assert set(_modes(caj).values()) == {"reposo"}
    assert caj.simulator._search_timer is None
    assert caj.context == Context(6, None, NOW)
    caj.guardar_aqui(17)
    await caj.pieza(6)
    assert set(_modes(caj).values()) == {"reposo"}


def _eggs(items, minimum=10, total=12):
    # Two stock items of one part in B1 (pk 1002): a kept-at-zero one and one made by hand.
    return FakeInventory([Part(6, "Huevos", minimum, total)], items, [])


async def test_contenido_one_row_per_part_with_summed_quantity():
    fake = _eggs([StockItem(2, 6, "", 0.0, 1002, False), StockItem(6, 6, "", 12.0, 1002)])
    r = await make(fake).contenido(17)
    assert r["piezas"] == [{"stock": 6, "pieza_pk": 6, "pieza": "Huevos", "cantidad": 12.0, "bajo": False}]


async def test_contenido_two_parts_two_rows_and_single_zero_item_row():
    fake = FakeInventory([Part(6, "Huevos", 0, 0), Part(5, "Arandela", 0, 3)],
                         [StockItem(2, 6, "", 0.0, 1002, False), StockItem(9, 5, "", 3.0, 1002)], [])
    r = await make(fake).contenido(17)
    assert [(p["pieza"], p["cantidad"]) for p in r["piezas"]] == [("Arandela", 3.0), ("Huevos", 0.0)]


async def test_buscar_one_row_per_part_per_drawer():
    fake = _eggs([StockItem(2, 6, "", 0.0, 1002, False), StockItem(6, 6, "", 12.0, 1002)])
    r = await make(fake).buscar("huevos")
    assert [(x["stock"], x["cantidad"], x["cajon"]) for x in r["resultados"]] == [(6, 12.0, "B1")]


async def test_contenido_refreshes_a_stale_low_list():
    # InvenTree changed by hand: the part is no longer low, but the report still says so.
    fake = _eggs([StockItem(2, 6, "", 0.0, 1002, False)], total=0)
    caj = make(fake)
    await caj.tick()
    assert [lp.part.pk for lp in caj.low.parts] == [6]
    fake.stock[6] = StockItem(6, 6, "", 12.0, 1002)
    fake.parts[6] = Part(6, "Huevos", 10, 12)
    await caj.contenido(17)
    await caj.drain()
    assert caj.low.parts == []


async def test_reads_refresh_low_at_most_one_at_a_time():
    fake = _eggs([StockItem(2, 6, "", 0.0, 1002, False)], total=0)
    caj = make(fake)
    await caj.sync()
    calls = []
    orig = caj.refresh_low

    async def counting():
        calls.append(1)
        await asyncio.sleep(0.05)
        await orig()

    caj.refresh_low = counting
    await caj.contenido(17)
    await caj.buscar("huevos")
    await caj.pieza(6)
    await caj.drain()
    assert len(calls) == 1
    await caj.contenido(17)  # nothing in flight any more: a new one may start
    await caj.drain()
    assert len(calls) == 2
