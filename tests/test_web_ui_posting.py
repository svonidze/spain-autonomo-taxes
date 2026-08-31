from __future__ import annotations

from pathlib import Path
import subprocess


def _static_root() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "autonomo_taxes" / "web_ui"


def test_posting_ui_static_assets_cover_dashboard_banner_and_review_flow() -> None:
    static_root = _static_root()
    html = (static_root / "index.html").read_text(encoding="utf-8")
    javascript = (static_root / "app.js").read_text(encoding="utf-8")

    assert 'id="posting-confirm-dialog"' in html
    assert 'id="posting-confirm-body"' in html
    assert 'id="confirm-posting-button"' in html

    for expected in (
        'fetchJSON(`/api/review/posting-preview?period=${encodeURIComponent(period)}`)',
        'fetchJSON("/api/review/post-ready", {',
        'await requestDashboardRefresh({showSuccessToast: false, period: submissionPeriod});',
        'data-nav-view="review"',
        '"dashboard.postingBannerTitle": "{count} approved transactions are waiting to post"',
        '"dashboard.postingBannerTitle": "{count} подтвержденных операций ждут проведения"',
        '"review.postingCleanup": "Cleanup applies"',
        '"review.postingCleanupBlocked": "Cleanup blocked"',
        '"review.postingStaleWarning": "Transactions were posted, but the tax calculation needs to be refreshed."',
        '"review.postingStaleWarning": "Операции проведены, налоговый расчёт требует обновления."',
        '"review.postConfirmAction": "Post ready"',
        '"review.postConfirmAction": "Провести"',
        '"review.postConfirmCleanupWarning": "Posting affects cleanup: applies {cleanupCount}, blocked {cleanupBlockedCount}. Review the reasons before confirming."',
        '"review.postConfirmCleanupWarning": "Проведение затронет cleanup: применится {cleanupCount}, заблокировано {cleanupBlockedCount}. Проверьте причины перед подтверждением."',
    ):
        assert expected in javascript


def test_posting_ui_behavioral_helpers_run_under_node() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "tests" / "test_web_ui_posting_behavior.js"
    subprocess.run(["node", str(script)], cwd=root, check=True)
