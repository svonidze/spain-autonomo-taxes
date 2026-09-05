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
STYLES_CSS = WEB_UI / "styles.css"
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


def test_chart_resources_are_part_of_the_built_application() -> None:
    from autonomo_taxes.ui_assets import UiAssets
    assets = UiAssets(WEB_UI)
    assert any(name.endswith(".js") for name in assets.files)
    # Initialization order is exercised by tests/browser/baseline.spec.ts


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


def test_chart_expand_dialog_is_wired_and_closes_with_the_view() -> None:
    index_source = INDEX_HTML.read_text(encoding="utf-8")
    assert index_source.count('id="chart-dialog"') == 1
    assert 'id="chart-dialog-slot"' in index_source
    assert 'id="chart-dialog-close"' in index_source

    source = APP_JS.read_text(encoding="utf-8")
    render_current_view = source[
        source.index("async function renderCurrentView(") : source.index(
            "\nasync function renderDashboard("
        )
    ]
    assert "closeChartDialog();" in render_current_view
    refresh_guard = source[
        source.index("const refreshVisibleExpenseData") : source.index(
            "window.setInterval(refreshVisibleExpenseData"
        )
    ]
    assert "chartDialog?.open" in refresh_guard
    assert "chart-expand-button" in CHARTS_JS.read_text(encoding="utf-8")

    # close() fires asynchronously, so the close listener — not closeChartDialog —
    # must own the cleanup, or the opener is already null when focus returns.
    close_helper = source[
        source.index("function closeChartDialog()") : source.index(
            "function withChartExpandAction("
        )
    ]
    assert "chartDialog.close();\n    return;" in close_helper
    close_listener = source[
        source.index('chartDialog?.addEventListener("close"') : source.index(
            'document.addEventListener("visibilitychange", refreshVisibleExpenseData)'
        )
    ]
    assert "opener.focus({preventScroll: true})" in close_listener


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


def test_expense_chart_keeps_labels_when_tables_reflow() -> None:
    chart_source = CHARTS_JS.read_text(encoding="utf-8")
    assert 'cell.setAttribute("data-label", headers[index] || "")' in chart_source
    assert "tableInitiallyOpen" in chart_source
    assert "chart-table-primary-on-narrow" in chart_source

    styles = STYLES_CSS.read_text(encoding="utf-8")
    narrow_rule = styles[styles.index("@media (max-width: 760px)") :]
    assert ".chart-table-primary-on-narrow .chart-host" in narrow_rule
    assert ".chart-table-primary-on-narrow .chart-legend" in narrow_rule
