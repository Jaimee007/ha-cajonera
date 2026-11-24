"""Keep InvenTree locations in step with the physical drawers (spec section 5).

A location is tied to a drawer by its address (metadata cajonera.direccion),
never by name; the name follows the position. Locations are never deleted:
they may hold stock.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Drawer, Location
from .naming import drawer_name, temp_name, unplaced_name


@dataclass(frozen=True)
class Create:
    name: str
    address: int


@dataclass(frozen=True)
class Rename:
    pk: int
    name: str


@dataclass(frozen=True)
class SyncPlan:
    actions: list[Create | Rename] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


def _wanted_name(drawer: Drawer) -> str | None:
    """Target name, or None if the drawer must not be synced (assembly error)."""
    if drawer.error:
        return None
    if drawer.x is None or drawer.y is None:
        return unplaced_name(drawer.address)
    return drawer_name(drawer.x, drawer.y)


def plan_sync(drawers: list[Drawer], locations: list[Location]) -> SyncPlan:
    by_address = {loc.address: loc for loc in locations if loc.address is not None}
    creates: list[Create] = []
    renames: list[tuple[Location, str]] = []
    notices: list[str] = []
    seen: set[int] = set()

    for drawer in drawers:
        seen.add(drawer.address)
        wanted = _wanted_name(drawer)
        if wanted is None:
            error = drawer.error or "fuera_reticula"
            notices.append(f"Cajón {drawer.address}: error de montaje ({error}), no se sincroniza.")
            continue
        loc = by_address.get(drawer.address)
        if loc is None:
            creates.append(Create(wanted, drawer.address))
            notices.append(f"Cajón {drawer.address} añadido como {wanted}.")
        elif loc.name != wanted:
            renames.append((loc, wanted))
            notices.append(f"Cajón {drawer.address}: {loc.name} → {wanted}.")

    for address, loc in by_address.items():
        if address in seen:
            continue
        wanted = unplaced_name(address)
        if loc.name != wanted:
            renames.append((loc, wanted))
            notices.append(f"Cajón {address} desconectado: su ubicación pasa a «{wanted}».")

    renames.sort(key=lambda r: r[0].pk)  # deterministic order
    # Two passes so that two drawers swapping places never share a name.
    actions: list[Create | Rename] = [Rename(loc.pk, temp_name(loc.address)) for loc, _ in renames]
    actions += [Rename(loc.pk, wanted) for loc, wanted in renames]
    actions += creates
    return SyncPlan(actions, notices)


async def apply_sync(api, root_pk: int, plan: SyncPlan) -> None:
    for action in plan.actions:
        if isinstance(action, Rename):
            await api.rename_location(action.pk, action.name)
        else:
            await api.create_location(root_pk, action.name, action.address)
