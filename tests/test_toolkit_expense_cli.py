"""The native expense/asset workflow exercised through agent commands only."""

from datetime import date
import json
from pathlib import Path
import stat
from uuid import uuid4
import pytest
from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import open as open_db
from autonomo_taxes.services import invoices
from test_expense_workflow import setup_draft
from test_toolkit_cli import invoke


def prepare(fixture, tmp_path, capsys):
    out = tmp_path / "private" / "draft.json"
    code, summary = invoke(
        capsys,
        fixture,
        ["expense", "draft", fixture["transaction_id"], "--out", str(out)],
    )
    assert code == 0, summary
    assert "payload" not in summary and stat.S_IMODE(out.stat().st_mode) == 0o600
    draft = json.loads(out.read_text())
    draft["payload"]["facts"]["document_number"] = "SYN-AGENT-EDIT"
    code, saved = invoke(
        capsys,
        fixture,
        ["expense", "save", fixture["transaction_id"]],
        payload={
            "payload": draft["payload"],
            "expected_version": draft["draft_version"],
            "source_snapshot_hash": draft["source_snapshot_hash"],
        },
    )
    assert code == 0, saved
    assert saved["draft_version"] > draft["draft_version"] and "payload" not in saved
    return saved


def preview(fixture, saved, capsys):
    with open_db(fixture["database"], read_only=True) as db:
        before = "\n".join(db.connection.iterdump())
    code, proposed = invoke(
        capsys,
        fixture,
        ["expense", "preview", fixture["transaction_id"]],
        payload={"expected_version": saved["draft_version"]},
    )
    assert code == 0, proposed
    with open_db(fixture["database"], read_only=True) as db:
        assert "\n".join(db.connection.iterdump()) == before
    return {
        "expected_version": saved["draft_version"],
        "preview_token": proposed["preview_token"],
        "request_id": str(uuid4()),
    }


@pytest.mark.parametrize("method", [None, "immediate", "linear"])
def test_native_cli_draft_save_preview_confirm_is_idempotent(
    tmp_path, capsys, monkeypatch, method
):
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture, _ = setup_draft(tmp_path, method=method)
    saved = prepare(fixture, tmp_path, capsys)
    request = preview(fixture, saved, capsys)
    code, result = invoke(
        capsys,
        fixture,
        ["expense", "confirm", fixture["transaction_id"]],
        payload=request,
    )
    assert code == 0 and result["posted"], result
    with open_db(fixture["database"], read_only=True) as db:
        counts = db.table_counts()
        actions = db.connection.execute(
            "SELECT COUNT(*) FROM expense_actions"
        ).fetchone()[0]
        assert (
            db.connection.execute(
                "SELECT lifecycle_status FROM transactions WHERE transaction_id=?",
                (fixture["transaction_id"],),
            ).fetchone()[0]
            == "posted"
        )
        assert db.connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0
    code, repeated = invoke(
        capsys,
        fixture,
        ["expense", "confirm", fixture["transaction_id"]],
        payload=request,
    )
    assert code == 0 and repeated["transaction_id"] == result["transaction_id"]
    with open_db(fixture["database"], read_only=True) as db:
        assert (
            db.connection.execute("SELECT COUNT(*) FROM expense_actions").fetchone()[0]
            == actions
        )
        assert db.table_counts()["transactions"] == counts["transactions"]


def test_cli_follow_up_failure_keeps_post_and_retry_does_not_repost(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture, _ = setup_draft(tmp_path)
    saved = prepare(fixture, tmp_path, capsys)
    request = preview(fixture, saved, capsys)
    original = invoices.InvoiceService.refresh_dashboard

    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic calculation failure")

    monkeypatch.setattr(invoices.InvoiceService, "refresh_dashboard", fail)
    code, result = invoke(
        capsys,
        fixture,
        ["expense", "confirm", fixture["transaction_id"]],
        payload=request,
    )
    assert code == 0 and result["posted"] and result["follow_up_pending"]
    assert "calculation_refresh_pending" in result["warnings"]
    monkeypatch.setattr(invoices.InvoiceService, "refresh_dashboard", original)
    code, recovered = invoke(
        capsys, fixture, ["expense", "follow-up", fixture["transaction_id"]]
    )
    assert (
        code == 0 and recovered["posted"] and not recovered["follow_up_pending"]
    ), recovered
    with open_db(fixture["database"], read_only=True) as db:
        assert (
            db.connection.execute("SELECT COUNT(*) FROM expense_actions").fetchone()[0]
            == 1
        )
        assert (
            db.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            == 1
        )


def test_cli_stale_draft_preview_and_request_key_fail_without_new_post(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture, _ = setup_draft(tmp_path)
    saved = prepare(fixture, tmp_path, capsys)
    code, error = invoke(
        capsys,
        fixture,
        ["expense", "preview", fixture["transaction_id"]],
        payload={"expected_version": saved["draft_version"] - 1},
    )
    assert code == 1 and error["code"] == "expense_stale"
    request = preview(fixture, saved, capsys)
    code, error = invoke(
        capsys,
        fixture,
        ["expense", "confirm", fixture["transaction_id"]],
        payload={**request, "preview_token": "stale"},
    )
    assert code == 1
    code, result = invoke(
        capsys,
        fixture,
        ["expense", "confirm", fixture["transaction_id"]],
        payload=request,
    )
    assert code == 0, result
    code, error = invoke(
        capsys,
        fixture,
        ["expense", "confirm", fixture["transaction_id"]],
        payload={**request, "expected_version": saved["draft_version"] + 1},
    )
    assert code == 1 and error["code"] == "expense_stale"


def test_cli_posts_a_later_due_depreciation_once(tmp_path, capsys, monkeypatch):
    from autonomo_taxes import expense_workflow, posting, review_packet

    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture, _ = setup_draft(tmp_path, method="linear")
    saved = prepare(fixture, tmp_path, capsys)
    request = preview(fixture, saved, capsys)
    code, result = invoke(
        capsys,
        fixture,
        ["expense", "confirm", fixture["transaction_id"]],
        payload=request,
    )
    assert code == 0, result
    code, schedule = invoke(capsys, fixture, ["assets", "schedule", result["asset_id"]])
    assert code == 0, schedule
    row = next(row for row in schedule["rows"] if not row["recognition_transaction_id"])
    due = date.fromisoformat(row["recognition_on"])

    class Due(date):
        @classmethod
        def today(cls):
            return due

    for module in (expense_workflow, posting, review_packet, invoices):
        monkeypatch.setattr(module, "date", Due)
    code, schedule = invoke(capsys, fixture, ["assets", "schedule", result["asset_id"]])
    assert code == 0
    assert next(
        item
        for item in schedule["rows"]
        if item["amortization_entry_id"] == row["amortization_entry_id"]
    )["can_post"]
    request = {"expected_version": row["row_version"], "request_id": str(uuid4())}
    code, error = invoke(
        capsys,
        fixture,
        ["assets", "post-depreciation", row["amortization_entry_id"]],
        payload={**request, "expected_version": row["row_version"] + 1},
    )
    assert code == 1 and error["code"] == "expense_stale"
    code, posted = invoke(
        capsys,
        fixture,
        ["assets", "post-depreciation", row["amortization_entry_id"]],
        payload=request,
    )
    assert code == 0 and posted["posted"], posted
    code, repeated = invoke(
        capsys,
        fixture,
        ["assets", "post-depreciation", row["amortization_entry_id"]],
        payload=request,
    )
    assert code == 0 and repeated["transaction_id"] == posted["transaction_id"]
