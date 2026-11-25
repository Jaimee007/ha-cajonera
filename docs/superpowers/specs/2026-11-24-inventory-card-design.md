# Inventory management from Home Assistant — design

Date: 2026-11-24 · Status: implemented · Owner of the product decisions: the cabinet's user

## 1. Intent

The cabinet's user wants to do **everything inventory-related from Home Assistant**, on the phone or the PC,
without opening InvenTree's web UI for day-to-day work. Today the `cajonera` integration can search and
withdraw; adding stock, correcting counts, creating parts and organising drawers still require InvenTree.

Typical moment: a purchase arrives. The user knows the **name**, sometimes a **part number**, and the
**quantity received**. Adding is the most common action; correcting to an exact count ("now there are 47")
is the second.

This also moves the integration out of the hardware repository (`Modular_Drawers`) into its own repository,
`ha-cajonera`, installable and updatable with **HACS**.

Product background and the original inventory design (drawer naming A1/B3, sync keyed by drawer address,
low stock, RGB signalling, Alexa): `Modular_Drawers/docs/09_gestion_inventario.md`. That document stays
the authority for those rules; this one adds to it.

## 2. Goals

| # | Goal |
|---|---|
| G1 | Search box **autocompletes** while typing, matching name, part number (IPN) and keywords |
| G2 | A **part sheet** shows stock per drawer and offers **Añadir** (primary), **Retirar** and **Actualizar a** |
| G3 | **Create a part** from the search box when nothing matches ("+ Crear «…»"), with the typed text as its name |
| G4 | When creating a part, the user picks a **category first**; if parts of that category already sit in a drawer, that drawer is **lit and preselected**, and the user can change it. The part always goes into an **existing drawer** |
| G5 | **Move** stock from one drawer to another |
| G6 | **Edit** a part: name, description, category, keywords, minimum stock, part number |
| G7 | **Create categories** from the card |
| G8 | Works well on a **phone** (touch, narrow screen) and on a PC |
| G9 | Integration and card ship together from `ha-cajonera` and install/update through **HACS** |

## 3. Non-goals

| Not doing | Why |
|---|---|
| Deleting parts or stock items from HA | Irreversible in InvenTree and drops the part's history. "Actualizar a 0" covers "there is none left"; real deletion stays in InvenTree's web UI |
| Purchase orders, suppliers, prices | InvenTree's web UI is the right tool |
| Creating new drawers/locations by hand | Drawers come from the cabinet itself (sync keyed by address, `09` §5). A part always goes into an existing drawer |
| Config flow (UI setup of the integration) | YAML setup works and is tested; config flow is a later improvement |
| Voice for the new actions ("he guardado 50…") | Belongs to the Alexa plan; the services added here are what that plan will call |

**Future (recorded, not built now):** part **photos** — either taken with the phone's camera from the card,
or given as an **image URL** that InvenTree downloads (`remote_image` on the part; requires InvenTree's
"download images from URL" setting). Shown as a thumbnail in the part sheet and in suggestions.

## 4. Anti-goals (would be a failure even if it "works")

- Autocomplete that lights drawers, moves the "he cogido" context or otherwise changes state.
- Typing fast leaves stale suggestions on screen (an older, slower answer overwriting a newer one).
- An add or count recorded twice, or on the wrong stock item, without the user noticing.
- A card that is hard to use one-handed on a phone.

## 5. Constraints

- InvenTree 1.7.3 (API 570), reached by HA through a Cloudflare tunnel today; latency matters.
- Every service answers within **3.5 s** (existing rule; the Alexa Lambda gives up at 4 s).
- `logic/` stays pure Python with no Home Assistant imports, tested with pytest; HA glue stays thin.
- InvenTree is the only inventory store (`09` I1). HA keeps caches only.
- Code, docs, comments and commit messages in `ha-cajonera` are **English**. User-facing strings stay **Spanish**.
- Never commit secrets. `.env.local` (InvenTree URL and token for manual tests) stays git-ignored.

## 6. Approaches considered

