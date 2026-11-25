"""Voice rules of `cajonera.voz`, tested without Home Assistant."""

from datetime import datetime, timedelta

from logic.models import Location, Part, StockItem
from logic.search import DOWN_PHRASE
from logic.voice import Pregunta, VoiceResult, resolve_parts, voz_buscar
from logic.withdraw import Context
from fakes import FakeInventory

NOW = datetime(2026, 11, 25, 12, 0, 0)
B1 = Location(11, "B1", 17)
B3 = Location(12, "B3", 18)
C1 = Location(13, "C1", 19)
LOCS = {loc.pk: loc for loc in (B1, B3, C1)}


def inv(parts, stock, extra_locations=()):
    return FakeInventory(parts, stock, list(LOCS.values()) + list(extra_locations))


def part(pk, name, total, minimum=0.0):
    return Part(pk, name, minimum, float(total))


def item(pk, part_pk, qty, loc):
    return StockItem(pk, part_pk, "", float(qty), loc)


async def test_buscar_with_no_part_asks_what():
    assert await voz_buscar(inv([], []), LOCS, None, NOW) == VoiceResult("¿Qué quieres buscar?")


async def test_resolve_parts_by_pk_pks_and_text():
    fake = inv([part(5, "Tornillo M3x8", 1), part(6, "Tuerca M3", 1)], [item(9, 5, 1, 11), item(10, 6, 1, 12)])
    assert [p.pk for p in await resolve_parts(fake, LOCS, pieza_pk=6, pieza_pks=None, texto=None)] == [6]
    assert [p.pk for p in await resolve_parts(fake, LOCS, pieza_pk=None, pieza_pks=[5, 6], texto=None)] == [5, 6]
    assert [p.pk for p in await resolve_parts(fake, LOCS, pieza_pk=None, pieza_pks=None, texto="tuerca")] == [6]
    assert await resolve_parts(fake, LOCS, pieza_pk=None, pieza_pks=None, texto=None) == []


async def test_buscar_one_part_lights_its_drawers_and_sets_context():
    fake = inv([part(5, "Tornillo M3x8", 40, 100)], [item(9, 5, 40, 11)])
    r = await voz_buscar(fake, LOCS, None, NOW, pieza_pk=5)
    assert r.frase == "Están en el B1. Quedan 40. Ojo, quedan pocos: 40, y el mínimo es 100."
    assert r.iluminar == [17] and r.bajos == {17}
    assert r.contexto == Context(5, 11, NOW)
    assert r.pregunta is None and r.refrescar_bajo


async def test_buscar_part_in_two_drawers_has_no_context_drawer():
    fake = inv([part(5, "Tornillo", 50)], [item(9, 5, 30, 11), item(10, 5, 20, 12)])
    r = await voz_buscar(fake, LOCS, None, NOW, pieza_pk=5)
    assert sorted(r.iluminar) == [17, 18]
    assert r.contexto == Context(5, None, NOW)


async def test_buscar_shared_keyword_searches_all_without_asking():
    parts = [part(5, "Tornillo M3x8", 40), part(6, "Tornillo M3x12", 30), part(7, "Tornillo M4x10", 20),
             part(8, "Tornillo M5", 10)]
    fake = inv(parts, [item(9, 5, 40, 11), item(10, 6, 30, 12), item(11, 7, 20, 13), item(12, 8, 10, 11)])
    r = await voz_buscar(fake, LOCS, None, NOW, pieza_pks=[5, 6, 7, 8])
    assert r.pregunta is None
    assert sorted(r.iluminar) == [17, 18, 19]
    assert r.frase.count(" en el ") <= 3 and r.frase.endswith(".")


async def test_buscar_caps_lit_drawers_at_ten():
    locs = {100 + i: Location(100 + i, f"A{i + 1}", i) for i in range(14)}
    fake = FakeInventory([part(5, "Resistencia", 14)], [item(200 + i, 5, 1, 100 + i) for i in range(14)], list(locs.values()))
    r = await voz_buscar(fake, locs, None, NOW, pieza_pk=5)
    assert len(r.iluminar) == 10


async def test_buscar_outside_cabinet_answers_real_location():
    fake = inv([part(5, "Huevos", 12)], [item(9, 5, 12, 77)], extra_locations=[Location(77, "Nevera", None)])
    r = await voz_buscar(fake, LOCS, None, NOW, pieza_pk=5)
    assert r.frase == "Está en Nevera. Quedan 12."
    assert r.iluminar == []


