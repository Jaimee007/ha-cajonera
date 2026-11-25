# Inventory card and HACS repository — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the `cajonera` integration into its own HACS repository and extend it so every day-to-day inventory action (autocomplete, add, withdraw, count, move, create and edit parts, create categories) is done from one Home Assistant card.

**Architecture:** Same two layers as today: pure-Python `logic/` (no Home Assistant imports, pytest) holds every rule; `__init__.py` only registers services. New logic modules: `stock_ops.py` (drawer-level add/count/move) and `catalog.py` (suggestions, part sheet, create/edit, categories, drawer suggestion). The card becomes a set of screens in one custom element, with pure helpers in `cajonera-layout.js` (node tests). The integration serves its own card files.

**Tech Stack:** Python 3.13, aiohttp, pytest + pytest-asyncio; Home Assistant custom integration (YAML setup); JavaScript ES2022 without dependencies, `node --test` (Node 24); InvenTree 1.7.3 REST API (API 570); HACS.

**Spec:** [docs/superpowers/specs/2026-11-24-inventory-card-design.md](../specs/2026-11-24-inventory-card-design.md). Product background: `Modular_Drawers/docs/09_gestion_inventario.md`.

## Global Constraints

- `logic/` never imports `homeassistant`. Check: `grep -rn --include=*.py homeassistant custom_components/cajonera/logic/` prints nothing.
- Every service answers within **3.5 s** (`DEADLINE` in `logic/service.py`); InvenTree failures answer `"No puedo consultar el inventario ahora mismo."`, never raise.
- A write that times out after it started answers `"No sé si se ha registrado. Revisa la cantidad antes de repetirlo."` (stock writes) — the existing withdrawal keeps its own wording.
- Everything is **per drawer**, never per stock item. Before a count or a move, the part's stock items in that drawer are merged into one.
- Stock items the integration creates or touches get `delete_on_deplete = false`.
- Part number goes in InvenTree's **IPN** field.
- Suggestions: start at **2** characters, **300 ms** debounce, at most **8** rows plus "+ Crear «texto»".
- `sugerir` has no side effects. `pieza` lights the part's drawers like a search and sets the "he cogido" context.
- Code, comments, docs and commit messages in `ha-cajonera` are **English**; user-facing strings **Spanish**, exactly as written in this plan.
- InvenTree endpoints (verified read-only on the user's 1.7.3): `POST /api/stock/add/`, `/api/stock/count/`, `/api/stock/transfer/` with `{"items":[{"pk","quantity"}],"notes"}` (transfer also `"location"`); `POST /api/stock/merge/` with `{"items":[{"item":pk}],"location":pk,"notes","allow_mismatched_suppliers":true,"allow_mismatched_status":true}`; `POST /api/part/` accepts `initial_stock: {"quantity","location"}` (quantity 0 adds no stock); `POST /api/part/category/` with `name`, `parent`; stock item has `delete_on_deplete`; metadata via `/api/metadata/stocklocation/<pk>/`.
- **Commits** follow each task's commit step. **Never push, tag or release**: the last task tells the user the exact commands.

## Review Focus

1. **Typing fast:** an older, slower suggestion answer arriving after a newer one must be discarded. → Task 7 (`acceptAnswer`).
2. **Count on a drawer with several stock items:** "Actualizar a 47" leaves the drawer total at 47, not one item at 47. → Task 3.
3. **Last unit gone:** withdrawing the last unit or counting to 0 keeps the stock item in its drawer, and search then says "pero no queda ninguno". → Tasks 3 and 5.
4. **Creating a part whose name exists with different case** ("tornillo m3x8" vs "Tornillo M3x8"): refused with the "Ya existe" phrase, nothing created. → Task 4.
5. **Quantities typed on a phone with a decimal comma** ("2,5") are read as 2.5; "", "abc", "-1" are rejected before any service call. → Task 7 (`parseQty`).

---

## File structure (in `ha-cajonera` after Task 1)

```
ha-cajonera/
├── custom_components/cajonera/
│   ├── __init__.py                 ← YAML setup, services, serves frontend/
│   ├── sensor.py, manifest.json, services.yaml, translations/{en,es}.json
│   ├── frontend/
│   │   ├── cajonera-card.js        ← the card (screens)
│   │   └── cajonera-layout.js      ← pure helpers
│   └── logic/
│       ├── (existing) models, naming, phrases, inventree, search, withdraw,
│       │   context, sync, low_stock, simulator, service
│       ├── stock_ops.py            ← NEW: add / count / move per drawer
│       └── catalog.py              ← NEW: suggest, part sheet, create, edit, categories, drawer suggestion
├── tests/                          ← pytest (conftest adds custom_components/cajonera to sys.path)
│   └── frontend/cajonera-layout.test.mjs
├── pyproject.toml, requirements-dev.txt, hacs.json, README.md, .gitignore
└── docs/superpowers/{specs,plans}/
```

All Python commands run from the repo root: `python -m pytest -q`. JS: `node --test tests/frontend/cajonera-layout.test.mjs`.

---

### Task 1: Migrate the integration into `ha-cajonera` and serve the card

**Files:**
- Bring in (with history): everything under `Modular_Drawers/homeassistant/`
- Move: `custom_components/` stays at root; `tests/` stays at root; `www/cajonera-*.js` → `custom_components/cajonera/frontend/`; `www/tests/` → `tests/frontend/`
- Delete: `configuration.example.yaml` (its content goes into README), `www/`
- Create: `hacs.json`, `README.md` (replace the initial one), `.gitignore`
- Modify: `custom_components/cajonera/__init__.py`, `manifest.json` (`version: 0.2.0`, add `"dependencies": ["persistent_notification", "http", "frontend"]`), `tests/conftest.py` (path), `tests/frontend/cajonera-layout.test.mjs` (import path)
- Modify (other repo, separate commit): `Modular_Drawers/homeassistant/` → only `README.md`; `Modular_Drawers/README.md`, `docs/04_diseno_software.md`, `docs/09_gestion_inventario.md` §12 point to `ha-cajonera`

**Interfaces:**
- Produces: `FRONTEND_URL = "/cajonera_frontend"`; the card is loaded from `/cajonera_frontend/cajonera-card.js?v=<manifest version>`.

- [ ] **Step 1: Split and bring the history.** In `Modular_Drawers` (branch `feature/inventario-ha`): `git subtree split --prefix=homeassistant -b ha-split`. In `ha-cajonera`: `git fetch ../Modular_Drawers ha-split` then `git merge --allow-unrelated-histories FETCH_HEAD -m "Import cajonera integration history from Modular_Drawers"`. Resolve the README conflict by keeping the incoming tree for now. Then in `Modular_Drawers`: `git branch -D ha-split`.
- [ ] **Step 2: Reorganise** with `git mv` into the layout above. `.gitignore`: `__pycache__/`, `.pytest_cache/`, `.env.local`, `dist/`. `hacs.json`: `{"name": "Cajonera", "render_readme": true, "homeassistant": "2024.7.0"}`.
- [ ] **Step 3: Run the existing suites.** `python -m pytest -q` → `126 passed`; `node --test tests/frontend/cajonera-layout.test.mjs` → `pass 9`.
- [ ] **Step 4: Serve the card from the integration.** In `async_setup`, after services: register `custom_components/cajonera/frontend` at `FRONTEND_URL` with `hass.http.async_register_static_paths([StaticPathConfig(FRONTEND_URL, str(path), cache_headers=False)])` and call `frontend.add_extra_js_url(hass, f"{FRONTEND_URL}/cajonera-card.js?v={version}")`, reading `version` from `manifest.json`. `cajonera-card.js` keeps importing `./cajonera-layout.js` (same folder, same URL prefix).
- [ ] **Step 5: README (English).** Sections: what it is (one paragraph, link to `Modular_Drawers` and its `09`); install with HACS (custom repository URL `https://github.com/Jaimee007/ha-cajonera`, category *Integration*); YAML configuration (the block from `configuration.example.yaml`, token in `secrets.yaml`, use the LOCAL InvenTree URL when possible); the card (`type: custom:cajonera-card`, no resource needed); **migrating from the manual zip** (delete `/config/www/cajonera-card.js`, `/config/www/cajonera-layout.js` and the `/local/cajonera-card.js` dashboard resource); services table (names only, link to `services.yaml`); development (`pip install -r requirements-dev.txt`, the two test commands).
- [ ] **Step 6: Check.** `python -m py_compile custom_components/cajonera/*.py` and both suites again. Expected: no output, `126 passed`, `pass 9`.
- [ ] **Step 7: Commit in `ha-cajonera`:** `Reorganise for HACS and serve the card from the integration`.
- [ ] **Step 8: `Modular_Drawers` pointer.** Replace `homeassistant/` contents with a `README.md` (Spanish, like the repo): one paragraph saying the integration lives in `https://github.com/Jaimee007/ha-cajonera` and how to install it. Update the three doc references. Commit in `Modular_Drawers` on `feature/inventario-ha`: `Move the Home Assistant integration to ha-cajonera`.

---

### Task 2: InvenTree client — new endpoints

**Files:**
- Modify: `custom_components/cajonera/logic/models.py`, `logic/inventree.py`, `tests/fakes.py`
- Test: `tests/test_inventree.py`

**Interfaces:**
- `Part` gains defaulted fields (existing constructors keep working): `ipn: str = ""`, `category_pk: int | None = None`, `description: str = ""`, `keywords: str = ""`. Mapped from `IPN`, `category`, `description`, `keywords` (None → "").
- New `@dataclass(frozen=True) Category(pk: int, name: str, path: str)` — `path` from `pathstring`, falling back to `name`.
- `StockItem` gains `delete_on_deplete: bool = True`.
- New `InvenTreeClient` methods (all raise `InvenTreeError` on failure):
  - `add_stock(stock_pk: int, quantity: float, notes: str) -> None` → `POST /api/stock/add/`
  - `count_stock(stock_pk: int, quantity: float, notes: str) -> None` → `POST /api/stock/count/`
  - `transfer_stock(stock_pk: int, quantity: float, location_pk: int, notes: str) -> None` → `POST /api/stock/transfer/` with `"location"`
  - `merge_stock(stock_pks: list[int], location_pk: int, notes: str) -> None` → `POST /api/stock/merge/` (body in Global Constraints)
  - `create_stock(part_pk: int, location_pk: int, quantity: float) -> StockItem` → `POST /api/stock/` with `{"part","location","quantity","delete_on_deplete": false}`
  - `set_keep_when_empty(stock_pk: int) -> None` → `PATCH /api/stock/<pk>/` `{"delete_on_deplete": false}`
  - `create_part(name: str, category_pk: int, location_pk: int, quantity: float, ipn: str = "", description: str = "", keywords: str = "", minimum: float = 0) -> Part` → `POST /api/part/` with `name, category, IPN, description, keywords, minimum_stock, component: true, active: true, initial_stock: {"quantity", "location"}`
  - `update_part(pk: int, **fields) -> None` → `PATCH /api/part/<pk>/`; accepted keys `name, description, category, keywords, IPN, minimum_stock`
  - `categories() -> list[Category]` → `GET /api/part/category/`
  - `create_category(name: str, parent_pk: int | None = None) -> Category` → `POST /api/part/category/`
  - `stock_in_category(category_pk: int, root_pk: int) -> list[StockItem]` → `GET /api/stock/?part_category=<pk>&location=<root>&cascade=true&part_detail=true` (`category` is rejected by 1.7.3)
- `FakeInventory` gets the same methods. Its `remove_stock` and `count_stock` **delete the item when it reaches 0 and `delete_on_deplete` is True** (like InvenTree). `merge_stock` sums into the first pk and deletes the rest. Records: `added`, `counted`, `transferred`, `merged`, `created_parts`, `created_categories`, `patched`.

- [ ] **Step 1: Failing tests** on the local test server, one per method, asserting method, path and exact JSON body. Plus `test_part_maps_ipn_category_keywords` and `test_categories_use_pathstring`.
- [ ] **Step 2:** `python -m pytest tests/test_inventree.py -q` → FAIL.
- [ ] **Step 3: Implement** the methods and fake.
- [ ] **Step 4:** `python -m pytest -q` → all pass (126 + new).
- [ ] **Step 5: Verify against the live instance** — only the read endpoints, from `.env.local`: `categories()` returns `[]` today; nothing is written. If a field name differs, change only the mapping in `inventree.py`.
- [ ] **Step 6: Commit:** `Add InvenTree stock, part and category endpoints`.

---

### Task 3: Drawer-level stock operations

**Files:**
- Create: `custom_components/cajonera/logic/stock_ops.py`
- Test: `tests/test_stock_ops.py`

**Interfaces:**
- Consumes: client methods (Task 2); `Location`, `Part`; `fmt_qty`; `DOWN_PHRASE` from `search`.
- Produces `@dataclass(frozen=True) OpResult(phrase: str, done: bool = False, address: int | None = None, low: bool = False, part_pk: int | None = None, location_pk: int | None = None)` and:
  - `async add_to_drawer(api, part_pk: int, address: int, quantity: float, locations: dict[int, Location]) -> OpResult`
  - `async count_drawer(api, part_pk: int, address: int, quantity: float, locations) -> OpResult`
  - `async move_between(api, part_pk: int, from_address: int, to_address: int, quantity: float | None, locations) -> OpResult`
  - `MAYBE_STOCK = "No sé si se ha registrado. Revisa la cantidad antes de repetirlo."`
- Rules and exact phrases (part name `N`, drawer names `X`, `Y`):

| Case | phrase |
|---|---|
| quantity invalid (add/move ≤ 0; count < 0; None) | `Dime una cantidad válida.` |
| address not in `locations` | `Ese cajón no está en el inventario.` |
| add | `Hecho. Ahora hay {total del cajón} en el X.` (creates a stock item with `create_stock` if the part has none there) |
| count | `Hecho. El X tiene ahora {quantity}.` |
| move, same drawer | `El origen y el destino son el mismo cajón.` |
| move, nothing in source | `No hay N en el X.` |
| move, more than in source | `Solo hay {total} en el X.` |
| move ok | `Hecho. He movido {q} del X al Y.` |
| InvenTreeError before any write | `DOWN_PHRASE` |
| InvenTreeError during/after the write | `MAYBE_STOCK`, `done=False` |

- Before count and move: if the part has more than one stock item in that drawer, `merge_stock` them; then every item touched gets `set_keep_when_empty` if its `delete_on_deplete` is True. Count sets the merged item to `quantity`. Move transfers from the merged item (`quantity=None` = all). After a successful write, re-read the part with `get_part` for `low` (on failure keep `done=True`, `low=False`).

- [ ] **Step 1: Failing tests:**
  - `test_add_to_existing_drawer_stock` — 40 in B1, add 10 → `"Hecho. Ahora hay 50 en el B1."`, `fake.added == [(9, 10)]`
  - `test_add_creates_stock_in_empty_drawer` — no stock in B3 → one `create_stock` with `delete_on_deplete` False
  - `test_count_merges_items_first` — B1 holds 40 + 5, count 47 → merged then counted, drawer total 47 *(Review Focus 2)*
  - `test_count_to_zero_keeps_item` — item with `delete_on_deplete=True`, count 0 → item still in `fake.stock` with 0 *(Review Focus 3)*
  - `test_move_all_and_partial`, `test_move_same_drawer_refused`, `test_move_more_than_source_refused`, `test_move_unknown_drawer_refused` — refusals write nothing
  - `test_invalid_quantities` — parametrised (0, -1, None per operation; count accepts 0)
  - `test_failure_after_write_says_maybe` — `fail_on={"count_stock"}` after merge → `MAYBE_STOCK`
  - `test_low_flag_after_add` — part minimum 100 → `low=True`
- [ ] **Step 2:** `python -m pytest tests/test_stock_ops.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `python -m pytest -q` → all pass.
- [ ] **Step 5: Commit:** `Add drawer-level add, count and move`.

---

### Task 4: Catalog — suggestions, part sheet, create, edit, categories

**Files:**
- Create: `custom_components/cajonera/logic/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: client (Task 2), `drawer_name`/`is_unplaced`, `Location`, `Drawer`.
- Produces:
  - `MAX_SUGGESTIONS = 8`
  - `async suggest(api, text: str, locations) -> list[dict]` → rows `{"pieza_pk", "nombre", "ipn", "categoria_pk", "total", "cajones": [names]}`; text stripped, fewer than **2** chars → `[]` without calling the API; includes parts in no drawer (`cajones: []`).
  - `async part_sheet(api, part_pk: int, locations) -> dict` → `{"pieza_pk","nombre","descripcion","categoria_pk","palabras_clave","ipn","minimo","total","bajo","cajones":[{"cajon","direccion","cantidad"}],"fuera": float}` — one row per drawer (summed), sorted by quantity descending; `fuera` = stock outside the cabinet.
  - `async suggest_drawer(api, category_pk: int, root_pk: int, drawers: list[Drawer], locations) -> dict` → `{"direccion","cajon","motivo"}`:
    - the drawer holding the most stock items of that category (ties: lowest address) → motivo `Ya hay piezas de esta categoría en el X.`
    - else the first drawer with no stock, by name order A1, A2, …, B1, … (`drawer_name` sort: row letter, then column) → motivo `Primer cajón vacío.`
    - else all nulls, motivo `Elige un cajón.`
  - `async create_part(api, name, category_pk, address, quantity, locations, ipn="", description="", keywords="", minimum=0) -> OpResult` (from `stock_ops`):
    - name stripped empty → `Dime un nombre para la pieza.`
    - exact name exists (case-insensitive, via `search_parts(name)` then compare) → `Ya existe «{existing name}». Ábrela para añadir.` with `part_pk` of the existing part *(Review Focus 4)*
    - quantity ≤ 0 or None → `Dime una cantidad válida.`; unknown address → `Ese cajón no está en el inventario.`
    - ok → `Hecho. He creado {name} con {q} en el X.`; then `set_keep_when_empty` on its new stock item (found via `stock_for_part`).
  - `async edit_part(api, part_pk: int, changes: dict) -> OpResult` — maps `nombre→name, descripcion→description, categoria→category, palabras_clave→keywords, ipn→IPN, minimo→minimum_stock`; empty `nombre` refused (`Dime un nombre para la pieza.`); ok → `Hecho. He guardado los cambios.`
  - `async list_categories(api) -> list[dict]` → `[{"pk","nombre","ruta"}]` sorted by `ruta`.
  - `async create_category(api, name: str, parent_pk: int | None) -> dict` → existing category with the same name and parent (case-insensitive) is returned instead of creating a duplicate; empty name → `{"frase": "Dime un nombre para la categoría."}`.

- [ ] **Step 1: Failing tests** — one per rule above, plus `test_suggest_short_text_does_not_call_api`, `test_suggest_includes_parts_without_drawer`, `test_part_sheet_sums_per_drawer_and_reports_outside`, `test_suggest_drawer_prefers_category_then_first_empty`, `test_create_part_duplicate_name_any_case`.
- [ ] **Step 2:** `python -m pytest tests/test_catalog.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** `python -m pytest -q` → all pass.
- [ ] **Step 5: Commit:** `Add catalog: suggestions, part sheet, create and edit`.

---

### Task 5: "Guardar" light mode and keep-when-empty during sync

**Files:**
- Modify: `logic/simulator.py`, `logic/service.py` (sync)
- Test: `tests/test_simulator.py`, `tests/test_service.py`

**Interfaces:**
- `DrawerSimulator.store_here(address: int, seconds: float = 30) -> None` — base mode `"guardar"`, `rgb (255, 0, 255)`; ends when that drawer opens, when another `store_here` or `search` replaces it, or after `seconds`. Opening it shows `"abierto"` (no green: nothing was being searched).
- `Cajonera.sync()` additionally calls `set_keep_when_empty` once on every stock item in cabinet drawers with `delete_on_deplete=True` (failures are logged and retried next sync, never block it).

- [ ] **Step 1: Failing tests:** `test_store_here_lights_magenta_until_opened`, `test_store_here_replaced_by_search`, `test_store_here_times_out`; `test_sync_marks_cabinet_stock_keep_when_empty` (stock outside the cabinet untouched); `test_withdraw_last_unit_keeps_item_and_search_says_none_left` — withdraw 4 of 4 after a sync, then `buscar("huevos")` → `"Tenías Huevos en el B1, pero no queda ninguno."` *(Review Focus 3)*.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** `python -m pytest -q` → all pass.
- [ ] **Step 5: Commit:** `Add store-here light mode and keep cabinet stock when empty`.

---

### Task 6: Services

**Files:**
- Modify: `logic/service.py`, `custom_components/cajonera/__init__.py`, `services.yaml`, `translations/{en,es}.json`
- Test: `tests/test_service.py`

**Interfaces:**
- New `Cajonera` methods, each bounded by the deadline exactly like `buscar`/`retirar` (pre-I/O validation first, then `_ready()`):
  - `sugerir(texto) -> {"sugerencias": [...]}` (timeout → `{"sugerencias": []}`)
  - `pieza(pieza_pk) -> part sheet` — also `simulator.search(addresses of its drawers, low)` and sets the context (`Context(part_pk, location_pk if one drawer else None, now)`)
  - `anadir(pieza_pk, direccion, cantidad)`, `actualizar(pieza_pk, direccion, cantidad)`, `mover(pieza_pk, desde, hasta, cantidad=None)` → `{"frase","hecho"}`; on `done`: `simulator.confirm(address, low)`, context set to the part and drawer, `refresh_low()` in background; timeout → `MAYBE_STOCK`
  - `categorias() -> {"categorias": [...]}`, `categoria_crear(nombre, padre=None) -> {"categoria": {...}}` or `{"frase": ...}`
  - `sugerir_cajon(categoria) -> {"direccion","cajon","motivo"}` — also `simulator.store_here(direccion)` when not null
  - `pieza_crear(nombre, categoria, direccion, cantidad, ipn="", descripcion="", palabras_clave="", minimo=0) -> {"frase","hecho","pieza_pk"}`
  - `pieza_editar(pieza_pk, **changes) -> {"frase","hecho"}`
- HA services with the same names (`sugerir`, `pieza`, `inventario_anadir`, `inventario_actualizar`, `inventario_mover`, `categorias`, `categoria_crear`, `sugerir_cajon`, `pieza_crear`, `pieza_editar`); read-only ones `SupportsResponse.ONLY`, writes `SupportsResponse.OPTIONAL`. Schemas: ids `vol.Coerce(int)`, quantities `vol.Any(None, vol.Coerce(float))`, texts `vol.Any(None, cv.string)`.

- [ ] **Step 1: Failing tests** (`test_service.py`): `test_sugerir_has_no_side_effects` (simulator all `reposo`, context unchanged), `test_pieza_lights_drawers_and_sets_context`, `test_anadir_confirms_on_drawer`, `test_write_timeout_says_maybe_stock`, `test_sugerir_cajon_lights_store_here`, `test_pieza_crear_then_sugerir_finds_it`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement `service.py`, then register in `__init__.py`, describe in `services.yaml`, name in both translation files. **Step 4:** `python -m pytest -q` → all pass; `python -m py_compile custom_components/cajonera/__init__.py` → no output.
- [ ] **Step 5: Commit:** `Add services for suggestions, part sheet, stock writes and catalog`.

---

### Task 7: Card helpers

**Files:**
- Modify: `custom_components/cajonera/frontend/cajonera-layout.js`
- Test: `tests/frontend/cajonera-layout.test.mjs`

**Interfaces (pure, exported):**
- `parseQty(text: string, {allowZero = false} = {}) -> number | null` — trims, accepts comma or dot, rejects empty, non-numeric, negative, and 0 unless `allowZero`.
- `suggestionRows(sugerencias: object[], texto: string) -> object[]` — the server rows (max 8) plus, when `texto.trim().length >= 2` and no row's `nombre` equals it case-insensitively, a last row `{crear: true, nombre: texto.trim()}`.
- `createSequencer() -> {next(): number, accept(n: number): boolean}` — `accept` is true only for the latest `next()` value. Name it `acceptAnswer` in tests' description.
- `defaultDrawer(cajones: {direccion, cantidad}[]) -> number | null` — address with the most quantity; null when empty.
- `debounceMs = 300`, `minChars = 2`.

- [ ] **Step 1: Failing tests:** `parseQty` cases `"2,5"→2.5`, `"10"→10`, `""/"abc"/"-1"→null`, `"0"→null`, `"0"` with `allowZero`→0 *(Review Focus 5)*; `suggestionRows` adds "Crear" only when no exact match and text ≥ 2; `acceptAnswer`: `a=next(); b=next(); accept(a)===false; accept(b)===true` *(Review Focus 1)*; `defaultDrawer`.
- [ ] **Step 2:** `node --test tests/frontend/cajonera-layout.test.mjs` → fail. **Step 3:** implement. **Step 4:** → all pass.
- [ ] **Step 5: Commit:** `Add card helpers for quantities, suggestions and stale answers`.

---

### Task 8: The card

**Files:**
- Modify: `custom_components/cajonera/frontend/cajonera-card.js`

**Interfaces:**
- Consumes: Task 6 services through `hass.callWS({type: "call_service", domain: "cajonera", service, service_data, return_response: true})`; Task 7 helpers; sensors `sensor.cajonera_mapa`, `sensor.cajonera_existencias_bajas`.
- Screens (one visible at a time, state in `this._screen`): `"buscar"`, `"ficha"`, `"crear"`, `"editar"`. The grid renders below `buscar` and `crear`; in `crear`, tapping a cell sets the target drawer instead of opening its contents.

- [ ] **Step 1: Search screen.** Input with `inputmode="search"`; on input, after `debounceMs` and `minChars`, call `sugerir` with a sequencer number; ignore stale answers. Rows from `suggestionRows`; ↑/↓/Enter on PC, tap on phone. Enter with no highlighted row → `inventario_buscar` (existing behaviour: result list, each row opens its sheet). Choosing a part → `pieza` → `"ficha"`. Choosing "+ Crear" → `"crear"` with the name prefilled.
- [ ] **Step 2: Part sheet.** Header (name, category path, IPN), rows per drawer, "sin cajón" when `cajones` is empty, `fuera` when > 0, low warning. Action segmented control **Añadir** (default) / **Retirar** / **Actualizar a**; drawer select defaulting to `defaultDrawer`; quantity input `inputmode="decimal"` (prefilled with the current drawer quantity for *Actualizar a*); one **Confirmar** button, disabled while waiting. *Retirar* calls `inventario_retirar` with `texto=<exact part name>` and `cajon=<drawer name>`: the exact-name rule resolves the part and the drawer is explicit, so it never depends on the 2-minute context. Below: **Mover** row (from, to, quantity optional) and **Editar** button.
- [ ] **Step 3: Create screen.** Name; category select from `categorias` with a last option "+ Nueva categoría…" that shows an inline name field and calls `categoria_crear`; choosing a category calls `sugerir_cajon` and preselects its drawer (shows `motivo`); tapping a grid cell changes it; quantity (required), IPN, minimum, keywords (optional). **Crear pieza** calls `pieza_crear`; on "Ya existe" offer **Abrir** (calls `pieza` with the returned `pieza_pk`).
- [ ] **Step 4: Edit screen.** Same fields as create without drawer/quantity, prefilled from the sheet; **Guardar** → `pieza_editar`, back to the sheet refreshed.
- [ ] **Step 5: State across re-renders.** Screen, chosen part, typed values, highlighted suggestion, focus and caret survive sensor updates (extend the existing `_query`/`_focus`/`_qty` approach to a single `this._form` object).
- [ ] **Step 6: Phone layout.** Single column under 600 px, touch targets ≥ 44 px, no hover-only controls.
- [ ] **Step 7: Check.** `node --check` on a copy named `.mjs`; stubbed-DOM smoke render of each screen (as done before) asserting each screen's key text appears. Both suites pass.
- [ ] **Step 8: Commit:** `Rebuild the card around search, part sheet and create screens`.

---

### Task 9: Release preparation and manual acceptance

**Files:**
- Modify: `README.md` (final services table), `manifest.json` (`0.2.0`), spec status → `implemented`

- [ ] **Step 1:** Both suites, `py_compile`, the no-HA-imports grep. Expected: all green, no output.
- [ ] **Step 2: Commit:** `Prepare 0.2.0`.
- [ ] **Step 3: Hand over to the user** (do not run them): `git push origin main` and a GitHub release `v0.2.0`. In HA: HACS → *Custom repositories* → add the repo as *Integration* → install (it overwrites `/config/custom_components/cajonera`); delete `/config/www/cajonera-card.js`, `/config/www/cajonera-layout.js` and the `/local/cajonera-card.js` dashboard resource; restart.
- [ ] **Step 4: Manual acceptance on the user's HA** (from the spec §7.5): create category *Tornillería*; create *Tornillo M3x8* with 40 in the suggested drawer (lit magenta); add 10; update to 47; move 7 to another drawer; withdraw by voice-style service; edit its keywords; type "torn" and see it suggested; all reflected in InvenTree.
