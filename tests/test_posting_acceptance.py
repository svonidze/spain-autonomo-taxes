from __future__ import annotations

from hashlib import sha256
import io
import json
from pathlib import Path
import sqlite3

import pytest

from autonomo_taxes import operational_cli
from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.posting import build_posting_preview


def test_post_batch_skips_stale_row_and_posts_later_ready_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    first = _seed_intake_transaction(tmp_path, key="stale-first")
    second = _seed_intake_transaction(tmp_path, key="stale-second")

    with initialize(first["database"]) as db:
        db.add_obligation(
            period_key="2026-Q3",
            obligation_code="130",
            filing_status="unknown",
            determination="unknown",
            blocking=False,
            explanation="Immutable acceptance sentinel",
        )
        db.create_filing_snapshot(
            "2026-Q3",
            payload={"form": "130", "values": {"19": "123.45"}},
            status="draft",
            snapshot_hash="posting-acceptance-snapshot",
        )
        db.connection.execute(
            """
            UPDATE transactions
            SET description = ?, row_version = row_version + 1
            WHERE transaction_id = ?
            """,
            ("stale-first-updated", first["transaction_id"]),
        )
        db.connection.commit()
        current_first = db.connection.execute(
            "SELECT row_version, lifecycle_status FROM transactions WHERE transaction_id = ?",
            (first["transaction_id"],),
        ).fetchone()
        before_obligations = _stable_rows(
            db.connection,
            "SELECT * FROM obligations WHERE period_id = (SELECT period_id FROM periods WHERE period_key = ?) ORDER BY obligation_id",
            ("2026-Q3",),
        )
        before_snapshots = _stable_rows(
            db.connection,
            "SELECT * FROM filing_snapshots WHERE period_id = (SELECT period_id FROM periods WHERE period_key = ?) ORDER BY filing_snapshot_id",
            ("2026-Q3",),
        )
        assert json.loads(before_obligations)
        assert json.loads(before_snapshots)

    request = {
        "period": "2026-Q3",
        "items": [
            {
                "review_id": f"transaction:{first['transaction_id']}",
                "expected_row_version": first["row_version"],
            },
            {
                "review_id": f"transaction:{second['transaction_id']}",
                "expected_row_version": second["row_version"],
            },
        ],
    }
    monkeypatch.setattr(operational_cli.sys, "stdin", io.StringIO(json.dumps(request)))

    assert main(_batch_args(first)) == 0
    emitted = json.loads(capsys.readouterr().out)

    assert emitted["status"] == "partial"
    assert emitted["summary"] == {
        "requested": 2,
        "posted": 1,
        "already_posted": 0,
        "already_finalized": 0,
        "skipped": 1,
        "failed": 0,
        "interrupted": 0,
        "not_attempted": 0,
    }
    first_result, second_result = emitted["results"]
    assert first_result["outcome"] == "skipped"
    assert first_result["reason_code"] == "skipped_stale"
    assert first_result["previous_row_version"] == current_first["row_version"]
    assert first_result["new_row_version"] == current_first["row_version"]
    assert second_result["outcome"] == "posted"
    assert second_result["previous_row_version"] == second["row_version"]
    assert second_result["new_row_version"] == second["row_version"] + 1

    with initialize(first["database"]) as db:
        rows = db.connection.execute(
            """
            SELECT transaction_id, lifecycle_status, row_version
            FROM transactions
            WHERE transaction_id IN (?, ?)
            ORDER BY transaction_id
            """,
            (first["transaction_id"], second["transaction_id"]),
        ).fetchall()
        after_obligations = _stable_rows(
            db.connection,
            "SELECT * FROM obligations WHERE period_id = (SELECT period_id FROM periods WHERE period_key = ?) ORDER BY obligation_id",
            ("2026-Q3",),
        )
        after_snapshots = _stable_rows(
            db.connection,
            "SELECT * FROM filing_snapshots WHERE period_id = (SELECT period_id FROM periods WHERE period_key = ?) ORDER BY filing_snapshot_id",
            ("2026-Q3",),
        )
    by_id = {row["transaction_id"]: row for row in rows}
    assert by_id[first["transaction_id"]]["lifecycle_status"] == "approved"
    assert by_id[first["transaction_id"]]["row_version"] == current_first["row_version"]
    assert by_id[second["transaction_id"]]["lifecycle_status"] == "posted"
    assert by_id[second["transaction_id"]]["row_version"] == second["row_version"] + 1
    assert before_obligations == after_obligations
    assert before_snapshots == after_snapshots


