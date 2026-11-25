import pytest

from logic.catalog import (
    MAX_SUGGESTIONS,
    create_category,
    create_part,
    edit_part,
    list_categories,
    part_sheet,
    suggest,
    suggest_drawer,
)
from logic.models import Category, Drawer, Location, Part, StockItem
from logic.search import DOWN_PHRASE
from logic.stock_ops import MAYBE_STOCK, OpResult
from fakes import FakeInventory

A1 = Location(10, "A1", 16)
A2 = Location(11, "A2", 17)
A10 = Location(12, "A10", 18)
B1 = Location(13, "B1", 19)
LOST = Location(14, "Cajón 20 (sin posición)", 20)
LOCS = {loc.pk: loc for loc in (A1, A2, A10, B1, LOST)}
DRAWERS = [Drawer(a, 0, 0) for a in (16, 17, 18, 19, 20)]

CATS = [
    Category(1, "Ferreteria", "Ferreteria"),
    Category(2, "Tornillos", "Ferreteria/Tornillos"),
    Category(3, "Electronica", "Electronica"),
]


def part(pk, name, cat=2, total=0.0, minimum=0.0, **kw):
    return Part(pk, name, minimum, total, category_pk=cat, **kw)


def stock(pk, part_pk, qty, loc, name="x"):
    return StockItem(pk, part_pk, name, float(qty), loc, delete_on_deplete=False)


def fake(parts=(), items=(), categories=CATS):
    return FakeInventory(list(parts), list(items), list(LOCS.values()), list(categories))


# suggest


async def test_suggest_short_text_does_not_call_api():
    f = fake([part(5, "Tornillo")])
    assert await suggest(f, " t ", LOCS) == []
    assert await suggest(f, "", LOCS) == []
    assert f.search_calls == 0


async def test_suggest_rows_have_drawers_and_total():
    f = fake(
        [part(5, "Tornillo M3", total=50, ipn="TM3")],
        [stock(1, 5, 30, 10), stock(2, 5, 20, 13), stock(3, 5, 5, 99)],
    )
    rows = await suggest(f, "  torn ", LOCS)
    assert rows == [
        {
            "pieza_pk": 5,
            "nombre": "Tornillo M3",
            "ipn": "TM3",
            "categoria_pk": 2,
            "total": 50.0,
            "cajones": ["A1", "B1"],
            "direcciones": [16, 19],
            "bajo": False,
        }
    ]


async def test_suggest_addresses_are_sorted_and_skip_unplaced_drawers():
    # B1 (19) is listed before A1 (16) in the stock, and the part is also in an unplaced drawer (20)
    f = fake([part(5, "Tornillo M3")], [stock(1, 5, 5, 13), stock(2, 5, 5, 10), stock(3, 5, 5, 14)])
    rows = await suggest(f, "tornillo", LOCS)
    assert rows[0]["direcciones"] == [16, 19]
    assert rows[0]["cajones"] == ["A1", "B1", "Cajón 20 (sin posición)"]


async def test_suggest_row_says_whether_the_part_is_low():
    f = fake([part(5, "Tornillo M3", total=3, minimum=10), part(6, "Tornillo M4", total=30, minimum=10)])
    rows = await suggest(f, "tornillo", LOCS)
    assert {r["pieza_pk"]: r["bajo"] for r in rows} == {5: True, 6: False}


async def test_suggest_includes_parts_without_drawer():
    f = fake([part(5, "Tornillo M3", total=7)], [stock(3, 5, 7, 99)])
    rows = await suggest(f, "tornillo", LOCS)
    assert rows[0]["cajones"] == []
    assert rows[0]["direcciones"] == []
    assert rows[0]["total"] == 7.0


async def test_suggest_is_capped():
    f = fake([part(i, f"Tornillo {i}") for i in range(1, 20)])
    assert len(await suggest(f, "tornillo", LOCS)) == MAX_SUGGESTIONS


async def test_suggest_down_returns_empty():
    f = fake([part(5, "Tornillo")])
    f.fail = True
    assert await suggest(f, "tornillo", LOCS) == []


# part_sheet


async def test_part_sheet_sums_per_drawer_and_reports_outside():
    f = fake(
        [part(5, "Tornillo M3", total=63, minimum=100, description="d", keywords="k", ipn="I")],
        [stock(1, 5, 10, 10), stock(2, 5, 3, 10), stock(3, 5, 40, 13), stock(4, 5, 10, 99), stock(5, 5, 0, 99)],
    )
    sheet = await part_sheet(f, 5, LOCS)
    assert sheet == {
        "pieza_pk": 5,
        "nombre": "Tornillo M3",
        "descripcion": "d",
        "categoria_pk": 2,
        "palabras_clave": "k",
        "ipn": "I",
        "minimo": 100.0,
        "total": 63.0,
        "bajo": True,
        "cajones": [
            {"cajon": "B1", "direccion": 19, "cantidad": 40.0},
            {"cajon": "A1", "direccion": 16, "cantidad": 13.0},
        ],
        "fuera": 10.0,
    }


