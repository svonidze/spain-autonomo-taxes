from __future__ import annotations

import json
from pathlib import Path

from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import LifecycleError, initialize


def test_local_config_autodiscovers_database_path(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("AUTONOMO_PRIVATE_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    config_dir = tmp_path / ".local"
    config_dir.mkdir()
    database = tmp_path / "private" / "ledger.sqlite"
    (config_dir / "config.yaml").write_text(
        f'ledger_db: "{database.as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert main(["db", "init"]) == 0
    emitted = json.loads(capsys.readouterr().out)

    assert Path(emitted["database"]) == database.resolve()
    assert database.is_file()


def test_explicit_database_path_overrides_local_config(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("AUTONOMO_PRIVATE_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    config_dir = tmp_path / ".local"
    config_dir.mkdir()
    configured = tmp_path / "configured" / "ledger.sqlite"
    explicit = tmp_path / "explicit" / "ledger.sqlite"
    (config_dir / "config.yaml").write_text(
        f'ledger_db: "{configured.as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert main(["db", "init", "--db", str(explicit)]) == 0
    emitted = json.loads(capsys.readouterr().out)

    assert Path(emitted["database"]) == explicit.resolve()
    assert explicit.is_file()
    assert not configured.exists()


def test_inbox_process_is_partial_failure_safe_and_idempotent(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    inbox = tmp_path / "Inbox"
    expense_dir = inbox / "2026-Q3" / "expense_invoice"
    expense_dir.mkdir(parents=True)
    evidence = tmp_path / "Evidence"
    valid = expense_dir / "valid.txt"
    valid.write_text(
        "Supplier Example SL invoice INV-7 dated 2026-07-18.\nTotal: 100,00 EUR",
        encoding="utf-8",
    )
    invalid = expense_dir / "missing-date.txt"
    invalid.write_text("Supplier document without a readable invoice date.", encoding="utf-8")
    with initialize(database):
        pass

    command = [
        "inbox",
        "process",
        "--db",
        str(database),
        "--period",
        "2026-Q3",
        "--inbox-root",
        str(inbox),
        "--archive-root",
        str(evidence),
    ]
    assert main(command) == 2
    first = json.loads(capsys.readouterr().out)
    assert first["counts"] == {
        "imported": 1,
        "already_imported": 0,
        "needs_review": 1,
        "needs_data": 1,
        "failed": 0,
    }
    assert valid.is_file()
    assert invalid.is_file()

    assert main(command) == 2
    second = json.loads(capsys.readouterr().out)
    assert second["counts"]["already_imported"] == 1
    with initialize(database) as db:
        assert db.table_counts()["documents"] == 1
        assert db.table_counts()["transactions"] == 1


def test_inbox_period_is_validation_not_an_override(tmp_path: Path, capsys) -> None:
    database = tmp_path / "ledger.sqlite"
    inbox = tmp_path / "Inbox"
    expense_dir = inbox / "2026-Q3" / "expense_invoice"
    expense_dir.mkdir(parents=True)
    (expense_dir / "q2.txt").write_text(
        "Supplier Example SL invoice INV-Q2 dated 2026-06-30. Total EUR 25.00.",
        encoding="utf-8",
    )
    with initialize(database):
        pass

    assert main(
        [
            "inbox",
            "process",
            "--db",
            str(database),
            "--period",
            "2026-Q3",
            "--inbox-root",
            str(inbox),
            "--archive-root",
            str(tmp_path / "Evidence"),
        ]
    ) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["counts"]["failed"] == 1
    assert "belongs to 2026-Q2" in report["items"][0]["message"]
    with initialize(database) as db:
        assert db.table_counts()["documents"] == 0


def test_inbox_process_registers_final_issued_income_for_review(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    inbox = tmp_path / "Inbox"
    income_dir = inbox / "2026-Q3" / "income_invoice"
    income_dir.mkdir(parents=True)
    (income_dir / "FACT-2026-177919007411.txt").write_text(
        "Invoice No: FACT-2026-177919007411 Date: 2026-07-31 "
        "Example Customer One software development. "
        "Invoice total: 500.00 USD",
        encoding="utf-8",
    )
    with initialize(database):
        pass

    assert main(
        [
            "inbox",
            "process",
            "--db",
            str(database),
            "--period",
            "2026-Q3",
            "--inbox-root",
            str(inbox),
            "--archive-root",
            str(tmp_path / "Evidence"),
        ]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["counts"] == {
        "imported": 1,
        "already_imported": 0,
        "needs_review": 1,
        "needs_data": 0,
        "failed": 0,
    }
    with initialize(database) as db:
        transaction = db.list_transactions(period_key="2026-Q3")[0]
        treatment = db.connection.execute(
            "SELECT tax_code FROM tax_treatments WHERE transaction_id = ?",
            (transaction["transaction_id"],),
        ).fetchone()
        assert transaction["entry_type"] == "income"
        assert transaction["amount_original_minor"] == 50000
        assert transaction["original_currency"] == "USD"
        assert transaction["amount_eur_minor"] is None
        assert treatment["tax_code"] == "unknown"


def test_inbox_process_reports_unsupported_files_instead_of_skipping_them(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    inbox = tmp_path / "Inbox"
    expense_dir = inbox / "2026-Q3" / "expense_invoice"
    expense_dir.mkdir(parents=True)
    unsupported = expense_dir / "receipt.heic"
    unsupported.write_bytes(b"not-a-supported-intake-format")
    (expense_dir / "desktop.ini").write_text("[.ShellClassInfo]", encoding="utf-8")
    with initialize(database):
        pass

    assert main(
        [
            "inbox",
            "process",
            "--db",
            str(database),
            "--period",
            "2026-Q3",
            "--inbox-root",
            str(inbox),
            "--archive-root",
            str(tmp_path / "Evidence"),
        ]
    ) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["counts"]["failed"] == 1
    assert report["items"] == [
        {
            "path": str(unsupported.relative_to(inbox / "2026-Q3")),
            "status": "failed",
            "message": "Unsupported Inbox file type: .heic",
        }
    ]


def test_inbox_process_requires_an_evidence_archive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private-root"))
    database = tmp_path / "ledger.sqlite"
    inbox = tmp_path / "Inbox"
    (inbox / "2026-Q3" / "expense_invoice").mkdir(parents=True)
    with initialize(database):
        pass
    monkeypatch.chdir(tmp_path)

    try:
        main(
            [
                "inbox",
                "process",
                "--db",
                str(database),
                "--period",
                "2026-Q3",
                "--inbox-root",
                str(inbox),
            ]
        )
    except ValueError as exc:
        assert "--archive-root is required" in str(exc)
    else:
        raise AssertionError("inbox process accepted a missing evidence archive")


def test_review_facade_approves_then_posts_through_existing_gates(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        document = db.upsert_document(
            external_key="review-document",
            document_type="expense_invoice",
            document_number="INV-8",
            issued_on="2026-07-18",
            period_key="2026-Q3",
            lifecycle_status="needs_review",
        )
        transaction = db.add_transaction(
            external_key="review-transaction",
            period_key="2026-Q3",
            transaction_date="2026-07-18",
            booking_date="2026-07-18",
            entry_type="expense",
            description="Reviewed expense",
            amount_minor=10000,
            document_id=document["document_id"],
            lifecycle_status="needs_review",
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="invoice_review",
            tax_code="domestic_input",
            taxable_base_minor=10000,
            deductible_irpf_minor=10000,
            include_modelo130=True,
        )

    assert main(["review", "list", "--db", str(database), "--period", "2026-Q3"]) == 0
    queue = json.loads(capsys.readouterr().out)
    assert {row["review_id"] for row in queue} == {
        f"document:{document['document_id']}",
        f"transaction:{transaction['transaction_id']}",
    }
    document_review = next(row for row in queue if row["kind"] == "document")
    transaction_review = next(row for row in queue if row["kind"] == "transaction")
    assert document_review["ready_to_approve"] is True
    assert transaction_review["ready_to_approve"] is False
    assert transaction_review["tax_treatments"][0]["tax_code"] == "domestic_input"

    assert main(
        [
            "review",
            "confirm",
            "--db",
            str(database),
            f"document:{document['document_id']}",
            "--expected-row-version",
            str(document["row_version"]),
        ]
    ) == 0
    approved_document = json.loads(capsys.readouterr().out)
    assert approved_document["lifecycle_status"] == "approved"

    assert main(
        [
            "review",
            "confirm",
            "--db",
            str(database),
            f"transaction:{transaction['transaction_id']}",
            "--expected-row-version",
            str(transaction["row_version"]),
        ]
    ) == 0
    approved_transaction = json.loads(capsys.readouterr().out)
    assert approved_transaction["lifecycle_status"] == "approved"

    assert main(["review", "list", "--db", str(database), "--period", "2026-Q3"]) == 0
    approved_queue = json.loads(capsys.readouterr().out)
    assert [row["review_id"] for row in approved_queue] == [
        f"transaction:{transaction['transaction_id']}"
    ]
    approved_review = approved_queue[0]
    assert approved_review["lifecycle_status"] == "approved"
    assert approved_review["ready_to_approve"] is False
    assert approved_review["ready_to_post"] is True
    assert approved_review["row_version"] == approved_transaction["row_version"]

    assert main(
        [
            "review",
            "post",
            "--db",
            str(database),
            f"transaction:{transaction['transaction_id']}",
            "--expected-row-version",
            str(approved_review["row_version"]),
        ]
    ) == 0
    posted = json.loads(capsys.readouterr().out)
    assert posted["lifecycle_status"] == "posted"


def test_review_list_does_not_mark_received_items_ready_to_approve(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        transaction = db.add_transaction(
            external_key="received-review-transaction",
            period_key="2026-Q3",
            transaction_date="2026-07-18",
            booking_date="2026-07-18",
            entry_type="expense",
            description="Received expense",
            amount_minor=10000,
            lifecycle_status="received",
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="invoice_review",
            tax_code="domestic_input",
            taxable_base_minor=10000,
            deductible_irpf_minor=10000,
            include_modelo130=True,
        )

    assert main(["review", "list", "--db", str(database), "--period", "2026-Q3"]) == 0
    queue = json.loads(capsys.readouterr().out)
    review = next(row for row in queue if row["kind"] == "transaction")
    assert review["lifecycle_status"] == "received"
    assert review["ready_to_approve"] is False
    assert review["ready_to_post"] is False


def test_review_list_defers_future_approved_transactions_until_their_date(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        transaction = db.add_transaction(
            external_key="future-approved-transaction",
            period_key="2099-Q3",
            transaction_date="2099-09-30",
            booking_date="2099-09-30",
            entry_type="expense",
            description="Future amortization",
            amount_minor=10000,
            lifecycle_status="approved",
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="invoice_review",
            tax_code="amortization",
            taxable_base_minor=10000,
            deductible_irpf_minor=10000,
            include_modelo130=True,
        )

    assert main(["review", "list", "--db", str(database), "--period", "2099-Q3"]) == 0
    queue = json.loads(capsys.readouterr().out)
    review = queue[0]
    assert review["lifecycle_status"] == "approved"
    assert review["ready_to_post"] is False
    assert review["posting_deferred_until"] == "2099-09-30"

    try:
        main(
            [
                "review",
                "post",
                "--db",
                str(database),
                review["review_id"],
                "--expected-row-version",
                str(review["row_version"]),
            ]
        )
    except LifecycleError as exc:
        assert "cannot be posted before 2099-09-30" in str(exc)
    else:
        raise AssertionError("future approved transaction was posted early")
