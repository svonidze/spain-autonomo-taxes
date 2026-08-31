import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_UI = REPO_ROOT / "src" / "autonomo_taxes" / "web_ui"
CHARTS_JS = WEB_UI / "charts.js"
APP_JS = WEB_UI / "app.js"
INDEX_HTML = WEB_UI / "index.html"
LOCAL_WEB = REPO_ROOT / "src" / "autonomo_taxes" / "local_web.py"


def _require_node() -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the web UI chart checks")
    return node


def test_chart_scene_model_behaves_deterministically() -> None:
    node = _require_node()
    result = subprocess.run(
        [node, str(Path(__file__).resolve().parent / "test_web_ui_charts.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert '"ok":true' in result.stdout.replace(" ", "")


@pytest.mark.parametrize("script", [CHARTS_JS, APP_JS])
def test_web_ui_scripts_parse(script: Path) -> None:
    node = _require_node()
    result = subprocess.run(
        [node, "--check", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_charts_js_is_served_and_loaded_before_app_js() -> None:
    local_web_source = LOCAL_WEB.read_text(encoding="utf-8")
    assert '"/charts.js"' in local_web_source

    index_source = INDEX_HTML.read_text(encoding="utf-8")
    charts_tag = index_source.index('<script src="/charts.js"></script>')
    app_tag = index_source.index('<script src="/app.js"></script>')
    assert charts_tag < app_tag


def test_dashboard_renders_chart_slots_from_analytics() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert "/api/analytics" in source
    assert "renderDashboardCharts(analyticsResult)" in source
    for slot in (
        "chart-business-result",
        "chart-tax-due",
        "chart-iva-position",
        "chart-tax-reserve",
        "chart-cumulative-net",
    ):
        assert f'id="{slot}"' in source, slot


def test_specialist_views_render_chart_slots() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    assert "mountViewAnalyticsChart" in source
    for slot in (
        "chart-expense-structure",
        "chart-review-aging",
        "chart-ytd-comparison",
        "chart-counterparty-concentration",
        "chart-amortization",
    ):
        assert f'id="{slot}"' in source, slot


def test_expense_chart_is_mounted_in_the_active_two_section_view() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    expenses = source[source.index("async function renderExpenses("):source.index("function transactionActions(")]
    assert 'id="chart-expense-structure"' in expenses
    assert 'mountViewAnalyticsChart("chart-expense-structure"' in expenses
    assert source.count('id="chart-expense-structure"') == 1
    assert 'await renderExpenses(renderGeneration)' in source


def test_charts_re_render_on_resize_from_the_single_dom_wiring_block() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    wiring = source.index("\nif (hasDOM) {")
    listener = source.index('window.addEventListener("resize", handleChartResize)')
    assert listener > wiring
    assert "viewChartRegistry.delete(slotId)" in source
    render_current_view = source[
        source.index("async function renderCurrentView(") : source.index(
            "\nasync function renderDashboard("
        )
    ]
    assert "viewChartRegistry.clear();" in render_current_view


def test_chart_i18n_keys_exist_in_both_locales() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    ru_start = source.index("const messages = {")
    en_start = source.index("  en: {", ru_start)
    messages_end = source.index("\n};", en_start)
    pattern = re.compile(r'"(charts\.[A-Za-z0-9.]+)"\s*:')
    ru_keys = set(pattern.findall(source[ru_start:en_start]))
    en_keys = set(pattern.findall(source[en_start:messages_end]))
    assert ru_keys, "chart i18n keys must exist"
    assert ru_keys == en_keys

    used = set(re.findall(r'[t(]\("(charts\.[A-Za-z0-9.]+)"\)', source[messages_end:]))
    missing = used - ru_keys
    assert not missing, f"t() references without dictionary entries: {sorted(missing)}"


def test_charts_js_respects_csp_and_determinism_rules() -> None:
    source = CHARTS_JS.read_text(encoding="utf-8")
    assert "createElementNS" in source
    assert "textContent" in source
    assert "AutonomoCharts" in source
    for forbidden in ("innerHTML", "Date.now", "Math.random", "document.write"):
        assert forbidden not in source, forbidden
    assert "style=" not in source
    assert re.search(r"\bon[a-z]+\s*=", source) is None
