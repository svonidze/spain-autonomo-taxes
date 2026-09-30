from __future__ import annotations

import csv
import json
from pathlib import Path

from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.sheet_intake import (
    EXPENSE_INTAKE_FIELDS,
    INCOME_INTAKE_FIELDS,
    IntakeSheetError,
    load_intake_csv,
    parse_intake_row,
    resolve_evidence_path,
)


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def _expense_row(**changes: str) -> dict[str, str]:
    row = {
        "system_id": "",
        "file_name": "supplier-invoice.txt",
        "supplier": "Example Supplier SL",
        "invoice_number": "EXP-2026-001",
        "invoice_date": "2026-07-20",
        "gross_amount": "121,00",
        "currency": "eur",
        "business_purpose": "Software used for client work",
        "business_use_percent": "",
        "expected_use_over_one_year": "unknown",
        "notes": "",
    }
    row.update(changes)
    return row


def _income_row(**changes: str) -> dict[str, str]:
    row = {
        "system_id": "",
        "file_name": "FACT-2026-177919007411.txt",
        "customer": "Example Customer Inc",
        "invoice_number": "FACT-2026-177919007411",
        "issue_date": "2026-07-31",
        "service_period_from": "2026-07-01",
        "service_period_to": "2026-07-31",
        "service_description": "Software development services",
        "gross_amount": "6595.80",
        "currency": "USD",
        "payment_due_date": "2026-08-30",
        "correction_of": "",
        "notes": "Final invoice issued to the customer",
    }
    row.update(changes)
    return row


def test_intake_row_normalizes_operator_values_and_fingerprint() -> None:
    row = parse_intake_row("expense_intake", 2, _expense_row())
    same = parse_intake_row(
        "expense_intake",
        99,
        _expense_row(gross_amount="EUR 121.00", currency="EUR"),
    )

    assert row.period_key == "2026-Q3"
    assert row.values["gross_amount"] == "121.00"
    assert row.values["currency"] == "EUR"
    assert row.values["business_use_percent"] == "100"
    assert row.row_fingerprint == same.row_fingerprint


def test_income_intake_rejects_bad_service_period_and_formula() -> None:
    try:
        parse_intake_row(
            "income_intake",
            2,
            _income_row(service_period_to="2026-06-30"),
        )
    except IntakeSheetError as exc:
        assert "service_period_to precedes" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("invalid service period was accepted")

    try:
        parse_intake_row(
            "income_intake",
            2,
            _income_row(notes="=HYPERLINK(\"https://example.invalid\")"),
        )
    except IntakeSheetError as exc:
        assert "formula prefix" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("formula injection was accepted")