async def test_buscar_unknown_text():
    r = await voz_buscar(inv([part(5, "Tornillo", 1)], [item(9, 5, 1, 11)]), LOCS, None, NOW, texto="arandela")
    assert r.frase == "No encuentro nada con ese nombre."


async def test_buscar_down():
    fake = inv([part(5, "Tornillo", 1)], [item(9, 5, 1, 11)])
    fake.fail = True
    r = await voz_buscar(fake, LOCS, None, NOW, pieza_pk=5)
    assert r.frase == DOWN_PHRASE and r.iluminar == [] and not r.refrescar_bajo


# ---- Task 2: questions and refusals

from logic.voice import Listo, plan_voice  # noqa: E402


async def plan(fake, *, accion="anadir", context=None, **kw):
    return await plan_voice(fake, LOCS, context, NOW, accion=accion, **kw)


def one_part_in_b1(qty=40):
    return inv([part(5, "Tornillo M3x8", qty)], [item(9, 5, qty, 11)])


def one_part_in_two_drawers():
    return inv([part(5, "Tornillo M3x8", 50)], [item(9, 5, 30, 11), item(10, 5, 20, 12)])


async def test_anadir_without_quantity_asks_cuantos():
    """Review Focus 1: no quantity is ever assumed."""
    r = await plan(one_part_in_b1(), pieza_pk=5)
    assert isinstance(r, VoiceResult)
    assert r.frase == "¿Cuántos?"
    assert r.pregunta == Pregunta("cantidad", pieza_pk=5, opciones=[])


async def test_anadir_with_uno_is_one():
    r = await plan(one_part_in_b1(), pieza_pk=5, uno=True)
    assert isinstance(r, Listo) and r.cantidad == 1 and r.location.name == "B1" and r.part.pk == 5


async def test_actualizar_asks_a_cuantos():
    r = await plan(one_part_in_b1(), accion="actualizar", pieza_pk=5)
    assert r.frase == "¿A cuántos?" and r.pregunta.falta == "cantidad"


async def test_zero_quantity_asks_again_except_actualizar():
    r = await plan(one_part_in_b1(), pieza_pk=5, cantidad=0)
    assert r.pregunta.falta == "cantidad"
    r = await plan(one_part_in_b1(), accion="retirar", pieza_pk=5, cantidad=-2)
    assert r.pregunta.falta == "cantidad"
    r = await plan(one_part_in_b1(), accion="actualizar", pieza_pk=5, cantidad=0)
    assert isinstance(r, Listo) and r.cantidad == 0


async def test_shared_keyword_write_asks_pieza_with_options():
    fake = inv([part(5, "Tornillo M3x8", 40), part(6, "Tornillo M3x12", 30)], [item(9, 5, 40, 11), item(10, 6, 30, 12)])
    r = await plan(fake, pieza_pks=[5, 6], cantidad=3)
    assert r.frase == "Hay Tornillo M3x8 y Tornillo M3x12. ¿Cuál?"
    assert r.pregunta == Pregunta("pieza", None, [{"pieza_pk": 5, "nombre": "Tornillo M3x8"}, {"pieza_pk": 6, "nombre": "Tornillo M3x12"}])


async def test_more_than_three_candidates_say_n_mas():
    parts = [part(5 + i, f"Tornillo {i}", 1) for i in range(5)]
    fake = inv(parts, [item(20 + i, 5 + i, 1, 11) for i in range(5)])
    r = await plan(fake, pieza_pks=[5, 6, 7, 8, 9], cantidad=1)
    assert r.frase == "Hay Tornillo 0, Tornillo 1, Tornillo 2 y 2 más. ¿Cuál?"
    assert len(r.pregunta.opciones) == 5


async def test_retirar_uses_fresh_context_part_and_drawer():
    fake = one_part_in_two_drawers()
    ctx = Context(5, 12, NOW - timedelta(seconds=30))
    r = await plan(fake, accion="retirar", context=ctx, cantidad=2)
    assert isinstance(r, Listo) and r.part.pk == 5 and r.location.name == "B3"


async def test_retirar_ignores_stale_context():
    ctx = Context(5, 11, NOW - timedelta(seconds=121))
    r = await plan(one_part_in_b1(), accion="retirar", context=ctx, cantidad=2)
    assert r.frase == "¿Qué has cogido?" and r.pregunta == Pregunta("pieza", None, [])


