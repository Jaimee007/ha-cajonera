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
