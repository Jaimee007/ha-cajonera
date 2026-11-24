import asyncio

import pytest

from logic.models import Drawer
from logic.simulator import DrawerSimulator

FAST = dict(search_seconds=0.1, found_seconds=0.05, flash_seconds=0.05, identify_seconds=0.05)
DRAWERS = [Drawer(0, 0, 0), Drawer(17, 0, 1, width=2), Drawer(21, 2, 1)]


@pytest.fixture
async def sim():
    return DrawerSimulator(DRAWERS, **FAST)


def state(sim, address):
    return next(s for s in sim.states() if s.drawer.address == address)


async def test_states_carry_names_and_start_idle(sim):
    assert [(s.drawer.address, s.name, s.mode, s.rgb) for s in sim.states()] == [
        (0, "A1", "reposo", (0, 0, 0)), (17, "B1", "reposo", (0, 0, 0)), (21, "B3", "reposo", (0, 0, 0))]
    assert sim.drawers() == DRAWERS


async def test_search_lights_blue_then_times_out(sim):
    sim.search([17], low=set())
    assert (state(sim, 17).mode, state(sim, 17).rgb) == ("buscado", (0, 0, 255))
    assert state(sim, 0).mode == "reposo"
    await asyncio.sleep(0.15)
    assert state(sim, 17).mode == "reposo"


async def test_new_search_replaces_old(sim):
    sim.search([17], low=set())
    sim.search([21], low=set())
    assert state(sim, 17).mode == "reposo"
    assert state(sim, 21).mode == "buscado"


async def test_low_drawer_uses_low_mode(sim):
    sim.search([17, 21], low={21})
    assert state(sim, 17).mode == "buscado"
    assert (state(sim, 21).mode, state(sim, 21).rgb) == ("buscado_bajo", (255, 120, 0))


async def test_opening_searched_drawer_shows_found_then_white_and_cancels_others(sim):
    sim.search([17, 21], low=set())
    sim.set_open(17, True)
    assert (state(sim, 17).mode, state(sim, 17).rgb) == ("encontrado", (0, 255, 0))
    assert state(sim, 21).mode == "reposo"
    await asyncio.sleep(0.08)
    assert (state(sim, 17).mode, state(sim, 17).rgb) == ("abierto", (255, 255, 255))
    sim.set_open(17, False)
    assert state(sim, 17).mode == "reposo"


async def test_opening_another_drawer_keeps_search(sim):
    sim.search([17], low=set())
    sim.set_open(0, True)
    assert state(sim, 0).mode == "abierto"
    assert state(sim, 17).mode == "buscado"


async def test_open_beats_search_and_confirm(sim):
    sim.set_open(21, True)
    sim.search([21], low=set())
    sim.confirm(21, low=False)
    assert state(sim, 21).mode == "abierto"


async def test_confirm_returns_to_previous_mode(sim):
    sim.search([17], low=set())
    sim.confirm(17, low=True)
    assert (state(sim, 17).mode, state(sim, 17).rgb) == ("confirmar_bajo", (255, 120, 0))
    await asyncio.sleep(0.08)
    assert state(sim, 17).mode == "buscado"
    sim.confirm(0, low=False)
    assert (state(sim, 0).mode, state(sim, 0).rgb) == ("confirmar_ok", (0, 255, 0))
    await asyncio.sleep(0.08)
    assert state(sim, 0).mode == "reposo"


async def test_identify_then_back(sim):
    sim.identify(0)
    assert (state(sim, 0).mode, state(sim, 0).rgb) == ("identificar", (255, 0, 255))
    await asyncio.sleep(0.08)
    assert state(sim, 0).mode == "reposo"


async def test_listener_called_on_change(sim):
    calls = []
    sim.add_listener(lambda: calls.append(1))
    sim.search([17], low=set())
    sim.set_open(0, True)
    assert len(calls) == 2


async def test_unknown_address_is_ignored(sim):
    sim.search([99], low=set())
    sim.confirm(99, low=False)
    sim.set_open(99, True)
    assert all(s.mode == "reposo" for s in sim.states())


async def test_store_here_lights_magenta_until_opened(sim):
    sim.store_here(17, seconds=5)
    assert (state(sim, 17).mode, state(sim, 17).rgb) == ("guardar", (255, 0, 255))
    assert state(sim, 0).mode == "reposo"
    sim.set_open(17, True)
    # Nothing was being searched: white straight away, no green "encontrado".
    assert (state(sim, 17).mode, state(sim, 17).rgb) == ("abierto", (255, 255, 255))
    sim.set_open(17, False)
    assert state(sim, 17).mode == "reposo"


async def test_store_here_replaced_by_search(sim):
    sim.store_here(17, seconds=5)
    sim.search([21], low=set())
    assert state(sim, 17).mode == "reposo"
    assert state(sim, 21).mode == "buscado"


async def test_store_here_replaces_previous_store_here_and_keeps_search_timer_clean(sim):
    sim.store_here(17, seconds=5)
    sim.store_here(21, seconds=5)
    assert state(sim, 17).mode == "reposo"
    assert state(sim, 21).mode == "guardar"
    sim.search([0], low=set())
    await asyncio.sleep(0.15)  # the search timer ends the search; the old store timer must not fire later
    assert all(s.mode == "reposo" for s in sim.states())


async def test_store_here_times_out(sim):
    sim.store_here(17, seconds=0.05)
    assert state(sim, 17).mode == "guardar"
    await asyncio.sleep(0.1)
    assert state(sim, 17).mode == "reposo"


async def test_store_here_unknown_address_is_ignored(sim):
    sim.store_here(99)
    assert all(s.mode == "reposo" for s in sim.states())