def test_intake_csv_requires_the_exact_tab_contract(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    _write_csv(path, tuple(reversed(EXPENSE_INTAKE_FIELDS)), [_expense_row()])

    try:
        load_intake_csv(path, "expense_intake")
    except IntakeSheetError as exc:
        assert "headers must be exactly" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("reordered intake headers were accepted")


def test_evidence_resolution_is_confined_to_the_quarter_inbox(tmp_path: Path) -> None:
    inbox = tmp_path / "Inbox"
    folder = inbox / "2026-Q3" / "expense_invoice"
    folder.mkdir(parents=True)
    evidence = folder / "supplier-invoice.txt"
    evidence.write_text("fixture", encoding="utf-8")
    row = parse_intake_row("expense_intake", 2, _expense_row())

    assert resolve_evidence_path(row, inbox) == evidence.resolve()
    escaped = parse_intake_row(
        "expense_intake",
        2,
        _expense_row(file_name="..\\income_invoice\\supplier-invoice.txt"),
    )
    try:
        resolve_evidence_path(escaped, inbox)
    except IntakeSheetError as exc:
        assert "must resolve inside" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("path traversal was accepted")


def test_expense_sheet_intake_writes_receipt_and_recovers_writeback(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    inbox = tmp_path / "Inbox"
    archive = tmp_path / "Evidence"
    expense_folder = inbox / "2026-Q3" / "expense_invoice"
    expense_folder.mkdir(parents=True)
    invoice = expense_folder / "supplier-invoice.txt"
    invoice.write_text(
        "Invoice EXP-2026-001 Date: 2026-07-20 Example Supplier SL. "
        "Software services. Invoice total: 121.00 EUR",
        encoding="utf-8",
    )
    source = tmp_path / "expense_intake.csv"
    writeback = tmp_path / "expense_intake-writeback.csv"
    _write_csv(source, EXPENSE_INTAKE_FIELDS, [_expense_row()])
    with initialize(database):
        pass

    command = [
        "intake",
        "apply",
        "--db",
        str(database),
        "--tab",
        "expense_intake",
        "--remote-csv",
        str(source),
        "--out-csv",
        str(writeback),
        "--inbox-root",
        str(inbox),
        "--archive-root",
        str(archive),
    ]
    assert main(command) == 0
    accepted = json.loads(capsys.readouterr().out)
    receipt_id = accepted["items"][0]["system_id"]
    transaction_id = accepted["items"][0]["transaction_id"]
    assert accepted["counts"]["accepted"] == 1
    assert accepted["meaning_of_system_id"] == "accepted_for_review_not_posted"

    with writeback.open(newline="", encoding="utf-8-sig") as handle:
        written = list(csv.DictReader(handle))
    assert written[0]["system_id"] == receipt_id

    with initialize(database) as db:
        assert db.table_counts()["intake_receipts"] == 1
        receipt = db.get_intake_receipt(receipt_id)
        assert receipt is not None
        assert receipt["transaction_id"] == transaction_id
        transaction_version = db.connection.execute(
            "SELECT row_version, lifecycle_status FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        assert transaction_version["lifecycle_status"] == "needs_review"
        transaction_version = transaction_version["row_version"]

    recovered_path = tmp_path / "recovered.csv"
    recovered_command = command.copy()
    recovered_command[recovered_command.index(str(writeback))] = str(recovered_path)
    assert main(recovered_command) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["counts"]["recovered"] == 1
    assert recovered["items"][0]["system_id"] == receipt_id

    with initialize(database) as db:
        assert db.table_counts()["intake_receipts"] == 1
        assert db.connection.execute(
            "SELECT row_version FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()["row_version"] == transaction_version

    packet_path = tmp_path / "packet.json"
    assert main(
        [
            "review",
            "prepare",
            "--db",
            str(database),
            f"transaction:{transaction_id}",
            "--out",
            str(packet_path),
        ]
    ) == 0
    capsys.readouterr()
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["state"]["operator_intake"]["system_id"] == receipt_id
    assert packet["decision"]["business_purpose"] == "Software used for client work"


def test_accepted_intake_row_cannot_be_edited_or_reused_for_same_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    inbox = tmp_path / "Inbox"
    archive = tmp_path / "Evidence"
    folder = inbox / "2026-Q3" / "income_invoice"
    folder.mkdir(parents=True)
    (folder / "FACT-2026-177919007411.txt").write_text(
        "Invoice No: FACT-2026-177919007411 Date: 2026-07-31 Example Customer Inc. "
        "Software development services. Invoice total: 6595.80 USD",
        encoding="utf-8",
    )
    source = tmp_path / "income.csv"
    writeback = tmp_path / "writeback.csv"
    _write_csv(source, INCOME_INTAKE_FIELDS, [_income_row()])
    with initialize(database):
        pass
    base = [
        "intake",
        "apply",
        "--db",
        str(database),
        "--tab",
        "income_intake",
        "--remote-csv",
        str(source),
        "--out-csv",
        str(writeback),
        "--inbox-root",
        str(inbox),
        "--archive-root",
        str(archive),
    ]
    assert main(base) == 0
    accepted = json.loads(capsys.readouterr().out)
    receipt_id = accepted["items"][0]["system_id"]

    edited = tmp_path / "edited.csv"
    _write_csv(
        edited,
        INCOME_INTAKE_FIELDS,
        [_income_row(system_id=receipt_id, notes="Changed after acceptance")],
    )
    edited_command = base.copy()
    edited_command[edited_command.index(str(source))] = str(edited)
    assert main(edited_command) == 2
    conflict = json.loads(capsys.readouterr().out)
    assert "accepted row was edited" in conflict["items"][0]["message"]

    cleared = tmp_path / "cleared.csv"
    _write_csv(
        cleared,
        INCOME_INTAKE_FIELDS,
        [_income_row(notes="Changed while system_id was cleared")],
    )
    cleared_command = base.copy()
    cleared_command[cleared_command.index(str(source))] = str(cleared)
    assert main(cleared_command) == 2
    duplicate = json.loads(capsys.readouterr().out)
    assert "evidence file was already accepted" in duplicate["items"][0]["message"]

    with initialize(database) as db:
        assert db.table_counts()["intake_receipts"] == 1
        assert db.table_counts()["documents"] == 1
        assert db.table_counts()["transactions"] == 1


def test_income_correction_blocker_is_restored_when_receipt_already_exists(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    inbox = tmp_path / "Inbox"
    archive = tmp_path / "Evidence"
    folder = inbox / "2026-Q3" / "income_invoice"
    folder.mkdir(parents=True)
    (folder / "FACT-2026-177919007411.txt").write_text(
        "Invoice No: FACT-2026-177919007411 Date: 2026-07-31 Example Customer Inc. "
        "Software development services. Invoice total: 6595.80 USD",
        encoding="utf-8",
    )
    source = tmp_path / "income.csv"
    writeback = tmp_path / "writeback.csv"
    _write_csv(
        source,
        INCOME_INTAKE_FIELDS,
        [_income_row(correction_of="SYNTH-DOCUMENT-010")],
    )
    with initialize(database):
        pass
    command = [
        "intake",
        "apply",
        "--db",
        str(database),
        "--tab",
        "income_intake",
        "--remote-csv",
        str(source),
        "--out-csv",
        str(writeback),
        "--inbox-root",
        str(inbox),
        "--archive-root",
        str(archive),
    ]

    assert main(command) == 0
    accepted = json.loads(capsys.readouterr().out)
    transaction_id = accepted["items"][0]["transaction_id"]
    with initialize(database) as db:
        issue = db.connection.execute(
            """
            SELECT validation_issue_id
            FROM validation_issues
            WHERE issue_code = 'invoice_correction_review' AND subject_id = ?
            """,
            (transaction_id,),
        ).fetchone()
        assert issue is not None
        with db.connection:
            db.connection.execute(
                "DELETE FROM validation_issues WHERE validation_issue_id = ?",
                (issue["validation_issue_id"],),
            )

    assert main(command) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["counts"]["recovered"] == 1
    with initialize(database) as db:
        assert db.connection.execute(
            """
            SELECT COUNT(*)
            FROM validation_issues
            WHERE issue_code = 'invoice_correction_review' AND subject_id = ?
            """,
            (transaction_id,),
        ).fetchone()[0] == 1


def test_intake_mixed_batch_writes_ids_only_for_successful_rows(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    inbox = tmp_path / "Inbox"
    archive = tmp_path / "Evidence"
    folder = inbox / "2026-Q3" / "expense_invoice"
    folder.mkdir(parents=True)
    (folder / "supplier-invoice.txt").write_text(
        "Invoice EXP-2026-001 Date: 2026-07-20 Example Supplier SL. "
        "Software services. Invoice total: 121.00 EUR",
        encoding="utf-8",
    )
    source = tmp_path / "expenses.csv"
    writeback = tmp_path / "writeback.csv"
    _write_csv(
        source,
        EXPENSE_INTAKE_FIELDS,
        [_expense_row(), _expense_row(file_name="invalid.txt", supplier="")],
    )
    with initialize(database):
        pass

    assert main(
        [
            "intake",
            "apply",
            "--db",
            str(database),
            "--tab",
            "expense_intake",
            "--remote-csv",
            str(source),
            "--out-csv",
            str(writeback),
            "--inbox-root",
            str(inbox),
            "--archive-root",
            str(archive),
        ]
    ) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["counts"]["accepted"] == 1
    assert result["counts"]["failed"] == 1
    with writeback.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["system_id"]
    assert rows[1]["system_id"] == ""


def test_intake_rejects_existing_transaction_outside_review_lifecycle(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    inbox = tmp_path / "Inbox"
    archive = tmp_path / "Evidence"
    folder = inbox / "2026-Q3" / "expense_invoice"
    folder.mkdir(parents=True)
    (folder / "supplier-invoice.txt").write_text(
        "Invoice EXP-2026-001 Date: 2026-07-20 Example Supplier SL. "
        "Software services. Invoice total: 121.00 EUR",
        encoding="utf-8",
    )
    source = tmp_path / "expenses.csv"
    writeback = tmp_path / "writeback.csv"
    _write_csv(source, EXPENSE_INTAKE_FIELDS, [_expense_row()])
    with initialize(database):
        pass
    command = [
        "intake",
        "apply",
        "--db",
        str(database),
        "--tab",
        "expense_intake",
        "--remote-csv",
        str(source),
        "--out-csv",
        str(writeback),
        "--inbox-root",
        str(inbox),
        "--archive-root",
        str(archive),
    ]
    assert main(command) == 0
    accepted = json.loads(capsys.readouterr().out)
    transaction_id = accepted["items"][0]["transaction_id"]
    with initialize(database) as db:
        with db.connection:
            db.connection.execute("DELETE FROM intake_receipts")
            db.connection.execute(
                "UPDATE transactions SET lifecycle_status = 'posted' WHERE transaction_id = ?",
                (transaction_id,),
            )

    assert main(command) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert "not needs_review" in rejected["items"][0]["message"]
    with initialize(database) as db:
        assert db.table_counts()["intake_receipts"] == 0
