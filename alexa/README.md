# Alexa bridge

Voice for the cajonera, through the skill **"mi atlas"**. Home Assistant does the thinking (`cajonera.voz`, the same
rules as the Lovelace card) and Alexa only speaks: the Lambda in this folder turns each Alexa intent into one call to
HA and reads the answer aloud. When HA needs one more answer ("¿Cuántos?", "¿en qué cajón?") Alexa asks and the
conversation goes on. If HA cannot be reached, a search is answered read-only from InvenTree and a write says honestly
whether it may have happened.

| File | What it is |
|---|---|
| `lambda_function.py` | The Lambda. One file, Python 3.9+, `urllib3` only. |
| `generate_model.py`, `spoken.py` | Build the skill's interaction model from your InvenTree parts and drawers. |
| `model_template.json` | The hand-written part of the model: intents and sample sentences. |
| `seed_test_parts.py` | Creates a set of test parts in InvenTree (optional). |
| `tests/` | `python -m pytest -q` from the repo root. No network. |

## 1. Deploy the Lambda

1. In the AWS console open the Lambda of your InvenTree skill (or create a new one, Python 3.9 or newer).
2. Replace the code with the content of `lambda_function.py`. The handler is `lambda_function.lambda_handler`.
3. **Configuration → General configuration → Timeout: 8 seconds.** The default is 3 s and would cut every answer.
4. **Configuration → Environment variables:**

   | Variable | Value |
   |---|---|
   | `HA_URL` | Public URL of Home Assistant, for example `https://ha.example.com` |
   | `HA_TOKEN` | A long-lived access token of a Home Assistant user (Profile → Security) |
   | `INVENTREE_URL` | Public URL of InvenTree (used only by the read-only fallback) |
   | `INVENTREE_TOKEN` | A **read-only** InvenTree token |
   | `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` | Optional: Cloudflare Access service token. Both or neither. |

5. `urllib3` ships with the Lambda Python runtimes. If your runtime lacks it, add the public `urllib3` layer.
6. Keep the Alexa trigger (Alexa Skills Kit) as it is.

The Lambda sends its own `User-Agent`: Cloudflare answers HTTP 403 to Python's default one.

## 2. Generate and import the interaction model

The model lists your parts, so run this whenever you add or rename parts or drawers:

```sh
python alexa/generate_model.py --env path/to/.env.local --template alexa/model_template.json --out alexa/atlas-model.json
```

`.env.local` holds `INVENTREE_URL` and `INVENTREE_TOKEN`. The script prints what changed since the last run and any
warning (a part with no speakable name is left out; above 500 values Alexa recognises the list worse). Add
`--dry-run` to look without writing.

Then, in the Alexa developer console: **Build → Interaction Model → JSON Editor**, paste or drag in
`alexa/atlas-model.json`, **Save** and **Build**. The file has no locale: import it into the locale of your skill.

Tips that make recognition better:
- Give every part **keywords** in InvenTree, written the way you say them: "resistencias, 10k, diez kilos".
  Digits are spelled out for you ("m3 por 8" also becomes "m tres por ocho").
- A word shared by several parts ("tornillos") is fine: a search lights all of them, and a write asks which one.

## 3. Home Assistant

Install or update the `cajonera` integration (HACS) so the service `cajonera.voz` exists. The Home Assistant user whose
token you gave the Lambda needs no administrator rights.

## 4. Try it

1. "Alexa, pregunta a mi atlas dónde están los tornillos" → several drawers light up, no question.
2. "añade tornillos" → "¿Cuántos?" → "cincuenta" → "Hay Tornillo M3x8, … ¿Cuál?" → "el m tres por ocho" → confirmation.
3. "añade un tornillo m tres por ocho" → adds exactly one.
4. "he cogido dos huevos" · "actualiza huevos a doce" · "resta tuercas" (asks "¿Cuántos?").
5. Say a wrong drawer on purpose ("en el C2"): it is refused and asked again, nothing is written.
6. Stop Home Assistant: "dónde están los tornillos" still answers (read only, nothing lights) and "añade cinco
   tornillos" says it changed nothing.

## Troubleshooting

- **Logs.** CloudWatch shows only the intent name, the slot names and the outcome. This is on purpose: the Alexa
  event carries an access token. To debug, replay the same event locally.
- **"No he podido hablar con Home Assistant, no he cambiado nada."** The request never reached HA: check `HA_URL`,
  the token and that HA is reachable from the internet.
- **"No sé si se ha registrado."** The request reached HA but no answer came back in time. Ask how many are left
  before repeating the order.
- **"No puedo consultar el inventario ahora mismo."** HA and the read-only fallback both failed: check
  `INVENTREE_URL` and its token.
- **HTTP 403 from InvenTree.** Cloudflare is blocking the request; make sure the Lambda code is the one in this folder
  (own `User-Agent`), or add the Cloudflare Access service token.
- **Alexa does not understand a part.** Add keywords to it in InvenTree and regenerate the model.
- **Rotating a token.** Change it in the environment variables; nothing else.
