import asyncio

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from logic.inventree import InvenTreeClient, InvenTreeError
from logic.models import Category, Location, Part, StockItem


class FakeServer:
    """A real local HTTP server with canned answers that records each request.

    Used instead of aioresponses, which is not compatible with aiohttp >= 3.14.
    """

    def __init__(self):
        self.routes = {}  # (method, path) -> list of (status, payload) or callables
        self.requests = []  # (method, path, query dict, headers, json body)

    def add(self, method, path, payload=None, status=200, delay=0.0):
        self.routes.setdefault((method, path), []).append((status, payload, delay))

    async def handle(self, request: web.Request):
        body = await request.json() if request.can_read_body else None
        self.requests.append((request.method, request.path, dict(request.query), dict(request.headers), body))
        answers = self.routes.get((request.method, request.path))
        if not answers:
            return web.json_response({"detail": "not found"}, status=404)
        status, payload, delay = answers[0] if len(answers) == 1 else answers.pop(0)
        if delay:
            await asyncio.sleep(delay)
        if getattr(self, "add_html", False):
            return web.Response(text="<html>login</html>", content_type="text/html", status=status)
        return web.json_response(payload, status=status)

    def calls(self, method):
        return [r for r in self.requests if r[0] == method]


@pytest.fixture
async def srv():
    fake = FakeServer()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", fake.handle)
    server = TestServer(app)
    await server.start_server()
    fake.base = str(server.make_url("")).rstrip("/")
    yield fake
    await server.close()


@pytest.fixture
async def client(srv):
    async with aiohttp.ClientSession() as session:
        yield InvenTreeClient(session, srv.base, "abc", timeout=0.5)


async def test_search_parts_maps_fields_and_decimal_strings(srv, client):
    srv.add("GET", "/api/part/", {"count": 1, "results": [
        {"pk": 5, "name": "Tornillo M3x8", "minimum_stock": "20.00000", "total_in_stock": "40.00000"}]})
    assert await client.search_parts("m3") == [Part(5, "Tornillo M3x8", 20.0, 40.0)]


async def test_search_parts_sends_search_limit_and_active(srv, client):
    srv.add("GET", "/api/part/", [])
    await client.search_parts("m3", limit=7)
    ((_, _, query, _, _),) = srv.calls("GET")
    assert query == {"search": "m3", "limit": "7", "active": "true"}


async def test_search_parts_accepts_plain_list_and_in_stock_fallback(srv, client):
    srv.add("GET", "/api/part/", [{"pk": 5, "name": "M3", "minimum_stock": 0, "in_stock": 3}])
    assert await client.search_parts("m3") == [Part(5, "M3", 0.0, 3.0)]


async def test_sends_token_header(srv, client):
    srv.add("GET", "/api/part/", [])
    await client.search_parts("m3")
    ((_, _, _, headers, _),) = srv.calls("GET")
    assert headers["Authorization"] == "Token abc"


async def test_stock_for_part_maps_items(srv, client):
    srv.add("GET", "/api/stock/", [
        {"pk": 9, "part": 5, "part_detail": {"name": "Tornillo M3x8"}, "quantity": "40.0", "location": 11},
        {"pk": 10, "part": 5, "part_detail": {"name": "Tornillo M3x8"}, "quantity": 2, "location": None}])
    items = await client.stock_for_part(5)
    ((_, _, query, _, _),) = srv.calls("GET")
    assert items == [StockItem(9, 5, "Tornillo M3x8", 40.0, 11), StockItem(10, 5, "Tornillo M3x8", 2.0, None)]
    assert query == {"part": "5", "part_detail": "true"}


async def test_stock_in_location_does_not_cascade(srv, client):
    srv.add("GET", "/api/stock/", [])
    await client.stock_in_location(11)
    ((_, _, query, _, _),) = srv.calls("GET")
    assert query == {"location": "11", "cascade": "false", "part_detail": "true"}


async def test_get_stock_and_get_part(srv, client):
    srv.add("GET", "/api/stock/9/", {"pk": 9, "part": 5, "part_detail": {"name": "M3"}, "quantity": 4, "location": 11})
    srv.add("GET", "/api/part/5/", {"pk": 5, "name": "M3", "minimum_stock": 1, "total_in_stock": 4})
    assert await client.get_stock(9) == StockItem(9, 5, "M3", 4.0, 11)
    assert await client.get_part(5) == Part(5, "M3", 1.0, 4.0)


