"""Catalog contracts and CSP checks independent of legacy source layout."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = ROOT / "src/autonomo_taxes/web_ui"
APP_JS = STATIC_ROOT / "app.js"
INDEX_HTML = STATIC_ROOT / "index.html"


def catalog(locale):
    result = {}
    for path in (ROOT / "frontend/src/locales" / locale).glob("*.json"):
        result.update(json.loads(path.read_text()))
    return result


def test_message_keys_exist_in_all_registered_locales():
    registry = json.loads((ROOT / "frontend/src/locales/registry.json").read_text())
    expected = catalog("ru").keys()
    assert expected
    for locale in registry:
        assert catalog(locale["code"]).keys() == expected


def test_literal_t_references_have_dictionary_entries():
    keys = catalog("ru").keys()
    used = set()
    for path in STATIC_ROOT.glob("*.js"):
        # These are global t() references; settings/workflow use their own domain adapters.
        if path.name != "app.js":
            continue
        used.update(re.findall(r'\bt\(["\']([a-z][A-Za-z0-9]*\.[A-Za-z0-9._]+)["\']', path.read_text()))
    assert not used - keys


def test_status_labels_exist_in_both_locales():
    expected = {key for key in catalog("ru") if key.startswith("statuses.labels.")}
    assert expected
    assert expected == {key for key in catalog("en") if key.startswith("statuses.labels.")}


def test_app_assets_avoid_inline_styles():
    assert "style=" not in APP_JS.read_text()
    assert "style=" not in INDEX_HTML.read_text()


def test_built_shell_uses_external_module_resources():
    from autonomo_taxes.ui_assets import UiAssets
    assets = UiAssets(STATIC_ROOT)
    html = assets.path("index.html").read_text()
    assert 'type="module"' in html and '/ui-assets/' in html
    assert '<script src="/app.js"' not in html
