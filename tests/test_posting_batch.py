from __future__ import annotations

from datetime import date
from hashlib import sha256
import io
import json
from pathlib import Path

import pytest

from autonomo_taxes import operational_cli
from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import LedgerDbError, initialize
from autonomo_taxes.posting import build_posting_preview


def test_build_posting_preview_separates_ready_deferred_and_blocked(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        ready = _add_review_transaction(
            db,
            external_key="preview-ready",
            period_key="2026-Q3",
            transaction_date="2026-07-20",
            tax_code="domestic_expense",
        )
        deferred = _add_review_transaction(
            db,
            external_key="preview-deferred",
            period_key="2026-Q4",
            transaction_date="2026-10-01",
            tax_code="domestic_expense",
        )
        blocked = _add_review_transaction(
            db,
            external_key="preview-blocked",
            period_key="2026-Q3",
            transaction_date="2026-07-22",
            tax_code="unknown",
        )
        q3 = build_posting_preview(db, period_key="2026-Q3", today=date(2026, 8, 4))
        q4 = build_posting_preview(db, period_key="2026-Q4", today=date(2026, 8, 4))

    assert q3["period_status"] == "open"
    assert q3["summary"]["approved_count"] == 2
    assert q3["summary"]["ready_count"] == 1
    assert q3["summary"]["blocked_count"] == 1
    assert q3["summary"]["deferred_count"] == 0
    assert q3["summary"]["ready_expense_eur_minor"] == 10000
    assert [item["transaction_id"] for item in q3["ready"]] == [ready["transaction_id"]]
    assert [item["transaction_id"] for item in q3["blocked"]] == [blocked["transaction_id"]]
    assert q3["ready"][0]["expected_row_version"] == ready["row_version"]
    assert q3["blocked"][0]["effective_amount_eur_formula"] == "amount_eur_minor"
    assert q3["blocked"][0]["cleanup"]["status"] == "not_applicable"
    assert q4["summary"]["ready_count"] == 0
    assert q4["summary"]["deferred_count"] == 1
    assert q4["summary"]["blocked_count"] == 0
    assert [item["transaction_id"] for item in q4["deferred"]] == [deferred["transaction_id"]]
    assert q4["deferred"][0]["posting_deferred_until"] == "2026-10-01"


def test_build_posting_preview_blocks_cleanup_when_roots_are_missing(
    tmp_path: Path,
) -> None:
    seeded = _seed_intake_transaction(tmp_path, key="preview-cleanup-blocked")
    with initialize(seeded["database"]) as db:
        preview = build_posting_preview(db, period_key="2026-Q3")

    assert preview["summary"]["approved_count"] == 1
    assert preview["summary"]["ready_count"] == 0
    assert preview["summary"]["blocked_count"] == 1
    assert preview["summary"]["cleanup_count"] == 1
    assert preview["summary"]["cleanup_blocked_count"] == 1
    assert preview["ready"] == []
    assert preview["deferred"] == []
    assert preview["blocked"][0]["cleanup"]["blocking"] is True
    assert preview["blocked"][0]["cleanup"]["reason_code"] == "inbox_roots_not_configured"
    assert preview["blocked"][0]["ready_to_post"] is False


def test_review_post_batch_reads_review_list_rows_and_is_row_version_idempotent(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(tmp_path, key="batch-idempotent")
    request = _review_ready_to_post_request(
        seeded["database"],
        seeded["inbox_root"],
        seeded["archive_root"],
        capsys,
    )

    monkeypatch.setattr(
        operational_cli.sys,
        "stdin",
        io.StringIO(json.dumps(request)),
    )
    assert main(
        [
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
    ) == 0
    first = json.loads(capsys.readouterr().out)

    assert first["status"] == "completed"
    assert first["interrupted"] is False
    assert first["summary"] == {
        "requested": 1,
        "posted": 1,
        "already_posted": 0,
        "already_finalized": 0,
        "interrupted": 0,
        "failed": 0,
        "not_attempted": 0,
        "skipped": 0,
    }
    assert first["results"][0]["outcome"] == "posted"
    assert first["results"][0]["result"]["inbox_cleanup"]["status"] == "deleted"
    assert not seeded["source"].exists()

    monkeypatch.setattr(
        operational_cli.sys,
        "stdin",
        io.StringIO(json.dumps(request)),
    )
    assert main(
        [
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
    ) == 0
    second = json.loads(capsys.readouterr().out)

    assert second["status"] == "completed"
    assert second["interrupted"] is False
    assert second["summary"] == {
        "requested": 1,
        "posted": 0,
        "already_posted": 1,
        "already_finalized": 0,
        "skipped": 0,
        "interrupted": 0,
        "failed": 0,
        "not_attempted": 0,
    }
    assert second["results"][0]["outcome"] == "already_posted"
    assert second["results"][0]["result"]["inbox_cleanup"]["status"] == "already_absent"
    with initialize(seeded["database"]) as db:
        stored = db.connection.execute(
            "SELECT lifecycle_status, row_version FROM transactions WHERE transaction_id = ?",
            (seeded["transaction_id"],),
        ).fetchone()
    assert stored["lifecycle_status"] == "posted"
    assert stored["row_version"] == seeded["row_version"] + 1


def test_review_post_batch_reports_interrupted_cleanup_and_can_resume(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(tmp_path, key="batch-interrupted")
    request = _review_ready_to_post_request(
        seeded["database"],
        seeded["inbox_root"],
        seeded["archive_root"],
        capsys,
    )
    real_cleanup = operational_cli.cleanup_expense_inbox_source

    def fail_cleanup(candidate):
        raise PermissionError("simulated lock")

    monkeypatch.setattr(posting_operations, "cleanup_expense_inbox_source", fail_cleanup)
    monkeypatch.setattr(
        operational_cli.sys,
        "stdin",
        io.StringIO(json.dumps(request)),
    )
    assert main(
        [
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
    ) == 0
    interrupted = json.loads(capsys.readouterr().out)

    assert interrupted["status"] == "partial"
    assert interrupted["interrupted"] is False
    assert interrupted["summary"] == {
        "requested": 1,
        "posted": 1,
        "already_posted": 0,
        "already_finalized": 0,
        "interrupted": 1,
        "failed": 0,
        "not_attempted": 0,
        "skipped": 0,
    }
    assert interrupted["results"][0]["outcome"] == "posted"
    assert interrupted["results"][0]["result"]["lifecycle_status"] == "posted"
    assert interrupted["results"][0]["result"]["reason_code"] == "posted_cleanup_failed"
    assert seeded["source"].is_file()

    monkeypatch.setattr(posting_operations, "cleanup_expense_inbox_source", real_cleanup)
    monkeypatch.setattr(
        operational_cli.sys,
        "stdin",
        io.StringIO(json.dumps(request)),
    )
    assert main(
        [
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
    ) == 0
    resumed = json.loads(capsys.readouterr().out)

    assert resumed["status"] == "completed"
    assert resumed["interrupted"] is False
    assert resumed["summary"] == {
        "requested": 1,
        "posted": 0,
        "already_posted": 1,
        "already_finalized": 0,
        "skipped": 0,
        "interrupted": 0,
        "failed": 0,
        "not_attempted": 0,
    }
    assert resumed["results"][0]["outcome"] == "already_posted"
    assert resumed["results"][0]["result"]["inbox_cleanup"]["status"] == "failed"
    assert seeded["source"].is_file()

    assert main(
        [
            "inbox",
            "cleanup-posted",
            "--db",
            str(seeded["database"]),
            f"transaction:{seeded['transaction_id']}",
            "--inbox-root",
            str(seeded["inbox_root"]),
            "--archive-root",
            str(seeded["archive_root"]),
        ]
    ) == 0
    cleanup = json.loads(capsys.readouterr().out)
    assert cleanup["inbox_cleanup"]["status"] == "deleted"
    assert not seeded["source"].exists()


def test_review_post_batch_keeps_prior_commits_when_a_later_row_fails(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    first = _seed_intake_transaction(tmp_path, key="batch-first")
    second = _seed_intake_transaction(tmp_path, key="batch-second", tax_code="unknown")
    assert first["database"] == second["database"]
    request = _review_full_period_request(
        first["database"],
        first["inbox_root"],
        first["archive_root"],
        capsys,
    )

    monkeypatch.setattr(
        operational_cli.sys,
        "stdin",
        io.StringIO(json.dumps(request)),
    )
    assert main(
        [
            "review",
            "post-batch",
            "--db",
            str(first["database"]),
            "--period",
            "2026-Q3",
            "--inbox-root",
            str(first["inbox_root"]),
            "--archive-root",
            str(first["archive_root"]),
        ]
    ) == 0
    emitted = json.loads(capsys.readouterr().out)

    assert emitted["status"] == "partial"
    assert emitted["interrupted"] is False
    assert emitted["summary"] == {
        "requested": 2,
        "posted": 1,
        "already_posted": 0,
        "already_finalized": 0,
        "interrupted": 0,
        "failed": 1,
        "not_attempted": 0,
        "skipped": 0,
    }
    outcomes = {item["review_id"]: item for item in emitted["results"]}
    assert outcomes[f"transaction:{first['transaction_id']}"]["outcome"] == "posted"
    assert outcomes[f"transaction:{second['transaction_id']}"]["outcome"] == "failed"
    with initialize(first["database"]) as db:
        stored = db.connection.execute(
            """
            SELECT transaction_id, lifecycle_status, row_version
            FROM transactions
            WHERE transaction_id IN (?, ?)
            ORDER BY transaction_id
            """,
            (first["transaction_id"], second["transaction_id"]),
        ).fetchall()
        by_id = {row["transaction_id"]: row for row in stored}
    assert by_id[first["transaction_id"]]["lifecycle_status"] == "posted"
    assert by_id[first["transaction_id"]]["row_version"] == first["row_version"] + 1
    assert by_id[second["transaction_id"]]["lifecycle_status"] == "approved"
    assert by_id[second["transaction_id"]]["row_version"] == second["row_version"]
    assert not first["source"].exists()
    assert second["source"].is_file()


def test_review_post_batch_stops_after_unexpected_interruption(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    first = _seed_intake_transaction(tmp_path, key="unexpected-first")
    second = _seed_intake_transaction(tmp_path, key="unexpected-second")
    third = _seed_intake_transaction(tmp_path, key="unexpected-third")
    request = _review_full_period_request(
        first["database"],
        first["inbox_root"],
        first["archive_root"],
        capsys,
    )
    real_post = operational_cli._post_transaction_review
    interrupted_review_id = request["items"][1]["review_id"]
    interrupted_transaction_id = interrupted_review_id.split(":", 1)[1]

    def flaky_post(*args, **kwargs):
        if kwargs["transaction_id"] == interrupted_transaction_id:
            raise RuntimeError("simulated unexpected interruption")
        return real_post(*args, **kwargs)

    monkeypatch.setattr(posting_operations, "_post_transaction_review", flaky_post)
    monkeypatch.setattr(
        operational_cli.sys,
        "stdin",
        io.StringIO(json.dumps(request)),
    )
    assert main(
        [
            "review",
            "post-batch",
            "--db",
            str(first["database"]),
            "--period",
            "2026-Q3",
            "--inbox-root",
            str(first["inbox_root"]),
            "--archive-root",
            str(first["archive_root"]),
        ]
    ) == 0
    emitted = json.loads(capsys.readouterr().out)

    assert emitted["status"] == "interrupted"
    assert emitted["interrupted"] is True
    assert emitted["summary"] == {
        "requested": 3,
        "posted": 1,
        "already_posted": 0,
        "already_finalized": 0,
        "interrupted": 0,
        "failed": 1,
        "not_attempted": 1,
        "skipped": 0,
    }
    outcomes = {item["review_id"]: item for item in emitted["results"]}
    assert outcomes[request["items"][0]["review_id"]]["outcome"] == "posted"
    assert outcomes[request["items"][1]["review_id"]]["outcome"] == "failed"
    assert outcomes[request["items"][2]["review_id"]]["outcome"] == "not_attempted"
    with initialize(first["database"]) as db:
        stored = db.connection.execute(
            """
            SELECT transaction_id, lifecycle_status, row_version
            FROM transactions
            WHERE transaction_id IN (?, ?, ?)
            ORDER BY transaction_id
            """,
            (first["transaction_id"], second["transaction_id"], third["transaction_id"]),
        ).fetchall()
        by_id = {row["transaction_id"]: row for row in stored}
    first_request_id = request["items"][0]["review_id"].split(":", 1)[1]
    second_request_id = request["items"][1]["review_id"].split(":", 1)[1]
    third_request_id = request["items"][2]["review_id"].split(":", 1)[1]
    assert by_id[first_request_id]["lifecycle_status"] == "posted"
    assert by_id[second_request_id]["lifecycle_status"] == "approved"
    assert by_id[third_request_id]["lifecycle_status"] == "approved"


@pytest.mark.parametrize("period_status", ["closed", "amended"])
def test_review_post_batch_reports_stale_preview_when_period_is_not_open(
    tmp_path: Path,
    monkeypatch,
    capsys,
    period_status: str,
) -> None:
    seeded = _seed_intake_transaction(tmp_path, key=f"stale-{period_status}")
    request = _review_ready_to_post_request(
        seeded["database"],
        seeded["inbox_root"],
        seeded["archive_root"],
        capsys,
    )
    with initialize(seeded["database"]) as db:
        db.connection.execute(
            "UPDATE periods SET status = ? WHERE period_key = ?",
            (period_status, "2026-Q3"),
        )
        db.connection.commit()

    monkeypatch.setattr(
        operational_cli.sys,
        "stdin",
        io.StringIO(json.dumps(request)),
    )
    assert main(
        [
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
    ) == 2
    emitted = json.loads(capsys.readouterr().out)

    assert emitted["status"] == "period_not_open"
    assert emitted["interrupted"] is False
    assert emitted["summary"] == {
        "requested": 1,
        "posted": 0,
        "already_posted": 0,
        "already_finalized": 0,
        "skipped": 0,
        "interrupted": 0,
        "failed": 0,
        "not_attempted": 1,
    }
    assert emitted["results"][0]["outcome"] == "not_attempted"
    assert "immutable after close" in emitted["results"][0]["result"]["message"]
    with initialize(seeded["database"]) as db:
        stored = db.connection.execute(
            "SELECT lifecycle_status, row_version FROM transactions WHERE transaction_id = ?",
            (seeded["transaction_id"],),
        ).fetchone()
    assert stored["lifecycle_status"] == "approved"
    assert stored["row_version"] == seeded["row_version"]


def test_review_post_batch_fails_closed_if_period_closes_after_preflight_before_first_mutation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(tmp_path, key="period-closes-after-preflight")
    request = _review_ready_to_post_request(
        seeded["database"],
        seeded["inbox_root"],
        seeded["archive_root"],
        capsys,
    )
    original = operational_cli._post_transaction_review

    def close_period_before_first_row(db, **kwargs):
        db.connection.execute(
            "UPDATE periods SET status = 'closed' WHERE period_key = ?",
            ("2026-Q3",),
        )
        db.connection.commit()
        raise LedgerDbError("Period 2026-Q3 is immutable after close")

    monkeypatch.setattr(
        posting_operations,
        "_post_transaction_review",
        close_period_before_first_row,
    )
    monkeypatch.setattr(
        operational_cli.sys,
        "stdin",
        io.StringIO(json.dumps(request)),
    )
    try:
        assert main(
            [
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
        ) == 2
    finally:
        monkeypatch.setattr(
            posting_operations,
            "_post_transaction_review",
            original,
        )
    emitted = json.loads(capsys.readouterr().out)

    assert emitted["status"] == "period_not_open"
    assert emitted["summary"] == {
        "requested": 1,
        "posted": 0,
        "already_posted": 0,
        "already_finalized": 0,
        "skipped": 0,
        "interrupted": 0,
        "failed": 0,
        "not_attempted": 1,
    }
    assert emitted["results"][0]["outcome"] == "not_attempted"
    assert emitted["results"][0]["result"]["reason_code"] == "period_not_open"
    with initialize(seeded["database"]) as db:
        transaction = db.connection.execute(
            "SELECT lifecycle_status, row_version FROM transactions WHERE transaction_id = ?",
            (seeded["transaction_id"],),
        ).fetchone()
    assert transaction["lifecycle_status"] == "approved"
    assert transaction["row_version"] == seeded["row_version"]


def _review_ready_to_post_request(
    database: Path,
    inbox_root: Path,
    archive_root: Path,
    capsys,
) -> dict[str, object]:
    assert main(
        [
            "review",
            "list",
            "--db",
            str(database),
            "--period",
            "2026-Q3",
            "--inbox-root",
            str(inbox_root),
            "--archive-root",
            str(archive_root),
            "--ready-to-post",
        ]
    ) == 0
    queue = json.loads(capsys.readouterr().out)
    return {
        "period": "2026-Q3",
        "items": [
            {
                "review_id": row["review_id"],
                "expected_row_version": row["expected_row_version"],
            }
            for row in queue
        ],
    }


def _review_full_period_request(
    database: Path,
    inbox_root: Path,
    archive_root: Path,
    capsys,
) -> dict[str, object]:
    assert main(
        [
            "review",
            "list",
            "--db",
            str(database),
            "--period",
            "2026-Q3",
            "--inbox-root",
            str(inbox_root),
            "--archive-root",
            str(archive_root),
        ]
    ) == 0
    queue = json.loads(capsys.readouterr().out)
    return {
        "period": "2026-Q3",
        "items": [
            {
                "review_id": row["review_id"],
                "expected_row_version": row["expected_row_version"],
            }
            for row in queue
            if row["kind"] == "transaction"
        ],
    }


def _seed_intake_transaction(
    root: Path,
    *,
    key: str,
    tax_code: str = "domestic_expense",
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
            tax_code=tax_code,
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


def _add_review_transaction(
    db,
    *,
    external_key: str,
    period_key: str,
    transaction_date: str,
    tax_code: str,
):
    transaction = db.add_transaction(
        external_key=external_key,
        period_key=period_key,
        transaction_date=transaction_date,
        booking_date=transaction_date,
        entry_type="expense",
        description=external_key,
        amount_minor=10000,
        lifecycle_status="approved",
    )
    db.add_detailed_tax_treatment(
        transaction_id=transaction["transaction_id"],
        treatment_type="invoice_review",
        tax_code=tax_code,
        taxable_base_minor=10000,
        deductible_irpf_minor=10000,
        include_modelo130=True,
    )
    return transaction


from autonomo_taxes.services import posting_operations