async def test_remove_stock_posts_items_and_notes(srv, client):
    srv.add("POST", "/api/stock/remove/", {})
    await client.remove_stock(9, 3, "cajonera: voz")
    ((_, _, _, _, body),) = srv.calls("POST")
    assert body == {"items": [{"pk": 9, "quantity": 3}], "notes": "cajonera: voz"}


async def test_ensure_root_returns_existing_top_level_location(srv, client):
    srv.add("GET", "/api/stock/location/", [
        {"pk": 2, "name": "Cajonera", "parent": 7},  # same name, not top level
        {"pk": 3, "name": "Cajonera", "parent": None}])
    assert await client.ensure_root() == 3
    assert srv.calls("POST") == []


async def test_ensure_root_creates_when_missing(srv, client):
    srv.add("GET", "/api/stock/location/", [])
    srv.add("POST", "/api/stock/location/", {"pk": 4, "name": "Cajonera", "parent": None})
    assert await client.ensure_root() == 4
    ((_, _, _, _, body),) = srv.calls("POST")
    assert body["name"] == "Cajonera"


async def test_child_locations_reads_address_from_metadata(srv, client):
    srv.add("GET", "/api/stock/location/", [{"pk": 11, "name": "B3"}, {"pk": 12, "name": "Otro"}])
    srv.add("GET", "/api/metadata/stocklocation/11/", {"metadata": {"cajonera": {"direccion": 17}}})
    srv.add("GET", "/api/metadata/stocklocation/12/", {"metadata": None})
    assert await client.child_locations(3) == [Location(11, "B3", 17), Location(12, "Otro", None)]
    assert srv.calls("GET")[0][2] == {"parent": "3"}


async def test_create_location_sets_metadata(srv, client):
    srv.add("POST", "/api/stock/location/", {"pk": 20, "name": "C3"})
    srv.add("PATCH", "/api/metadata/stocklocation/20/", {})
    assert await client.create_location(3, "C3", 17) == Location(20, "C3", 17)
    ((_, _, _, _, post),) = srv.calls("POST")
    ((_, _, _, _, patch),) = srv.calls("PATCH")
    assert post == {"name": "C3", "parent": 3}
    assert patch == {"metadata": {"cajonera": {"direccion": 17}}}


async def test_rename_location_patches_name(srv, client):
    srv.add("PATCH", "/api/stock/location/20/", {})
    await client.rename_location(20, "C1")
    ((_, _, _, _, body),) = srv.calls("PATCH")
    assert body == {"name": "C1"}


async def test_low_stock_parts_uses_filter(srv, client):
    srv.add("GET", "/api/part/", [{"pk": 5, "name": "M3", "minimum_stock": 20, "total_in_stock": 3}])
    assert await client.low_stock_parts() == [Part(5, "M3", 20.0, 3.0)]
    assert srv.calls("GET")[0][2]["low_stock"] == "true"


async def test_http_500_raises_inventree_error(srv, client):
    srv.add("GET", "/api/part/", {}, status=500)
    with pytest.raises(InvenTreeError):
        await client.search_parts("m3")


async def test_timeout_raises_inventree_error(srv, client):
    srv.add("GET", "/api/part/", [], delay=1.0)  # client timeout is 0.5 s
    with pytest.raises(InvenTreeError):
        await client.search_parts("m3")


async def test_connection_error_raises_inventree_error():
    async with aiohttp.ClientSession() as session:
        client = InvenTreeClient(session, "http://127.0.0.1:1", "abc", timeout=0.5)
        with pytest.raises(InvenTreeError):
            await client.search_parts("m3")


async def test_html_body_raises_inventree_error(srv, client):
    """M2: a proxy/login page answering 200 with HTML must not escape as ValueError."""
    srv.add("GET", "/api/part/", None)
    srv.add_html = True
    with pytest.raises(InvenTreeError):
        await client.search_parts("m3")


async def test_unexpected_shape_raises_inventree_error(srv, client):
    srv.add("GET", "/api/part/", [{"unexpected": True}])
    with pytest.raises(InvenTreeError):
        await client.search_parts("m3")


