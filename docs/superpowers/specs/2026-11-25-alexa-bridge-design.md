# Alexa bridge — design

Date: 2026-11-25 · Status: implemented · Owner of the product decisions: the cabinet's user

## 1. Intent

Voice and the Lovelace card must behave **identically**: same part matching, same per-drawer rules, same
refusals, same lights. Today the user's Alexa skill ("mi atlas") talks to InvenTree on its own, picks the
first stock item it finds (`items[0]`), knows nothing about drawers, never lights a drawer, assumes a quantity
of 1 when none is said, and closes the session after every answer.

The goal is to turn that Lambda into a **thin bridge to Home Assistant**: each intent calls one HA service,
and Alexa speaks the sentence HA returns. When HA needs one more piece of information (how many, which drawer,
which part) Alexa asks and the conversation continues. If HA is unreachable, the Lambda falls back to a
**read-only** answer straight from InvenTree.

Product background: `Modular_Drawers/docs/09_gestion_inventario.md` (Spanish; §4.4 is the fallback design).
The card design and its rules: `2026-11-24-inventory-card-design.md`.

## 2. Goals

| # | Goal |
|---|---|
| V1 | One HA service, `cajonera.voz`, implements search, add, withdraw and count **by voice**, with the card's rules |
| V2 | When something is missing or ambiguous, HA says what is missing; Alexa asks and keeps the session open |
| V3 | **No quantity is ever assumed.** "añade tornillos" asks "¿cuántos?"; "añade **un** tornillo" is 1 |
| V4 | At most **two consecutive questions**; then Alexa sends the user to the panel |
| V5 | Alexa receives the **exact part** (InvenTree part id), not a loose word |
| V6 | Asking "en qué cajón" lights the candidate drawers in search mode, as a search does |
| V7 | HA down or slow: answer "where is it" from InvenTree, read-only, without lighting anything; never write |
| V8 | The skill's interaction model is **generated** from InvenTree by a script the user runs, not hand-maintained |
| V9 | Everything lives in `ha-cajonera/alexa/`, with tests; the user deploys the Lambda and imports the model |

## 3. Non-goals

| Not doing | Why |
|---|---|
| Creating parts, categories or drawers by voice | The card does it; voice would need many follow-up questions |
| Deleting anything by voice | Same irreversibility reasoning as the card |
| Ordinals ("la primera", "la segunda") | Names are enough for now |
| Replacing the Alexa Smart Home Lambda | That one forwards Smart Home events to HA's `/api/alexa/smart_home` and is unrelated; it stays as is |
| Dynamic entities (`DynamicEntitiesDirective`) | They only live for one session and do not work for "Alexa, pregunta a mi atlas…" |
| Deploying to AWS | The user pastes the Lambda and imports the model |

## 4. Anti-goals (a failure even if it "works")

- A write recorded on the wrong part or wrong drawer because a word was ambiguous.
- Assuming a quantity ("1") when the user did not say one.
- Saying "no he podido" when the write may have been applied (double write on retry).
- Different answers from voice and panel to the same request.
- A misheard drawer ("be tres" → B3) silently becoming a different drawer.
- Secrets or the full Alexa event (which carries an access token) written to CloudWatch logs.
- Alexa going silent: the skill must always answer inside Alexa's 8 s limit.

## 5. Constraints

- Alexa gives a skill **8 s**, and a cold start plus TLS handshakes eat into it. The Lambda's own timeout is
  configured to **8 s**. Budget: the HA call gets at most **4 s**; the read-only fallback gets what is left minus
  a 1 s margin (computed from `context.get_remaining_time_in_millis()`, never a fixed 3 s). `urllib3` is used with
  **`retries=False`**, so a POST can never be sent twice.
- A connection failure that never reached HA (DNS, refused, connect timeout) is **certain not to have written**:
  say "No he podido hablar con Home Assistant, no he cambiado nada." Only a read timeout or a 5xx after the request
  was sent uses the "no sé si se ha registrado" wording.