async def test_part_sheet_keeps_a_drawer_emptied_to_zero():
    f = fake([part(5, "x", total=0)], [stock(1, 5, 0, 10)])
    sheet = await part_sheet(f, 5, LOCS)
    assert sheet["cajones"] == [{"cajon": "A1", "direccion": 16, "cantidad": 0.0}]


async def test_part_sheet_down():
    f = fake([part(5, "x")])
    f.fail = True
    assert await part_sheet(f, 5, LOCS) == {"frase": DOWN_PHRASE}


# suggest_drawer


async def test_suggest_drawer_prefers_category_then_first_empty():
    f = fake(
        [part(5, "a", cat=2), part(6, "b", cat=3)],
        [stock(1, 5, 4, 12), stock(2, 5, 4, 12), stock(3, 5, 4, 11), stock(4, 6, 9, 10)],
    )
    r = await suggest_drawer(f, 2, 1, DRAWERS, LOCS)
    assert r == {"direccion": 18, "cajon": "A10", "motivo": "Ya hay piezas de esta categoría en el A10."}
    # No stock of category 1: A1, A2 and A10 are taken, so the first empty drawer is B1.
    r = await suggest_drawer(f, 1, 1, DRAWERS, LOCS)
    assert r == {"direccion": 19, "cajon": "B1", "motivo": "Primer cajón vacío."}


async def test_suggest_drawer_category_tie_picks_lowest_address():
    f = fake([part(5, "a")], [stock(1, 5, 4, 13), stock(2, 5, 4, 11)])
    r = await suggest_drawer(f, 2, 1, DRAWERS, LOCS)
    assert r["direccion"] == 17


async def test_suggest_drawer_first_empty_sorts_numerically():
    f = fake([part(5, "a", cat=3)], [stock(1, 5, 4, 10), stock(2, 5, 4, 11)])
    r = await suggest_drawer(f, 2, 1, DRAWERS, LOCS)
    assert (r["cajon"], r["motivo"]) == ("A10", "Primer cajón vacío.")


async def test_suggest_drawer_ignores_zero_stock_and_drawers_missing_from_simulator():
    # A1 holds only a zero-quantity item, so it counts as empty.
    f = fake([part(5, "a", cat=3)], [stock(1, 5, 0, 10)])
    r = await suggest_drawer(f, 2, 1, DRAWERS, LOCS)
    assert r["cajon"] == "A1"
    # Drawer 16 (A1) does not physically exist: the next empty one is A2.
    r = await suggest_drawer(f, 2, 1, [d for d in DRAWERS if d.address != 16], LOCS)
    assert r["cajon"] == "A2"


async def test_suggest_drawer_nothing_free_asks_to_choose():
    # Every placed drawer is occupied; the unplaced one is not a candidate.
    f = fake([part(5, "a", cat=3)], [stock(1, 5, 4, 10), stock(2, 5, 4, 11), stock(3, 5, 4, 12), stock(4, 5, 4, 13)])
    r = await suggest_drawer(f, 1, 1, DRAWERS, LOCS)
    assert r == {"direccion": None, "cajon": None, "motivo": "Elige un cajón."}


async def test_suggest_drawer_down():
    f = fake()
    f.fail = True
    r = await suggest_drawer(f, 2, 1, DRAWERS, LOCS)
    assert r == {"direccion": None, "cajon": None, "motivo": DOWN_PHRASE}


# create_part


async def test_create_part_ok_keeps_stock_and_reports_low():
    f = fake()
    r = await create_part(f, "  Tornillo M3  ", 2, 17, 25, LOCS, ipn="TM3", minimum=10)
    new = f.created_parts[0]
    assert new.name == "Tornillo M3" and new.ipn == "TM3"
    assert r == OpResult(
        "Hecho. He creado Tornillo M3 con 25 en el A2.", done=True, address=17, low=False, part_pk=new.pk, location_pk=11
    )
    [(stock_pk, patch)] = f.patched
    assert patch == {"delete_on_deplete": False} and f.stock[stock_pk].part_pk == new.pk


async def test_create_part_low_uses_reread_part():
    f = fake()
    r = await create_part(f, "Tornillo", 2, 17, 5, LOCS, minimum=10)
    assert r.low is True


async def test_create_part_low_is_false_when_reread_fails():
    f = fake()
    f.fail_on.add("get_part")
    r = await create_part(f, "Tornillo", 2, 17, 5, LOCS, minimum=10)
    assert r.done and r.low is False