def test_post_batch_already_posted_beats_stale_expected_version_without_cleanup_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(tmp_path, key="already-posted")
    initial = {
        "period": "2026-Q3",
        "items": [
            {
                "review_id": f"transaction:{seeded['transaction_id']}",
                "expected_row_version": seeded["row_version"],
            }
        ],
    }
    monkeypatch.setattr(operational_cli.sys, "stdin", io.StringIO(json.dumps(initial)))
    assert main(_batch_args(seeded)) == 0
    json.loads(capsys.readouterr().out)

    stale_repeat = {
        "period": "2026-Q3",
        "items": [
            {
                "review_id": f"transaction:{seeded['transaction_id']}",
                "expected_row_version": 0,
            }
        ],
    }
    monkeypatch.setattr(operational_cli.sys, "stdin", io.StringIO(json.dumps(stale_repeat)))
    assert main(_batch_args(seeded)) == 0
    emitted = json.loads(capsys.readouterr().out)

    assert emitted["status"] == "completed"
    assert emitted["summary"] == {
        "requested": 1,
        "posted": 0,
        "already_posted": 1,
        "already_finalized": 0,
        "skipped": 0,
        "failed": 0,
        "interrupted": 0,
        "not_attempted": 0,
    }
    result = emitted["results"][0]
    assert result["outcome"] == "already_posted"
    assert result["reason_code"] == "already_posted"
    assert result["cleanup_status"] == "already_absent"
    assert result["previous_row_version"] == seeded["row_version"] + 1
    assert result["new_row_version"] == seeded["row_version"] + 1
    assert not seeded["source"].exists()

    with initialize(seeded["database"]) as db:
        stored = db.connection.execute(
            "SELECT lifecycle_status, row_version FROM transactions WHERE transaction_id = ?",
            (seeded["transaction_id"],),
        ).fetchone()
    assert stored["lifecycle_status"] == "posted"
    assert stored["row_version"] == seeded["row_version"] + 1


def test_post_batch_already_finalized_beats_stale_expected_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(tmp_path, key="already-finalized")
    with initialize(seeded["database"]) as db:
        db.connection.execute(
            """
            UPDATE transactions
            SET lifecycle_status = 'included_in_snapshot', row_version = row_version + 1
            WHERE transaction_id = ?
            """,
            (seeded["transaction_id"],),
        )
        db.connection.commit()

    request = {
        "period": "2026-Q3",
        "items": [
            {
                "review_id": f"transaction:{seeded['transaction_id']}",
                "expected_row_version": 0,
            }
        ],
    }
    monkeypatch.setattr(operational_cli.sys, "stdin", io.StringIO(json.dumps(request)))

    assert main(_batch_args(seeded)) == 0
    emitted = json.loads(capsys.readouterr().out)

    assert emitted["status"] == "completed"
    assert emitted["summary"] == {
        "requested": 1,
        "posted": 0,
        "already_posted": 0,
        "already_finalized": 1,
        "skipped": 0,
        "failed": 0,
        "interrupted": 0,
        "not_attempted": 0,
    }
    result = emitted["results"][0]
    assert result["outcome"] == "already_finalized"
    assert result["reason_code"] == "already_finalized"
    assert result["cleanup_status"] == "not_applicable"
    assert result["previous_row_version"] == seeded["row_version"] + 1
    assert result["new_row_version"] == seeded["row_version"] + 1


