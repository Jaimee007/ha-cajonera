"""What is said aloud when stock is changed by hand on the card."""

from datetime import datetime

from logic.announce import spoken_action
from logic.models import Drawer, Part, StockItem
from logic.service import Cajonera
from logic.simulator import DrawerSimulator
from fakes import FakeInventory

NOW = datetime(2026, 11, 26, 12, 0, 0)


def test_spoken_actions_name_part_quantity_and_drawer():
    assert spoken_action("retirar", "Tornillo M3x8", "B3", 4, "Hecho. Quedan 113.") == "He retirado 4 de Tornillo M3x8 del B3. Quedan 113."
    assert spoken_action("anadir", "Huevos", "B1", 6, "Hecho. Ahora hay 18 en el B1.") == "He añadido 6 de Huevos al B1. Ahora hay 18 en el B1."
    assert spoken_action("actualizar", "Huevos", "B1", 8, "Hecho. El B1 tiene ahora 8.") == "He dejado Huevos en 8 en el B1. El B1 tiene ahora 8."
    assert spoken_action("mover", "Huevos", "B3", 3, "Hecho. He movido 3 del B1 al B3.") == "He movido 3 de Huevos del B1 al B3."


def test_unknown_action_says_nothing():
    assert spoken_action("borrar", "Huevos", "B1", 1, "Hecho.") is None


def make(announced):
    fake = FakeInventory([Part(5, "Tornillo M3x8", 20, 40)], [StockItem(9, 5, "Tornillo M3x8", 40.0, 1001)], [])
    return Cajonera(fake, DrawerSimulator([Drawer(0, 0, 0), Drawer(17, 0, 1)]), deadline=1, now=lambda: NOW, on_announce=announced.append)


async def test_a_withdrawal_by_hand_is_announced_with_the_part_name():
    said = []
    caj = make(said)
    await caj.sync()
    r = await caj.retirar(pieza_pk=5, cantidad=4, cajon="A1")
    await caj.drain()
    assert r["hecho"] and len(said) == 1
    assert said[0]["accion"] == "retirar" and said[0]["pieza"] == "Tornillo M3x8" and said[0]["cajon"] == "A1"
    assert said[0]["frase"].startswith("He retirado 4 de Tornillo M3x8 del A1.")


async def test_add_count_and_move_by_hand_are_announced():
    said = []
    caj = make(said)
    await caj.sync()
    await caj.anadir(5, 0, 2)
    await caj.actualizar(5, 0, 10)
    await caj.mover(5, 0, 17, 3)
    await caj.drain()
    assert [s["accion"] for s in said] == ["anadir", "actualizar", "mover"]
    assert said[2]["frase"].startswith("He movido 3 de Tornillo M3x8 del A1 al B1.")


async def test_a_refused_order_is_not_announced():
    said = []
    caj = make(said)
    await caj.sync()
    await caj.retirar(pieza_pk=5, cantidad=999, cajon="A1")
    await caj.anadir(5, 0, 0)
    await caj.drain()
    assert said == []


async def test_voice_orders_are_not_announced_by_the_service():
    """Alexa already says the answer; repeating it on the Echo would be the same sentence twice."""
    said = []
    caj = make(said)
    await caj.sync()
    await caj.voz("retirar", pieza_pk=5, cantidad=2, cajon="A1")
    await caj.drain()
    assert said == []
