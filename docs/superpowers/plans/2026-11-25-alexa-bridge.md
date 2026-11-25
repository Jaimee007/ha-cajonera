# Alexa bridge — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Voice and the Lovelace card behave identically: a new HA service `cajonera.voz` holds the voice rules, the user's Alexa Lambda becomes a thin bridge that speaks its answers (and asks follow-up questions), and a script generates the skill's interaction model from InvenTree.

**Architecture:** Pure-Python rules in `custom_components/cajonera/logic/voice.py` return a `VoiceResult` (sentence, optional question, and the lights/confirm/context side effects as data); `Cajonera.voz` in `logic/service.py` applies them and `__init__.py` registers the service. The Lambda (`alexa/lambda_function.py`, one file, stdlib + `urllib3`) maps Alexa intents to `cajonera.voz`, keeps the pending question in Alexa session attributes and falls back to a read-only InvenTree lookup. `alexa/generate_model.py` (+ `alexa/spoken.py`) turns InvenTree parts and drawers into the skill's model JSON using a hand-written template.

**Tech Stack:** Python 3.13 for the integration (pytest, pytest-asyncio); the Lambda must also run on **Python 3.9+** (`from __future__ import annotations`, no `match`); `urllib3`; Alexa Skills Kit raw JSON (no `ask-sdk`); InvenTree 1.7.3 REST API.

**Spec:** [docs/superpowers/specs/2026-11-25-alexa-bridge-design.md](../specs/2026-11-25-alexa-bridge-design.md). Rules it builds on: `2026-11-24-inventory-card-design.md` and `Modular_Drawers/docs/09_gestion_inventario.md` §4.3–4.4.

## Global Constraints

