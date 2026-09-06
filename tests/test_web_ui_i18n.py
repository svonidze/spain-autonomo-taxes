"""Catalog contracts and CSP checks independent of legacy source layout."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = ROOT / "src/autonomo_taxes/web_ui"
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


def test_status_labels_exist_in_both_locales():
    expected = {key for key in catalog("ru") if key.startswith("statuses.labels.")}
    assert expected
    assert expected == {key for key in catalog("en") if key.startswith("statuses.labels.")}


def test_built_shell_uses_external_module_resources():
    from autonomo_taxes.ui_assets import UiAssets
    assets = UiAssets(STATIC_ROOT)
    html = assets.path("index.html").read_text()
    assert 'type="module"' in html and '/ui-assets/' in html
    assert '<script src="/app.js"' not in html