async def test_metadata_uses_generic_endpoint_not_the_redirecting_one(srv, client):
    """InvenTree 1.x answers the legacy /api/stock/location/<pk>/metadata/ with a
    301, and a PATCH that follows a 301 becomes a GET: the address was never saved."""
    srv.add("POST", "/api/stock/location/", {"pk": 20, "name": "C3"})
    srv.add("PATCH", "/api/metadata/stocklocation/20/", {})
    await client.create_location(3, "C3", 17)
    assert [r[1] for r in srv.calls("PATCH")] == ["/api/metadata/stocklocation/20/"]


# Stock operations


async def test_add_stock_posts_items_and_notes(srv, client):
    srv.add("POST", "/api/stock/add/", {})
    await client.add_stock(9, 4, "cajonera: alta")
    ((method, path, _, _, body),) = srv.calls("POST")
    assert (method, path) == ("POST", "/api/stock/add/")
    assert body == {"items": [{"pk": 9, "quantity": 4}], "notes": "cajonera: alta"}


async def test_count_stock_posts_items_and_notes(srv, client):
    srv.add("POST", "/api/stock/count/", {})
    await client.count_stock(9, 12, "cajonera: recuento")
    ((method, path, _, _, body),) = srv.calls("POST")
    assert (method, path) == ("POST", "/api/stock/count/")
    assert body == {"items": [{"pk": 9, "quantity": 12}], "notes": "cajonera: recuento"}


async def test_transfer_stock_posts_location(srv, client):
    srv.add("POST", "/api/stock/transfer/", {})
    await client.transfer_stock(9, 3, 11, "cajonera: mover")
    ((method, path, _, _, body),) = srv.calls("POST")
    assert (method, path) == ("POST", "/api/stock/transfer/")
    assert body == {"items": [{"pk": 9, "quantity": 3}], "location": 11, "notes": "cajonera: mover"}


async def test_merge_stock_posts_items_and_flags(srv, client):
    srv.add("POST", "/api/stock/merge/", {})
    await client.merge_stock([9, 10, 12], 11, "cajonera: fusion")
    ((method, path, _, _, body),) = srv.calls("POST")
    assert (method, path) == ("POST", "/api/stock/merge/")
    assert body == {
        "items": [{"item": 9}, {"item": 10}, {"item": 12}],
        "location": 11,
        "notes": "cajonera: fusion",
        "allow_mismatched_suppliers": True,
        "allow_mismatched_status": True,
    }


async def test_create_stock_posts_and_maps_item(srv, client):
    srv.add("POST", "/api/stock/", {
        "pk": 30, "part": 5, "quantity": "7.0", "location": 11, "delete_on_deplete": False,
        "part_detail": {"name": "M3"}})
    item = await client.create_stock(5, 11, 7)
    ((method, path, _, _, body),) = srv.calls("POST")
    assert (method, path) == ("POST", "/api/stock/")
    assert body == {"part": 5, "location": 11, "quantity": 7, "delete_on_deplete": False}
    assert item == StockItem(30, 5, "M3", 7.0, 11, False)


async def test_set_keep_when_empty_patches_delete_on_deplete(srv, client):
    srv.add("PATCH", "/api/stock/9/", {})
    await client.set_keep_when_empty(9)
    ((method, path, _, _, body),) = srv.calls("PATCH")
    assert (method, path) == ("PATCH", "/api/stock/9/")
    assert body == {"delete_on_deplete": False}


async def test_stock_maps_delete_on_deplete_defaulting_to_true(srv, client):
    srv.add("GET", "/api/stock/9/", {"pk": 9, "part": 5, "quantity": 4, "location": 11, "delete_on_deplete": False})
    srv.add("GET", "/api/stock/10/", {"pk": 10, "part": 5, "quantity": 4, "location": 11})
    assert (await client.get_stock(9)).delete_on_deplete is False
    assert (await client.get_stock(10)).delete_on_deplete is True


async def test_stock_in_category_uses_part_category_filter(srv, client):
    srv.add("GET", "/api/stock/", [
        {"pk": 9, "part": 5, "part_detail": {"name": "M3"}, "quantity": 4, "location": 11}])
    items = await client.stock_in_category(8, 3)
    ((_, path, query, _, _),) = srv.calls("GET")
    assert path == "/api/stock/"
    assert query == {"part_category": "8", "location": "3", "cascade": "true", "part_detail": "true"}
    assert items == [StockItem(9, 5, "M3", 4.0, 11)]


# Parts and categories


