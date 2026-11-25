"""Guard: every card module must be fetched with the same ?v=<version> as the entry file.

The integration registers `cajonera-card.js?v=<manifest version>`. A static relative
import (`from "./x.js"`) carries no query, so the browser would cache the other modules
independently of the entry and mix releases after an update. The modules therefore import
each other dynamically with the entry's own query string.
"""

import json
import re
from pathlib import Path

INTEGRATION = Path(__file__).parents[1] / "custom_components" / "cajonera"
FRONTEND = INTEGRATION / "frontend"
STATIC_RELATIVE_IMPORT = re.compile(r"^\s*import\b[^;]*?\bfrom\s*[\"']\./", re.MULTILINE | re.DOTALL)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_entry_and_views_have_no_static_relative_imports():
    for name in ("cajonera-card.js", "cajonera-views.js"):
        assert not STATIC_RELATIVE_IMPORT.search(_text(FRONTEND / name)), name


def test_modules_import_their_siblings_with_the_entry_version():
    card = _text(FRONTEND / "cajonera-card.js")
    views = _text(FRONTEND / "cajonera-views.js")
    for source in (card, views):
        assert "new URL(import.meta.url).search" in source
        assert "await import(`./cajonera-layout.js${V}`)" in source
    assert "await import(`./cajonera-views.js${V}`)" in card


def test_layout_stays_a_leaf_module():
    assert "import " not in re.sub(r"//.*", "", _text(FRONTEND / "cajonera-layout.js")).replace("import.meta", "")


def test_manifest_version_is_what_the_entry_url_gets():
    version = json.loads(_text(INTEGRATION / "manifest.json"))["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    init = _text(INTEGRATION / "__init__.py")
    # _read_version reads the manifest's "version" ...
    assert '(INTEGRATION_DIR / "manifest.json")' in init
    assert '["version"]' in init
    # ... and the entry URL carries it as the query string.
    assert 'f"{FRONTEND_URL}/cajonera-card.js?v={version}"' in init