async def test_retirar_context_of_another_part_is_not_used_for_the_drawer():
    fake = inv([part(5, "Tornillo", 50), part(6, "Tuerca", 5)], [item(9, 5, 30, 11), item(10, 5, 20, 12), item(11, 6, 5, 13)])
    ctx = Context(6, 13, NOW - timedelta(seconds=10))
    r = await plan(fake, accion="retirar", context=ctx, pieza_pk=5, cantidad=2)
    assert r.pregunta.falta == "cajon"


async def test_explicit_drawer_not_holding_the_part_is_refused():
    """Review Focus 2: a misheard drawer is refused and re-asked, never used or created."""
    for accion in ("retirar", "actualizar"):
        fake = one_part_in_two_drawers()
        r = await plan(fake, accion=accion, pieza_pk=5, cantidad=2, cajon="C1")
        assert r.frase == "No hay Tornillo M3x8 en el C1; hay en el B1 y en el B3. ¿En cuál?"
        assert r.pregunta == Pregunta("cajon", 5, ["B1", "B3"])
        assert fake.added == [] and fake.removed == [] and fake.counted == []


async def test_unknown_drawer_name_is_refused():
    r = await plan(one_part_in_two_drawers(), pieza_pk=5, cantidad=2, cajon="Z9")
    assert r.frase == "No conozco el cajón Z9. ¿En cuál?"
    assert r.pregunta == Pregunta("cajon", 5, ["B1", "B3"])


async def test_explicit_drawer_matches_case_insensitively():
    r = await plan(one_part_in_two_drawers(), accion="retirar", pieza_pk=5, cantidad=2, cajon="b3")
    assert isinstance(r, Listo) and r.location.name == "B3"


async def test_several_drawers_ask_cajon_and_light_candidates():
    r = await plan(one_part_in_two_drawers(), pieza_pk=5, cantidad=2)
    assert r.frase == "Hay en el B1 y en el B3, ¿en cuál?"
    assert r.pregunta == Pregunta("cajon", 5, ["B1", "B3"])
    assert sorted(r.iluminar) == [17, 18]


async def test_anadir_on_part_in_no_drawer_asks_every_drawer():
    fake = inv([part(5, "Huevos", 12)], [item(9, 5, 12, 77)])
    r = await plan(fake, pieza_pk=5, cantidad=2)
    assert r.frase == "¿En qué cajón la guardo?"
    assert r.pregunta == Pregunta("cajon", 5, ["B1", "B3", "C1"])


async def test_retirar_on_part_in_no_drawer_refuses():
    fake = inv([part(5, "Huevos", 12)], [item(9, 5, 12, 77)])
    r = await plan(fake, accion="retirar", pieza_pk=5, cantidad=2)
    assert r.frase == "Hay 12 de Huevos, pero no está en ningún cajón." and r.pregunta is None


async def test_anadir_to_wrong_drawer_when_part_lives_elsewhere_is_refused_to_the_panel():
    r = await plan(one_part_in_b1(), pieza_pk=5, cantidad=2, cajon="C1")
    assert r.frase == "Tornillo M3x8 no está en el C1; está en el B1. Para ponerla ahí, hazlo desde el panel."
    assert r.pregunta is None


async def test_part_in_no_drawer_may_be_placed_in_an_explicit_drawer():
    fake = inv([part(5, "Huevos", 12)], [item(9, 5, 12, 77)])
    r = await plan(fake, pieza_pk=5, cantidad=2, cajon="C1")
    assert isinstance(r, Listo) and r.location.name == "C1"


async def test_single_drawer_part_is_chosen_automatically():
    r = await plan(one_part_in_b1(), accion="retirar", pieza_pk=5, cantidad=2)
    assert isinstance(r, Listo) and r.location.name == "B1"


async def test_unplaced_drawers_are_never_offered():
    unplaced = Location(14, "Cajón 20 (sin posición)", 20)
    locs = dict(LOCS)
    locs[14] = unplaced
    fake = FakeInventory([part(5, "Huevos", 12)], [item(9, 5, 12, 77)], list(locs.values()))
    r = await plan_voice(fake, locs, None, NOW, accion="anadir", pieza_pk=5, cantidad=2)
    assert "Cajón 20 (sin posición)" not in r.pregunta.opciones


async def test_text_that_matches_nothing():
    r = await plan(one_part_in_b1(), texto="arandela", cantidad=2)
    assert r.frase == "No encuentro nada con ese nombre." and r.pregunta is None


async def test_no_part_asks_which_for_writes():
    r = await plan(one_part_in_b1(), accion="anadir", cantidad=2)
    assert r.frase == "¿De qué producto?" and r.pregunta == Pregunta("pieza", None, [])