- Every response that keeps the session open carries a **reprompt**, or Alexa ends the session silently.
- The Lambda keeps **no state between invocations** other than Alexa **session attributes**.
- Runtime dependencies of the Lambda: Python standard library plus `urllib3` (the user's HA Lambda already uses it).
- InvenTree is behind a Cloudflare tunnel that **rejects Python's default User-Agent** (HTTP 403): the Lambda
  sends its own `User-Agent`. Cloudflare Access service-token headers are supported but **off by default**.
- Configuration only through environment variables: `HA_URL`, `HA_TOKEN`, `INVENTREE_URL`,
  `INVENTREE_TOKEN`, optional `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`. No secret in the repo or in logs.
- `logic/` in the integration stays free of Home Assistant imports and tested with pytest.
- Code, comments, docs and commit messages in English; everything Alexa says is Spanish, and the sample
  utterances use correct Spanish orthography (accents).
- Skill invocation name stays **"mi atlas"**; the five existing intents keep their names.

## 6. Approaches considered

| Approach | Trade-offs | |
|---|---|---|
| **A · HA coordinates, Lambda bridges** | One implementation of every rule; voice and panel cannot drift; the Lambda stays tiny and testable. Needs new HA service and a multi-turn protocol | **Chosen** (user decision in `09` §10) |
| B · Lambda writes to InvenTree itself | Works when HA is down; but two implementations of every rule, which drift | Rejected |
| C · One HA service per action (`voz_buscar`, `voz_anadir`, …) | Slightly simpler HA side | Rejected: more surface, the rules end up copied between services |

Multi-turn: **Alexa asks and the user answers** ("en el B3") — chosen over making the user repeat the whole
order. Part knowledge: the model is **generated from InvenTree** — chosen over a hand-kept list or session-only
dynamic entities.

## 7. Design

### 7.1 Layout

```
ha-cajonera/alexa/
├── lambda_function.py          ← the bridge (single file, pasted into AWS)
├── generate_model.py           ← reads InvenTree, writes the skill's interaction model JSON
├── model_template.json         ← hand-written part: intents, sample utterances, fixed types
├── seed_test_parts.py          ← (exists) creates test parts
├── README.md                   ← deploy + regenerate steps
└── tests/                      ← pytest, no network
custom_components/cajonera/logic/voice.py   ← the rules of `cajonera.voz` (pure Python)
```

### 7.2 The HA service `cajonera.voz`

Registered like the other services, `SupportsResponse.ONLY`, answering within the 3.5 s deadline.

Input (all optional except `accion`): `accion` ∈ `buscar | anadir | retirar | actualizar`; `pieza_pk` (int);
`pieza_pks` (list of int: the candidates Alexa resolved from a shared keyword); `texto` (str: the spoken name
when nothing was resolved); `cantidad` (float); `cajon` (str, e.g. "B3"); `uno` (bool: the user said "un/una").

Output: `{frase, hecho, pregunta}`. `frase` is always speakable. `pregunta` is `null` or
`{falta: "cantidad"|"cajon"|"pieza", pieza_pk: int|null, opciones: [...]}`, set only when **nothing was written**
and one more answer is needed. `pieza_pk` in `pregunta` is the part HA already resolved, so the Lambda never
re-resolves it. `opciones` are drawer names or `{pieza_pk, nombre}`, at most 20; the spoken sentence names at
most 3 ("…, … y 4 más").

Rules, in order; the first that fits wins (same rules as the card and `09` §4.3):

1. **Part.**
   - `pieza_pk` given → use it.
   - Else `pieza_pks`: for `buscar` search **all** of them, with **no question** — every drawer that holds any of
     them is lit (up to 10) and the sentence lists up to 3. For writes, ask `pieza` with those candidates.
   - Else `texto` → the existing exact-name and drawer-stocked rules (several → ask `pieza`; none → "No encuentro
     nada con ese nombre.").
   - Else, `retirar` only: the part of the last search or write if it is younger than 2 minutes (the "he cogido 3"
     flow). Otherwise `buscar` answers "¿Qué quieres buscar?" and writes ask `pieza` with no options.
2. **Quantity** (writes only), after the part so the question carries `pieza_pk`. None and not `uno` → ask
   `cantidad` ("¿Cuántos?"; for `actualizar` "¿A cuántos?"). `uno` → 1. A value ≤ 0 (< 0 for `actualizar`) →
   ask `cantidad` again.
3. **Drawer.**
   - An explicit `cajon` is **honoured or refused, never ignored**. If the part is in some drawer, the drawer must
     be one of its drawers; otherwise refuse and ask `cajon` again with the part's drawers ("No hay X en el C2;
     hay en el B3 y en el C1. ¿En cuál?"). An unknown drawer name is refused the same way.
   - No `cajon`: the part is in one drawer → that one. In several: `retirar` uses the context drawer if it is for
     the same part and younger than 2 minutes, otherwise ask `cajon` and **light the candidates** (search mode);
     `anadir` and `actualizar` ask `cajon`.
   - The part is in no drawer: `retirar`/`actualizar` → "pero no está en ningún cajón"; `anadir` → ask `cajon`
     with every real drawer (no suggestion yet).
   - **Voice never adds stock to a drawer where the part is not, when the part already lives elsewhere**: it
     refuses and points to the panel. This differs from the card **on purpose**: a misheard drawer ("be tres" for
     "ce tres") must not create stock in the wrong place. A part in no drawer may be placed anywhere.
4. **Execute** with the existing drawer-level operations (`add_to_drawer`, `count_drawer`, `withdraw_by_part`),
   keeping their phrases, keep-when-empty, "maybe registered" wording and low-stock warning. The confirmation
   names the part, the quantity and the drawer ("Hecho. He retirado 2 de Huevos del B1. Quedan 10."), so a
   misheard number is audible.
5. **`buscar`** uses the existing search phrase and lights. A part that is only **outside** the cabinet answers
   with its real InvenTree location ("Está en Nevera. Quedan 12.") instead of "no está en ningún cajón".

After a successful write: confirm light on the drawer, context set to the part and drawer, background low-stock
refresh — identical to the card's writes.

### 7.3 The Lambda

Raw Alexa JSON, no `ask-sdk` (as the user's current Lambda). Handles `LaunchRequest`, `IntentRequest`,
`SessionEndedRequest`. Launch keeps the current welcome ("Bienvenido al inventario. ¿Qué quieres consultar o
modificar?"); Help, Stop, Cancel, No and Fallback keep their current meaning; `AMAZON.NavigateHomeIntent` ends
the session politely.

Mapping: Consultar and Localizar → `buscar`; Añadir → `anadir`; Restar → `retirar`; Actualizar → `actualizar`.

**Slot `producto`.** Flatten every `resolutionsPerAuthority` entry with status `ER_SUCCESS_MATCH`, de-duplicate by
value id. One id → `pieza_pk`. Several ids (a shared keyword such as "tornillos") → `pieza_pks`, capped at 20.
`ER_SUCCESS_NO_MATCH` or no resolution → send the raw `value` as `texto`. The slot `cantidad` is a number; the
slot `uno` (type `UnoUna`) sets `uno`.

**Conversation.** On a `pregunta` the Lambda keeps the session open with a reprompt and stores
`{accion, pieza_pk, pieza_pks, texto, cantidad, cajon, uno, falta, opciones, fallos, preguntas}` in session
attributes (`opciones` capped at 20). The answer intents (`RespuestaCajonIntent` with slot `cajon`,
`RespuestaCantidadIntent` with `cantidad`, `RespuestaPiezaIntent` with `producto`) fill the missing field and call
`cajonera.voz` again. A `pieza` answer is **intersected** with the stored candidates, so a shared keyword does not
burn another question.
- An answer **fails** when it does not fill the pending field (empty slot, not among `opciones`, or a refusal).
  After **2 consecutive failures**, or **4 questions for one order**, Alexa says "No consigo entenderlo. Hazlo
  desde el panel de la cajonera." and ends the session. A filled field resets the failure count.
- A new **order intent** while a question is pending replaces the pending state.
- An answer intent with **no pending state** answers "¿Qué quieres hacer?" and keeps the session open.
- The pending state dies with the session.

**Fallback (V7).** `cajonera.voz` fails or exceeds its time:
- `buscar` → read-only InvenTree lookup (candidate parts, locations, quantities): "Está en el B3. Ahora no puedo
  encender el cajón."; nothing lit; if InvenTree also fails: "No puedo consultar el inventario ahora mismo."
- any write → the wording of §5 (certainly-not-written versus maybe-written).

**Logging.** Log intent name, slot names and outcome class only. Never the event, the headers or any token.
**Speed.** One module-level `urllib3.PoolManager` reused across warm invocations; short phrases; no work after
the answer is built.

### 7.4 The interaction model

`generate_model.py --env <file> --template alexa/model_template.json --out <file>` reads **all active parts**
(name, keywords, id) and the stock locations that are cabinet drawers (children of the location `Cajonera`, as the
integration does), and writes the full model JSON. The file has no locale; the user imports it into the console of
the skill's locale.

- **`TipoProducto`**: one value per part, `id` = the InvenTree part pk. A *spoken form* is a lowercase string of
  letters and spaces. Each name and each keyword is cleaned (lowercase, symbols removed or turned into spaces,
  spaces collapsed); a cleaned form that still contains digits **also** yields a **spelled-out** form (digit runs
  turned into Spanish words, 0–999 999: "m3 por 8" → "m tres por ocho"), and both are kept. The first spoken form
  is `name.value`; the rest are `synonyms` (de-duplicated). A trailing "s" is added to a single word ending in a
  vowel, and removed from a word ending in "as" or "os"; no other plurals are guessed (the keywords carry them). A
  part with **no** spoken form is left out and listed in a warning.
- **Shared synonyms** stay on every part that has them; Alexa then returns every match and the Lambda asks (or, for
  `buscar`, searches all of them).
- **`TipoCajon`**: one value per drawer, `id` = the drawer name ("B3"), synonyms for how people say it ("be tres",
  "b tres", "ve tres"). Row letters beyond "J" are skipped with a warning.
- **`UnoUna`**: values "un", "una" (fixed; in the template).
- **Intents and utterances come from the template**, not from InvenTree (appendix A lists what they cover). The
  template keeps the user's five intents and their slot names and types, restores accents, adds the three answer
  intents with **carrier phrases** ("en el {cajon}", "el de {producto}", "{cantidad}" alone for numbers only) and
  removes the risky utterances: under Actualizar "hay {cantidad} {producto}", "quedan {cantidad} {producto}" and
  "tengo {cantidad} {producto}" (they set a quantity from what sounds like a comment), and the bare "{producto}"
  under Consultar.
- Before writing, print a **diff summary** against the previous output file if it exists (parts added, removed,
  renamed, value count) and the warnings (parts left out, characters removed). Warn above 500 values, fail above
  20 000.
- The script never talks to Amazon; the user imports the JSON (*Build → Interaction Model → JSON Editor*) and
  builds.

### 7.5 Testing

- `voice.py`: pytest with the existing `FakeInventory`, one test per rule above, plus: shared keyword asks
  `pieza`; "añade un tornillo" is 1 and "añade tornillos" asks; wrong drawer refused with nothing written;
  asking `cajon` lights candidates; outside-cabinet part answers its real location; HA-style timeout wording.
- Lambda: pytest with canned Alexa events and a fake HA transport (injectable `call_ha`/`call_inventree`):
  every intent, the two-question limit, fallback paths, "write may have happened" wording, no event logged.
- Generator: pytest with canned InvenTree JSON: ids, synonyms, spelled-out digit forms, shared keywords, parts with no spoken form left out with a warning, diff summary, the 500/20 000
  thresholds, template risky utterances absent.
- Manual acceptance (user): import the model, deploy the Lambda, then run the spoken examples of §1 and §7.3.

## 8. Left to the builder

- The exact HA REST call (`POST /api/services/cajonera/voz?return_response`) and its response envelope.
- The wording of every short Alexa phrase not fixed above, in natural Spanish, consistent with the card's.
- The Spanish number speller's edge cases (0–999 999, "cien"/"ciento", "uno"/"un").
- Exact utterances of the three answer intents, within the carrier-phrase rule of §7.4.
- Whether Alexa's real transcription of "M3" needs an extra synonym: checked in the manual acceptance with the
  seeded parts and reported back by the user.

## Appendix A — what the template's utterances must cover (from the user's current model)

- **Consultar** (`producto`): "cuántos/cuántas/cuánta/cuánto {producto} quedan/me quedan/hay/tengo", "cuánto queda de",
  "stock de", "cantidad de", "quedan/queda/tengo/hay {producto}", "consulta / mira si hay / comprueba / revisa …".
- **Añadir** (`producto`, `cantidad`, `uno`): añade, suma, agrega, mete, "he metido", guarda, repón, "pon … más",
  "he comprado", each also as "que añada…"; the quantity may be "un/una" (`uno`) or a number.
- **Restar** (`producto`, `cantidad`, `uno`): resta, quita, elimina, saca, descuenta, gasta, "he gastado", "he usado",
  "he cogido", "me he comido", each also as "que reste…".
- **Actualizar** (`producto`, `cantidad`): actualiza, "pon … a/en", "deja … en", fija, ajusta, establece,
  "marca {cantidad}", "cambia … a".
- **Localizar** (`producto`): dónde están/está/tengo/guardo, "dónde se guardan", "en qué sitio/ubicación/caja/cajón
  está", localiza, busca, "ubicación de", "sitio de".
- The invocation name stays **"mi atlas"**.
