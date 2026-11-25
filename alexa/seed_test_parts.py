"""Create a small set of TEST parts in InvenTree so the Alexa model generator has something to read.

Run it by hand, once:  python alexa/seed_test_parts.py --env path/to/.env.local
It creates categories and parts through the integration's own code (logic/catalog.py), so it also
exercises that code against a real InvenTree. Every part gets "[prueba]" in its description, so the
test data can be filtered and deleted from InvenTree's web UI. Existing parts are never touched or
duplicated: a part whose name already exists is skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "cajonera"))

from logic.catalog import create_category, create_part, list_categories  # noqa: E402
from logic.inventree import InvenTreeClient  # noqa: E402

TAG = "[prueba]"

# category -> list of (name, quantity, minimum, keywords, address of the drawer)
# Drawer addresses are the ones of the simulated cabinet: 3 = A2, 4 = A3, 17 = B1, 21 = B3.
PARTS = {
    "Resistencias": [
        ("Resistencia 100 ohmios", 50, 20, "resistencias, resistencia, 100 ohm", 4),
        ("Resistencia 220 ohmios", 50, 20, "resistencias, resistencia, 220 ohm", 4),
        ("Resistencia 1k", 80, 20, "resistencias, resistencia, 1k, mil ohmios", 4),
        ("Resistencia 4k7", 60, 20, "resistencias, resistencia, 4k7, cuatro con siete", 4),
        ("Resistencia 10k", 100, 30, "resistencias, resistencia, 10k, diez kilos", 4),
        ("Resistencia 100k", 40, 20, "resistencias, resistencia, 100k", 4),
    ],
    "Condensadores": [
        ("Condensador 100nF", 100, 30, "condensadores, condensador, 100 nano, cien nanos", 3),
        ("Condensador 1uF", 60, 20, "condensadores, condensador, 1 micro", 3),
        ("Condensador 10uF", 40, 15, "condensadores, condensador, 10 micro", 3),
        ("Condensador 470uF", 15, 5, "condensadores, condensador, 470 micro, electrolitico", 3),
    ],
    "Tornillería": [
        ("Tornillo M3x8", 120, 40, "tornillos, tornillo, m3, m3 por 8", 21),
        ("Tornillo M3x12", 80, 30, "tornillos, tornillo, m3, m3 por 12", 21),
        ("Tornillo M4x10", 60, 20, "tornillos, tornillo, m4, m4 por 10", 21),
        ("Tuerca M3", 150, 50, "tuercas, tuerca, m3", 21),
        ("Arandela M3", 150, 50, "arandelas, arandela, m3", 21),
    ],
    "Cables": [
        ("Cable dupont macho-hembra", 40, 10, "cables, cable, dupont, jumper", 17),
        ("Cable plano IDC 6 hilos", 12, 4, "cables, cable, cable plano, idc", 17),
        ("Cable USB-C", 5, 2, "cables, cable, usb c, usb tipo c", 17),
        ("Hilo de estaño", 3, 1, "estaño, hilo de estaño, soldadura", 17),
    ],
    "Electrónica": [
        ("LED rojo 5mm", 60, 20, "leds, led, led rojo, diodo led", 3),
        ("LED RGB", 25, 10, "leds, led, led rgb", 3),
        ("Pila CR2032", 8, 3, "pilas, pila, botón, cr2032", 21),
        ("Pila AA", 12, 4, "pilas, pila, aa", 21),
    ],
}


def load_env(path: Path) -> tuple[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env["INVENTREE_URL"].rstrip("/"), env["INVENTREE_TOKEN"]


async def main(env_path: Path, dry_run: bool) -> int:
    url, token = load_env(env_path)
    headers = {"User-Agent": "Python/3.13 aiohttp/3.14.5"}  # the Cloudflare tunnel rejects urllib's default
    async with aiohttp.ClientSession(headers=headers) as session:
        api = InvenTreeClient(session, url, token, timeout=10)
        root = await api.ensure_root()
        locations = {loc.pk: loc for loc in await api.child_locations(root)}
        by_address = {loc.address: loc.name for loc in locations.values() if loc.address is not None}
        needed = {p[4] for parts in PARTS.values() for p in parts}
        missing = needed - set(by_address)
        if missing:
            print(f"These drawer addresses are not in InvenTree yet: {sorted(missing)}. Run the sync first.")
            return 1
        total = sum(len(parts) for parts in PARTS.values())
        print(f"{len(PARTS)} categories, {total} parts, drawers: {sorted(by_address[a] for a in needed)}")
        if dry_run:
            print("dry run: nothing written")
            return 0

        categories = {c["nombre"].casefold(): c["pk"] for c in await list_categories(api)}
        created = skipped = 0
        for category, parts in PARTS.items():
            pk = categories.get(category.casefold())
            if pk is None:
                row = await create_category(api, category, None)
                pk = row.get("pk")
                if pk is None:
                    print(f"  category {category}: {row.get('frase')}")
                    return 1
                print(f"  + category {category}")
            for name, qty, minimum, keywords, address in parts:
                result = await create_part(
                    api, name, pk, address, qty, locations, description=f"{TAG} {category}", keywords=keywords, minimum=minimum
                )
                if result.done:
                    created += 1
                    print(f"  + {name} x{qty} in {by_address[address]}")
                elif result.part_pk is not None:
                    skipped += 1
                    print(f"  = {name} (already there)")
                else:
                    print(f"  ! {name}: {result.phrase}")
                    return 1
        print(f"done: {created} created, {skipped} already existed")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", type=Path, required=True, help="file with INVENTREE_URL and INVENTREE_TOKEN")
    parser.add_argument("--dry-run", action="store_true", help="check the drawers exist and print the plan; write nothing")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.env, args.dry_run)))