async def test_create_part_empty_name():
    f = fake()
    assert (await create_part(f, "   ", 2, 17, 5, LOCS)).phrase == "Dime un nombre para la pieza."
    assert f.created_parts == []


@pytest.mark.parametrize("name", ["tornillo m3", "TORNILLO M3", "Tornillo M3"])
async def test_create_part_duplicate_name_any_case(name):
    f = fake([part(5, "Tornillo M3")])
    r = await create_part(f, name, 2, 17, 5, LOCS)
    assert r == OpResult("Ya existe «Tornillo M3». Ábrela para añadir.", part_pk=5)
    assert f.created_parts == []


async def test_create_part_similar_name_is_not_duplicate():
    f = fake([part(5, "Tornillo M3 largo")])
    r = await create_part(f, "Tornillo M3", 2, 17, 5, LOCS)
    assert r.done


@pytest.mark.parametrize("qty", [0, -1, None])
async def test_create_part_invalid_quantity(qty):
    f = fake()
    assert (await create_part(f, "T", 2, 17, qty, LOCS)).phrase == "Dime una cantidad válida."
    assert f.created_parts == []


async def test_create_part_unknown_address():
    f = fake()
    assert (await create_part(f, "T", 2, 99, 5, LOCS)).phrase == "Ese cajón no está en el inventario."
    assert f.created_parts == []


async def test_create_part_down_before_write():
    f = fake()
    f.fail = True
    assert await create_part(f, "T", 2, 17, 5, LOCS) == OpResult(DOWN_PHRASE)


async def test_create_part_error_after_write_says_maybe():
    f = fake()
    f.fail_on.add("set_keep_when_empty")
    assert await create_part(f, "T", 2, 17, 5, LOCS) == OpResult(MAYBE_STOCK)


# edit_part


async def test_edit_part_maps_fields():
    f = fake([part(5, "Tornillo")])
    r = await edit_part(
        f,
        5,
        {"nombre": " Tornillo M4 ", "descripcion": "d", "categoria": 3, "palabras_clave": "k", "ipn": "I", "minimo": "12"},
    )
    assert r.phrase == "Hecho. He guardado los cambios." and r.done and r.part_pk == 5
    assert f.patched == [
        (5, {"name": "Tornillo M4", "description": "d", "category": 3, "keywords": "k", "IPN": "I", "minimum_stock": 12.0})
    ]


async def test_edit_part_ignores_unknown_keys():
    f = fake([part(5, "Tornillo")])
    r = await edit_part(f, 5, {"cantidad": 3, "ipn": "I"})
    assert r.done and f.patched == [(5, {"IPN": "I"})]


async def test_edit_part_empty_name_refused():
    f = fake([part(5, "Tornillo")])
    r = await edit_part(f, 5, {"nombre": "  ", "ipn": "I"})
    assert r == OpResult("Dime un nombre para la pieza.")
    assert f.patched == []


async def test_edit_part_down():
    f = fake([part(5, "Tornillo")])
    f.fail = True
    assert await edit_part(f, 5, {"ipn": "I"}) == OpResult(DOWN_PHRASE)


# categories


async def test_list_categories_sorted_by_path():
    f = fake()
    rows = await list_categories(f)
    assert rows == [
        {"pk": 3, "nombre": "Electronica", "ruta": "Electronica"},
        {"pk": 1, "nombre": "Ferreteria", "ruta": "Ferreteria"},
        {"pk": 2, "nombre": "Tornillos", "ruta": "Ferreteria/Tornillos"},
    ]


async def test_list_categories_down():
    f = fake()
    f.fail = True
    assert await list_categories(f) == []


async def test_create_category_new_under_parent():
    f = fake()
    row = await create_category(f, " Tuercas ", 1)
    assert row == {"pk": f.created_categories[0].pk, "nombre": "Tuercas", "ruta": "Ferreteria/Tuercas"}


async def test_create_category_existing_same_parent_is_returned():
    f = fake()
    row = await create_category(f, "tornillos", 1)
    assert row == {"pk": 2, "nombre": "Tornillos", "ruta": "Ferreteria/Tornillos"}
    assert f.created_categories == []


async def test_create_category_same_name_other_parent_is_created():
    f = fake()
    row = await create_category(f, "Tornillos", None)
    assert row["ruta"] == "Tornillos" and len(f.created_categories) == 1


async def test_create_category_top_level_existing():
    f = fake()
    row = await create_category(f, "ferreteria", None)
    assert row["pk"] == 1 and f.created_categories == []


async def test_create_category_empty_name():
    f = fake()
    assert await create_category(f, "  ", None) == {"frase": "Dime un nombre para la categoría."}


async def test_create_category_down():
    f = fake()
    f.fail = True
    assert await create_category(f, "X", None) == {"frase": DOWN_PHRASE}