- Branch `feature/alexa-bridge` (already holds the seed script and the spec). **Never push, tag or release**; the last task hands the user the commands. Commits are local; end each message with the `Co-Authored-By` trailer of the model that wrote the code.
- `logic/` never imports `homeassistant`: `grep -rn --include=*.py homeassistant custom_components/cajonera/logic/` prints nothing.
- **No quantity is ever assumed.** "añade tornillos" asks "¿Cuántos?"; "añade **un** tornillo" is 1.
- At most **two consecutive failed answers**, or **four questions for one order**, then "No consigo entenderlo. Hazlo desde el panel de la cajonera." and the session ends.
- Alexa gives **8 s**; the Lambda's own timeout is configured to **8 s**; the HA call gets at most **4 s**; the read-only fallback gets the time left minus a **1 s** margin (`context.get_remaining_time_in_millis()`); `urllib3` runs with **`retries=False`** so a POST is never sent twice.
- A failure that never reached HA (DNS, refused, connect timeout, HTTP 4xx) is certain not to have written: "No he podido hablar con Home Assistant, no he cambiado nada." A read timeout, a dropped connection or HTTP 5xx after the request was sent: "No sé si se ha registrado. Pregúntame cuánto queda antes de repetirlo."
- Every response that keeps the session open carries a **reprompt**.
- Lambda configuration only through environment variables `HA_URL`, `HA_TOKEN`, `INVENTREE_URL`, `INVENTREE_TOKEN`, optional `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` (Cloudflare Access headers off unless both are set). The Lambda sends its own `User-Agent` (Cloudflare returns 403 to Python's default). **Logs contain only the intent name, slot names and an outcome class** — never the event, headers or a token.
- Invocation name stays **"mi atlas"**; the five existing intents keep their names (`ConsultarInventarioIntent`, `AnadirInventarioIntent`, `RestarInventarioIntent`, `ActualizarInventarioIntent`, `LocalizarInventarioIntent`) and slot names (`producto`, `cantidad`).
- Code, comments, docs and commit messages in **English**; everything Alexa says is **Spanish** with correct orthography (accents), exactly as written in this plan.
- Alexa lists are capped: question options **20**, spoken names **3** ("…, … y N más"), drawers lit **10** (`MAX_LIT`).

## Review Focus

1. **"añade un tornillo" vs "añade tornillos"** — 1 vs a question. → Tasks 2 and 5.
2. **A misheard drawer** ("en el C2" for a part that lives in B1/B3) is refused and re-asked, never silently used or created. → Task 2.
3. **Wording after a failed write**: certain-not-written vs maybe-written, and the POST is never retried. → Task 6.
4. **A shared keyword** ("tornillos" → several ids): `buscar` lights all of them without asking; writes ask which, and the answer is intersected with the stored candidates. → Tasks 1, 2 and 5.
5. **Logs never contain the event or a token**, even when everything fails. → Task 6.

---

## File structure

```
custom_components/cajonera/logic/
├── voice.py            ← NEW  Pregunta, VoiceResult, Listo; resolve_parts, voz_buscar, plan_voice, voz
├── search.py           ← modify: search_parts_result() factored out of search()
├── withdraw.py         ← modify: candidate_parts(); notes parameter on withdraw_by_part
├── service.py          ← modify: Cajonera.voz
└── inventree.py        ← modify: get_location()
custom_components/cajonera/{__init__.py, services.yaml, translations/{es,en}.json}  ← register `voz`
alexa/
├── lambda_function.py  ← NEW  the bridge (one file)
├── spoken.py           ← NEW  Spanish spoken forms and number speller (generator only)
├── generate_model.py   ← NEW
├── model_template.json ← NEW  hand-written intents and utterances
├── README.md           ← NEW
├── seed_test_parts.py  ← exists
└── tests/              ← NEW  conftest.py, test_lambda_*.py, test_spoken.py, test_generate_model.py
tests/test_voice.py, tests/test_service.py, tests/test_inventree.py, tests/fakes.py  ← extended
pyproject.toml          ← testpaths = ["tests", "alexa/tests"]
```

Python commands run from the repo root: `python -m pytest -q`. Baseline before Task 1: **307 passed**.

---

### Task 1: Voice core — part resolution and voice search

**Files:**
- Modify: `custom_components/cajonera/logic/withdraw.py`, `logic/search.py`, `logic/inventree.py`, `tests/fakes.py`
- Create: `custom_components/cajonera/logic/voice.py`
- Test: `tests/test_voice.py`, `tests/test_inventree.py`, `tests/test_search.py`, `tests/test_withdraw.py`

**Interfaces:**
- Produces in `withdraw.py`: `async def candidate_parts(api, text: str, locations: dict[int, Location]) -> list[Part]` — exactly the filtering `_resolve_part` does today (an exact case-insensitive name match wins and returns just that part; otherwise the parts that have stock in cabinet drawers; if none is stocked, all matches), with `_resolve_part` rewritten to call it. Existing withdraw tests stay green unchanged.
- Produces in `search.py`: `async def search_parts_result(api, parts: list[Part], locations, outside: dict[int, str] | None = None) -> SearchResult` — everything `search()` does after it has found the parts (stock lookup, per-drawer grouping, phrases, caps), with `search()` now calling it. When no part has stock in a cabinet drawer and `outside` (InvenTree location pk → name) is given, the phrase is `"Está en {nombre}. Quedan {total}."` using the outside location(s) (several → `"Está en A, B y C. Quedan {total}."`); without `outside` the old phrase stays.
- Produces in `inventree.py` and `FakeInventory`: `async def get_location(self, pk: int) -> Location` → `GET /api/stock/location/<pk>/` mapped to `Location(pk, name, address)` (address from `/api/metadata/stocklocation/<pk>/` is **not** fetched here: use `address=None`).
- Produces in `voice.py`:
  - `@dataclass(frozen=True) Pregunta(falta: str, pieza_pk: int | None = None, opciones: list = field(default_factory=list))`
  - `@dataclass(frozen=True) VoiceResult(frase: str, hecho: bool = False, pregunta: Pregunta | None = None, iluminar: list[int] = [], bajos: set[int] = set(), confirmar: tuple[int, bool] | None = None, contexto: Context | None = None, refrescar_bajo: bool = False)` (use `field(default_factory=…)`)
  - `async def resolve_parts(api, locations, *, pieza_pk, pieza_pks, texto) -> list[Part]` — `pieza_pk` → `[get_part]`; else `pieza_pks` (first 20) → their parts; else `texto` → `candidate_parts`; else `[]`.
  - `async def voz_buscar(api, locations, context, now, *, pieza_pk=None, pieza_pks=None, texto=None) -> VoiceResult`.

- [ ] **Step 1: Failing tests.**
  - `test_inventree.py`: `test_get_location_maps_name` (server answers `{"pk": 7, "name": "Nevera"}` → `Location(7, "Nevera", None)`).
  - `test_withdraw.py`: `test_candidate_parts_exact_name_wins`, `test_candidate_parts_prefers_stocked_in_cabinet`.
  - `test_search.py`: `test_search_parts_result_matches_search` (same result as `search()` for the same parts), `test_outside_cabinet_uses_real_location` → `"Está en Nevera. Quedan 12."`, `test_outside_ignored_when_cabinet_has_stock`.
  - `test_voice.py` (use `FakeInventory`, the `make`-style helpers of `tests/test_service.py`):
    - `test_buscar_with_no_part_asks_what` → `VoiceResult("¿Qué quieres buscar?")`
    - `test_buscar_one_part_lights_its_drawers_and_sets_context` (`iluminar == [addresses]`, `bajos` for low parts, `contexto == Context(part_pk, location_pk, now)` for one part in one drawer, `contexto.location_pk is None` when in two drawers)
    - `test_buscar_shared_keyword_searches_all_without_asking` (`pieza_pks=[a, b, c]` → `pregunta is None`, every drawer lit, phrase names at most 3 parts)
    - `test_buscar_caps_lit_drawers_at_ten`
    - `test_buscar_outside_cabinet_answers_real_location` (the fake holds a location not in `locations`)
    - `test_buscar_unknown_text` → `"No encuentro nada con ese nombre."`
    - `test_buscar_down` (`fake.fail = True`) → `DOWN_PHRASE`, nothing lit
- [ ] **Step 2:** `python -m pytest -q` → the new tests FAIL (names not defined).
- [ ] **Step 3: Implement** the pieces in the order listed under Interfaces; `voz_buscar` fetches names for stock locations that are not in `locations` with `api.get_location` and passes them as `outside`.
- [ ] **Step 4:** `python -m pytest -q` → all pass (307 + new).
- [ ] **Step 5: Commit:** `Add voice core: part resolution and voice search`.

---

### Task 2: Voice rules — questions and refusals

**Files:**
- Modify: `custom_components/cajonera/logic/voice.py`
- Test: `tests/test_voice.py`

**Interfaces:**
- Consumes: Task 1; `withdraw.CONTEXT_TTL`, `stock_ops._find`-style drawer lookup (copy the one-liner, do not import a private); `search.is_unplaced`, `MAX_LIT`.
- Produces: `@dataclass(frozen=True) Listo(part: Part, location: Location, cantidad: float)` and
  `async def plan_voice(api, locations, context, now, *, accion, pieza_pk=None, pieza_pks=None, texto=None, cantidad=None, cajon=None, uno=False) -> VoiceResult | Listo` for `accion` ∈ `anadir | retirar | actualizar`. It returns a `VoiceResult` with a `pregunta` or a refusal (nothing written), or `Listo` when everything is known.
- Rules, first that fits wins; phrases exact (`{X}` = drawer name, `{Pieza}` = part name):
  1. **Part.** `resolve_parts`. None found: `retirar` with a context younger than `CONTEXT_TTL` → that part; otherwise ask `pieza` with no options: `"¿Qué has cogido?"` for `retirar`, `"¿De qué producto?"` otherwise. Several: `anadir`/`retirar`/`actualizar` ask `pieza` with `opciones=[{"pieza_pk", "nombre"}]` (max 20), phrase `"Hay {a}, {b} y {c}. ¿Cuál?"` (more than 3 → `"Hay {a}, {b}, {c} y {n} más. ¿Cuál?"`). Text given but nothing found → `VoiceResult("No encuentro nada con ese nombre.")`.
  2. **Quantity**, after the part, so the question carries `pieza_pk`. `uno` → 1. None → ask `cantidad`: `"¿Cuántos?"` (`actualizar`: `"¿A cuántos?"`), `opciones=[]`. ≤ 0 (< 0 for `actualizar`) → ask again with the same phrases.
  3. **Drawer.** Only drawers in `locations` that are not unplaced count.
     - Explicit `cajon` (case-insensitive): honoured or refused, never ignored. Part in some drawers and `cajon` not one of them: `"No hay {Pieza} en el {X}; hay en el B1 y en el B3. ¿En cuál?"` → ask `cajon` with the part's drawers. Unknown drawer name: `"No conozco el cajón {X}. ¿En cuál?"` → ask `cajon` with the part's drawers (or every drawer when the part has none).
     - No `cajon`, part in one drawer → that one. In several: `retirar` uses the context drawer if `context.part_pk` is this part, fresh, and `location_pk` is one of its drawers; otherwise ask `cajon` (`"Hay en el B1 y en el B3, ¿en cuál?"`, up to 3 names then `"y N más"`) with `iluminar` = those drawers' addresses. `anadir`/`actualizar` ask `cajon` the same way.
     - Part in no drawer: `retirar`/`actualizar` → `VoiceResult(f"Hay {total} de {Pieza}, pero no está en ningún cajón.")` (no question); `anadir` → ask `cajon` with every real drawer name sorted, phrase `"¿En qué cajón la guardo?"`.
     - **`anadir` with an explicit `cajon` that is a real drawer but not one of the part's drawers, when the part already lives in some drawer** → refuse, no question: `"{Pieza} no está en el {X}; está en el B1. Para ponerla ahí, hazlo desde el panel."` (deliberately stricter than the card).
  4. All known → `Listo(part, location, cantidad)`.

- [ ] **Step 1: Failing tests** in `test_voice.py` (names are the behaviour):
  - `test_anadir_without_quantity_asks_cuantos` and `…_with_uno_is_one` (Review Focus 1: `uno=True` → `Listo.cantidad == 1`; `uno=False, cantidad=None` → `pregunta.falta == "cantidad"` and `pregunta.pieza_pk` set)
  - `test_actualizar_asks_a_cuantos`, `test_zero_quantity_asks_again_except_actualizar`
  - `test_shared_keyword_write_asks_pieza_with_options` and `test_more_than_three_candidates_say_n_mas`
  - `test_retirar_uses_fresh_context_part_and_drawer`, `test_retirar_ignores_stale_context` (121 s old), `test_retirar_context_of_another_part_is_not_used`
  - `test_explicit_drawer_not_holding_the_part_is_refused` (Review Focus 2: phrase exact, `pregunta.falta == "cajon"`, options are the part's drawers, `fake.removed == [] and fake.added == []`)
  - `test_unknown_drawer_name_is_refused`
  - `test_several_drawers_ask_cajon_and_light_candidates` (`iluminar` set)
  - `test_anadir_on_part_in_no_drawer_asks_every_drawer`, `test_retirar_on_part_in_no_drawer_refuses`
  - `test_anadir_to_wrong_drawer_when_part_lives_elsewhere_is_refused_to_the_panel`
  - `test_single_drawer_part_is_chosen_automatically`
  - `test_unplaced_drawers_are_never_offered`
- [ ] **Step 2:** run → FAIL. **Step 3:** implement `Listo` and `plan_voice`. **Step 4:** `python -m pytest -q` → all pass.
- [ ] **Step 5: Commit:** `Add voice rules: questions and refusals`.

---

### Task 3: Voice execution and confirmation

**Files:**
- Modify: `custom_components/cajonera/logic/voice.py`, `logic/withdraw.py`
- Test: `tests/test_voice.py`, `tests/test_withdraw.py`

**Interfaces:**
- Consumes: `plan_voice`, `Listo` (Task 2); `stock_ops.add_to_drawer`, `count_drawer` (`-> OpResult(phrase, done, address, low, part_pk, location_pk)`); `withdraw.withdraw_by_part`.
- Modifies: `withdraw_by_part(..., notes: str = "cajonera: panel")` — the note string passed to `_remove`; existing behaviour and tests unchanged.
- Produces: `async def voz(api, locations, context, now, *, accion, pieza_pk=None, pieza_pks=None, texto=None, cantidad=None, cajon=None, uno=False) -> VoiceResult` — `buscar` → `voz_buscar`; the writes → `plan_voice` and, on `Listo`, the matching operation with notes `"cajonera: voz"`.
- On success (`done`), the `VoiceResult` has `hecho=True`, `confirmar=(address, low)`, `contexto=Context(part_pk, location_pk, now)` and `refrescar_bajo=True`. A refusal or failure writes nothing and sets none of them.
- **Confirmation sentence** names the part, the quantity and the drawer, then the operation's own remaining text: it starts `"Hecho. He retirado {q} de {Pieza} del {X}."` / `"Hecho. He añadido {q} de {Pieza} al {X}."` / `"Hecho. He dejado {Pieza} en {q} en el {X}."`, followed by the original result phrase **minus its leading `"Hecho. "`** (e.g. `"Quedan 10."`, `"Ahora hay 170 en el B3."`, low-stock suffix kept). Quantities with `fmt_qty`.
- Failure wording passes through: the operations already return `MAYBE_STOCK`/`MAYBE_DONE`/`DOWN_PHRASE`; `voz` returns them as `frase` with `hecho=False`.

- [ ] **Step 1: Failing tests:** `test_retirar_confirmation_names_part_quantity_drawer` (exact: `"Hecho. He retirado 2 de Huevos del B1. Quedan 10."`), `test_anadir_confirmation`, `test_actualizar_confirmation`, `test_success_sets_confirm_context_and_low_refresh`, `test_refusal_has_no_side_effects`, `test_write_failure_after_start_says_maybe_registered`, `test_voice_uses_the_voice_note` (`fake.last_notes == "cajonera: voz"`), `test_buscar_via_voz_matches_voz_buscar`; in `test_withdraw.py`: `test_withdraw_by_part_default_note_is_panel`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement `voz` and the `notes` parameter. **Step 4:** full suite passes.
- [ ] **Step 5: Commit:** `Execute voice orders with spoken confirmations`.

---

### Task 4: The `cajonera.voz` service

**Files:**
- Modify: `custom_components/cajonera/logic/service.py`, `__init__.py`, `services.yaml`, `translations/es.json`, `translations/en.json`, `README.md` (services table)
- Test: `tests/test_service.py`

**Interfaces:**
- Consumes: `voice.voz`, `VoiceResult` (Task 3); `Cajonera._bounded`, `_ready`, `simulator`, `context`, `now`, `locations`.
- Produces: `async def voz(self, accion: str, pieza_pk=None, pieza_pks=None, texto=None, cantidad=None, cajon=None, uno=False) -> dict` returning `{"frase": str, "hecho": bool, "pregunta": None | {"falta": str, "pieza_pk": int | None, "opciones": list}}`.
- Behaviour: `_ready()` false → `{"frase": DOWN_PHRASE, "hecho": False, "pregunta": None}`. Otherwise run `voz` under `_bounded`; on a timeout: `buscar` → the DOWN shape, a write → `{"frase": MAYBE_STOCK, "hecho": False, "pregunta": None}`. Apply the result: `iluminar` non-empty → `simulator.search(iluminar, bajos)`; `confirmar` → `simulator.confirm(*confirmar)`; `contexto` not None → `self.context = contexto`; `refrescar_bajo` → the existing background low-stock refresh used by other writes.
- HA service `voz` (`SupportsResponse.ONLY`): fields `accion` (required, `vol.In(["buscar","anadir","retirar","actualizar"])`), `pieza_pk` (`vol.Any(None, ID)`), `pieza_pks` (`vol.Any(None, [ID])`, max 20 items), `texto` (`OPTIONAL_TEXT`), `cantidad` (`QUANTITY`), `cajon` (`OPTIONAL_TEXT`), `uno` (`cv.boolean`, default false). Described in `services.yaml`, named in both translation files, one row in the README's services table.

- [ ] **Step 1: Failing tests** (`test_service.py`): `test_voz_buscar_lights_drawers_and_sets_context`, `test_voz_asks_cajon_and_lights_candidates`, `test_voz_write_confirms_on_the_drawer`, `test_voz_question_leaves_everything_unchanged`, `test_voz_write_timeout_says_maybe_registered`, `test_voz_buscar_timeout_says_down`, `test_voz_down_when_inventree_down`, `test_voz_response_shape` (exact keys).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement `Cajonera.voz`, then register in `__init__.py` (same style as the other services), `services.yaml`, translations, README. **Step 4:** `python -m pytest -q`, `python -m py_compile custom_components/cajonera/__init__.py` → pass / no output; the no-HA-imports grep prints nothing.
- [ ] **Step 5: Commit:** `Add the cajonera.voz service`.

---

### Task 5: Lambda conversation logic

**Files:**
- Create: `alexa/lambda_function.py`, `alexa/tests/conftest.py` (adds `alexa/` to `sys.path`), `alexa/tests/test_lambda_logic.py`
- Modify: `pyproject.toml` (`testpaths = ["tests", "alexa/tests"]`)

**Interfaces (no network in this task; transports are injected):**
- Constants: `MAX_FALLOS = 2`, `MAX_PREGUNTAS = 4`, `MAX_OPCIONES = 20`.
- `class HAError(Exception)` with attribute `sent: bool` (False = certain nothing was written).
- `def resolve_producto(slot: dict | None) -> dict` → `{"pieza_pk": int}` (one id), `{"pieza_pks": [int…]}` (several, ≤ 20, de-duplicated, order kept), `{"texto": str}` (no match or no resolution but a raw value), `{}` (empty). Flatten every `resolutionsPerAuthority` entry with status code `ER_SUCCESS_MATCH`; ids are `values[i].value.id` as strings → `int`.
- `def resolve_cajon(slot: dict | None) -> str | None` → the resolved id (e.g. `"B3"`), else the raw value upper-cased with spaces removed, else `None`.
- `def slot_number(slot) -> float | None` (`AMAZON.NUMBER` value, comma tolerated); `def slot_uno(slot) -> bool` (a resolved `UnoUna` value present).
- `def build_request(intent_name: str, slots: dict) -> dict | None` → `{"accion": "buscar"|"anadir"|"retirar"|"actualizar", **producto fields, "cantidad"?, "uno"?}` for the five order intents; `None` for anything else.
- `def speak(text: str, *, end: bool = True, reprompt: str | None = None, attrs: dict | None = None) -> dict` → Alexa response; `end=False` without `reprompt` uses `text` as the reprompt.
- `def handle_event(event: dict, call_ha, fallback_buscar, remaining_ms: int = 8000) -> dict` — `call_ha(payload: dict, timeout: float) -> dict` returns the `voz` output (`frase`, `hecho`, `pregunta`) or raises `HAError`; `fallback_buscar(request: dict, timeout: float) -> str` returns a sentence or raises `RuntimeError`.
- Behaviour of `handle_event`:
  - `LaunchRequest` → `"Bienvenido al inventario. ¿Qué quieres consultar o modificar?"`, session open, reprompt `"Dime qué producto quieres consultar o modificar."`. `SessionEndedRequest` → `{"version": "1.0"}`. Help, Stop/Cancel/No/NavigateHome, Fallback answer as the current Lambda does (Help lists examples; Stop/Cancel/No/NavigateHome say `"Hasta luego."` and end; Fallback `"No te he entendido. Prueba a decir: añade seis huevos o dónde están los tornillos."` open).
  - An order intent → `build_request`, call HA with `timeout = 4.0`. `pregunta` set → keep the session open, speak `frase`, store `{request, falta, opciones, fallos, preguntas}` in session attributes (opciones ≤ 20). No `pregunta` → speak `frase` and end.
  - `RespuestaCajonIntent` (slot `cajon`), `RespuestaCantidadIntent` (`cantidad`), `RespuestaPiezaIntent` (`producto`): with a pending state fill the missing field (`falta`) and call HA again; the pieza answer is **intersected** with the stored `opciones` (an ambiguous answer narrows, it does not reset). An answer **fails** when it does not fill the pending field (empty slot, not among the options, or HA asks the same thing again); a fill resets `fallos`. `fallos >= MAX_FALLOS` or `preguntas >= MAX_PREGUNTAS` → `"No consigo entenderlo. Hazlo desde el panel de la cajonera."` and end. No pending state → `"¿Qué quieres hacer?"`, open.
  - A new order intent while a question is pending replaces the pending state.
  - A `pieza_pks` request for a **write** is sent as is (HA asks); for `buscar` it is sent as is too (HA searches all).
  - `HAError` handling is Task 6.

- [ ] **Step 1: Failing tests** (`alexa/tests/test_lambda_logic.py`, canned events built by small helper functions; `call_ha` is a fake recording its payloads):
  - `test_resolve_producto_one_id`, `…_several_ids_flattened_and_deduplicated`, `…_no_match_falls_back_to_text`, `…_empty`
  - `test_resolve_cajon_prefers_resolved_id`, `test_slot_number_and_uno`
  - `test_add_with_number_sends_cantidad`, `test_add_with_uno_sends_uno_and_no_cantidad`, `test_add_without_either_sends_neither` (Review Focus 1: the Lambda never invents a quantity)
  - `test_question_keeps_session_open_with_reprompt_and_stores_state`
  - `test_cajon_answer_fills_the_field_and_ends_on_success`
  - `test_pieza_answer_is_intersected_with_stored_candidates` (Review Focus 4)
  - `test_two_failed_answers_send_the_user_to_the_panel`, `test_four_questions_send_the_user_to_the_panel`, `test_a_filled_answer_resets_the_failure_count`
  - `test_new_order_replaces_pending_state`, `test_answer_without_pending_state_asks_what_to_do`
  - `test_launch_help_stop_fallback_wording`
  - `test_every_open_response_has_a_reprompt` (iterate all open responses the other tests produce)
- [ ] **Step 2:** `python -m pytest alexa/tests -q` → FAIL. **Step 3:** implement. **Step 4:** `python -m pytest -q` → all pass.
- [ ] **Step 5: Commit:** `Add the Lambda conversation logic`.

---

### Task 6: Lambda transports, fallback and logging

**Files:**
- Modify: `alexa/lambda_function.py`
- Test: `alexa/tests/test_lambda_transport.py`

**Interfaces:**
- Consumes: `handle_event`, `HAError`, `speak` (Task 5).
- Produces: `lambda_handler(event, context) -> dict` (the AWS entry point; reads `remaining_ms` from `context.get_remaining_time_in_millis()`); module-level `_http = urllib3.PoolManager()` reused across warm invocations; `def ha_call(payload: dict, timeout: float) -> dict` → `POST {HA_URL}/api/services/cajonera/voz?return_response` with `Authorization: Bearer {HA_TOKEN}`, JSON body, `retries=False`, `urllib3.Timeout(connect=2.0, read=timeout)`, returning the `service_response` object of HA's reply envelope `{"changed_states": [...], "service_response": {...}}`; `def inventree_readonly(request: dict, timeout: float) -> str` → read-only lookup (parts by id/ids/text, their stock and locations, `User-Agent` of its own, Cloudflare Access headers only when both env vars are set) returning `"Está en el B3. Ahora no puedo encender el cajón."`-style sentences (one drawer → `"Está en el {X}."`; several → `"Hay en el B1 y en el B3."`; quantities from the stock; nothing found → `"No encuentro nada con ese nombre."`), raising `RuntimeError` on failure.
- `HAError.sent` mapping: connect timeout, DNS/connection refused, HTTP 4xx → `sent=False`; read timeout, dropped connection, HTTP 5xx → `sent=True`.
- In `handle_event`, `HAError` handling: `buscar` → try `fallback_buscar(request, timeout=max(0.5, (remaining_ms / 1000) - 4.0 - 1.0))`, else (`RuntimeError`) `"No puedo consultar el inventario ahora mismo."`; any write → `sent=False`: `"No he podido hablar con Home Assistant, no he cambiado nada."`, `sent=True`: `"No sé si se ha registrado. Pregúntame cuánto queda antes de repetirlo."`; the session ends. A fallback or failure never keeps a pending question.
- **Logging:** a single helper `_log(intent: str, slots: list[str], outcome: str)` is the only thing that writes logs; `print(event)`, `json.dumps(event)` and header/URL logging do not exist.

- [ ] **Step 1: Failing tests:** `test_ha_call_posts_expected_request` (fake `_http`: URL, headers, body, `retries=False`), `test_ha_call_returns_service_response`, `test_ha_error_sent_flag_per_failure_kind` (parametrised: connect timeout, refused, 401, 404 → `False`; read timeout, protocol error, 500, 502 → `True`), `test_write_failure_wording_depends_on_sent` (Review Focus 3), `test_post_is_never_retried` (the fake transport counts exactly one request on failure), `test_buscar_falls_back_to_inventree_read_only`, `test_fallback_deadline_uses_remaining_time`, `test_fallback_failure_says_down`, `test_cloudflare_headers_only_when_both_env_vars_set`, `test_user_agent_is_not_pythons_default`, `test_logs_never_contain_the_event_or_tokens` (Review Focus 5: capture `logging`/`print` output while running every path, including failures, with an event whose `context.System.apiAccessToken` and env tokens are distinctive strings; assert none appears), `test_lambda_handler_end_to_end_with_fake_transport`.
- [ ] **Step 2:** `python -m pytest alexa/tests -q` → FAIL. **Step 3:** implement. **Step 4:** full suite passes; `python -m py_compile alexa/lambda_function.py` → no output.
- [ ] **Step 5: Commit:** `Add Lambda transports, read-only fallback and safe logging`.

---

### Task 7: Spoken forms for the model generator

**Files:**
- Create: `alexa/spoken.py`
- Test: `alexa/tests/test_spoken.py`

**Interfaces (pure functions):**
- `def number_to_spanish(n: int) -> str` for `0 ≤ n ≤ 999 999` (`0` → `"cero"`, `1` → `"uno"`, `21` → `"veintiuno"`, `100` → `"cien"`, `101` → `"ciento uno"`, `1000` → `"mil"`, `21 000` → `"veintiún mil"`, `999 999` → `"novecientos noventa y nueve mil novecientos noventa y nueve"`); outside the range → `ValueError`.
- `def clean(text: str) -> str` → lowercase, every character that is not a letter (accents and `ñ` kept), digit or space becomes a space, spaces collapsed and trimmed.
- `def spoken_forms(text: str) -> list[str]` → de-duplicated, order kept: the cleaned form first (if it has no digit it is the only one) then the **spelled-out** form when the cleaned form has digits: each run of digits becomes its words (`"tornillo m3x8"` → `"tornillo m tres x ocho"`; `"4k7"` → `"cuatro k siete"`); a run above 999 999 skips the spelled form (the cleaned form with digits is then **not** speakable: return `[]`). Forms that still contain a digit are dropped, so the result only has letters and spaces; empty cleaned text → `[]`.
- `def plural_variants(form: str) -> list[str]` → for a **single word** ending in a vowel: `form + "s"`; ending in `as` or `os`: `form[:-1]`; nothing else is guessed.
- `def drawer_synonyms(name: str) -> list[str]` for names like `"B3"`: `["b tres"?]` — the letter spoken as Spanish letter names (`a` → `"a"`, `b` → `"be"` and `"b"`, `c` → `"ce"` and `"c"`, `d` → `"de"`, `e` → `"e"`, `f` → `"efe"`, `g` → `"ge"`, `h` → `"hache"`, `i` → `"i"`, `j` → `"jota"`) × the number spelled (`3` → `"tres"`); for `B3`: `"be tres"`, `"b tres"`, `"ve tres"`. A letter after `j` → `[]`.

- [ ] **Step 1: Failing tests:** `test_number_to_spanish_known_values` (the examples above plus `16`, `30`, `31`, `200`, `500`, `1001`), `test_number_out_of_range`, `test_clean_removes_symbols_and_keeps_accents`, `test_spoken_forms_spells_digits` (`"m3 por 8"` → includes `"m tres por ocho"`), `test_spoken_forms_drops_unspeakable`, `test_plural_variants_rules`, `test_drawer_synonyms_b3`, `test_drawer_synonyms_beyond_j_are_empty`.
- [ ] **Step 2:** `python -m pytest alexa/tests/test_spoken.py -q` → FAIL. **Step 3:** implement. **Step 4:** pass.
- [ ] **Step 5: Commit:** `Add Spanish spoken forms for the model generator`.

---

### Task 8: The model generator and template

**Files:**
- Create: `alexa/generate_model.py`, `alexa/model_template.json`
- Test: `alexa/tests/test_generate_model.py`

**Interfaces:**
- `model_template.json`: a complete `interactionModel` with `invocationName: "mi atlas"`; intents `AMAZON.CancelIntent`, `HelpIntent`, `StopIntent`, `NavigateHomeIntent`, `NoIntent`, `FallbackIntent`, the five order intents with their `producto`/`cantidad` slots (plus slot `uno` of type `UnoUna` on Añadir and Restar), and the three answer intents `RespuestaCajonIntent` (slot `cajon`: `TipoCajon`), `RespuestaCantidadIntent` (slot `cantidad`: `AMAZON.NUMBER`), `RespuestaPiezaIntent` (slot `producto`: `TipoProducto`). Utterances follow spec appendix A **with accents restored** and with the risky ones removed (under Actualizar: "hay/quedan/tengo {cantidad} {producto}" and the "en total" variants; under Consultar: the bare `{producto}`). Añadir and Restar utterances carry both `{cantidad} {producto}` and `{uno} {producto}` forms. Answer-intent utterances use carrier phrases (`"en el {cajon}"`, `"el {cajon}"`, `"el de {producto}"`, `"{cantidad}"` alone for the number intent only). `types` has `UnoUna` (values "un", "una") and **empty** `TipoProducto`/`TipoCajon` placeholders (`"values": []`).
- `def build_model(template: dict, parts: list[dict], drawers: list[str]) -> tuple[dict, list[str]]` — `parts` rows `{"pk": int, "name": str, "keywords": str}`; returns the model and the warnings. `TipoProducto` values: `{"id": str(pk), "name": {"value": first spoken form, "synonyms": [...]}}` where forms come from `spoken_forms(name)` and `spoken_forms(keyword)` for each comma-separated keyword, plus `plural_variants` of single-word forms; de-duplicated within the value, never containing the value's own name; a part with no spoken form is omitted and listed in the warnings. `TipoCajon` values: `{"id": "B3", "name": {"value": "be tres", "synonyms": [...]}}` from `drawer_synonyms` (drawers with none are omitted with a warning). Shared synonyms stay on every part that has them. Warn above **500** values in a type; raise `ValueError` above **20 000**.
- `def diff_summary(previous: dict | None, new: dict) -> list[str]` — lines like `"+ 3 parts: …"`, `"- 1 part: …"`, `"~ 2 renamed"`, and the value counts per type; `["no previous model"]` when `previous` is `None`.
- `def fetch_inventory(base_url: str, token: str, opener=None) -> tuple[list[dict], list[str]]` — active parts (`GET /api/part/?active=true`, following `next` pagination) and the names of the children of the location `Cajonera`; sends the non-default `User-Agent`; `opener` is injectable for tests.
- CLI: `python alexa/generate_model.py --env FILE --template alexa/model_template.json --out FILE`; reads `INVENTREE_URL`/`INVENTREE_TOKEN` from the env file (same format as `.env.local`); prints the diff summary and warnings before writing; `--dry-run` writes nothing. `main(argv) -> int`.

- [ ] **Step 1: Failing tests:** template checks — `test_template_keeps_invocation_name_and_five_intents`, `test_every_utterance_slot_exists_in_its_intent`, `test_no_utterance_is_shared_between_intents` (Alexa rejects it), `test_risky_utterances_are_gone`, `test_answer_intents_use_carrier_phrases`; generator — `test_part_value_has_id_and_synonyms`, `test_shared_keyword_stays_on_every_part` (Review Focus 4), `test_part_without_spoken_form_is_omitted_with_warning`, `test_digits_are_spelled_out_in_synonyms`, `test_drawer_values`, `test_warns_above_500_and_fails_above_20000`, `test_diff_summary`, `test_fetch_inventory_follows_pagination` (canned pages), `test_cli_dry_run_writes_nothing`.
- [ ] **Step 2:** `python -m pytest alexa/tests -q` → FAIL. **Step 3:** implement. **Step 4:** pass.
- [ ] **Step 5: Dry run on the user's InvenTree** (read-only): `python alexa/generate_model.py --env C:/Users/gomezja/Documents/Proyectos/Modular_Drawers/homeassistant/.env.local --template alexa/model_template.json --out alexa/atlas-model.json --dry-run`. Expected: a diff summary with 25 parts (the 23 seeded + Huevos + "tornillo m3") and 4–5 drawers, no error. Never print the token.
- [ ] **Step 6: Commit:** `Add the Alexa model generator and template`.

---

### Task 9: Documentation, release preparation and acceptance script

**Files:**
- Create: `alexa/README.md`, add `alexa/atlas-model.json` to `.gitignore`
- Modify: `custom_components/cajonera/manifest.json` (`0.3.0`), `README.md`, `docs/superpowers/specs/2026-11-25-alexa-bridge-design.md` (`Status: implemented`)

- [ ] **Step 1: `alexa/README.md`** (English): what the bridge is; **Deploy the Lambda** (create or reuse the InvenTree skill's Lambda, paste `lambda_function.py`, handler `lambda_function.lambda_handler`, **timeout 8 s**, runtime Python 3.9+, the environment variables, no layers needed because `urllib3` ships with the runtime — if the runtime lacks it, add the public `urllib3` layer); **Generate the model** (command, reading the summary, importing in *Build → Interaction Model → JSON Editor*, *Save* and *Build*), re-run whenever parts are added; **Rotate the token** note; **Troubleshooting** (CloudWatch shows only intent names and outcomes by design; HTTP 403 means the Cloudflare User-Agent rule; "no puedo consultar" means InvenTree unreachable).
- [ ] **Step 2:** root `README.md`: a short "Voice (Alexa)" section linking to `alexa/README.md`; `manifest.json` version `0.3.0`; spec status.
- [ ] **Step 3: Checks:** `python -m pytest -q` (all pass), `node --test tests/frontend/cajonera-layout.test.mjs` (32 pass), `python -m py_compile custom_components/cajonera/__init__.py custom_components/cajonera/sensor.py alexa/lambda_function.py alexa/generate_model.py alexa/spoken.py`, the no-HA-imports grep (no output), `grep -n "print(" alexa/lambda_function.py` (no output), and the secrets guard: `git ls-files | grep -i -E "env|token|secret"` (no output).
- [ ] **Step 4: Commit:** `Prepare 0.3.0 and document the Alexa bridge`.
- [ ] **Step 5: Hand over to the user** (do not run): `git push -u origin feature/alexa-bridge`, merge, GitHub release `0.3.0`; in HACS **Update** Cajonera and restart HA; run the generator against the real InvenTree **without** `--dry-run`, import `alexa/atlas-model.json` in the Alexa console and build; paste `lambda_function.py` into the Lambda, set timeout 8 s and the environment variables; then the spoken acceptance: "Alexa, pregunta a mi atlas dónde están los tornillos" (lights several drawers, no question) · "añade tornillos" → "¿Cuántos?" → "cincuenta" → "¿cuál?" → "el m tres por ocho" → "¿en cuál?" if needed → confirmation · "añade un tornillo m tres por ocho" (adds 1) · "he cogido dos huevos" · "actualiza huevos a doce" · "resta tuercas" (asks "¿Cuántos?") · a deliberately wrong drawer ("en el C2") is refused and asked again · with HA stopped, "dónde están los tornillos" still answers (read-only) and "añade cinco tornillos" says it changed nothing.

---

## Self-review notes (controller)

- **Spec coverage:** V1–V3 Tasks 1–4; V4 Task 5; V5 Tasks 5, 8; V6 Task 2 (`iluminar`) applied in Task 4; V7 Task 6; V8 Tasks 7–8; V9 Task 9. Non-goals respected (no creation/deletion by voice, no ordinals, no Smart Home Lambda change).
- **Type consistency:** `Pregunta`/`VoiceResult`/`Listo` defined in Tasks 1–2 and used unchanged in Tasks 3–4; the Lambda consumes the dict shape produced in Task 4 (`frase`, `hecho`, `pregunta{falta, pieza_pk, opciones}`).
- **Known risks left to execution:** the exact HA response envelope for `?return_response` (Task 6 assumes `service_response`; if it differs, `ha_call` is the only place to change); Alexa's real transcription of "M3" and spelled digits (verified by the user in the spoken acceptance).
