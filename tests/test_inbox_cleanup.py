from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from autonomo_taxes import operational_cli
from autonomo_taxes.cli import main
from autonomo_taxes.intake import cleanup_expense_inbox_source
from autonomo_taxes.ledger_db import LifecycleError, initialize


def test_review_post_cleans_expense_source_using_configured_roots(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(tmp_path)
    config_dir = tmp_path / ".local"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "\n".join(
            [
                f'ledger_db: "{seeded["database"].as_posix()}"',
                f'inbox_root: "{seeded["inbox_root"].as_posix()}"',
                f'archive_root: "{seeded["archive_root"].as_posix()}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert main(_post_command(seeded, include_db=False)) == 0
    emitted = json.loads(capsys.readouterr().out)

    assert emitted["lifecycle_status"] == "posted"
    assert emitted["inbox_cleanup"]["status"] == "deleted"
    assert emitted["inbox_cleanup"]["file_name"] == seeded["source"].name
    assert "source_path" not in emitted["inbox_cleanup"]
    assert not seeded["source"].exists()
    assert seeded["archive"].read_bytes() == seeded["content"]
    with initialize(seeded["database"]) as db:
        transaction = db.connection.execute(
            "SELECT lifecycle_status FROM transactions WHERE transaction_id = ?",
            (seeded["transaction_id"],),
        ).fetchone()
        document = db.connection.execute(
            "SELECT lifecycle_status FROM documents WHERE document_id = ?",
            (seeded["document_id"],),
        ).fetchone()
    assert transaction["lifecycle_status"] == "posted"
    assert document["lifecycle_status"] == "approved"


def test_posting_gate_failure_leaves_expense_source_untouched(
    tmp_path: Path,
) -> None:
    seeded = _seed_intake_transaction(tmp_path, tax_code="unknown")

    with pytest.raises(LifecycleError, match="Unknown tax treatment"):
        main(_post_command(seeded))

    assert seeded["source"].read_bytes() == seeded["content"]
    assert seeded["archive"].read_bytes() == seeded["content"]
    with initialize(seeded["database"]) as db:
        status = db.connection.execute(
            "SELECT lifecycle_status FROM transactions WHERE transaction_id = ?",
            (seeded["transaction_id"],),
        ).fetchone()["lifecycle_status"]
    assert status == "approved"


def test_cleanup_preflight_failure_does_not_post_or_delete_source(
    tmp_path: Path,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(tmp_path)
    seeded["archive"].unlink()

    assert main(_post_command(seeded)) == 2
    emitted = json.loads(capsys.readouterr().out)

    assert emitted["lifecycle_status"] == "approved"
    assert emitted["inbox_cleanup"]["status"] == "failed"
    assert "Evidence archive" in emitted["inbox_cleanup"]["message"]
    assert seeded["source"].read_bytes() == seeded["content"]
    with initialize(seeded["database"]) as db:
        status = db.connection.execute(
            "SELECT lifecycle_status FROM transactions WHERE transaction_id = ?",
            (seeded["transaction_id"],),
        ).fetchone()["lifecycle_status"]
    assert status == "approved"


def test_review_post_accepts_an_already_absent_inbox_source(
    tmp_path: Path,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(tmp_path)
    seeded["source"].unlink()

    assert main(_post_command(seeded)) == 0
    emitted = json.loads(capsys.readouterr().out)

    assert emitted["lifecycle_status"] == "posted"
    assert emitted["inbox_cleanup"]["status"] == "already_absent"
    assert seeded["archive"].read_bytes() == seeded["content"]


def test_unlink_failure_after_commit_returns_structured_error(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(tmp_path)
    original_unlink = Path.unlink
    source = seeded["source"].resolve()

    def fail_source_unlink(path: Path, *args, **kwargs) -> None:
        if path.resolve() == source:
            raise PermissionError("simulated Drive lock")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_source_unlink)

    assert main(_post_command(seeded)) == 2
    emitted = json.loads(capsys.readouterr().out)

    assert emitted["lifecycle_status"] == "posted"
    assert emitted["inbox_cleanup"]["status"] == "failed"
    assert "PermissionError" in emitted["inbox_cleanup"]["message"]
    assert str(tmp_path) not in emitted["inbox_cleanup"]["message"]
    assert seeded["source"].is_file()
    assert seeded["archive"].is_file()
    with initialize(seeded["database"]) as db:
        status = db.connection.execute(
            "SELECT lifecycle_status FROM transactions WHERE transaction_id = ?",
            (seeded["transaction_id"],),
        ).fetchone()["lifecycle_status"]
    assert status == "posted"


def test_source_change_after_preflight_is_detected_before_unlink(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(tmp_path)
    real_cleanup = cleanup_expense_inbox_source

    def change_then_cleanup(candidate):
        candidate.source_path.write_bytes(b"changed after preflight")
        return real_cleanup(candidate)

    monkeypatch.setattr(
        operational_cli,
        "cleanup_expense_inbox_source",
        change_then_cleanup,
    )

    assert main(_post_command(seeded)) == 2
    emitted = json.loads(capsys.readouterr().out)

    assert emitted["lifecycle_status"] == "posted"
    assert emitted["inbox_cleanup"]["status"] == "failed"
    assert "Inbox source SHA-256" in emitted["inbox_cleanup"]["message"]
    assert seeded["source"].read_bytes() == b"changed after preflight"
    assert seeded["archive"].read_bytes() == seeded["content"]


def test_cleanup_posted_is_idempotent_and_can_show_private_paths(
    tmp_path: Path,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(tmp_path, lifecycle_status="posted")
    command = [
        "inbox",
        "cleanup-posted",
        "--db",
        str(seeded["database"]),
        f"transaction:{seeded['transaction_id']}",
        "--inbox-root",
        str(seeded["inbox_root"]),
        "--archive-root",
        str(seeded["archive_root"]),
        "--show-paths",
    ]

    assert main(command) == 0
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["lifecycle_status"] == "posted"
    assert deleted["inbox_cleanup"]["status"] == "deleted"
    assert Path(deleted["inbox_cleanup"]["source_path"]) == seeded["source"]
    assert Path(deleted["inbox_cleanup"]["archive_path"]) == seeded["archive"]

    assert main(command) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["inbox_cleanup"]["status"] == "already_absent"
    assert seeded["archive"].is_file()


def test_review_post_does_not_clean_income_intake(
    tmp_path: Path,
    capsys,
) -> None:
    seeded = _seed_intake_transaction(
        tmp_path,
        intake_tab="income_intake",
        document_type="income_invoice",
        entry_type="income",
    )

    assert main(_post_command(seeded, include_roots=False)) == 0
    emitted = json.loads(capsys.readouterr().out)

    assert emitted["lifecycle_status"] == "posted"
    assert emitted["inbox_cleanup"]["status"] == "not_applicable"
    assert seeded["source"].is_file()
    assert seeded["archive"].is_file()


def _post_command(
    seeded: dict[str, object],
    *,
    include_db: bool = True,
    include_roots: bool = True,
) -> list[str]:
    command = ["review", "post"]
    if include_db:
        command.extend(["--db", str(seeded["database"])])
    command.extend(
        [
            f"transaction:{seeded['transaction_id']}",
            "--expected-row-version",
            str(seeded["row_version"]),
        ]
    )
    if include_roots:
        command.extend(
            [
                "--inbox-root",
                str(seeded["inbox_root"]),
                "--archive-root",
                str(seeded["archive_root"]),
            ]
        )
    return command


def _seed_intake_transaction(
    root: Path,
    *,
    intake_tab: str = "expense_intake",
    document_type: str = "expense_invoice",
    entry_type: str = "expense",
    lifecycle_status: str = "approved",
    tax_code: str = "domestic_expense",
) -> dict[str, object]:
    database = root / "ledger.sqlite"
    period_key = "2026-Q3"
    inbox_root = root / "Inbox"
    archive_root = root / "Evidence"
    source = inbox_root / period_key / document_type / "invoice.pdf"
    archive = archive_root / period_key / document_type / "digest-invoice.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    archive.parent.mkdir(parents=True, exist_ok=True)
    content = b"immutable invoice"
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
            document_type=document_type,
            document_number="INV-2026-001",
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
            entry_type=entry_type,
            description="Reviewed invoice",
            amount_minor=12100,
            direction="credit" if entry_type == "income" else "debit",
            lifecycle_status=lifecycle_status,
            document_id=document["document_id"],
            source_hash=digest,
        )
        treatment = db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="invoice_review",
            tax_code=tax_code,
            taxable_base_minor=10000,
            vat_minor=2100,
            deductible_irpf_minor=12100 if entry_type == "expense" else 0,
            deductible_vat_minor=0,
            include_modelo130=True,
        )
        db.add_intake_receipt(
            intake_tab=intake_tab,
            source_row_number=2,
            row_fingerprint=f"row-{digest}-{intake_tab}",
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
        "content": content,
        "document_id": document["document_id"],
        "transaction_id": transaction["transaction_id"],
        "row_version": transaction["row_version"],
    }
