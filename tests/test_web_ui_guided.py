from __future__ import annotations

from pathlib import Path
import subprocess


def test_web_ui_guided_helpers_run_under_node() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "tests" / "test_web_ui_guided.js"
    subprocess.run(["node", str(script)], cwd=root, check=True)


def test_web_ui_guided_markers_exist_in_app() -> None:
    javascript = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "autonomo_taxes"
        / "web_ui"
        / "app.js"
    ).read_text(encoding="utf-8")
    for expected in (
        'fetchJSON("/api/review/confirm", {',
        'body: JSON.stringify({packet, fx: fxSpec}),',
        'id="review-primary-button"',
        'id="review-reject-button"',
        'class="secondary-button review-back-link"',
        'data-spa data-open-review-id',
        'window.addEventListener("popstate", () => {',
        'navigateToRoute(viewButton.dataset.navView)',
        'periodSelect.disabled = state.view === "review" && Boolean(state.review.selectedReviewId)',
    ):
        assert expected in javascript
