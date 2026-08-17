from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from autonomo_taxes.cli import main
from autonomo_taxes.intake import IntakeResult
from autonomo_taxes.ledger_db import LedgerDB, LedgerDbError, LifecycleError, initialize
from autonomo_taxes.parsers import LedgerEntry
from autonomo_taxes.tax_engine import CalculationBlocked


def test_db_init_and_status(tmp_path: Path, capsys) -> None:
    database = tmp_path / "ledger.sqlite"

    assert main(["db", "init", "--db", str(database)]) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["schema_version"] == 18

    assert main(["db", "status", "--db", str(database)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["database"] == str(database.resolve())
    assert status["counts"]["transactions"] == 0


def test_storage_backend_upsert_uses_non_secret_configuration(tmp_path: Path, capsys) -> None:
    database = tmp_path / "ledger.sqlite"
    assert main(["db", "init", "--db", str(database)]) == 0
    capsys.readouterr()
    payload = tmp_path / "backend.json"
    payload.write_text(
        json.dumps(
            {
                "backend_key": "local_test",
                "display_name": "Local test",
                "driver_key": "filesystem",
                "provider_key": "local",
                "access_mode": "read_write",
                "config": {"schema_version": 1, "root": str(tmp_path / "root")},
                "credential_ref": None,
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "storage",
                "backend-upsert",
                "--db",
                str(database),
                "--input",
                str(payload),
            ]
        )
        == 0
    )
    row = json.loads(capsys.readouterr().out)
    assert row["backend_key"] == "local_test"


def test_backup_restore_creates_missing_target_parent(tmp_path: Path, capsys) -> None:
    source = tmp_path / "source.sqlite"
    backup = tmp_path / "backup.sqlite"
    restored = tmp_path / "clean-machine" / "nested" / "autonomo.sqlite"

    with initialize(source) as database:
        database.upsert_counterparty(
            external_key="restore-clean-machine",
            tax_id="ESC12345678",
            display_name="Clean Machine Vendor",
        )

    assert main(["backup", "create", "--db", str(source), "--out", str(backup)]) == 0
    capsys.readouterr()
    assert not restored.parent.exists()

    assert (
        main(["backup", "restore", "--db", str(restored), "--from", str(backup)])
        == 0
    )
    result = json.loads(capsys.readouterr().out)

    assert restored.is_file()
    assert Path(result["safety_backup"]).is_file()
    with LedgerDB.open(restored, read_only=True) as database:
        names = database.connection.execute(
            "SELECT display_name FROM counterparties ORDER BY display_name"
        ).fetchall()
    assert [row["display_name"] for row in names] == ["Clean Machine Vendor"]


def test_calendar_cli_imports_lists_and_exports_source_backed_deadlines(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    export_dir = tmp_path / "sheet"
    calendar_path = Path(__file__).parents[1] / "config" / "tax-calendar-2026.json"
    with initialize(database):
        pass

    command = [
        "calendar",
        "import",
        "--db",
        str(database),
        "--input",
        str(calendar_path),
    ]
    assert main(command) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["calendar_year"] == 2026
    assert imported["entry_count"] == 33
    assert len(imported["source_file_hash"]) == 64

    assert main(command) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert {row["row_version"] for row in repeated["entries"]} == {1}

    assert main(
        [
            "calendar",
            "list",
            "--db",
            str(database),
            "--period",
            "2026-Q3",
        ]
    ) == 0
    q3 = json.loads(capsys.readouterr().out)
    assert {row["form_code"] for row in q3} == {"111", "115", "130", "216", "303", "349"}
    assert {row["statutory_due_on"] for row in q3} == {"2026-10-20"}

    assert main(["sheet", "export", "--db", str(database), "--out-dir", str(export_dir)]) == 0
    capsys.readouterr()
    with (export_dir / "tax_calendar.csv").open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
        header = list(rows[0])
    assert len(rows) == 33
    assert header == [
        "uuid",
        "row_version",
        "status",
        "calendar_year",
        "period_key",
        "form_code",
        "filing_opens_on",
        "internal_due_on",
        "direct_debit_cutoff_on",
        "statutory_due_on",
        "source_url",
        "source_checked_on",
        "notes",
    ]


def test_profile_and_aeat_book_preview_cli_are_fail_closed_and_explicit(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    profile_input = tmp_path / "profile.json"
    profile_input.write_text(
        json.dumps(
            {
                "tax_id": "X0000000A",
                "full_name": "Example Taxpayer",
                "residency_country": "ES",
                "source_hash": "modelo-036-hash",
            }
        ),
        encoding="utf-8",
    )
    assert main(["db", "init", "--db", str(database)]) == 0
    capsys.readouterr()
    assert main(["profile", "set", "--db", str(database), "--input", str(profile_input)]) == 0
    profile = json.loads(capsys.readouterr().out)

    activity_input = tmp_path / "activity.json"
    activity_input.write_text(
        json.dumps(
            {
                "taxpayer_profile_id": profile["taxpayer_profile_id"],
                "activity_key": "software-development",
                "aeat_activity_code": "A",
                "aeat_activity_type": "05",
                "iae_section": "2",
                "iae_group_epigraph": "763",
                "description": "Programmers and computer analysts",
                "starts_on": "2023-05-31",
                "irpf_method": "estimacion_directa_simplificada",
                "iva_regime": "general",
                "source_reference": "Modelo 036",
                "source_hash": "activity-source-hash",
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "profile",
                "activity-upsert",
                "--db",
                str(database),
                "--input",
                str(activity_input),
            ]
        )
        == 0
    )
    activity = json.loads(capsys.readouterr().out)
    assert activity["iae_group_epigraph"] == "763"

    assert main(["profile", "show", "--db", str(database)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert len(shown["profiles"]) == 1
    assert len(shown["activities"]) == 1

    output = tmp_path / "aeat-preview.json"
    assert (
        main(
            [
                "books",
                "aeat-preview",
                "--db",
                str(database),
                "--period",
                "2026-Q3",
                "--out",
                str(output),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    projection = json.loads(output.read_text(encoding="utf-8"))
    assert result["data_projection_ready"] is True
    assert result["xlsx_generation_supported"] is True
    assert projection["contract"]["filename_rule_status"] == (
        "unverified_public_sources_conflict"
    )
    assert projection["provisional_filename"].endswith(".xlsx")


def test_profile_cli_hash_is_stable_across_versioned_no_op_retry(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    profile_input = tmp_path / "profile.json"
    profile_input.write_text(
        json.dumps(
            {
                "tax_id": "X0000000A",
                "full_name": "Original Taxpayer",
                "residency_country": "ES",
            }
        ),
        encoding="utf-8",
    )
    assert main(["db", "init", "--db", str(database)]) == 0
    capsys.readouterr()
    assert main(["profile", "set", "--db", str(database), "--input", str(profile_input)]) == 0
    created = json.loads(capsys.readouterr().out)

    updated_payload = {
        "tax_id": "X0000000A",
        "full_name": "Reviewed Taxpayer",
        "residency_country": "ES",
        "expected_row_version": created["row_version"],
    }
    profile_input.write_text(json.dumps(updated_payload), encoding="utf-8")
    assert main(["profile", "set", "--db", str(database), "--input", str(profile_input)]) == 0
    updated = json.loads(capsys.readouterr().out)
    assert updated["row_version"] == 2

    updated_payload["expected_row_version"] = updated["row_version"]
    profile_input.write_text(json.dumps(updated_payload), encoding="utf-8")
    assert main(["profile", "set", "--db", str(database), "--input", str(profile_input)]) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["row_version"] == 2
    assert repeated["source_hash"] == updated["source_hash"]


def test_counterparty_identity_cli_preserves_aeat_type_and_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        supplier = db.upsert_counterparty(
            external_key="supplier-ge",
            display_name="Foreign Supplier",
            country_code="GE",
        )
    identity_input = tmp_path / "identity.json"
    identity_input.write_text(
        json.dumps(
            {
                "counterparty_id": supplier["counterparty_id"],
                "identity_kind": "official_id",
                "country_code": "GE",
                "identifier": "345795641",
                "source_reference": "Public registry extract",
                "source_hash": "registry-extract-hash",
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "counterparties",
                "identity-upsert",
                "--db",
                str(database),
                "--input",
                str(identity_input),
            ]
        )
        == 0
    )
    identity = json.loads(capsys.readouterr().out)
    assert identity["aeat_id_type"] == "04"
    assert identity["source_reference"] == "Public registry extract"

    assert (
        main(
            [
                "counterparties",
                "identity-list",
                "--db",
                str(database),
                "--counterparty-id",
                supplier["counterparty_id"],
            ]
        )
        == 0
    )
    listed = json.loads(capsys.readouterr().out)
    assert [row["identifier"] for row in listed] == ["345795641"]


def test_aeat_preview_writes_structured_failure_when_profile_is_missing(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    output = tmp_path / "aeat-preview.json"
    assert main(["db", "init", "--db", str(database)]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "books",
                "aeat-preview",
                "--db",
                str(database),
                "--period",
                "2026-Q3",
                "--out",
                str(output),
            ]
        )
        == 2
    )
    result = json.loads(capsys.readouterr().out)
    projection = json.loads(output.read_text(encoding="utf-8"))
    assert result["data_projection_ready"] is False
    assert result["counts"]["blockers"] == 1
    assert projection["blockers"][0]["code"] == "projection_prerequisite_missing"
    assert "taxpayer profile" in projection["blockers"][0]["message"].lower()


def test_invoice_cli_prepares_reviewed_draft_without_booking_income(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        customer = db.upsert_counterparty(
            external_key="customer-us",
            tax_id="12-3456789",
            display_name="Foreign Customer",
            country_code="US",
        )

    template_path = tmp_path / "template.json"
    template_path.write_text(
        json.dumps(
            {
                "template_key": "foreign-software",
                "template_name": "Foreign software services",
                "counterparty_id": customer["counterparty_id"],
                "currency": "USD",
                "default_lines": [
                    {
                        "description": "Software development",
                        "quantity": "1",
                        "unit_amount_minor": 659580,
                        "tax_code": "outside_scope",
                        "channel_tax_code": "N2",
                        "tax_rate_basis_points": 0,
                    }
                ],
                "channel_hint": "aeat_verifactu",
                "delivery_email": "billing@example.com",
                "recipient_address_line1": "100 Example Street",
                "recipient_postal_code": "10001",
                "recipient_city": "New York",
                "recipient_region": "NY",
            }
        ),
        encoding="utf-8",
    )
    assert main(["invoice", "template-upsert", "--db", str(database), "--input", str(template_path)]) == 0
    template = json.loads(capsys.readouterr().out)

    draft_path = tmp_path / "draft.json"
    draft_path.write_text(
        json.dumps(
            {
                "draft_key": "foreign-software:2026-07",
                "template_key": template["template_key"],
                "period_key": "2026-Q3",
                "service_on": "2026-06-30",
                "planned_issue_on": "2026-07-01",
            }
        ),
        encoding="utf-8",
    )
    assert main(["invoice", "draft", "--db", str(database), "--input", str(draft_path)]) == 0
    draft = json.loads(capsys.readouterr().out)
    assert draft["issuance_guard"] == "not_issued_do_not_book_as_income"

    assert (
        main(
            [
                "invoice",
                "review",
                "--db",
                str(database),
                draft["outgoing_invoice_draft_id"],
                "--expected-row-version",
                str(draft["row_version"]),
            ]
        )
        == 0
    )
    reviewed = json.loads(capsys.readouterr().out)
    assert reviewed["lifecycle_status"] == "reviewed"

    with initialize(database) as db:
        assert db.list_transactions(period_key="2026-Q3") == []


def test_period_dashboard_is_read_only_and_never_claims_filing_readiness(
    tmp_path: Path, capsys
) -> None:
    database = tmp_path / "ledger.sqlite"
    output = tmp_path / "dashboard"
    with initialize(database) as db:
        posted = db.add_transaction(
            external_key="dashboard-posted",
            period_key="2026-Q3",
            transaction_date="2026-07-01",
            booking_date="2026-07-01",
            entry_type="income",
            description="Posted income",
            amount_minor=100000,
            currency="EUR",
            lifecycle_status="posted",
        )
        approved = db.add_transaction(
            external_key="dashboard-approved",
            period_key="2026-Q3",
            transaction_date="2026-07-02",
            booking_date="2026-07-02",
            entry_type="expense",
            description="Approved expense",
            amount_minor=12100,
            currency="EUR",
            lifecycle_status="approved",
        )
        db.add_detailed_tax_treatment(
            transaction_id=posted["transaction_id"],
            treatment_type="income",
            tax_code="outside_scope",
            taxable_base_minor=100000,
            include_modelo130=True,
            include_modelo303=True,
        )
        db.add_detailed_tax_treatment(
            transaction_id=approved["transaction_id"],
            treatment_type="expense",
            tax_code="domestic_input",
            taxable_base_minor=10000,
            vat_minor=2100,
            deductible_irpf_minor=10000,
            deductible_vat_minor=2100,
            include_modelo130=True,
            include_modelo303=True,
        )
        blocking_issue = db.add_validation_issue(
            period_key="2026-Q3",
            issue_code="dashboard_fixture",
            severity="error",
            message="Dashboard must preserve issue identity",
            blocking=True,
        )
        db.add_obligation(
            period_key="2026-Q3",
            obligation_code="111",
            filing_status="waived",
            determination="not_due",
            blocking=False,
            explanation="No reportable professional withholding in reviewed rows.",
        )
        db.add_obligation(
            period_key="2026-Q3",
            obligation_code="130",
            due_on="2026-10-20",
            filing_status="due",
            determination="due",
            blocking=True,
            explanation="Quarterly payment is due.",
        )
        db.add_obligation(
            period_key="2026-Q3",
            obligation_code="349",
            due_on="2026-10-20",
            filing_status="unknown",
            determination="unknown",
            blocking=True,
            explanation="Quarter is incomplete.",
        )
        before = db.table_counts()

    assert (
        main(
            [
                "period",
                "dashboard",
                "--db",
                str(database),
                "2026-Q3",
                "--as-of",
                "2026-07-16",
                "--out-dir",
                str(output),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    dashboard = json.loads((output / "dashboard.json").read_text(encoding="utf-8"))

    assert result["filing_assessment_performed"] is False
    assert result["filing_ready"] is False
    assert result["submission_ready"] is False
    assert result["expected_item_count"] == 1
    assert dashboard["posted_actual"]["income_eur"] == "1000.00"
    assert dashboard["approved_forecast_delta"]["expense_irpf_deductible_eur"] == "100.00"
    assert {row["obligation_code"] for row in dashboard["obligations"]} == {"111", "130", "349"}
    assert next(
        row for row in dashboard["obligations"] if row["obligation_code"] == "130"
    )["due_on"] == "2026-10-20"
    assert any(
        row["kind"] == "validation_issue"
        and row["reference"] == blocking_issue["validation_issue_id"]
        for row in dashboard["blocking_items"]
    )
    assert any(
        row["kind"] == "obligation_filing_pending" and row["reference"] == "130"
        for row in dashboard["expected_items"]
    )
    with initialize(database) as db:
        assert db.table_counts() == before


def test_intake_never_posts_document(tmp_path: Path, capsys) -> None:
    database = tmp_path / "ledger.sqlite"
    invoice = tmp_path / "invoice.txt"
    invoice.write_text(
        "Invoice INV-1 dated 2026-04-02 for consulting services. Total EUR 100.00.",
        encoding="utf-8",
    )
    with initialize(database):
        pass

    assert (
        main(
            [
                "ingest",
                "--db",
                str(database),
                str(invoice),
                "--kind",
                "expense_invoice",
                "--period",
                "2026-Q2",
                "--issued-on",
                "2026-04-02",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["posting_eligible"] is False
    assert result["status"] == "extracted"

    with initialize(database) as db:
        stored = db.connection.execute("SELECT lifecycle_status FROM documents").fetchone()
        assert stored["lifecycle_status"] == "extracted"
        assert db.table_counts()["transactions"] == 0


def test_income_intake_infers_q3_and_archives_original(tmp_path: Path, capsys) -> None:
    database = tmp_path / "ledger.sqlite"
    invoice = tmp_path / "FACT-2026-00001.txt"
    archive = tmp_path / "evidence"
    invoice.write_text(
        "Invoice No: FACT-2026-00001 Date: 2026-07-15 "
        "Example Customer One consulting services. Invoice total: 100.00 EUR",
        encoding="utf-8",
    )
    with initialize(database):
        pass

    assert (
        main(
            [
                "ingest",
                "--db",
                str(database),
                str(invoice),
                "--kind",
                "income_invoice",
                "--archive-root",
                str(archive),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)

    assert result["period"] == "2026-Q3"
    assert result["issued_on"] == "2026-07-15"
    assert Path(result["archived_path"]).is_file()
    assert result["invoice_amounts"]["gross"] == "100.00"
    assert result["transaction"]["created"] is True
    assert result["transaction"]["lifecycle_status"] == "extracted"
    assert result["transaction"]["amount_minor"] == 10000
    assert result["transaction"]["tax_code"] == "unknown"
    with initialize(database) as db:
        document = db.connection.execute("SELECT * FROM documents").fetchone()
        period = db.connection.execute(
            "SELECT period_key FROM periods WHERE period_id = ?", (document["period_id"],)
        ).fetchone()
        assert period["period_key"] == "2026-Q3"
        assert document["document_number"] == "FACT-2026-00001"
        assert document["total_minor"] == 10000
        assert Path(document["source_path"]).is_file()
        attachment = db.connection.execute(
            """
            SELECT da.*, f.content_sha256, f.byte_size, fr.provider_locator,
                   fr.is_primary, fr.last_verified_at, sb.backend_key
            FROM document_attachments da
            JOIN files f ON f.file_id = da.file_id
            JOIN file_replicas fr ON fr.file_id = f.file_id
            JOIN storage_backends sb ON sb.storage_backend_id = fr.storage_backend_id
            WHERE da.document_id = ? AND da.attachment_role = 'source'
            """,
            (document["document_id"],),
        ).fetchone()
        assert attachment["content_sha256"] == result["sha256"]
        assert attachment["byte_size"] == Path(document["source_path"]).stat().st_size
        assert attachment["backend_key"] == "local_staging"
        assert attachment["is_primary"] == 1
        assert attachment["last_verified_at"]
        transaction = db.connection.execute("SELECT * FROM transactions").fetchone()
        assert transaction["document_id"] == document["document_id"]
        assert transaction["lifecycle_status"] == "extracted"
        issue = db.connection.execute(
            "SELECT * FROM validation_issues WHERE issue_code = 'transaction_tax_review'"
        ).fetchone()
        assert issue["subject_id"] == transaction["transaction_id"]
        treatment = db.connection.execute("SELECT * FROM tax_treatments").fetchone()
        assert treatment["transaction_id"] == transaction["transaction_id"]
        assert treatment["treatment_type"] == "invoice_review"
        assert treatment["tax_code"] == "unknown"


def test_transaction_apply_fx_converts_original_minor_atomically(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        rate = db.add_fx_rate(
            rate_date="2026-07-01",
            base_currency="USD",
            quote_currency="EUR",
            rate="0.87850303",
            rate_source="banco_de_espana",
            source_hash="boe-2026-07-01-usd",
        )
        transaction = db.add_transaction(
            external_key="issued-invoice:SYNTH-DOCUMENT-010",
            period_key="2026-Q3",
            transaction_date="2026-07-01",
            booking_date="2026-07-01",
            entry_type="income",
            description="USD invoice",
            amount_minor=659580,
            currency="USD",
            amount_original_minor=659580,
            original_currency="USD",
            lifecycle_status="received",
        )

    command = [
        "transactions",
        "apply-fx",
        "--db",
        str(database),
        transaction["transaction_id"],
        "--fx-rate-id",
        rate["fx_rate_id"],
        "--expected-row-version",
        "1",
    ]
    assert main(command) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["amount_eur_minor"] == 579443
    assert applied["row_version"] == 2

    command[-1] = "2"
    assert main(command) == 0
    idempotent = json.loads(capsys.readouterr().out)
    assert idempotent["amount_eur_minor"] == 579443
    assert idempotent["row_version"] == 2


def test_review_apply_fx_records_source_and_updates_transaction(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    payload_path = tmp_path / "fx-review.json"
    with initialize(database) as db:
        transaction = db.add_transaction(
            external_key="review-fx:USD-1",
            period_key="2026-Q3",
            transaction_date="2026-07-01",
            booking_date="2026-07-01",
            entry_type="expense",
            description="USD expense",
            amount_minor=10000,
            currency="USD",
            amount_original_minor=10000,
            original_currency="USD",
            lifecycle_status="needs_review",
        )
    payload_path.write_text(
        json.dumps(
            {
                "review_id": f"transaction:{transaction['transaction_id']}",
                "expected_row_version": 1,
                "rate_date": "2026-07-01",
                "rate": "0.85",
                "rate_source": "banco_de_espana",
                "source_reference": "Banco de Espana daily exchange rates",
            }
        ),
        encoding="utf-8",
    )

    assert main(
        [
            "review",
            "apply-fx",
            "--db",
            str(database),
            "--input",
            str(payload_path),
        ]
    ) == 0
    applied = json.loads(capsys.readouterr().out)

    assert applied["amount_eur_minor"] == 8500
    assert applied["row_version"] == 2
    with LedgerDB.open(database, read_only=True) as db:
        fx = db.connection.execute(
            "SELECT rate, rate_source, source_reference FROM fx_rates"
        ).fetchone()
    assert dict(fx) == {
        "rate": "0.85",
        "rate_source": "banco_de_espana",
        "source_reference": "Banco de Espana daily exchange rates",
    }


def test_expense_intake_persists_parsed_total_and_number(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "ledger.sqlite"
    invoice = tmp_path / "utility.pdf"
    invoice.write_bytes(b"fixture")
    digest = hashlib.sha256(b"fixture").hexdigest()
    suggestion = LedgerEntry(
        kind="expense",
        date=date(2026, 7, 13),
        document=str(invoice),
        counterparty="Synthetic Party 005",
        description="SYNTH-DOCUMENT-014",
        amount_original=Decimal("108.37"),
        currency="EUR",
        amount_eur=Decimal("108.37"),
        deductible_eur=None,
        category="home_utility_review",
        confidence="high",
        review_required=True,
        notes="Business-use allocation requires review",
    )
    monkeypatch.setattr(
        "autonomo_taxes.operational_cli.inspect_document",
        lambda *args, **kwargs: IntakeResult(
            source_path=str(invoice),
            sha256=digest,
            kind="expense_invoice",
            mime_type="application/pdf",
            extraction_method="pdf_text",
            extracted_text="parsed fixture",
            status="extracted",
            structural_errors=(),
        ),
    )
    monkeypatch.setattr(
        "autonomo_taxes.operational_cli._document_suggestion",
        lambda *args, **kwargs: suggestion,
    )
    with initialize(database):
        pass

    assert main(["ingest", "--db", str(database), str(invoice), "--kind", "expense_invoice"]) == 0
    capsys.readouterr()

    with initialize(database) as db:
        document = db.connection.execute("SELECT * FROM documents").fetchone()
        assert document["document_number"] == "SYNTH-DOCUMENT-014"
        assert document["total_minor"] == 10837
        transaction = db.connection.execute("SELECT * FROM transactions").fetchone()
        assert transaction["amount_minor"] == 10837
        assert transaction["lifecycle_status"] == "needs_review"


def test_income_intake_document_only_does_not_create_transaction(
    tmp_path: Path, capsys
) -> None:
    database = tmp_path / "ledger.sqlite"
    invoice = tmp_path / "FACT-2026-SYNTH-DOCUMENT-044.txt"
    invoice.write_text(
        "Invoice No: FACT-2026-SYNTH-DOCUMENT-044 Date: 2026-07-16 "
        "Example Customer One services. Invoice total: 100.00 EUR",
        encoding="utf-8",
    )
    with initialize(database):
        pass

    assert main(
        [
            "ingest",
            "--db",
            str(database),
            str(invoice),
            "--kind",
            "income_invoice",
            "--document-only",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["transaction"] is None
    with initialize(database) as db:
        assert db.table_counts()["documents"] == 1
        assert db.table_counts()["transactions"] == 0


def test_invoice_intake_is_idempotent_and_does_not_reopen_review_issues(
    tmp_path: Path, capsys
) -> None:
    database = tmp_path / "ledger.sqlite"
    invoice = tmp_path / "FACT-2026-00009.txt"
    invoice.write_text(
        "Invoice No: FACT-2026-00009 Date: 2026-07-17 "
        "Example Customer One services. Invoice total: 125.00 EUR",
        encoding="utf-8",
    )
    command = [
        "ingest",
        "--db",
        str(database),
        str(invoice),
        "--kind",
        "income_invoice",
    ]
    with initialize(database):
        pass

    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    with initialize(database) as db:
        issue = db.connection.execute(
            "SELECT * FROM validation_issues WHERE issue_code = 'transaction_tax_review'"
        ).fetchone()
        db.resolve_issue(
            issue["validation_issue_id"],
            reason="Reviewed in fixture",
            expected_row_version=issue["row_version"],
        )

    assert main(command) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["transaction"]["created"] is True
    assert second["transaction"]["created"] is False
    assert second["transaction"]["transaction_id"] == first["transaction"]["transaction_id"]
    with pytest.raises(ValueError, match="belongs to 2026-Q3, not requested 2026-Q4"):
        main(command + ["--period", "2026-Q4"])
    with initialize(database) as db:
        assert db.table_counts()["documents"] == 1
        assert db.table_counts()["transactions"] == 1
        assert db.table_counts()["validation_issues"] == 2
        resolved = db.connection.execute(
            "SELECT issue_status FROM validation_issues WHERE issue_code = 'transaction_tax_review'"
        ).fetchone()
        assert resolved["issue_status"] == "resolved"

    corrected_amounts = command + [
        "--gross",
        "125.00",
        "--taxable-base",
        "100.00",
        "--vat",
        "25.00",
        "--currency",
        "EUR",
    ]
    assert main(corrected_amounts) == 0
    capsys.readouterr()
    with initialize(database) as db:
        treatment = db.connection.execute("SELECT * FROM tax_treatments").fetchone()
        reopened = db.connection.execute(
            "SELECT * FROM validation_issues WHERE issue_code = 'transaction_tax_review'"
        ).fetchone()
        assert treatment["taxable_base_minor"] == 10000
        assert treatment["vat_minor"] == 2500
        assert reopened["issue_status"] == "open"

    with pytest.raises(LedgerDbError, match="invoice review treatment conflicts"):
        main(
            command
            + [
                "--gross",
                "125.00",
                "--taxable-base",
                "90.00",
                "--vat",
                "35.00",
                "--currency",
                "EUR",
            ]
        )


def test_corrected_reingest_backfills_document_and_reconciles_review_issues(
    tmp_path: Path, capsys
) -> None:
    database = tmp_path / "ledger.sqlite"
    invoice = tmp_path / "invoice-2026-07-18.txt"
    invoice.write_text(
        "Supplier invoice dated 2026-07-18; scan did not expose the payable total.",
        encoding="utf-8",
    )
    base_command = [
        "ingest",
        "--db",
        str(database),
        str(invoice),
        "--kind",
        "expense_invoice",
        "--issued-on",
        "2026-07-18",
    ]
    with initialize(database):
        pass

    assert main(base_command) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["transaction"]["reason"] == "invoice_amounts_incomplete"

    assert main(
        base_command
        + [
            "--gross",
            "71.39",
            "--taxable-base",
            "59.00",
            "--vat",
            "12.39",
            "--currency",
            "EUR",
        ]
    ) == 0
    corrected = json.loads(capsys.readouterr().out)

    assert corrected["transaction"]["created"] is True
    assert corrected["transaction"]["amount_minor"] == 7139
    with initialize(database) as db:
        document = db.connection.execute("SELECT * FROM documents").fetchone()
        assert document["currency"] == "EUR"
        assert document["total_minor"] == 7139
        issues = {
            row["issue_code"]: row
            for row in db.connection.execute("SELECT * FROM validation_issues").fetchall()
        }
        assert issues["transaction_draft_missing_amount"]["issue_status"] == "resolved"
        assert issues["transaction_draft_missing_amount"]["resolution_reason"]
        assert issues["transaction_tax_review"]["issue_status"] == "open"
        treatment = db.connection.execute("SELECT * FROM tax_treatments").fetchone()
        assert treatment["taxable_base_minor"] == 5900
        assert treatment["vat_minor"] == 1239


def test_sheet_tax_treatment_review_is_versioned_and_idempotent(
    tmp_path: Path, capsys
) -> None:
    database = tmp_path / "ledger.sqlite"
    invoice = tmp_path / "review-invoice-2026-07-19.txt"
    export_dir = tmp_path / "sheet"
    invoice.write_text(
        "Supplier invoice dated 2026-07-19 with reviewed amounts supplied separately.",
        encoding="utf-8",
    )
    with initialize(database):
        pass
    assert main(
        [
            "ingest",
            "--db",
            str(database),
            str(invoice),
            "--kind",
            "expense_invoice",
            "--issued-on",
            "2026-07-19",
            "--gross",
            "71.39",
            "--taxable-base",
            "59.00",
            "--vat",
            "12.39",
            "--currency",
            "EUR",
        ]
    ) == 0
    capsys.readouterr()
    assert main(["sheet", "export", "--db", str(database), "--out-dir", str(export_dir)]) == 0
    capsys.readouterr()
    csv_path = export_dir / "tax_treatments.csv"
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    row = rows[0]
    row["row_version"] = "2"
    row["tax_code"] = "domestic_input"
    row["aeat_invoice_type"] = "F1"
    row["aeat_operation_key"] = "01"
    row["aeat_reverse_charge"] = "0"
    row["aeat_expense_concept"] = "G19"
    row["deductible_irpf_eur"] = "59.00"
    row["deductible_vat_eur"] = "12.39"
    row["include_modelo130"] = "1"
    row["include_modelo303"] = "1"
    row["notes"] = "Reviewed accounting-service invoice."
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)

    command = [
        "sheet",
        "apply",
        "--db",
        str(database),
        "--tab",
        "tax_treatments",
        "--remote-csv",
        str(csv_path),
    ]
    assert main(command) == 0
    applied = json.loads(capsys.readouterr().out)
    assert len(applied["applied"]) == 1
    with initialize(database) as db:
        treatment = db.connection.execute("SELECT * FROM tax_treatments").fetchone()
        assert treatment["row_version"] == 2
        assert treatment["tax_code"] == "domestic_input"
        assert treatment["aeat_invoice_type"] == "F1"
        assert treatment["aeat_operation_key"] == "01"
        assert treatment["aeat_reverse_charge"] == 0
        assert treatment["aeat_expense_concept"] == "G19"
        assert treatment["taxable_base_minor"] == 5900
        assert treatment["vat_minor"] == 1239
        assert treatment["deductible_irpf_minor"] == 5900
        assert treatment["deductible_vat_minor"] == 1239
        assert treatment["include_modelo130"] == 1
        assert treatment["include_modelo303"] == 1

    assert main(command) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["applied"] == []

    row["deductible_irpf_eur"] = "58.00"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)
    assert main(command) == 2
    conflict = json.loads(capsys.readouterr().out)
    assert conflict["conflicts"][0]["reason"] == "divergent_same_version"


def test_sheet_invoice_review_treatment_is_immutable_after_posting(
    tmp_path: Path, capsys
) -> None:
    database = tmp_path / "ledger.sqlite"
    export_dir = tmp_path / "sheet"
    with initialize(database) as db:
        transaction = db.add_transaction(
            external_key="posted-invoice-review",
            period_key="2026-Q3",
            transaction_date="2026-07-20",
            booking_date="2026-07-20",
            entry_type="expense",
            description="Posted invoice review",
            amount_minor=10000,
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="invoice_review",
            tax_code="domestic_input",
            taxable_base_minor=10000,
            deductible_irpf_minor=10000,
            include_modelo130=True,
        )

    assert main(["sheet", "export", "--db", str(database), "--out-dir", str(export_dir)]) == 0
    capsys.readouterr()
    csv_path = export_dir / "tax_treatments.csv"
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    assert rows[0]["status"] == "closed"
    rows[0]["row_version"] = "2"
    rows[0]["deductible_irpf_eur"] = "99.00"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    assert main(
        [
            "sheet",
            "apply",
            "--db",
            str(database),
            "--tab",
            "tax_treatments",
            "--remote-csv",
            str(csv_path),
        ]
    ) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["conflicts"][0]["reason"] == "immutable_row"


def test_sheet_legacy_csv_preserves_explicit_false_reverse_charge(
    tmp_path: Path, capsys
) -> None:
    database = tmp_path / "ledger.sqlite"
    export_dir = tmp_path / "sheet"
    with initialize(database) as db:
        transaction = db.add_transaction(
            period_key="2026-Q3",
            transaction_date="2026-07-20",
            booking_date="2026-07-20",
            entry_type="expense",
            description="Domestic reviewed invoice",
            amount_minor=12100,
            lifecycle_status="needs_review",
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="invoice_review",
            tax_code="domestic_input",
            aeat_invoice_type="F1",
            aeat_operation_key="01",
            aeat_reverse_charge=False,
            aeat_expense_concept="G19",
            taxable_base_minor=10000,
            vat_minor=2100,
            deductible_irpf_minor=10000,
            deductible_vat_minor=2100,
        )

    assert main(["sheet", "export", "--db", str(database), "--out-dir", str(export_dir)]) == 0
    capsys.readouterr()
    csv_path = export_dir / "tax_treatments.csv"
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        row = next(csv.DictReader(handle))
    row.pop("aeat_reverse_charge")
    row["row_version"] = "2"
    row["notes"] = "Reviewed through a legacy sheet without the schema-13 flag column."
    fieldnames = list(row)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)

    assert main(
        [
            "sheet",
            "apply",
            "--db",
            str(database),
            "--tab",
            "tax_treatments",
            "--remote-csv",
            str(csv_path),
        ]
    ) == 0
    capsys.readouterr()
    with initialize(database) as db:
        treatment = db.connection.execute("SELECT * FROM tax_treatments").fetchone()
        assert treatment["aeat_reverse_charge"] == 0


def test_calculate_130_uses_year_specific_rule(tmp_path: Path, capsys) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        income = db.add_transaction(
            external_key="income-2023",
            period_key="2023-Q2",
            transaction_date="2023-04-10",
            booking_date="2023-04-10",
            entry_type="income",
            description="Issued invoice",
            amount_minor=100000,
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=income["transaction_id"],
            treatment_type="income",
            tax_code="outside_scope",
            taxable_base_minor=100000,
            include_modelo130=True,
        )
        expense = db.add_transaction(
            external_key="expense-2023",
            period_key="2023-Q2",
            transaction_date="2023-04-11",
            booking_date="2023-04-11",
            entry_type="expense",
            description="Reviewed expense",
            amount_minor=20000,
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=expense["transaction_id"],
            treatment_type="expense",
            tax_code="domestic_input",
            taxable_base_minor=20000,
            deductible_irpf_minor=20000,
            include_modelo130=True,
        )

    assert (
        main(
            [
                "calculate",
                "--db",
                str(database),
                "--form",
                "130",
                "--year",
                "2023",
                "--quarter",
                "2",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["values"]["difficult_expenses"] == "56.00"


def test_due_but_unfiled_obligation_allows_calculation_and_blocks_only_close(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        income = db.add_transaction(
            external_key="income-2026",
            period_key="2026-Q2",
            transaction_date="2026-04-10",
            booking_date="2026-04-10",
            entry_type="income",
            description="Issued invoice",
            amount_minor=100000,
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=income["transaction_id"],
            treatment_type="income",
            tax_code="outside_scope",
            taxable_base_minor=100000,
            include_modelo130=True,
        )
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="due",
            determination="due",
            explanation="Fixture filing is not submitted yet.",
        )

    assert main(["calculate", "--db", str(database), "--form", "130", "--year", "2026", "--quarter", "2"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["values"]["01"] == "1000.00"

    with initialize(database) as db:
        db.add_validation_issue(
            period_key="2026-Q2",
            issue_code="missing_evidence",
            message="Fixture evidence is unresolved.",
            severity="error",
            blocking=True,
        )

    with pytest.raises(CalculationBlocked, match="unresolved production accounting data"):
        main(["calculate", "--db", str(database), "--form", "130", "--year", "2026", "--quarter", "2"])


def test_obligation_mark_not_due_clears_default_blocking_flag(tmp_path: Path, capsys) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        obligation = db.add_obligation(
            period_key="2026-Q2",
            obligation_code="349",
            filing_status="unknown",
            determination="unknown",
        )

    assert (
        main(
            [
                "obligations",
                "mark",
                "--db",
                str(database),
                "--period",
                "2026-Q2",
                "--form",
                "349",
                "--determination",
                "not_due",
                "--filing-status",
                "waived",
                "--explanation",
                "No intracommunity operations in the quarter.",
                "--due-on",
                "2026-07-20",
                "--expected-row-version",
                str(obligation["row_version"]),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["blocking"] == 0
    assert result["due_on"] == "2026-07-20"

    with initialize(database) as db:
        assert db.validate_period("2026-Q2")["unresolved_obligations"] == []


def test_obligation_record_evidence_hashes_and_attaches_source(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    evidence = tmp_path / "aeat-account-check.pdf"
    evidence.write_bytes(b"%PDF-1.4 reviewed account evidence")
    with initialize(database) as db:
        obligation = db.add_obligation(
            period_key="2023",
            obligation_code="347",
            filing_status="waived",
            determination="not_due",
            blocking=False,
        )

    args = [
        "obligations",
        "record-evidence",
        "--db",
        str(database),
        "--period",
        "2023",
        "--form",
        "347",
        "--kind",
        "aeat_account_check",
        "--evidence",
        str(evidence),
        "--notes",
        "No filed Modelo 347 shown in Mis expedientes.",
    ]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["obligation_id"] == obligation["obligation_id"]
    assert first["source_hash"] == hashlib.sha256(evidence.read_bytes()).hexdigest()
    assert main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["obligation_evidence_id"] == first["obligation_evidence_id"]

    with initialize(database) as db:
        rows = db.list_obligation_evidence(
            obligation_id=obligation["obligation_id"]
        )
    assert len(rows) == 1
    assert rows[0]["evidence_kind"] == "aeat_account_check"


def test_production_calculation_authoritative_history_is_explicit_and_provenanced(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        batch = db.add_import_batch(
            source_name="xolo_source_book_rows",
            source_hash="authoritative-source-book-batch",
        )
        document = db.upsert_document(
            external_key="authoritative-income-document",
            import_batch_id=batch["import_batch_id"],
            document_type="ingresos_book",
            document_number="INV-2026-1",
            issued_on="2026-04-10",
            period_key="2026-Q2",
            lifecycle_status="approved",
            source_hash="authoritative-income-document-hash",
        )
        db.add_document_source(
            document_id=document["document_id"],
            import_batch_id=batch["import_batch_id"],
            source_book_line_id="official-income-row-1",
            source_file="Libros_contables_2026.xlsx",
            source_row_number="2",
            source_hash="official-income-row-hash",
        )
        income = db.add_transaction(
            external_key="authoritative-income",
            period_key="2026-Q2",
            transaction_date="2026-04-10",
            booking_date="2026-04-10",
            entry_type="income",
            description="Official Xolo income row",
            amount_minor=100000,
            lifecycle_status="approved",
            document_id=document["document_id"],
        )
        db.add_detailed_tax_treatment(
            transaction_id=income["transaction_id"],
            treatment_type="income",
            tax_code="outside_scope",
            taxable_base_minor=100000,
            include_modelo130=True,
        )
        db.create_filing_snapshot(
            "2026-Q2",
            status="baseline",
            filed_on="2026-07-08",
            payload={"form": "130", "filed_values": {"01": "1000.00"}},
        )

    command = [
        "calculate",
        "--db",
        str(database),
        "--form",
        "130",
        "--year",
        "2026",
        "--quarter",
        "2",
    ]
    with pytest.raises(CalculationBlocked, match="unresolved production accounting data"):
        main(command)

    assert main([*command, "--allow-authoritative-history"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["values"]["01"] == "1000.00"
    assert result["authoritative_history_mode"] is True


def test_modelo130_production_validates_prior_ytd_quarters(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        db.add_transaction(
            external_key="unreviewed-q1-income",
            period_key="2026-Q1",
            transaction_date="2026-03-31",
            booking_date="2026-03-31",
            entry_type="income",
            description="Unreviewed prior-quarter income",
            amount_minor=50000,
            lifecycle_status="needs_review",
        )
        q2 = db.add_transaction(
            external_key="posted-q2-income",
            period_key="2026-Q2",
            transaction_date="2026-06-30",
            booking_date="2026-06-30",
            entry_type="income",
            description="Posted current-quarter income",
            amount_minor=100000,
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=q2["transaction_id"],
            treatment_type="income",
            tax_code="service_income",
            taxable_base_minor=100000,
            include_modelo130=True,
        )

    with pytest.raises(CalculationBlocked, match="2026-Q1 has unresolved production accounting data"):
        main(["calculate", "--db", str(database), "--form", "130", "--year", "2026", "--quarter", "2"])


def test_modelo130_authoritative_history_allows_filed_prior_quarter_with_unrelated_obligation(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        q1 = db.add_transaction(
            external_key="posted-q1-income",
            period_key="2026-Q1",
            transaction_date="2026-03-31",
            booking_date="2026-03-31",
            entry_type="income",
            description="Filed prior-quarter income",
            amount_minor=50000,
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=q1["transaction_id"],
            treatment_type="income",
            tax_code="service_income",
            taxable_base_minor=50000,
            include_modelo130=True,
        )
        q2 = db.add_transaction(
            external_key="posted-q2-income",
            period_key="2026-Q2",
            transaction_date="2026-06-30",
            booking_date="2026-06-30",
            entry_type="income",
            description="Current-quarter income",
            amount_minor=100000,
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=q2["transaction_id"],
            treatment_type="income",
            tax_code="service_income",
            taxable_base_minor=100000,
            include_modelo130=True,
        )
        db.add_obligation(
            period_key="2026-Q1",
            obligation_code="130",
            filing_status="filed",
            determination="due",
            blocking=False,
        )
        db.create_filing_snapshot(
            "2026-Q1",
            status="baseline",
            filed_on="2026-04-20",
            payload={"form": "130", "filed_values": {"01": "500.00", "07": "95.00"}},
            form_code="130",
        )
        db.add_obligation(
            period_key="2026-Q1",
            obligation_code="111",
            filing_status="unknown",
            determination="unknown",
            explanation="Separate historical withholding review.",
        )

    command = [
        "calculate",
        "--db",
        str(database),
        "--form",
        "130",
        "--year",
        "2026",
        "--quarter",
        "2",
    ]
    with pytest.raises(CalculationBlocked, match="Prior YTD period 2026-Q1 must be closed"):
        main(command)

    assert main([*command, "--allow-authoritative-history"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["values"]["01"] == "1500.00"
    assert result["authoritative_history_mode"] is True


def test_unknown_current_obligation_blocks_production_calculation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        income = db.add_transaction(
            external_key="current-income",
            period_key="2026-Q2",
            transaction_date="2026-06-30",
            booking_date="2026-06-30",
            entry_type="income",
            description="Current-quarter income",
            amount_minor=100000,
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=income["transaction_id"],
            treatment_type="income",
            tax_code="service_income",
            taxable_base_minor=100000,
            include_modelo130=True,
        )
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="303",
            filing_status="unknown",
            determination="unknown",
            explanation="VAT obligation has not been determined.",
        )

    with pytest.raises(CalculationBlocked, match="2026-Q2 has unresolved production accounting data"):
        main(["calculate", "--db", str(database), "--form", "130", "--year", "2026", "--quarter", "2"])


def test_db_backed_modelo303_routes_linked_asset_to_capital_goods_boxes(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        transaction = db.add_transaction(
            external_key="capital-input",
            period_key="2026-Q2",
            transaction_date="2026-04-10",
            booking_date="2026-04-10",
            entry_type="expense",
            description="Capital purchase",
            amount_minor=12100,
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="iva_input",
            tax_code="domestic_input",
            taxable_base_minor=10000,
            vat_minor=2100,
            deductible_vat_minor=2100,
            include_modelo303=True,
        )
        db.add_asset(
            asset_code="CAPITAL-303",
            cost_minor=10000,
            currency="EUR",
            depreciation_method="straight_line",
            acquisition_transaction_id=transaction["transaction_id"],
            source_hash="capital-303-asset",
        )

    assert main(["calculate", "--db", str(database), "--form", "303", "--year", "2026", "--quarter", "2"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["values"]["28"] == "0.00"
    assert result["values"]["29"] == "0.00"
    assert result["values"]["30"] == "100.00"
    assert result["values"]["31"] == "21.00"


def test_revolut_reimport_is_idempotent_after_period_close(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    statement = tmp_path / "revolut.csv"
    statement.write_text(
        "Completed Date,Reference,Counterparty,Amount,Currency,Amount in EUR\n"
        "2026-03-25,INV-Q1-1,Client A,100.00,EUR,100.00\n",
        encoding="utf-8",
    )
    with initialize(database) as db:
        transaction = db.add_transaction(
            external_key="INV-Q1-1",
            period_key="2026-Q1",
            transaction_date="2026-03-20",
            booking_date="2026-03-20",
            entry_type="income",
            description="Q1 invoice",
            amount_minor=10000,
            lifecycle_status="posted",
        )

    command = ["bank", "import-revolut", "--db", str(database), "--csv", str(statement)]
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["payments"][0]["match"]["matched_candidate_ids"] == [transaction["transaction_id"]]

    with initialize(database) as db:
        db.close_period("2026-Q1", expected_row_version=1)

    assert main(command) == 0
    capsys.readouterr()
    with initialize(database) as db:
        count = db.connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    assert count == 1


def test_manual_payment_evidence_is_archived_idempotent_and_keeps_tax_date(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    evidence = tmp_path / "gmail-payment.json"
    archive_root = tmp_path / "evidence"
    evidence.write_text(
        json.dumps(
            {
                "gmail_message_id": "message-1",
                "paid_on": "2026-03-23",
                "amount": "2000.00",
                "reference": "INV-1",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    with initialize(database) as db:
        transaction = db.add_transaction(
            external_key="INV-1",
            period_key="2026-Q1",
            transaction_date="2026-03-22",
            booking_date="2026-03-22",
            entry_type="expense",
            description="Accrued supplier invoice",
            amount_minor=200180,
            lifecycle_status="posted",
        )
        db.close_period("2026-Q1", expected_row_version=1)

    command = [
        "bank",
        "record-payment",
        "--db",
        str(database),
        "--transaction-id",
        transaction["transaction_id"],
        "--evidence",
        str(evidence),
        "--paid-on",
        "2026-03-23",
        "--amount",
        "2000.00",
        "--source-system",
        "gmail_revolut_notification",
        "--external-id",
        "message-1",
        "--reference",
        "INV-1",
        "--account-name",
        "Revolut",
        "--counterparty-name",
        "Supplier",
        "--archive-root",
        str(archive_root),
    ]
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["already_imported"] is False
    assert first["period"] == "2026-Q1"
    assert first["paid_on"] == "2026-03-23"
    archived = archive_root / first["evidence_locator"]
    assert archived.read_bytes() == evidence.read_bytes()

    with initialize(database) as db:
        payment = db.connection.execute("SELECT * FROM payments").fetchone()
        stored_transaction = db.connection.execute(
            "SELECT transaction_date, booking_date FROM transactions WHERE transaction_id = ?",
            (transaction["transaction_id"],),
        ).fetchone()
        assert payment["transaction_id"] == transaction["transaction_id"]
        assert payment["match_status"] == "manual"
        assert payment["amount_minor"] == 200000
        assert payment["amount_eur_minor"] == 200000
        assert payment["source_system"] == "gmail_revolut_notification"
        source = json.loads(payment["source_row_json"])
        assert source["evidence_sha256"] == hashlib.sha256(evidence.read_bytes()).hexdigest()
        assert source["evidence_locator"] == first["evidence_locator"]
        assert stored_transaction["transaction_date"] == "2026-03-22"
        assert stored_transaction["booking_date"] == "2026-03-22"

    assert main(command) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["already_imported"] is True
    with initialize(database) as db:
        assert db.connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 1

    conflicting = [*command]
    conflicting[conflicting.index("2000.00")] = "1999.00"
    with pytest.raises(LedgerDbError, match="different fields"):
        main(conflicting)


def test_manual_payment_evidence_rejects_unknown_transaction_and_nonpositive_amount(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    evidence = tmp_path / "payment.json"
    empty_config = tmp_path / "config.yaml"
    evidence.write_text("{}", encoding="utf-8")
    empty_config.write_text("{}", encoding="utf-8")
    with initialize(database):
        pass

    without_archive = [
        "--config",
        str(empty_config),
        "bank",
        "record-payment",
        "--db",
        str(database),
        "--transaction-id",
        "missing",
        "--evidence",
        str(evidence),
        "--paid-on",
        "2026-03-23",
        "--amount",
        "10.00",
        "--source-system",
        "manual",
        "--external-id",
        "payment-1",
    ]
    with pytest.raises(ValueError, match="--archive-root is required"):
        main(without_archive)

    base = [*without_archive, "--archive-root", str(tmp_path / "archive")]
    with pytest.raises(LedgerDbError, match="Unknown transaction_id"):
        main(base)

    zero = [*base]
    zero[zero.index("10.00")] = "0"
    with pytest.raises(ValueError, match="amount must be greater than zero"):
        main(zero)


def test_manual_tax_payment_evidence_is_archived_for_filed_due_obligation(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    evidence = tmp_path / "tax-debit.json"
    archive_root = tmp_path / "evidence"
    evidence.write_text(
        json.dumps(
            {
                "paid_on": "2026-07-20",
                "amount": "2639.12",
                "reference": "Modelo 130 2026-Q2",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    with initialize(database) as db:
        obligation = db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            determination="due",
            filed_at="2026-07-08",
        )
        db.close_period("2026-Q2", expected_row_version=1)

    command = [
        "bank",
        "record-payment",
        "--db",
        str(database),
        "--tax-obligation",
        "2026-Q2:130",
        "--evidence",
        str(evidence),
        "--paid-on",
        "2026-07-20",
        "--amount",
        "2639.12",
        "--source-system",
        "bank_debit_evidence",
        "--external-id",
        "q2-modelo130-debit",
        "--reference",
        "Modelo 130 2026-Q2",
        "--account-name",
        "Tax payment account",
        "--counterparty-name",
        "AEAT",
        "--archive-root",
        str(archive_root),
    ]
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["already_imported"] is False
    assert first["transaction_id"] is None
    assert first["obligation_id"] == obligation["obligation_id"]
    assert first["obligation_code"] == "130"
    assert first["period"] == "2026-Q2"
    assert "tax_payment_evidence" in first["evidence_locator"]
    assert (archive_root / first["evidence_locator"]).read_bytes() == evidence.read_bytes()

    with initialize(database) as db:
        payment = db.connection.execute("SELECT * FROM payments").fetchone()
        assert payment["transaction_id"] is None
        assert payment["obligation_id"] == obligation["obligation_id"]
        assert payment["amount_minor"] == 263912
        assert payment["paid_on"] == "2026-07-20"

    repeated = [*command]
    selector_index = repeated.index("--tax-obligation")
    repeated[selector_index : selector_index + 2] = [
        "--obligation-id",
        obligation["obligation_id"],
    ]
    assert main(repeated) == 0
    assert json.loads(capsys.readouterr().out)["already_imported"] is True


def test_manual_tax_payment_rejects_unknown_not_due_and_unfiled_obligations(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    evidence = tmp_path / "tax-debit.json"
    archive_root = tmp_path / "evidence"
    evidence.write_text("{}", encoding="utf-8")
    with initialize(database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="349",
            filing_status="waived",
            determination="not_due",
        )
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="303",
            filing_status="due",
            determination="due",
        )

    def command(selector: str, external_id: str) -> list[str]:
        return [
            "bank",
            "record-payment",
            "--db",
            str(database),
            "--tax-obligation",
            selector,
            "--evidence",
            str(evidence),
            "--paid-on",
            "2026-07-20",
            "--amount",
            "10.00",
            "--source-system",
            "manual",
            "--external-id",
            external_id,
            "--archive-root",
            str(archive_root),
        ]

    with pytest.raises(LedgerDbError, match="Unknown tax obligation"):
        main(command("2026-Q2:130", "missing"))
    with pytest.raises(LedgerDbError, match="is not due"):
        main(command("2026-Q2:349", "not-due"))
    with pytest.raises(LedgerDbError, match="is not filed"):
        main(command("2026-Q2:303", "unfiled"))
    with pytest.raises(ValueError, match="requires a YYYY-QN period"):
        main(command("2026:130", "bad-period"))


def test_revolut_import_rolls_back_entire_batch_when_a_later_row_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "ledger.sqlite"
    statement = tmp_path / "revolut.csv"
    statement.write_text(
        "Completed Date,Reference,Counterparty,Amount,Currency,Amount in EUR\n"
        "2026-03-25,ROW-1,Client A,100.00,EUR,100.00\n"
        "2026-03-26,ROW-2,Client B,200.00,EUR,200.00\n",
        encoding="utf-8",
    )
    with initialize(database):
        pass

    original_add_payment = LedgerDB.add_payment
    call_count = 0

    def fail_second_payment(self, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated second-row failure")
        return original_add_payment(self, **kwargs)

    monkeypatch.setattr(LedgerDB, "add_payment", fail_second_payment)
    with pytest.raises(RuntimeError, match="second-row failure"):
        main(["bank", "import-revolut", "--db", str(database), "--csv", str(statement)])

    with initialize(database) as db:
        count = db.connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
    assert count == 0


def test_revolut_import_claims_legacy_hash_without_duplicate(tmp_path: Path, capsys) -> None:
    database = tmp_path / "ledger.sqlite"
    statement = tmp_path / "revolut.csv"
    statement.write_text(
        "Completed Date,Reference,Counterparty,Amount,Currency,Amount in EUR\n"
        "2026-07-10,INV-Q3-1,Client A,100.00,EUR,100.00\n",
        encoding="utf-8",
    )
    with initialize(database) as db:
        transaction = db.add_transaction(
            external_key="INV-Q3-1",
            period_key="2026-Q3",
            transaction_date="2026-07-05",
            booking_date="2026-07-05",
            entry_type="income",
            description="Q3 invoice",
            amount_minor=10000,
            lifecycle_status="posted",
        )
        legacy_hash = hashlib.sha256(b"old-file-digest:old-payment-id").hexdigest()
        legacy = db.add_payment(
            transaction_id=transaction["transaction_id"],
            paid_on="2026-07-10",
            amount_minor=10000,
            currency="EUR",
            original_reference="INV-Q3-1",
            match_status="exact",
            source_hash=legacy_hash,
        )

    assert main(["bank", "import-revolut", "--db", str(database), "--csv", str(statement)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["claimed_legacy"] == 1
    assert result["payments"] == []
    with initialize(database) as db:
        rows = db.connection.execute("SELECT * FROM payments").fetchall()
        assert len(rows) == 1
        assert rows[0]["payment_id"] == legacy["payment_id"]
        assert rows[0]["source_hash"] == legacy_hash
        assert rows[0]["source_system"] == "revolut"
        assert rows[0]["external_id"]


def test_revolut_does_not_double_settle_transaction_paid_by_another_source(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    statement = tmp_path / "revolut.csv"
    statement.write_text(
        "Completed Date,Reference,Counterparty,Amount,Currency,Amount in EUR\n"
        "2026-07-10,INV-Q3-1,Client A,100.00,EUR,100.00\n",
        encoding="utf-8",
    )
    with initialize(database) as db:
        transaction = db.add_transaction(
            external_key="INV-Q3-1",
            period_key="2026-Q3",
            transaction_date="2026-07-05",
            booking_date="2026-07-05",
            entry_type="income",
            description="Q3 invoice",
            amount_minor=10000,
            lifecycle_status="posted",
        )
        db.add_payment(
            transaction_id=transaction["transaction_id"],
            paid_on="2026-07-10",
            amount_minor=10000,
            currency="EUR",
            original_reference="INV-Q3-1",
            match_status="exact",
            source_hash="zenmoney-payment",
            source_system="zenmoney",
            external_id="zen-1",
            account_name="Business",
        )

    assert main(["bank", "import-revolut", "--db", str(database), "--csv", str(statement)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["payments"][0]["match"]["outcome"] == "unmatched"
    with initialize(database) as db:
        rows = db.connection.execute(
            "SELECT transaction_id, match_status FROM payments ORDER BY created_at, payment_id"
        ).fetchall()
        assert len(rows) == 2
        assert sum(row["transaction_id"] == transaction["transaction_id"] for row in rows) == 1


def test_full_zenmoney_export_flags_and_resolves_disappearing_payment(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    statement = tmp_path / "zenmoney.csv"
    header = "Date,Account,Outcome,Currency,id\n"
    original = header + "2026-07-10,Business,10.00,EUR,zen-1\n"
    statement.write_text(original, encoding="utf-8")
    with initialize(database):
        pass
    command = [
        "bank",
        "import-zenmoney",
        "--db",
        str(database),
        "--csv",
        str(statement),
        "--period",
        "2026-Q3",
        "--account",
        "Business",
    ]

    assert main(command) == 0
    capsys.readouterr()
    statement.write_text(header, encoding="utf-8")
    assert main(command) == 0
    missing = json.loads(capsys.readouterr().out)
    assert len(missing["missing_from_export"]) == 1
    with initialize(database) as db:
        issue = db.connection.execute(
            "SELECT * FROM validation_issues WHERE issue_code = 'payment_missing_from_zenmoney_export'"
        ).fetchone()
        assert issue["issue_status"] == "open"
        assert issue["blocking"] == 1

    statement.write_text(original, encoding="utf-8")
    assert main(command) == 0
    restored = json.loads(capsys.readouterr().out)
    assert restored["missing_from_export"] == []
    with initialize(database) as db:
        issue = db.connection.execute(
            "SELECT * FROM validation_issues WHERE issue_code = 'payment_missing_from_zenmoney_export'"
        ).fetchone()
        assert issue["issue_status"] == "resolved"


def test_zenmoney_inspection_cli_does_not_require_or_modify_database(
    tmp_path: Path,
    capsys,
) -> None:
    statement = tmp_path / "zenmoney.csv"
    statement.write_text(
        "Date,Account,Outcome,Currency,id\n"
        "2026-07-10,Revolut Pro,10.00,EUR,zen-1\n",
        encoding="utf-8",
    )

    assert main(["bank", "inspect-zenmoney", "--csv", str(statement)]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["row_count"] == 1
    assert result["starts_on"] == "2026-07-10"
    assert result["accounts"] == [
        {
            "account_name": "Revolut Pro",
            "outcome_or_single_account_rows": 1,
            "income_rows": 0,
            "currencies": ["EUR"],
        }
    ]
    assert len(result["source_sha256"]) == 64


def test_zenmoney_import_filters_period_archives_source_and_matches_without_rebooking(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    statement = tmp_path / "zenmoney.csv"
    archive = tmp_path / "archive"
    statement.write_text(
        "Date,Category,Payee,Comment,Account,Outcome,Currency,id\n"
        "2026-07-10,AI,OpenAI,INV-Q3-OPENAI,Business,103.00,EUR,zm-q3-1\n"
        "2026-06-10,AI,OpenAI,OLD-Q2,Business,103.00,EUR,zm-q2-1\n",
        encoding="utf-8",
    )
    with initialize(database) as db:
        document = db.upsert_document(
            external_key="doc-q3-openai",
            document_type="expense_invoice",
            document_number="INV-Q3-OPENAI",
            issued_on="2026-07-05",
            period_key="2026-Q3",
            lifecycle_status="approved",
        )
        transaction = db.add_transaction(
            external_key="expense-q3-openai",
            period_key="2026-Q3",
            transaction_date="2026-07-05",
            booking_date="2026-07-05",
            entry_type="expense",
            description="OpenAI July",
            amount_minor=10300,
            amount_eur_minor=10300,
            lifecycle_status="approved",
            document_id=document["document_id"],
        )

    command = [
        "bank",
        "import-zenmoney",
        "--db",
        str(database),
        "--csv",
        str(statement),
        "--period",
        "2026-Q3",
        "--account",
        "Business",
        "--archive-root",
        str(archive),
    ]
    assert main(command) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["imported"]) == 1
    assert result["imported"][0]["match"]["matched_candidate_ids"] == [
        transaction["transaction_id"]
    ]
    assert result["skipped"][0]["reason"] == "outside_period"
    assert Path(result["archived_path"]).is_file()

    assert main(command) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["already_imported"] == 1
    assert repeated["imported"] == []
    with initialize(database) as db:
        payment = db.connection.execute("SELECT * FROM payments").fetchone()
        stored_transaction = db.connection.execute(
            "SELECT transaction_date FROM transactions WHERE transaction_id = ?",
            (transaction["transaction_id"],),
        ).fetchone()
        assert payment["transaction_id"] == transaction["transaction_id"]
        assert payment["source_system"] == "zenmoney"
        assert payment["account_name"] == "Business"
        assert payment["counterparty_name"] == "OpenAI"
        assert stored_transaction["transaction_date"] == "2026-07-05"

    statement.write_text(
        "Date,Category,Payee,Comment,Account,Outcome,Currency,id\n"
        "2026-07-10,AI,OpenAI,CHANGED-REFERENCE,Business,103.00,EUR,zm-q3-1\n",
        encoding="utf-8",
    )
    assert main(command) == 0
    description_changed = json.loads(capsys.readouterr().out)
    assert description_changed["already_imported"] == 1

    statement.write_text(
        "Date,Category,Payee,Comment,Account,Outcome,Currency,id\n"
        "2026-07-10,AI,OpenAI,CHANGED-REFERENCE,Business,104.00,EUR,zm-q3-1\n",
        encoding="utf-8",
    )
    with pytest.raises(LedgerDbError, match="changed accounting fields"):
        main(command)


def test_sheet_export_keeps_full_payment_header_when_ledger_has_no_payments(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    output = tmp_path / "sheet"
    with initialize(database):
        pass

    assert main(["sheet", "export", "--db", str(database), "--out-dir", str(output)]) == 0
    capsys.readouterr()
    with (output / "payments.csv").open(newline="", encoding="utf-8-sig") as handle:
        header = next(csv.reader(handle))

    assert header == [
        "uuid",
        "row_version",
        "status",
        "transaction_id",
        "obligation_id",
        "paid_on",
        "amount_minor",
        "currency",
        "amount_eur_minor",
        "original_reference",
        "fee_minor",
        "fee_currency",
        "source_system",
        "external_id",
        "account_name",
        "counterparty_name",
        "category",
        "comment",
    ]
    with (output / "tax_treatments.csv").open(
        newline="", encoding="utf-8-sig"
    ) as handle:
        treatment_header = next(csv.reader(handle))
    assert treatment_header == [
        "uuid",
        "row_version",
        "status",
        "period_key",
        "transaction_id",
        "transaction_lifecycle",
        "transaction_date",
        "entry_type",
        "description",
        "counterparty",
        "document_number",
        "gross_original",
        "original_currency",
        "amount_eur",
        "treatment_type",
        "jurisdiction",
        "tax_code",
        "aeat_invoice_type",
        "aeat_operation_key",
        "aeat_operation_qualification",
        "aeat_exemption_code",
        "aeat_reverse_charge",
        "aeat_expense_concept",
        "rate_basis_points",
        "deductible_ratio",
        "taxable_base_eur",
        "vat_eur",
        "deductible_irpf_eur",
        "deductible_vat_eur",
        "withholding_eur",
        "include_modelo130",
        "include_modelo303",
        "include_modelo347",
        "rule_version_id",
        "notes",
    ]


def test_sheet_reconcile_is_idempotent_and_closed_rows_are_immutable(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    remote = tmp_path / "transactions.csv"
    with initialize(database) as db:
        transaction = db.add_transaction(
            external_key="closed-row",
            period_key="2026-Q2",
            transaction_date="2026-04-10",
            booking_date="2026-04-10",
            entry_type="expense",
            description="Closed expense",
            amount_minor=1000,
            lifecycle_status="posted",
        )
        db.close_period("2026-Q2", expected_row_version=1)

    with remote.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["uuid", "row_version", "status", "lifecycle_status", "description", "amount_eur_minor"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "uuid": transaction["transaction_id"],
                "row_version": transaction["row_version"],
                "status": "closed",
                "lifecycle_status": "posted",
                "description": "Closed expense",
                "amount_eur_minor": "1000",
            }
        )

    assert main(["sheet", "reconcile", "--db", str(database), "--tab", "transactions", "--remote-csv", str(remote)]) == 0
    unchanged = json.loads(capsys.readouterr().out)
    assert unchanged["ok"] is True
    assert unchanged["unchanged"] == [transaction["transaction_id"]]

    rows = list(csv.DictReader(remote.open(encoding="utf-8")))
    rows[0]["description"] = "Attempted rewrite"
    with remote.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    assert main(["sheet", "reconcile", "--db", str(database), "--tab", "transactions", "--remote-csv", str(remote)]) == 2
    conflict = json.loads(capsys.readouterr().out)
    assert conflict["conflicts"][0]["reason"] == "immutable_row"


def test_closed_period_issue_is_exported_as_immutable_and_apply_returns_conflict(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    export_dir = tmp_path / "sheet"
    with initialize(database) as db:
        issue = db.add_validation_issue(
            period_key="2026-Q2",
            issue_code="historical-note",
            severity="info",
            message="Non-blocking historical note.",
            blocking=False,
        )
        db.close_period("2026-Q2", expected_row_version=1)

    assert main(["sheet", "export", "--db", str(database), "--out-dir", str(export_dir)]) == 0
    capsys.readouterr()
    csv_path = export_dir / "issues.csv"
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    assert rows[0]["uuid"] == issue["validation_issue_id"]
    assert rows[0]["status"] == "closed"

    rows[0]["row_version"] = "2"
    rows[0]["status"] = "resolved"
    rows[0]["resolution_reason"] = "Attempted late resolution"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    command = ["sheet", "apply", "--db", str(database), "--tab", "issues", "--remote-csv", str(csv_path)]
    assert main(command) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["applied"] == []
    assert result["conflicts"] == [
        {
            "uuid": issue["validation_issue_id"],
            "reason": "immutable_row",
            "local_row_version": 1,
            "remote_row_version": 2,
        }
    ]


def test_q2_2026_historical_replay_matches_filed_baseline_without_balancing_plug(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        db.ensure_period("2026-Q1")
        db.create_filing_snapshot(
            "2026-Q1",
            status="baseline",
            filed_on="2026-04-15",
            payload={"form": "130", "baseline_kind": "xolo_submitted", "filed_values": {"07": "2659.01"}},
        )
        income = db.add_transaction(
            external_key="income-q2",
            period_key="2026-Q2",
            transaction_date="2026-06-30",
            booking_date="2026-06-30",
            entry_type="income",
            description="Xolo source-book YTD income",
            amount_minor=3677089,
            lifecycle_status="approved",
        )
        db.add_detailed_tax_treatment(
            transaction_id=income["transaction_id"],
            treatment_type="income",
            tax_code="historical_income",
            taxable_base_minor=3677089,
            include_modelo130=True,
        )
        expense = db.add_transaction(
            external_key="q2-ytd-expense",
            period_key="2026-Q2",
            transaction_date="2026-06-30",
            booking_date="2026-06-30",
            entry_type="expense",
            description="Xolo source-book YTD deductible total",
            amount_minor=1028023,
            lifecycle_status="approved",
        )
        db.add_detailed_tax_treatment(
            transaction_id=expense["transaction_id"],
            treatment_type="expense",
            tax_code="historical_expense",
            taxable_base_minor=1028023,
            deductible_irpf_minor=1028023,
            include_modelo130=True,
        )
        db.create_filing_snapshot(
            "2026-Q2",
            status="baseline",
            filed_on="2026-07-08",
            payload={
                "form": "130",
                "baseline_kind": "xolo_submitted",
                "filed_values": {"01": "36770.89", "02": "10280.23", "19": "2639.12"},
            },
        )

    assert (
        main(
            [
                "calculate",
                "--db",
                str(database),
                "--form",
                "130",
                "--year",
                "2026",
                "--quarter",
                "2",
                "--mode",
                "verify_history",
                "--difficult-expenses-policy",
                "source_book_total",
            ]
        )
        == 0
    )
    replay = json.loads(capsys.readouterr().out)
    assert replay["values"]["01"] == "36770.89"
    assert replay["values"]["02"] == "10280.23"
    assert replay["values"]["19"] == "2639.12"
    assert replay["diff_from_filed"]["01"] == "0.00"
    assert replay["diff_from_filed"]["02"] == "0.00"
    assert replay["diff_from_filed"]["19"] == "0.00"
    assert replay["values"]["difficult_expenses"] == "0.00"

    assert (
        main(
            [
                "calculate",
                "--db",
                str(database),
                "--form",
                "130",
                "--year",
                "2026",
                "--quarter",
                "2",
                "--mode",
                "verify_history",
            ]
        )
        == 0
    )
    recomputed = json.loads(capsys.readouterr().out)
    assert recomputed["values"]["02"] == "11604.76"
    assert recomputed["diff_from_filed"]["02"] == "1324.53"


def test_foreign_country_does_not_assign_modelo216_without_explicit_tax_code(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        supplier = db.upsert_counterparty(
            external_key="georgian-contractor",
            display_name="Georgian Contractor",
            country_code="GE",
        )
        transaction = db.add_transaction(
            external_key="ordinary-foreign-expense",
            period_key="2026-Q2",
            transaction_date="2026-06-30",
            booking_date="2026-06-30",
            entry_type="expense",
            description="Ordinary reviewed expense",
            amount_minor=200240,
            lifecycle_status="posted",
            counterparty_id=supplier["counterparty_id"],
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="expense",
            tax_code="professional_expense",
            taxable_base_minor=200240,
            deductible_irpf_minor=200240,
            include_modelo130=True,
        )

    assert (
        main(["calculate", "--db", str(database), "--form", "216", "--year", "2026", "--quarter", "2"])
        == 0
    )
    modelo216 = json.loads(capsys.readouterr().out)

    assert modelo216["values"]["recipient_count"] == "0"
    assert modelo216["values"]["income_count"] == "0"
    assert modelo216["values"]["base"] == "0.00"
    assert modelo216["values"]["negative_return"] == "no"
    assert modelo216["warnings"] == []


def test_production_retention_calculations_keep_explicit_zero_withholding_rows(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        nonresident = db.upsert_counterparty(
            external_key="foreign-contractor",
            display_name="Foreign Contractor",
            country_code="GE",
        )
        professional = db.upsert_counterparty(
            external_key="spanish-professional",
            display_name="Spanish Professional",
            country_code="ES",
        )
        for external_key, counterparty_id, tax_code, amount_minor in (
            ("nonresident-service", nonresident["counterparty_id"], "nonresident_income", 200240),
            ("resident-service", professional["counterparty_id"], "professional_withholding", 8267),
        ):
            transaction = db.add_transaction(
                external_key=external_key,
                period_key="2026-Q2",
                transaction_date="2026-06-30",
                booking_date="2026-06-30",
                entry_type="expense",
                description=external_key,
                amount_minor=amount_minor,
                lifecycle_status="posted",
                counterparty_id=counterparty_id,
            )
            db.add_detailed_tax_treatment(
                transaction_id=transaction["transaction_id"],
                treatment_type="withholding",
                tax_code=tax_code,
                taxable_base_minor=amount_minor,
                withholding_minor=0,
            )

    assert main(["calculate", "--db", str(database), "--form", "216", "--year", "2026", "--quarter", "2"]) == 0
    modelo216 = json.loads(capsys.readouterr().out)
    assert modelo216["values"]["recipient_count"] == "1"
    assert modelo216["values"]["income_count"] == "1"
    assert modelo216["values"]["base"] == "2002.40"
    assert modelo216["values"]["withholding"] == "0.00"
    assert modelo216["values"]["negative_return"] == "yes"
    assert "does not establish treaty relief" in modelo216["warnings"][0]

    assert main(["calculate", "--db", str(database), "--form", "111", "--year", "2026", "--quarter", "2"]) == 0
    modelo111 = json.loads(capsys.readouterr().out)
    assert modelo111["values"]["recipient_count"] == "1"
    assert modelo111["values"]["base"] == "82.67"
    assert modelo111["values"]["withholding"] == "0.00"


def test_verify_history_uses_filed_reduction_and_keeps_adjustments_out_of_production(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        income = db.add_transaction(
            external_key="historical-income",
            period_key="2023-Q2",
            transaction_date="2023-06-30",
            booking_date="2023-06-30",
            entry_type="income",
            description="Historical income",
            amount_minor=100000,
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=income["transaction_id"],
            treatment_type="income",
            tax_code="historical_income",
            taxable_base_minor=100000,
            include_modelo130=True,
        )
        adjustment = db.add_transaction(
            external_key="annual-close-adjustment",
            period_key="2023-Q2",
            transaction_date="2023-06-30",
            booking_date="2023-06-30",
            entry_type="verify_history_adjustment",
            description="Documented annual close bridge",
            amount_minor=-10000,
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=adjustment["transaction_id"],
            treatment_type="adjustment",
            tax_code="verify_history_annual_close_adjustment",
            deductible_irpf_minor=-10000,
            include_modelo130=True,
        )
        db.create_filing_snapshot(
            "2023-Q2",
            status="baseline",
            filed_on="2023-07-20",
            payload={
                "form": "130",
                "baseline_kind": "xolo_submitted",
                "filed_values": {
                    "01": "1000.00",
                    "02": "-100.00",
                    "13": "100.00",
                    "19": "120.00",
                },
            },
        )

    assert main(
        [
            "calculate",
            "--db",
            str(database),
            "--form",
            "130",
            "--year",
            "2023",
            "--quarter",
            "2",
            "--mode",
            "verify_history",
            "--difficult-expenses-policy",
            "source_book_total",
        ]
    ) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["values"]["02"] == "-100.00"
    assert replay["values"]["13"] == "100.00"
    assert replay["values"]["19"] == "120.00"

    assert main(
        [
            "calculate",
            "--db",
            str(database),
            "--form",
            "130",
            "--year",
            "2023",
            "--quarter",
            "2",
            "--difficult-expenses-policy",
            "exclude_by_documented_decision",
            "--decision-ref",
            "fixture:no-difficult-expenses",
        ]
    ) == 0
    production = json.loads(capsys.readouterr().out)
    assert production["values"]["02"] == "0.00"
    assert production["values"]["19"] == "200.00"


def test_native_final_snapshot_drives_previous_positive_and_negative_carry(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        db.ensure_period("2026-Q1")
        db.close_period("2026-Q1", expected_row_version=1)
        db.create_filing_snapshot(
            "2026-Q1",
            status="final",
            filed_on="2026-04-20",
            payload={
                "form": "130",
                "values": {"07": "100.00", "15": "0.00", "19": "-50.00"},
            },
        )
        income = db.add_transaction(
            external_key="native-q2-income",
            period_key="2026-Q2",
            transaction_date="2026-04-10",
            booking_date="2026-04-10",
            entry_type="income",
            description="Issued invoice",
            amount_minor=100000,
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=income["transaction_id"],
            treatment_type="income",
            tax_code="outside_scope",
            taxable_base_minor=100000,
            include_modelo130=True,
        )

    assert main(["calculate", "--db", str(database), "--form", "130", "--year", "2026", "--quarter", "2"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["values"]["05"] == "100.00"
    assert result["values"]["15"] == "50.00"


def test_native_final_modelo303_snapshot_drives_vat_compensation(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        db.ensure_period("2026-Q2")
        db.close_period("2026-Q2", expected_row_version=1)
        db.create_filing_snapshot(
            "2026-Q2",
            status="final",
            filed_on="2026-07-20",
            payload={
                "form": "303",
                "values": {"110": "975.85", "78": "0.00", "87": "975.85", "72": "407.36"},
            },
        )
        db.create_filing_snapshot(
            "2026-Q2",
            status="baseline",
            filed_on="2026-07-20T12:00:00",
            payload={"form": "303", "filed_values": {"result": "-407.36"}},
        )
        expense = db.add_transaction(
            external_key="native-q3-vat-expense",
            period_key="2026-Q3",
            transaction_date="2026-07-10",
            booking_date="2026-07-10",
            entry_type="expense",
            description="Domestic expense",
            amount_minor=12100,
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=expense["transaction_id"],
            treatment_type="iva_input",
            tax_code="domestic_input",
            taxable_base_minor=10000,
            vat_minor=2100,
            deductible_vat_minor=2100,
            include_modelo303=True,
        )

    assert main(
        ["calculate", "--db", str(database), "--form", "303", "--year", "2026", "--quarter", "3"]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["values"]["110"] == "1383.21"
    assert result["values"]["87"] == "1383.21"
    assert result["values"]["72"] == "21.00"
    assert result["values"]["compensation_carryforward"] == "1404.21"


def test_production_cli_can_review_post_and_calculate_a_transaction(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    transaction_json = tmp_path / "transaction.json"
    treatment_json = tmp_path / "treatment.json"
    transaction_json.write_text(
        json.dumps(
            {
                "external_key": "CLI-INVOICE-1",
                "period_key": "2026-Q3",
                "transaction_date": "2026-07-10",
                "booking_date": "2026-07-10",
                "entry_type": "income",
                "description": "Issued invoice",
                "amount_minor": 100000,
            }
        ),
        encoding="utf-8",
    )

    assert main(["db", "init", "--db", str(database)]) == 0
    capsys.readouterr()
    assert main(["transactions", "add", "--db", str(database), "--input", str(transaction_json)]) == 0
    transaction = json.loads(capsys.readouterr().out)
    for lifecycle_status in ("extracted", "needs_review", "approved"):
        assert main(
            [
                "transactions",
                "transition",
                "--db",
                str(database),
                transaction["transaction_id"],
                "--to-status",
                lifecycle_status,
                "--expected-row-version",
                str(transaction["row_version"]),
            ]
        ) == 0
        transaction = json.loads(capsys.readouterr().out)

    treatment_json.write_text(
        json.dumps(
            {
                "transaction_id": transaction["transaction_id"],
                "treatment_type": "income",
                "tax_code": "outside_scope",
                "taxable_base_minor": 100000,
                "include_modelo130": True,
            }
        ),
        encoding="utf-8",
    )
    assert main(["tax-treatment", "add", "--db", str(database), "--input", str(treatment_json)]) == 0
    capsys.readouterr()
    assert main(
        [
            "transactions",
            "transition",
            "--db",
            str(database),
            transaction["transaction_id"],
            "--to-status",
            "posted",
            "--expected-row-version",
            str(transaction["row_version"]),
        ]
    ) == 0
    capsys.readouterr()

    assert main(["calculate", "--db", str(database), "--form", "130", "--year", "2026", "--quarter", "3"]) == 0
    calculation = json.loads(capsys.readouterr().out)
    assert calculation["values"]["01"] == "1000.00"


def test_sheet_apply_is_idempotent_and_blocks_same_version_edits(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    export_dir = tmp_path / "sheet"
    with initialize(database) as db:
        transaction = db.add_transaction(
            external_key="sheet-review",
            period_key="2026-Q3",
            transaction_date="2026-07-10",
            booking_date="2026-07-10",
            entry_type="expense",
            description="Review in Sheet",
            amount_minor=1000,
            lifecycle_status="needs_review",
        )

    assert main(["sheet", "export", "--db", str(database), "--out-dir", str(export_dir)]) == 0
    capsys.readouterr()
    csv_path = export_dir / "transactions.csv"
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    rows[0]["row_version"] = "2"
    rows[0]["lifecycle_status"] = "approved"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    command = ["sheet", "apply", "--db", str(database), "--tab", "transactions", "--remote-csv", str(csv_path)]
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert len(first["applied"]) == 1
    assert main(command) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["applied"] == []

    rows[0]["lifecycle_status"] = "posted"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    assert main(command) == 2
    conflict = json.loads(capsys.readouterr().out)
    assert conflict["conflicts"][0]["reason"] == "divergent_same_version"

    with initialize(database) as db:
        stored = db.connection.execute(
            "SELECT lifecycle_status, row_version FROM transactions WHERE transaction_id = ?",
            (transaction["transaction_id"],),
        ).fetchone()
        assert stored["lifecycle_status"] == "approved"
        assert stored["row_version"] == 2


def test_sheet_apply_updates_counterparty_tax_profile_atomically(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    export_dir = tmp_path / "sheet"
    with initialize(database) as db:
        counterparty = db.upsert_counterparty(
            external_key="intake-name:eniplenitudeiberiasl",
            display_name="Synthetic Party 005",
            country_code="ZZ",
        )

    assert main(["sheet", "export", "--db", str(database), "--out-dir", str(export_dir)]) == 0
    capsys.readouterr()
    csv_path = export_dir / "counterparties.csv"
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    rows[0].update(
        {
            "row_version": "2",
            "tax_id": "TEST-TAX-ID-005",
            "country_code": "ES",
            "vat_id": "ESTEST-TAX-ID-005",
            "legal_form": "legal_entity",
            "professional_supplier": "false",
            "retention_expected": "false",
        }
    )
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    command = [
        "sheet",
        "apply",
        "--db",
        str(database),
        "--tab",
        "counterparties",
        "--remote-csv",
        str(csv_path),
    ]
    assert main(command) == 0
    applied = json.loads(capsys.readouterr().out)
    assert [row["counterparty_id"] for row in applied["applied"]] == [
        counterparty["counterparty_id"]
    ]
    assert main(command) == 0
    assert json.loads(capsys.readouterr().out)["applied"] == []

    with initialize(database) as db:
        stored = db.connection.execute(
            "SELECT * FROM counterparties WHERE counterparty_id = ?",
            (counterparty["counterparty_id"],),
        ).fetchone()
        assert stored["row_version"] == 2
        assert stored["tax_id"] == "TEST-TAX-ID-005"
        assert stored["country_code"] == "ES"
        assert stored["vat_id"] == "ESTEST-TAX-ID-005"
        assert stored["legal_form"] == "legal_entity"
        assert stored["professional_supplier"] == 0
        assert stored["retention_expected"] == 0


def test_sheet_apply_preserves_legal_form_when_legacy_csv_omits_column(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    export_dir = tmp_path / "sheet"
    with initialize(database) as db:
        counterparty = db.upsert_counterparty(
            external_key="legacy-sheet-legal-form",
            display_name="Reviewed Foreign Company",
            country_code="US",
        )
        db.update_counterparty_review(
            counterparty["counterparty_id"],
            display_name=counterparty["display_name"],
            tax_id=None,
            country_code="US",
            vat_id=None,
            roi_status="unknown",
            professional_supplier=True,
            retention_expected=False,
            email=None,
            phone=None,
            legal_form="legal_entity",
            expected_row_version=counterparty["row_version"],
        )

    assert main(
        ["sheet", "export", "--db", str(database), "--out-dir", str(export_dir)]
    ) == 0
    capsys.readouterr()
    csv_path = export_dir / "counterparties.csv"
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = [
            name for name in list(rows[0]) if name != "legal_form"
        ]
    rows[0].pop("legal_form")
    rows[0]["row_version"] = "3"
    rows[0]["phone"] = "+1-555-0100"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    assert main(
        [
            "sheet",
            "apply",
            "--db",
            str(database),
            "--tab",
            "counterparties",
            "--remote-csv",
            str(csv_path),
        ]
    ) == 0
    capsys.readouterr()
    with initialize(database) as db:
        stored = db.connection.execute(
            "SELECT legal_form, phone FROM counterparties WHERE counterparty_id = ?",
            (counterparty["counterparty_id"],),
        ).fetchone()

    assert stored["legal_form"] == "legal_entity"
    assert stored["phone"] == "+1-555-0100"


def test_sheet_apply_can_reject_a_reviewed_document(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    export_dir = tmp_path / "sheet"
    evidence = tmp_path / "utility.pdf"
    evidence.write_bytes(b"utility evidence")
    with initialize(database) as db:
        document = db.upsert_document(
            external_key="utility-document",
            document_type="expense_invoice",
            document_number="UTILITY-1",
            issued_on="2026-07-13",
            period_key="2026-Q3",
            currency="EUR",
            total_minor=10837,
            lifecycle_status="extracted",
        )
        document = db.set_document_storage(
            document["document_id"],
            source_path=str(evidence),
            mime_type="application/pdf",
            expected_row_version=document["row_version"],
        )

    assert main(["sheet", "export", "--db", str(database), "--out-dir", str(export_dir)]) == 0
    capsys.readouterr()
    csv_path = export_dir / "inbox_review.csv"
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    rows[0]["row_version"] = str(document["row_version"] + 1)
    rows[0]["status"] = "rejected"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    command = [
        "sheet",
        "apply",
        "--db",
        str(database),
        "--tab",
        "inbox_review",
        "--remote-csv",
        str(csv_path),
    ]
    assert main(command) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["applied"][0]["lifecycle_status"] == "rejected"

    with initialize(database) as db:
        stored = db.connection.execute(
            "SELECT * FROM documents WHERE document_id = ?",
            (document["document_id"],),
        ).fetchone()
        assert stored["lifecycle_status"] == "rejected"
        assert stored["source_path"] == str(evidence)
        assert stored["row_version"] == document["row_version"] + 1


def test_sheet_apply_rolls_back_every_row_when_a_later_transition_fails(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    export_dir = tmp_path / "sheet"
    with initialize(database) as db:
        first = db.add_transaction(
            external_key="sheet-atomic-first",
            period_key="2026-Q3",
            transaction_date="2026-07-10",
            booking_date="2026-07-10",
            entry_type="expense",
            description="First reviewed row",
            amount_minor=1000,
            lifecycle_status="needs_review",
        )
        second = db.add_transaction(
            external_key="sheet-atomic-second",
            period_key="2026-Q3",
            transaction_date="2026-07-11",
            booking_date="2026-07-11",
            entry_type="expense",
            description="Invalid later row",
            amount_minor=2000,
            lifecycle_status="needs_review",
        )

    assert main(["sheet", "export", "--db", str(database), "--out-dir", str(export_dir)]) == 0
    capsys.readouterr()
    csv_path = export_dir / "transactions.csv"
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    by_uuid = {row["uuid"]: row for row in rows}
    by_uuid[first["transaction_id"]]["row_version"] = "2"
    by_uuid[first["transaction_id"]]["lifecycle_status"] = "approved"
    by_uuid[second["transaction_id"]]["row_version"] = "2"
    by_uuid[second["transaction_id"]]["lifecycle_status"] = "posted"
    rows = [by_uuid[first["transaction_id"]], by_uuid[second["transaction_id"]]]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    command = ["sheet", "apply", "--db", str(database), "--tab", "transactions", "--remote-csv", str(csv_path)]
    with pytest.raises(LifecycleError, match="needs_review -> posted"):
        main(command)

    with initialize(database) as db:
        stored = db.connection.execute(
            "SELECT transaction_id, lifecycle_status, row_version FROM transactions ORDER BY external_key"
        ).fetchall()
        assert [(row["lifecycle_status"], row["row_version"]) for row in stored] == [
            ("needs_review", 1),
            ("needs_review", 1),
        ]


def test_sheet_export_marks_asset_closed_from_amortization_period(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    export_dir = tmp_path / "sheet"
    with initialize(database) as db:
        asset = db.add_asset(
            asset_code="ASSET-CLOSED-AMORTIZATION",
            cost_minor=120000,
            currency="EUR",
            depreciation_method="linear",
            source_hash="asset-source",
        )
        entry = db.add_amortization_entry(
            asset_id=asset["asset_id"],
            period_key="2026-Q3",
            amount_minor=10000,
            source_hash="amortization-source",
        )
        db.connection.execute(
            "UPDATE periods SET status = 'closed' WHERE period_id = ?",
            (entry["period_id"],),
        )
        db.connection.commit()

    assert main(["sheet", "export", "--db", str(database), "--out-dir", str(export_dir)]) == 0
    capsys.readouterr()
    with (export_dir / "assets.csv").open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["uuid"] == asset["asset_id"]
    assert rows[0]["status"] == "closed"
