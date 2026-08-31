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


def test_charts_js_respects_csp_and_determinism_rules() -> None:
    source = CHARTS_JS.read_text(encoding="utf-8")
    assert "createElementNS" in source
    assert "textContent" in source
    assert "AutonomoCharts" in source
    for forbidden in ("innerHTML", "Date.now", "Math.random", "document.write"):
        assert forbidden not in source, forbidden
    assert "style=" not in source
    assert re.search(r"\bon[a-z]+\s*=", source) is None