| Approach | Trade-offs | |
|---|---|---|
| **A · One card, screens that replace each other** (search → part sheet → create) | Search-then-create is one gesture; the drawer grid is shared by every screen; one place to learn. The card grows, so screens must replace each other instead of stacking | **Chosen** by the user |
| B · Two cards ("Cajonera" and "Alta de piezas") | Each card is simpler; the create card could live only on the PC dashboard. But the grid is duplicated, and "search, not found, create" is split across cards | Rejected |

For the repository: **own repo `ha-cajonera` with HACS** (chosen) versus keeping it in `Modular_Drawers` and
copying a zip by hand. HACS needs one integration per repository with `custom_components/<domain>/` at the
root, and gives one-click updates.

## 7. Design

### 7.1 Repository layout

```
ha-cajonera/
├── custom_components/cajonera/
│   ├── __init__.py, sensor.py, manifest.json, services.yaml, translations/{en,es}.json
│   ├── frontend/cajonera-card.js, cajonera-layout.js   ← served by the integration
│   └── logic/                                          ← pure Python
├── tests/                 ← pytest (logic) and node --test (frontend layout)
├── hacs.json, README.md, LICENSE
└── docs/
```

**The integration serves its own card.** On setup it registers `custom_components/cajonera/frontend/` as a
static path and adds the card as a frontend module, so installing the integration from HACS also installs
the card, always at a matching version. The manually added `/local/cajonera-card.js` resource and the copies
in `/config/www/` must be removed after migrating (README covers it).

Git history of `Modular_Drawers/homeassistant/` is carried over (subtree split), then reorganised.
`Modular_Drawers` keeps hardware, firmware and `09`; its `homeassistant/` folder is replaced by a pointer
to `ha-cajonera`.

### 7.2 New services

All answer within the deadline, return a Spanish `frase` when something is said to the user, and never
raise on InvenTree failures (they answer "No puedo consultar el inventario ahora mismo.").

| Service | Input | Returns | Side effects |
|---|---|---|---|
| `cajonera.sugerir` | `texto` | `{sugerencias: [{pieza_pk, nombre, ipn, categoria, total, cajones: [..]}]}`, at most **8**. **Every active InvenTree part** matches, including parts in no drawer (`cajones: []`, shown as «sin cajón»), so an existing part can always be opened instead of hitting the duplicate-name rule | **None.** No LED, no context |
| `cajonera.pieza` | `pieza_pk` | part sheet: name, description, category, keywords, IPN, minimum, total, `cajones: [{cajon, direccion, cantidad}]` (**one row per drawer**, summed), plus `fuera: cantidad` for stock outside the cabinet | **Lights the part's drawers in search mode** (blue, or blue/orange if low, 30 s), like `inventario_buscar`, and sets the "he cogido" context to this part (and drawer if only one) |
| `cajonera.inventario_anadir` | `pieza_pk`, `direccion`, `cantidad` | `{frase, hecho}` | Adds to the part's stock in that drawer, or **creates a stock item there** if it has none. Confirms on the drawer (green flash) |
| `cajonera.inventario_actualizar` | `pieza_pk`, `direccion`, `cantidad` (≥ 0) | `{frase, hecho}` | Sets the **drawer total** of that part to `cantidad` (InvenTree stocktake) |
| `cajonera.inventario_mover` | `pieza_pk`, `desde` (address), `hasta` (address), `cantidad` (optional: everything in `desde`) | `{frase, hecho}` | Transfers between drawers |
| `cajonera.categorias` | — | `{categorias: [{pk, nombre, ruta}]}` | — |
| `cajonera.categoria_crear` | `nombre`, `padre` (optional) | `{categoria: {pk, nombre, ruta}}` | Creates the category |
| `cajonera.sugerir_cajon` | `categoria` | `{direccion, cajon, motivo}` or nulls | Only answers. The card lights the drawer in **"guardar" mode** (magenta, slow breathing, `09` S6) with `guardar_aqui` when it accepts the answer, and puts it out with `guardar_apagar` (no fields) when a null answer is accepted or the create screen is left; the light also ends when the drawer is opened, another light replaces it, or 30 s pass |
| `cajonera.pieza_crear` | `nombre`, `categoria`, `direccion`, `cantidad`, optional `ipn`, `descripcion`, `palabras_clave`, `minimo` | `{frase, hecho, pieza_pk}` | Creates the part with its initial stock in that drawer, in one InvenTree call when possible |
| `cajonera.pieza_editar` | `pieza_pk` + any of `nombre`, `descripcion`, `categoria`, `palabras_clave`, `ipn`, `minimo` | `{frase, hecho}` | Patches the part |