async def test_part_maps_ipn_category_keywords(srv, client):
    srv.add("GET", "/api/part/5/", {
        "pk": 5, "name": "M3", "minimum_stock": 1, "total_in_stock": 4,
        "IPN": "SCR-M3", "category": 8, "description": "Tornillo", "keywords": "tornillo m3"})
    srv.add("GET", "/api/part/6/", {
        "pk": 6, "name": "M4", "minimum_stock": 0, "total_in_stock": 0,
        "IPN": None, "category": None, "description": None, "keywords": None})
    assert await client.get_part(5) == Part(5, "M3", 1.0, 4.0, "SCR-M3", 8, "Tornillo", "tornillo m3")
    assert await client.get_part(6) == Part(6, "M4", 0.0, 0.0, "", None, "", "")


async def test_create_part_posts_initial_stock(srv, client):
    srv.add("POST", "/api/part/", {
        "pk": 40, "name": "M5", "minimum_stock": 2, "total_in_stock": 0, "IPN": "SCR-M5",
        "category": 8, "description": "Tornillo M5", "keywords": "m5"})
    part = await client.create_part(
        "M5", 8, 11, 25, ipn="SCR-M5", description="Tornillo M5", keywords="m5", minimum=2)
    ((method, path, _, _, body),) = srv.calls("POST")
    assert (method, path) == ("POST", "/api/part/")
    assert body == {
        "name": "M5", "category": 8, "IPN": "SCR-M5", "description": "Tornillo M5", "keywords": "m5",
        "minimum_stock": 2, "component": True, "active": True,
        "initial_stock": {"quantity": 25, "location": 11},
    }
    assert part == Part(40, "M5", 2.0, 0.0, "SCR-M5", 8, "Tornillo M5", "m5")


async def test_create_part_defaults(srv, client):
    srv.add("POST", "/api/part/", {"pk": 41, "name": "X"})
    await client.create_part("X", 8, 11, 1)
    ((_, _, _, _, body),) = srv.calls("POST")
    assert body["IPN"] == "" and body["description"] == "" and body["keywords"] == ""
    assert body["minimum_stock"] == 0


async def test_update_part_patches_given_fields(srv, client):
    srv.add("PATCH", "/api/part/5/", {})
    await client.update_part(5, name="M3 nuevo", minimum_stock=4, IPN="SCR")
    ((method, path, _, _, body),) = srv.calls("PATCH")
    assert (method, path) == ("PATCH", "/api/part/5/")
    assert body == {"name": "M3 nuevo", "minimum_stock": 4, "IPN": "SCR"}


async def test_update_part_rejects_unknown_fields(srv, client):
    with pytest.raises(ValueError):
        await client.update_part(5, active=False)
    assert srv.requests == []


async def test_categories_use_pathstring(srv, client):
    srv.add("GET", "/api/part/category/", [
        {"pk": 8, "name": "Tornillos", "pathstring": "Ferreteria/Tornillos"},
        {"pk": 9, "name": "Tuercas"}])
    assert await client.categories() == [
        Category(8, "Tornillos", "Ferreteria/Tornillos"), Category(9, "Tuercas", "Tuercas")]


async def test_create_category_posts_name_and_parent(srv, client):
    srv.add("POST", "/api/part/category/", {"pk": 12, "name": "Tuercas", "pathstring": "Ferreteria/Tuercas"})
    cat = await client.create_category("Tuercas", parent_pk=8)
    ((method, path, _, _, body),) = srv.calls("POST")
    assert (method, path) == ("POST", "/api/part/category/")
    assert body == {"name": "Tuercas", "parent": 8}
    assert cat == Category(12, "Tuercas", "Ferreteria/Tuercas")


async def test_create_category_without_parent_omits_it(srv, client):
    srv.add("POST", "/api/part/category/", {"pk": 13, "name": "Raiz"})
    cat = await client.create_category("Raiz")
    ((_, _, _, _, body),) = srv.calls("POST")
    assert body == {"name": "Raiz"}
    assert cat == Category(13, "Raiz", "Raiz")


async def test_new_write_endpoints_raise_inventree_error_on_http_error(srv, client):
    srv.add("POST", "/api/stock/add/", {}, status=400)
    with pytest.raises(InvenTreeError):
        await client.add_stock(9, 1, "x")


async def test_get_location_maps_name(srv, client):
    srv.add("GET", "/api/stock/location/7/", {"pk": 7, "name": "Nevera", "parent": None})
    assert await client.get_location(7) == Location(7, "Nevera", None)