def test_build_posting_preview_uses_dashboard_amount_formula_for_eur_and_missing_fx(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        eur = db.add_transaction(
            external_key="posting-preview-eur",
            period_key="2026-Q3",
            transaction_date="2026-07-10",
            booking_date="2026-07-10",
            entry_type="expense",
            description="EUR expense",
            amount_minor=12345,
            currency="EUR",
            lifecycle_status="approved",
        )
        db.add_detailed_tax_treatment(
            transaction_id=eur["transaction_id"],
            treatment_type="invoice_review",
            tax_code="domestic_expense",
            taxable_base_minor=12345,
            deductible_irpf_minor=12345,
            include_modelo130=True,
        )
        foreign = db.add_transaction(
            external_key="posting-preview-foreign",
            period_key="2026-Q3",
            transaction_date="2026-07-11",
            booking_date="2026-07-11",
            entry_type="expense",
            description="USD expense without EUR value",
            amount_minor=9000,
            currency="USD",
            original_currency="USD",
            amount_eur_minor=None,
            fx_rate_id=None,
            lifecycle_status="approved",
        )
        db.add_detailed_tax_treatment(
            transaction_id=foreign["transaction_id"],
            treatment_type="invoice_review",
            tax_code="domestic_expense",
            taxable_base_minor=9000,
            deductible_irpf_minor=9000,
            include_modelo130=True,
        )

        preview = build_posting_preview(db, period_key="2026-Q3")

    items = {item["transaction_id"]: item for item in preview["items"]}
    assert items[eur["transaction_id"]]["effective_amount_eur_minor"] == 12345
    assert items[eur["transaction_id"]]["effective_amount_eur_formula"] == "amount_eur_minor"
    assert items[foreign["transaction_id"]]["effective_amount_eur_minor"] == 0
    assert items[foreign["transaction_id"]]["effective_amount_eur_formula"] == "missing_foreign_exchange"
    assert preview["summary"]["ready_expense_eur_minor"] == 12345


def test_post_batch_returns_retry_later_when_sqlite_is_busy_before_first_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(tmp_path, key="db-busy")
    request = {
        "period": "2026-Q3",
        "items": [
            {
                "review_id": f"transaction:{seeded['transaction_id']}",
                "expected_row_version": seeded["row_version"],
            }
        ],
    }
    lock = sqlite3.connect(seeded["database"])
    lock.execute("BEGIN EXCLUSIVE")
    try:
        monkeypatch.setattr(operational_cli.sys, "stdin", io.StringIO(json.dumps(request)))
        assert main(_batch_args(seeded)) == 2
        emitted = json.loads(capsys.readouterr().out)
    finally:
        lock.rollback()
        lock.close()

    assert emitted["status"] == "retry_later"
    assert emitted["error"] == "db_busy"
    assert emitted["period"] == "2026-Q3"


def test_post_batch_cleanup_runtime_error_keeps_commit_and_reports_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(tmp_path, key="cleanup-runtime-error")
    request = {
        "period": "2026-Q3",
        "items": [
            {
                "review_id": f"transaction:{seeded['transaction_id']}",
                "expected_row_version": seeded["row_version"],
            }
        ],
    }

    def fail_cleanup(_candidate) -> None:
        raise RuntimeError("simulated unexpected cleanup failure")

    monkeypatch.setattr(posting_operations, "cleanup_expense_inbox_source", fail_cleanup)
    monkeypatch.setattr(operational_cli.sys, "stdin", io.StringIO(json.dumps(request)))

    assert main(_batch_args(seeded)) == 0
    emitted = json.loads(capsys.readouterr().out)

    assert emitted["status"] == "partial"
    assert emitted["interrupted"] is False
    assert emitted["summary"]["posted"] == 1
    assert emitted["results"][0]["outcome"] == "posted"
    assert emitted["results"][0]["reason_code"] == "posted_cleanup_failed"
    assert emitted["results"][0]["cleanup_status"] == "failed"
    assert seeded["source"].is_file()
    assert seeded["archive"].is_file()
    with initialize(seeded["database"]) as db:
        stored = db.connection.execute(
            "SELECT lifecycle_status, row_version FROM transactions WHERE transaction_id = ?",
            (seeded["transaction_id"],),
        ).fetchone()
    assert stored["lifecycle_status"] == "posted"
    assert stored["row_version"] == seeded["row_version"] + 1


def test_post_batch_unexpected_error_before_first_commit_is_structured_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(tmp_path, key="precommit-internal-error")
    request = {
        "period": "2026-Q3",
        "items": [
            {
                "review_id": f"transaction:{seeded['transaction_id']}",
                "expected_row_version": seeded["row_version"],
            }
        ],
    }

    def fail_before_commit(*_args, **_kwargs):
        raise RuntimeError("simulated infrastructure failure")

    monkeypatch.setattr(posting_operations, "_post_transaction_review", fail_before_commit)
    monkeypatch.setattr(operational_cli.sys, "stdin", io.StringIO(json.dumps(request)))

    assert main(_batch_args(seeded)) == 2
    emitted = json.loads(capsys.readouterr().out)

    assert emitted == {
        "status": "internal_error",
        "error": "internal_error",
        "period": "2026-Q3",
        "message": "simulated infrastructure failure",
    }
    with initialize(seeded["database"]) as db:
        stored = db.connection.execute(
            "SELECT lifecycle_status, row_version FROM transactions WHERE transaction_id = ?",
            (seeded["transaction_id"],),
        ).fetchone()
    assert stored["lifecycle_status"] == "approved"
    assert stored["row_version"] == seeded["row_version"]
    assert seeded["source"].is_file()
    assert seeded["archive"].is_file()


def _batch_args(seeded: dict[str, object]) -> list[str]:
    return [
        "review",
        "post-batch",
        "--db",
        str(seeded["database"]),
        "--period",
        "2026-Q3",
        "--inbox-root",
        str(seeded["inbox_root"]),
        "--archive-root",
        str(seeded["archive_root"]),
    ]


def _seed_intake_transaction(
    root: Path,
    *,
    key: str,
) -> dict[str, object]:
    database = root / "ledger.sqlite"
    period_key = "2026-Q3"
    inbox_root = root / "Inbox"
    archive_root = root / "Evidence"
    source = inbox_root / period_key / "expense_invoice" / f"{key}.pdf"
    archive = archive_root / period_key / "expense_invoice" / f"digest-{key}.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    archive.parent.mkdir(parents=True, exist_ok=True)
    content = f"immutable invoice {key}".encode("utf-8")
    source.write_bytes(content)
    archive.write_bytes(content)
    digest = sha256(content).hexdigest()

    with initialize(database) as db:
        batch = db.add_import_batch(
            source_name=str(source.resolve()),
            source_hash=digest,
            batch_key=f"document:{digest}",
        )
        document = db.upsert_document(
            external_key=f"sha256:{digest}",
            import_batch_id=batch["import_batch_id"],
            document_type="expense_invoice",
            document_number=f"INV-{key}",
            issued_on="2026-07-20",
            period_key=period_key,
            total_minor=12100,
            lifecycle_status="approved",
            source_hash=digest,
        )
        document = db.set_document_storage(
            document["document_id"],
            source_path=str(archive.resolve()),
            mime_type="application/pdf",
            expected_row_version=document["row_version"],
        )
        transaction = db.add_transaction(
            external_key=f"intake-document:{document['document_id']}",
            period_key=period_key,
            transaction_date="2026-07-20",
            booking_date="2026-07-20",
            entry_type="expense",
            description=f"Reviewed invoice {key}",
            amount_minor=12100,
            lifecycle_status="approved",
            document_id=document["document_id"],
            source_hash=digest,
        )
        treatment = db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="invoice_review",
            tax_code="domestic_expense",
            taxable_base_minor=10000,
            vat_minor=2100,
            deductible_irpf_minor=12100,
            deductible_vat_minor=0,
            include_modelo130=True,
        )
        db.add_intake_receipt(
            intake_tab="expense_intake",
            source_row_number=2,
            row_fingerprint=f"row-{digest}-{key}",
            evidence_sha256=digest,
            document_id=document["document_id"],
            transaction_id=transaction["transaction_id"],
            treatment_id=treatment["treatment_id"],
            input_payload={"file_name": source.name},
        )
    return {
        "database": database,
        "inbox_root": inbox_root,
        "archive_root": archive_root,
        "source": source.resolve(),
        "archive": archive.resolve(),
        "transaction_id": transaction["transaction_id"],
        "row_version": transaction["row_version"],
    }


def _stable_rows(connection: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> str:
    rows = connection.execute(sql, params).fetchall()
    normalized = [
        {key: row[key] for key in row.keys() if key not in {"updated_at", "created_at"}}
        for row in rows
    ]
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False)


from autonomo_taxes.services import posting_operations
