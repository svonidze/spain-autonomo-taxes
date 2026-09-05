"""Static contract checks for web UI translations and CSP hygiene.

These guards keep the hand-rolled i18n dictionaries in app.js aligned
between the two locales and stop inline styles from sneaking into assets
governed by the strict ``style-src 'self'`` policy, where they would be
silently dropped by the browser at runtime.
"""

import re
from pathlib import Path

STATIC_ROOT = Path(__file__).resolve().parents[1] / "src" / "autonomo_taxes" / "web_ui"
APP_JS = STATIC_ROOT / "app.js"
INDEX_HTML = STATIC_ROOT / "index.html"

MESSAGE_KEY = re.compile(r'"([a-z][A-Za-z0-9]*\.[A-Za-z0-9._]+)"\s*:')
BARE_KEY = re.compile(r"^    ([a-z0-9_]+):", re.MULTILINE)


def _messages_sections() -> tuple[str, str, str]:
    source = APP_JS.read_text(encoding="utf-8")
    ru_start = source.index("const messages = {")
    en_start = source.index("  en: {", ru_start)
    messages_end = source.index("\n};", en_start)
    return source, source[ru_start:en_start], source[en_start:messages_end]


def test_message_keys_exist_in_both_locales() -> None:
    _, ru_section, en_section = _messages_sections()
    ru_keys = set(MESSAGE_KEY.findall(ru_section))
    en_keys = set(MESSAGE_KEY.findall(en_section))
    assert ru_keys, "message keys must exist"
    assert ru_keys == en_keys, (
        f"missing in en: {sorted(ru_keys - en_keys)}; "
        f"missing in ru: {sorted(en_keys - ru_keys)}"
    )


def test_literal_t_references_have_dictionary_entries() -> None:
    source, ru_section, _ = _messages_sections()
    ru_keys = set(MESSAGE_KEY.findall(ru_section))
    used = set(re.findall(r'\bt\("([a-z][A-Za-z0-9]*\.[A-Za-z0-9._]+)"', source))
    missing = used - ru_keys
    assert not missing, f"t() references without dictionary entries: {sorted(missing)}"


def test_status_labels_exist_in_both_locales() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("const statusMessages = {")
    end = source.index("\n};", start)
    section = source[start:end]
    en_start = section.index("  en: {")
    ru_keys = set(BARE_KEY.findall(section[:en_start]))
    en_keys = set(BARE_KEY.findall(section[en_start:]))
    assert ru_keys, "status labels must exist"
    assert ru_keys == en_keys, (
        f"missing in en: {sorted(ru_keys - en_keys)}; "
        f"missing in ru: {sorted(en_keys - ru_keys)}"
    )


def test_app_assets_avoid_inline_styles() -> None:
    # status-help.js is exempt: it positions its tooltip via style writes.
    assert "style=" not in APP_JS.read_text(encoding="utf-8")
    assert "style=" not in INDEX_HTML.read_text(encoding="utf-8")


def test_built_shell_uses_external_module_resources() -> None:
    from autonomo_taxes.ui_assets import UiAssets
    assets = UiAssets(STATIC_ROOT)
    html = assets.path("index.html").read_text()
    assert 'type="module"' in html
    assert '/ui-assets/' in html
    assert '<script src="/app.js"' not in html
    # Help initialization and Back/Forward are tested in the real browser.
