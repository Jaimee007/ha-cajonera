from logic.models import Drawer, Location
from logic.sync import Create, Rename, SyncPlan, apply_sync, plan_sync
from fakes import FakeInventory


def test_new_drawer_creates_location():
    plan = plan_sync([Drawer(17, 2, 1)], [])  # x=2, y=1 -> row B, column 3
    assert plan.actions == [Create("B3", 17)]
    assert plan.notices == ["Cajón 17 añadido como B3."]


def test_already_in_sync_is_noop():
    assert plan_sync([Drawer(17, 2, 1)], [Location(1, "B3", 17)]) == SyncPlan([], [])


def test_move_renames_through_temp_name():
    plan = plan_sync([Drawer(17, 0, 2)], [Location(1, "B1", 17)])
    assert plan.actions == [Rename(1, "_dir17"), Rename(1, "C1")]
    assert plan.notices == ["Cajón 17: B1 → C1."]


def test_swap_uses_temp_names_first():
    plan = plan_sync([Drawer(17, 0, 1), Drawer(18, 0, 2)], [Location(1, "B1", 18), Location(2, "C1", 17)])
    assert plan.actions == [Rename(1, "_dir18"), Rename(2, "_dir17"), Rename(1, "C1"), Rename(2, "B1")]


def test_missing_drawer_becomes_unplaced():
    plan = plan_sync([], [Location(1, "B3", 17)])
    assert plan.actions == [Rename(1, "_dir17"), Rename(1, "Cajón 17 (sin posición)")]
    assert plan.notices == ["Cajón 17 desconectado: su ubicación pasa a «Cajón 17 (sin posición)»."]


def test_missing_drawer_already_unplaced_is_noop():
    assert plan_sync([], [Location(1, "Cajón 17 (sin posición)", 17)]) == SyncPlan([], [])


def test_drawer_without_position_becomes_unplaced():
    plan = plan_sync([Drawer(17, None, None)], [Location(1, "B3", 17)])
    assert plan.actions == [Rename(1, "_dir17"), Rename(1, "Cajón 17 (sin posición)")]


def test_new_drawer_without_position_is_created_unplaced():
    plan = plan_sync([Drawer(17, None, None)], [])
    assert plan.actions == [Create("Cajón 17 (sin posición)", 17)]


def test_returning_drawer_gets_its_grid_name_back():
    plan = plan_sync([Drawer(17, 2, 1)], [Location(1, "Cajón 17 (sin posición)", 17)])
    assert plan.actions == [Rename(1, "_dir17"), Rename(1, "B3")]


def test_error_drawer_is_skipped_with_notice():
    plan = plan_sync([Drawer(17, 2, 1, error="duplicada")], [Location(1, "B1", 17)])
    assert plan.actions == []
    assert plan.notices == ["Cajón 17: error de montaje (duplicada), no se sincroniza."]


def test_outside_grid_is_skipped():
    plan = plan_sync([Drawer(5, -1, 0)], [])
    assert plan.actions == []
    assert plan.notices == ["Cajón 5: error de montaje (fuera_reticula), no se sincroniza."]


def test_location_without_address_is_ignored():
    assert plan_sync([], [Location(1, "Otra cosa", None)]) == SyncPlan([], [])


async def test_apply_sync_calls_api_in_order():
    fake = FakeInventory(locations=[Location(1, "B1", 18), Location(2, "C1", 17)])
    plan = plan_sync([Drawer(17, 0, 1), Drawer(18, 0, 2), Drawer(19, 1, 0)], list(fake.locations.values()))
    await apply_sync(fake, FakeInventory.ROOT_PK, plan)
    assert fake.renamed == [(1, "_dir18"), (2, "_dir17"), (1, "C1"), (2, "B1")]
    assert [(loc.name, loc.address) for loc in fake.created] == [("A2", 19)]


def test_location_left_without_address_is_adopted_not_duplicated():
    """A sync that died between creating a location and saving its address leaves an
    address-less location with the drawer's name. The next sync must adopt it."""
    from logic.sync import SetAddress
    plan = plan_sync([Drawer(0, 0, 0), Drawer(3, 1, 0)], [Location(2, "A1", None)])
    assert plan.actions == [SetAddress(2, 0), Create("A2", 3)]
    assert plan.notices == ["Cajón 0 asociado a la ubicación A1.", "Cajón 3 añadido como A2."]


def test_unrelated_location_without_address_is_not_adopted():
    plan = plan_sync([Drawer(0, 0, 0)], [Location(2, "Estantería", None)])
    assert plan.actions == [Create("A1", 0)]


async def test_apply_sync_sets_address():
    from logic.sync import SetAddress
    fake = FakeInventory(locations=[Location(2, "A1", None)])
    await apply_sync(fake, FakeInventory.ROOT_PK, SyncPlan([SetAddress(2, 0)], []))
    assert fake.locations[2] == Location(2, "A1", 0)
