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


async def test_clear_store_clears_only_the_store_light(sim):
    sim.clear_store()  # nothing lit: no-op
    sim.store_here(17)
    sim.clear_store()
    assert all(s.mode == "reposo" for s in sim.states())
    assert sim._store_timer is None
    sim.search([21], low=set())
    sim.clear_store()
    assert state(sim, 21).mode == "buscado"


async def test_search_with_no_drawers_clears_lights_and_arms_no_timer(sim):
    sim.store_here(17)
    sim.search([], low=set())
    assert all(s.mode == "reposo" for s in sim.states())
    assert sim._search_timer is None and sim._store_timer is None


# Selection: the drawer tapped on the card

async def test_select_lights_only_that_drawer_and_puts_every_order_light_out(sim):
    sim.search([0, 21], low={21})
    sim.select(17)
    assert (state(sim, 17).mode, state(sim, 17).rgb) == ("seleccionado", (0, 200, 255))
    assert state(sim, 0).mode == "reposo" and state(sim, 21).mode == "reposo"
    sim.store_here(21, seconds=5)
    sim.select(17)
    assert [s.mode for s in sim.states()] == ["reposo", "seleccionado", "reposo"]


async def test_select_ends_after_its_time():
    sim = DrawerSimulator(DRAWERS, select_seconds=0.05, **FAST)
    sim.select(17)
    assert state(sim, 17).mode == "seleccionado"
    await asyncio.sleep(0.1)
    assert state(sim, 17).mode == "reposo"
    assert all(s.mode == "reposo" for s in sim.states())


async def test_select_again_renews_the_time():
    sim = DrawerSimulator(DRAWERS, select_seconds=0.1, **FAST)
    sim.select(17)
    await asyncio.sleep(0.06)
    sim.select(17)  # renewed: the first timer would have ended it at 0.1
    await asyncio.sleep(0.06)  # t = 0.12
    assert state(sim, 17).mode == "seleccionado"
    await asyncio.sleep(0.08)  # t = 0.20, past the renewed end at 0.16
    assert state(sim, 17).mode == "reposo"


async def test_select_another_drawer_moves_the_light():
    sim = DrawerSimulator(DRAWERS, select_seconds=0.1, **FAST)
    sim.select(17)
    await asyncio.sleep(0.06)
    sim.select(21)
    assert [s.mode for s in sim.states()] == ["reposo", "reposo", "seleccionado"]
    await asyncio.sleep(0.06)  # the first timer must not put the new one out
    assert state(sim, 21).mode == "seleccionado"


async def test_search_replaces_a_selection_and_cancels_its_timer():
    sim = DrawerSimulator(DRAWERS, select_seconds=0.05, search_seconds=5, found_seconds=0.05,
                          flash_seconds=0.05, identify_seconds=0.05)
    sim.select(17)
    sim.search([21], low=set())
    assert [s.mode for s in sim.states()] == ["reposo", "reposo", "buscado"]
    assert sim._select_timer is None
    await asyncio.sleep(0.1)
    assert state(sim, 21).mode == "buscado"


async def test_store_here_replaces_a_selection_and_cancels_its_timer():
    sim = DrawerSimulator(DRAWERS, select_seconds=0.05, **FAST)
    sim.select(17)
    sim.store_here(21, seconds=5)
    assert [s.mode for s in sim.states()] == ["reposo", "reposo", "guardar"]
    assert sim._select_timer is None
    await asyncio.sleep(0.1)
    assert state(sim, 21).mode == "guardar"


async def test_select_replaces_a_search_and_its_timer_does_not_fire_later():
    sim = DrawerSimulator(DRAWERS, select_seconds=5, search_seconds=0.05, found_seconds=0.05,
                          flash_seconds=0.05, identify_seconds=0.05)
    sim.search([21], low=set())
    sim.select(17)
    assert sim._search_timer is None
    await asyncio.sleep(0.1)
    assert state(sim, 17).mode == "seleccionado"


async def test_opening_the_selected_drawer_is_white_and_ends_the_selection(sim):
    sim.select(17)
    sim.set_open(17, True)
    assert (state(sim, 17).mode, state(sim, 17).rgb) == ("abierto", (255, 255, 255))
    assert sim._select_timer is None
    sim.set_open(17, False)
    assert state(sim, 17).mode == "reposo"  # no green "encontrado", and the light stays out


async def test_opening_another_drawer_leaves_the_selection(sim):
    sim.select(17)
    sim.set_open(21, True)
    assert state(sim, 21).mode == "abierto"
    assert state(sim, 17).mode == "seleccionado"


async def test_open_drawer_stays_white_when_selected(sim):
    sim.set_open(17, True)
    sim.select(17)
    assert state(sim, 17).mode == "abierto"


async def test_clear_selection_leaves_a_search_in_progress_alone(sim):
    sim.clear_selection()  # nothing lit: no-op
    sim.select(17)
    sim.clear_selection()
    assert all(s.mode == "reposo" for s in sim.states())
    assert sim._select_timer is None
    sim.search([21], low=set())
    sim.clear_selection()
    assert state(sim, 21).mode == "buscado"
    sim.store_here(0, seconds=5)
    sim.clear_selection()
    assert state(sim, 0).mode == "guardar"


async def test_select_unknown_address_puts_everything_to_rest_without_a_timer(sim):
    sim.search([17], low=set())
    sim.select(99)
    assert all(s.mode == "reposo" for s in sim.states())
    assert sim._select_timer is None and sim._search_timer is None


async def test_identify_still_a_short_overlay(sim):
    sim.select(21)
    sim.identify(17)
    assert state(sim, 17).mode == "identificar"
    assert state(sim, 21).mode == "seleccionado"
    await asyncio.sleep(0.1)
    assert state(sim, 17).mode == "reposo"
