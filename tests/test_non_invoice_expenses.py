from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

import pytest

from autonomo_taxes.aeat_books import build_aeat_book_projection
from autonomo_taxes.cli import main
from autonomo_taxes.intake import archive_evidence
from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.non_invoice_expenses import (
    NonInvoiceExpenseError,
    NonInvoiceExpenseInput,
    record_non_invoice_expense,
)


def test_social_security_cli_creates_reviewed_unposted_g45_expense(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    evidence = tmp_path / "tgss-debit.txt"
    evidence.write_text(
        "TGSS debit receipt 2026-07-01 reference RETA-2026-07 amount EUR 370.59",
        encoding="utf-8",
    )
    with initialize(database) as db:
        _activity(db)
        db.upsert_counterparty(
            external_key="tgss",
            tax_id="Q2827003A",
            display_name="Synthetic Party 011",
            country_code="ES",
        )

    command = [
        "expense",
        "record",
        "--db",
        str(database),
        "--kind",
        "social-security",
        "--evidence",
        str(evidence),
        "--date",
        "2026-07-01",
        "--amount-eur",
        "370.59",
        "--deductible-eur",
        "370.59",
        "--reference",
        "RETA-2026-07",
        "--archive-root",
        str(tmp_path / "Evidence"),
    ]
    assert main(command) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["status"] == "created"
    assert output["transaction_lifecycle_status"] == "approved"
    assert output["aeat_invoice_type"] == "SF"
    assert output["aeat_expense_concept"] == "G45"
    assert output["evidence_file"] == evidence.name
    assert str(tmp_path) not in output["archive_file"]
    assert (tmp_path / "Evidence" / "2026-Q3" / "social_security_evidence" / output["archive_file"]).is_file()

    with initialize(database) as db:
        transaction = db.connection.execute(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (output["transaction_id"],),
        ).fetchone()
        treatment = db.connection.execute(
            "SELECT * FROM tax_treatments WHERE transaction_id = ?",
            (output["transaction_id"],),
        ).fetchone()
        document = db.connection.execute(
            "SELECT * FROM documents WHERE document_id = ?",
            (output["document_id"],),
        ).fetchone()
        assert transaction["lifecycle_status"] == "approved"
        assert transaction["amount_eur_minor"] == 37059
        assert document["lifecycle_status"] == "approved"
        assert treatment["deductible_irpf_minor"] == 37059
        assert treatment["taxable_base_minor"] == 0
        assert treatment["vat_minor"] == 0
        assert treatment["deductible_vat_minor"] == 0
        assert treatment["include_modelo130"] == 1
        assert treatment["include_modelo303"] == 0
        assert "Business purpose:" in treatment["notes"]
        attachment = db.connection.execute(
            """
            SELECT da.attachment_role, f.content_sha256, fr.provider_locator
            FROM document_attachments da
            JOIN files f ON f.file_id = da.file_id
            JOIN file_replicas fr ON fr.file_id = f.file_id
            WHERE da.document_id = ?
            """,
            (output["document_id"],),
        ).fetchone()
        assert attachment["attachment_role"] == "source"
        assert attachment["content_sha256"] == sha256(
            (tmp_path / "Evidence" / "2026-Q3" / "social_security_evidence" / output["archive_file"])
            .read_bytes()
        ).hexdigest()
        assert attachment["provider_locator"].startswith("2026-Q3/")

    assert main(["review", "list", "--db", str(database), "--period", "2026-Q3", "--ready-to-post"]) == 0
    queue = json.loads(capsys.readouterr().out)
    assert [row["review_id"] for row in queue] == [output["review_id"]]


def test_social_security_import_is_idempotent_and_rejects_changed_amount(
    tmp_path: Path,
    capsys,
) -> None:
    database, evidence = _social_security_setup(tmp_path)
    command = _social_security_command(database, evidence, tmp_path / "Evidence")

    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(command) == 0
    second = json.loads(capsys.readouterr().out)

    assert second["status"] == "already_recorded"
    assert second["transaction_id"] == first["transaction_id"]
    with initialize(database) as db:
        assert db.table_counts()["documents"] == 1
        assert db.table_counts()["transactions"] == 1
        assert db.table_counts()["tax_treatments"] == 1

    changed = list(command)
    changed[changed.index("--amount-eur") + 1] = "371.00"
    with pytest.raises(NonInvoiceExpenseError, match="conflicting values"):
        main(changed)


def test_partial_tgss_deduction_is_preserved_for_surcharge_evidence(tmp_path: Path) -> None:
    database, source = _social_security_setup(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    archived = archive_evidence(
        source,
        tmp_path / "Evidence",
        period_key="2026-Q3",
        evidence_kind="social_security_evidence",
        digest=digest,
    )
    request = _request(archived, digest, gross="104.33", deductible="86.94")

    with initialize(database) as db:
        result = record_non_invoice_expense(db, request)
        treatment = db.connection.execute(
            "SELECT * FROM tax_treatments WHERE transaction_id = ?",
            (result["transaction_id"],),
        ).fetchone()

    assert treatment["deductible_irpf_minor"] == 8694
    assert treatment["deductible_ratio"] == pytest.approx(
        float(Decimal("86.94") / Decimal("104.33"))
    )


def test_bank_fee_can_create_a_source_backed_foreign_counterparty(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    source = tmp_path / "bank-fee.txt"
    source.write_text("Revolut Bank UAB fee receipt 2026-07-02 EUR 3.50", encoding="utf-8")
    digest = sha256(source.read_bytes()).hexdigest()
    archived = archive_evidence(
        source,
        tmp_path / "Evidence",
        period_key="2026-Q3",
        evidence_kind="bank_fee_evidence",
        digest=digest,
    )
    with initialize(database) as db:
        _activity(db)
        result = record_non_invoice_expense(
            db,
            NonInvoiceExpenseInput(
                kind="bank-fee",
                evidence_sha256=digest,
                evidence_mime_type="text/plain",
                archived_path=archived,
                transaction_date=date(2026, 7, 2),
                gross_eur=Decimal("3.50"),
                deductible_eur=Decimal("3.50"),
                reference="BANK-FEE-2026-07-02",
                business_purpose="Fee on the account used for client receipts",
                description="Revolut business account fee",
                counterparty_name="Revolut Bank UAB",
                counterparty_country="LT",
                counterparty_identity_kind="vat_id",
                    counterparty_identifier="TEST-ID-001",
            ),
        )
        treatment = db.connection.execute(
            "SELECT * FROM tax_treatments WHERE transaction_id = ?",
            (result["transaction_id"],),
        ).fetchone()
        identity = db.connection.execute(
            "SELECT * FROM counterparty_identities WHERE counterparty_id = ?",
            (result["counterparty_id"],),
        ).fetchone()

    assert treatment["aeat_expense_concept"] == "G24"
    assert treatment["aeat_invoice_type"] == "SF"
    assert identity["aeat_id_type"] == "02"
    assert identity["country_code"] == "LT"
    assert identity["identifier"] == "TEST-ID-001"


def test_dry_run_does_not_copy_evidence_or_mutate_database(
    tmp_path: Path,
    capsys,
) -> None:
    database, evidence = _social_security_setup(tmp_path)
    archive_root = tmp_path / "Evidence"
    command = _social_security_command(database, evidence, archive_root) + ["--dry-run"]

    assert main(command) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["status"] == "dry_run"
    assert not archive_root.exists()
    with initialize(database) as db:
        assert db.table_counts()["documents"] == 0
        assert db.table_counts()["transactions"] == 0
        assert db.table_counts()["tax_treatments"] == 0
        assert db.connection.execute("SELECT COUNT(*) FROM periods").fetchone()[0] == 0


def test_validation_and_sql_failure_roll_back_every_ledger_row(tmp_path: Path) -> None:
    database, source = _social_security_setup(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    archived = archive_evidence(
        source,
        tmp_path / "Evidence",
        period_key="2026-Q3",
        evidence_kind="social_security_evidence",
        digest=digest,
    )
    request = _request(archived, digest)

    with initialize(database) as db:
        db.connection.execute(
            """
            CREATE TRIGGER fail_non_invoice_treatment
            BEFORE INSERT ON tax_treatments
            WHEN NEW.treatment_type = 'non_invoice_review'
            BEGIN
                SELECT RAISE(ABORT, 'forced treatment failure');
            END
            """
        )
        db.connection.commit()
        with pytest.raises(Exception, match="forced treatment failure"):
            record_non_invoice_expense(db, request)
        assert db.connection.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0] == 0
        assert db.table_counts()["documents"] == 0
        assert db.connection.execute("SELECT COUNT(*) FROM document_sources").fetchone()[0] == 0
        assert db.table_counts()["transactions"] == 0
        assert db.table_counts()["tax_treatments"] == 0


def test_record_rejects_invalid_amount_future_date_and_tampered_archive(tmp_path: Path) -> None:
    database, source = _social_security_setup(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    archived = archive_evidence(
        source,
        tmp_path / "Evidence",
        period_key="2026-Q3",
        evidence_kind="social_security_evidence",
        digest=digest,
    )
    request = _request(archived, digest)

    with initialize(database) as db:
        with pytest.raises(NonInvoiceExpenseError, match="between zero and the gross"):
            record_non_invoice_expense(
                db,
                replace(request, deductible_eur=Decimal("500.00")),
            )
        with pytest.raises(NonInvoiceExpenseError, match="Future-dated"):
            record_non_invoice_expense(
                db,
                replace(request, transaction_date=date(2099, 1, 1), period_key="2099-Q1"),
            )
        archived.write_text("tampered", encoding="utf-8")
        with pytest.raises(NonInvoiceExpenseError, match="SHA-256"):
            record_non_invoice_expense(db, request)
        assert db.table_counts()["transactions"] == 0


def test_posted_social_security_projects_to_aeat_expense_book(tmp_path: Path) -> None:
    database, source = _social_security_setup(tmp_path)
    digest = sha256(source.read_bytes()).hexdigest()
    archived = archive_evidence(
        source,
        tmp_path / "Evidence",
        period_key="2026-Q3",
        evidence_kind="social_security_evidence",
        digest=digest,
    )
    with initialize(database) as db:
        result = record_non_invoice_expense(db, _request(archived, digest))
        db.transition_transaction(
            result["transaction_id"],
            lifecycle_status="posted",
            expected_row_version=result["transaction_row_version"],
        )
        repeated = record_non_invoice_expense(db, _request(archived, digest))
        projection = build_aeat_book_projection(db, period_key="2026-Q3")

    assert repeated["status"] == "already_recorded"
    assert repeated["transaction_lifecycle_status"] == "posted"
    assert projection["counts"]["blockers"] == 0
    assert projection["counts"]["expense"] == 1
    assert projection["expense_rows"][0]["tipo_factura"] == "SF"
    assert projection["expense_rows"][0]["concepto_gasto"] == "G45"
    assert projection["expense_rows"][0]["gasto_deducible_eur"] == "370.59"
    assert projection["expense_rows"][0]["total_factura_eur"] == "370.59"
    assert projection["expense_rows"][0]["base_imponible_eur"] == "370.59"
    assert projection["expense_rows"][0]["tipo_iva_percent"] == ""
    assert projection["expense_rows"][0]["cuota_iva_soportado_eur"] == ""


def _social_security_setup(tmp_path: Path) -> tuple[Path, Path]:
    database = tmp_path / "ledger.sqlite"
    source = tmp_path / "tgss-debit.txt"
    source.write_text(
        "TGSS debit receipt 2026-07-01 reference RETA-2026-07 amount EUR 370.59",
        encoding="utf-8",
    )
    with initialize(database) as db:
        _activity(db)
        db.upsert_counterparty(
            external_key="tgss",
            tax_id="Q2827003A",
            display_name="Synthetic Party 011",
            country_code="ES",
        )
    return database, source


def _activity(db):
    profile = db.upsert_taxpayer_profile(
        tax_id="X0000000A",
        full_name="Example Taxpayer",
        source_hash="profile-source",
    )
    return db.upsert_business_activity(
        taxpayer_profile_id=profile["taxpayer_profile_id"],
        activity_key="software-development",
        aeat_activity_code="A",
        aeat_activity_type="05",
        iae_section="2",
        iae_group_epigraph="763",
        description="Software development",
        starts_on="2023-05-31",
        source_reference="Modelo 036",
        source_hash="activity-source",
    )


def _social_security_command(database: Path, evidence: Path, archive_root: Path) -> list[str]:
    return [
        "expense",
        "record",
        "--db",
        str(database),
        "--kind",
        "social-security",
        "--evidence",
        str(evidence),
        "--date",
        "2026-07-01",
        "--amount-eur",
        "370.59",
        "--deductible-eur",
        "370.59",
        "--reference",
        "RETA-2026-07",
        "--archive-root",
        str(archive_root),
    ]


def _request(
    archived: Path,
    digest: str,
    *,
    gross: str = "370.59",
    deductible: str = "370.59",
) -> NonInvoiceExpenseInput:
    return NonInvoiceExpenseInput(
        kind="social-security",
        evidence_sha256=digest,
        evidence_mime_type="text/plain",
        archived_path=archived,
        transaction_date=date(2026, 7, 1),
        gross_eur=Decimal(gross),
        deductible_eur=Decimal(deductible),
        reference="RETA-2026-07",
        business_purpose="Mandatory contribution for the registered activity",
        description="Autonomo social security contribution",
    )