Rules:

- **Everything works per drawer, never per stock item** (same as voice withdrawal, `09` §4.3). InvenTree may
  hold several stock items of one part in one drawer; the user only knows the drawer. Before a count or a
  move, the part's stock items in that drawer are **merged into one** (InvenTree's stock merge), then the
  single item is counted or transferred. Adding goes to that merged item. *(Decided by the builder after
  the review: per-item actions would let "Actualizar a 47" leave the drawer at 52.)*
- **A part stays in its drawer at zero** (user's decision, to keep the cabinet in order). Stock items the
  integration creates or touches get `delete_on_deplete = false`, so "Actualizar a 0" or withdrawing the last
  unit keeps an empty stock item in the drawer. Searches then answer "Tenías … en el B3, pero no queda
  ninguno", light the drawer so the user knows where to restock, and the part counts as low if it has a
  minimum. Existing stock items in cabinet drawers are switched to `delete_on_deplete = false` once, during
  location sync.
- **Opening a part lights it** (user's decision): choosing a suggestion, or pressing Enter on a highlighted
  one, opens the part sheet and lights its drawers like a search. Pressing **Enter on typed text** with no
  suggestion highlighted runs the existing search (`inventario_buscar`): it lights every matching drawer and
  shows the result list, each row opening its part sheet.

- **Drawer suggestion (G4):** the drawer that holds the **most stock items** of parts in that category
  (ties: lowest address). If no drawer holds that category, suggest the **first empty drawer** by name
  order (A1, A2, …, B1, …); if there is none, suggest nothing and let the user pick. The suggestion is only
  a preselection: any existing drawer is accepted. *(Empty-drawer fallback is the builder's proposal; the
  user asked only for the category rule.)*
- **Part number:** stored in InvenTree's **IPN** field. It needs no manufacturer company and is already
  searchable. *(Proposal: a manufacturer part with MPN would require choosing or creating a manufacturer
  company for each part, which the user does not usually know.)*
- **Duplicate names:** `pieza_crear` refuses when a part with exactly the same name (case-insensitive)
  already exists, and answers "Ya existe «Tornillo M3x8». Ábrela para añadir." The card offers to open it.
- **Quantities:** `cantidad` > 0 for add, move and create; ≥ 0 for update. Otherwise "Dime una cantidad válida."
  with nothing written.
- **After any write:** refresh the low-stock report, set the "he cogido" context to the part and drawer touched,
  and confirm on the drawer (green, or orange if the part is now low — same as withdrawal).
- **Unknown drawer address** (not in `locations`): refuse, nothing written.
- **Timeout after a write has started:** answer "No sé si se ha registrado. Revisa la cantidad antes de repetirlo."
  (same reasoning as `09` §4.4 for withdrawals).

### 7.3 Card

Screens replace each other inside one card; the drawer grid stays visible under the search screen and
the create screen (the create screen needs it to pick a drawer).

```
┌ Buscar ───────────────────────┐   ┌ Ficha: Tornillo M3x8 ─────────┐   ┌ Nueva pieza ──────────────────┐
│ [ torn|                    ]  │   │ ← Volver        [Editar]      │   │ ← Volver                      │
│ ▸ Tornillo M3x8   B1 · 40 uds │   │ Tornillería · IPN M3-8        │   │ Nombre   [Tornillo M4x10   ]  │
│ ▸ Tornillo M3x12  B3 · 12 ⚠   │   │ B1 ........ 40 uds            │   │ Categoría[Tornillería    ▾]  │
│ ▸ + Crear «torn»              │   │ B3 ........  5 uds            │   │ Cajón    B1 (sugerido) ←toca  │
│                               │   │                               │   │ Cantidad [ 50 ]               │
│  C  [C1][C2][C3]              │   │ [ Añadir ][Retirar][Actualizar]│  │ Ref./IPN [        ] opcional  │
│  B  [B1 ...][B3]              │   │ Cajón [B1 ▾]  Cantidad [ 10 ] │   │ Mínimo   [  ] · Palabras [  ] │
│  A  [A1][A2][A3]              │   │        [ Confirmar ]          │   │        [ Crear pieza ]        │
└───────────────────────────────┘   │ Mover: [B3 ▾] → [C1 ▾] [Mover]│   │  (grid: B1 pulsing magenta)   │
                                    └───────────────────────────────┘   └───────────────────────────────┘
```

- **Autocomplete:** starts at **2** characters, waits **300 ms** after the last keystroke, shows at most
  8 suggestions plus "+ Crear «texto»" as the last row. Each request carries a sequence number; an answer
  older than the latest request is discarded. Keyboard: ↑/↓ and Enter on PC; tap on phone.
- **Part sheet:** the action selector defaults to **Añadir**. The drawer selector defaults to the drawer
  with the most stock of that part. **Actualizar a** shows the current quantity prefilled. Each write
  disables its button until the answer arrives (prevents double submission).
- **Create screen:** category first (dropdown with "+ Nueva categoría…" inline); choosing it calls
  `sugerir_cajon` and preselects the drawer; tapping another grid cell changes it.
- **Edit:** a form like the create screen, prefilled, without the drawer and quantity fields.
- **Phone:** single column, touch targets ≥ 44 px, numeric keyboard for quantities (`inputmode="decimal"`),
  no hover-only affordances. State (screen, chosen part, typed values, focus) survives sensor-driven re-renders.
- Every `frase` is shown as a toast; errors too.

### 7.4 Migration (first task)

1. From `Modular_Drawers` branch `feature/inventario-ha` (the code running on the user's HA), split
   `homeassistant/` with its history (`git subtree split`).
2. Bring that history into `ha-cajonera` on top of its existing initial commit (merge of unrelated
   histories, no force-push), then move files into the HACS layout of §7.1.
3. Add `hacs.json`, an English `README.md` (install with HACS, YAML configuration, migrating from the
   manual zip: remove `/config/www/cajonera-*.js` and the `/local/cajonera-card.js` resource), and versioning:
   `manifest.json` `version` bumped per release, GitHub releases tagged `vX.Y.Z`. The repo is public.
4. In `Modular_Drawers`, replace `homeassistant/` with a short README pointing to `ha-cajonera`, and update
   the references in its docs. That is a separate commit in that repository.

### 7.5 Testing

- `logic/` additions get pytest tests with the existing `FakeInventory`, extended with add, count, transfer,
  create part, create category, edit, categories and stock-by-category.
- The InvenTree client gets tests on the local HTTP test server for each new endpoint (method, path, body).
- Frontend: pure helpers (suggestion merging, stale-answer guard, drawer default choice) in
  `cajonera-layout.js`, tested with `node --test`. The rest is checked manually on HA.
- Manual acceptance on the user's HA: create a category and a part, see the suggested drawer lit, add,
  update, move, withdraw, edit, all reflected in InvenTree.

## 8. Left to the builder

- Exact InvenTree endpoints and field names for add, count, transfer, part create (`initial_stock`),
  category list/create and stock filtered by category: verify against `/api-doc/` of the installed version
  (1.7.3), as done for metadata (`/api/metadata/stocklocation/<pk>/`, the per-model path redirects).
- Whether InvenTree's part search already matches IPN and keywords (it should); if not, merge results.
- The card's visual details within §7.3.
- The HA API used to serve the frontend files (`async_register_static_paths` with `StaticPathConfig`,
  and `frontend.add_extra_js_url`), per the HA version installed.
