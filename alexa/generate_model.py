"""Generate the Alexa skill's interaction model from InvenTree.

    python alexa/generate_model.py --env .env.local --template alexa/model_template.json --out alexa/atlas-model.json

Reads the active parts (name, keywords, id) and the cabinet's drawers from InvenTree, fills the two custom slot
types of the hand-written template, prints what changed since the previous output, and writes the JSON to import
in the Alexa console (Build > Interaction Model > JSON Editor). It never talks to Amazon. Standard library only.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from spoken import drawer_synonyms, number_to_spanish, plural_variants, spoken_forms  # noqa: F401 (number_word)

WARN_VALUES = 500  # Alexa recognises long lists worse
MAX_VALUES = 20_000  # a hard limit of the platform
ROOT_LOCATION = "Cajonera"
USER_AGENT = "MiAtlasModelGenerator/1.0"  # Cloudflare answers 403 to Python's default User-Agent


def number_word(n: int) -> str:
    """Spanish words for 0..999 999 (used to build distinct names in tests)."""
    return number_to_spanish(n)


# ---- Building the model

def _forms_for(part: dict) -> list[str]:
    """Every spoken form of a part: its name first, then each keyword, plus the single-word plurals."""
    forms: list[str] = []
    sources = [part.get("name") or ""] + [k for k in (part.get("keywords") or "").split(",")]
    for source in sources:
        for form in spoken_forms(source):
            if form not in forms:
                forms.append(form)
    for form in list(forms):
        for variant in plural_variants(form):
            if variant not in forms:
                forms.append(variant)
    return forms


def build_model(template: dict, parts: list[dict], drawers: list[str]) -> tuple[dict, list[str]]:
    """The template with `TipoProducto` and `TipoCajon` filled in, and the warnings found on the way."""
    model = copy.deepcopy(template)
    warnings: list[str] = []
    types = {t["name"]: t for t in model["interactionModel"]["languageModel"]["types"]}

    product_values = []
    for part in parts:
        forms = _forms_for(part)
        if not forms:
            warnings.append(f"part {part.get('pk')} ({part.get('name')!r}) has no speakable name or keyword: left out")
            continue
        product_values.append({"id": str(part["pk"]), "name": {"value": forms[0], "synonyms": forms[1:]}})
    drawer_values = []
    for name in drawers:
        forms = drawer_synonyms(name)
        if not forms:
            warnings.append(f"drawer {name!r} cannot be spoken (rows go from A to J): left out")
            continue
        drawer_values.append({"id": name, "name": {"value": forms[0], "synonyms": forms[1:]}})

    for label, values in (("TipoProducto", product_values), ("TipoCajon", drawer_values)):
        if len(values) > MAX_VALUES:
            raise ValueError(f"{label} has {len(values)} values; Alexa accepts at most {MAX_VALUES}")
        if len(values) > WARN_VALUES:
            warnings.append(f"{label} has {len(values)} values: above {WARN_VALUES} Alexa recognises the list worse")
        types[label]["values"] = values
    return model, warnings


def _part_index(model: dict) -> dict[str, str]:
    for t in model["interactionModel"]["languageModel"]["types"]:
        if t["name"] == "TipoProducto":
            return {v["id"]: v["name"]["value"] for v in t["values"]}
    return {}


def diff_summary(previous: dict | None, new: dict) -> list[str]:
    """Human-readable lines: parts added, removed, renamed, and the value counts."""
    if previous is None:
        return ["no previous model"]
    old, cur = _part_index(previous), _part_index(new)
    added = sorted(set(cur) - set(old), key=int)
    removed = sorted(set(old) - set(cur), key=int)
    renamed = [pk for pk in cur if pk in old and cur[pk] != old[pk]]
    lines = []
    if added:
        lines.append(f"+ {len(added)} parts: " + ", ".join(cur[pk] for pk in added[:10]))
    if removed:
        lines.append(f"- {len(removed)} part{'s' if len(removed) != 1 else ''}: " + ", ".join(old[pk] for pk in removed[:10]))
    if renamed:
        lines.append(f"~ {len(renamed)} renamed: " + ", ".join(f"{old[pk]} -> {cur[pk]}" for pk in renamed[:10]))
    lines.append(f"parts: {len(old)} -> {len(cur)}")
    return lines


# ---- Reading InvenTree

def _urlopen(url: str, headers: dict):
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        return type("Response", (), {"status": response.status, "data": response.read()})()


def _get(opener, url: str, headers: dict) -> dict:
    response = opener(url, headers)
    if response.status != 200:
        raise RuntimeError(f"InvenTree answered HTTP {response.status} for {url.split('?')[0]}")
    return json.loads(response.data.decode("utf-8"))


def fetch_inventory(base_url: str, token: str, opener=None) -> tuple[list[dict], list[str]]:
    """Active parts (all pages) and the names of the drawers: the children of the location `Cajonera`."""
    opener = opener or _urlopen
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Token {token}", "Accept": "application/json", "User-Agent": USER_AGENT}
    parts: list[dict] = []
    url = f"{base}/api/part/?active=true&limit=100"
    while url:
        page = _get(opener, url, headers)
        for row in page.get("results", []):
            parts.append({"pk": row["pk"], "name": row.get("name") or "", "keywords": row.get("keywords") or ""})
        url = page.get("next")
    locations = _get(opener, f"{base}/api/stock/location/?limit=500", headers)
    rows = locations.get("results", locations) if isinstance(locations, dict) else locations
    root = next((r["pk"] for r in rows if r.get("name") == ROOT_LOCATION and r.get("parent") is None), None)
    drawers = sorted((r["name"] for r in rows if root is not None and r.get("parent") == root), key=_drawer_order)
    return parts, drawers


def _drawer_order(name: str) -> tuple:
    letters = "".join(c for c in name if c.isalpha())
    digits = "".join(c for c in name if c.isdigit())
    return (len(letters), letters, int(digits) if digits else 0)


# ---- Command line

def _read_env(path: Path) -> dict:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the Alexa interaction model from InvenTree.")
    parser.add_argument("--env", type=Path, required=True, help="file with INVENTREE_URL and INVENTREE_TOKEN")
    parser.add_argument("--template", type=Path, required=True, help="the hand-written model_template.json")
    parser.add_argument("--out", type=Path, required=True, help="where to write the model to import in Alexa")
    parser.add_argument("--dry-run", action="store_true", help="show the summary and warnings; write nothing")
    args = parser.parse_args(argv)

    env = _read_env(args.env)
    for key in ("INVENTREE_URL", "INVENTREE_TOKEN"):
        if not env.get(key):
            print(f"{key} is missing in {args.env}", file=sys.stderr)
            return 1
    template = json.loads(args.template.read_text(encoding="utf-8"))
    try:
        parts, drawers = fetch_inventory(env["INVENTREE_URL"], env["INVENTREE_TOKEN"])
        model, warnings = build_model(template, parts, drawers)
    except (RuntimeError, ValueError, urllib.error.URLError) as err:
        print(f"could not build the model: {err}", file=sys.stderr)
        return 1

    previous = None
    if args.out.exists():
        try:
            previous = json.loads(args.out.read_text(encoding="utf-8"))
        except ValueError:
            previous = None
    for line in diff_summary(previous, model):
        print(line)
    types = {t["name"]: t for t in model["interactionModel"]["languageModel"]["types"]}
    print(f"TipoProducto: {len(types['TipoProducto']['values'])} values · TipoCajon: {len(types['TipoCajon']['values'])} values")
    for warning in warnings:
        print(f"warning: {warning}")
    if args.dry_run:
        print("dry run: nothing written")
        return 0
    args.out.write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
