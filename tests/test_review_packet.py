from __future__ import annotations

from datetime import date
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import initialize, open as open_ledger_db
from autonomo_taxes.review_packet import ReviewPacketError, prepare_review_work_item


def _period_key(value: date) -> str:
    return f"{value.year}-Q{((value.month - 1) // 3) + 1}"


def _invoice_fixture(
    tmp_path: Path,
    *,
    entry_type: str = "expense",
    currency: str = "EUR",
    country_code: str = "ES",
    transaction_date: str | None = None,
    display_name: str | None = None,
) -> dict[str, object]:
    database = tmp_path / "ledger.sqlite"
    source = tmp_path / "private" / "supplier-invoice.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"immutable invoice fixture")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    tax_date = transaction_date or date.today().isoformat()
    period = _period_key(date.fromisoformat(tax_date))
    original_minor = 12100 if entry_type == "expense" else 50000
    with initialize(database) as db:
        counterparty = db.upsert_counterparty(
            external_key="review-packet-counterparty",
            display_name=display_name
            or ("Supplier Example SL" if entry_type == "expense" else "Foreign Customer Inc"),
            country_code=country_code,
        )
        document = db.upsert_document(
            external_key=f"sha256:{digest}",
            counterparty_id=counterparty["counterparty_id"],
            document_type="expense_invoice" if entry_type == "expense" else "income_invoice",
            document_number="INV-REVIEW-1",
            issued_on=tax_date,
            period_key=period,
            currency=currency,
            total_minor=original_minor,
            lifecycle_status="needs_review",
            source_hash=digest,
        )
        document = db.set_document_storage(
            document["document_id"],
            source_path=str(source),
            mime_type="application/pdf",
            expected_row_version=document["row_version"],
        )
        transaction = db.add_transaction(
            external_key="review-packet-transaction",
            period_key=period,
            transaction_date=tax_date,
            booking_date=tax_date,
            entry_type=entry_type,
            description="Reviewed invoice fixture",
            amount_minor=original_minor,
            currency=currency,
            amount_original_minor=original_minor,
            original_currency=currency,
            amount_eur_minor=original_minor if currency == "EUR" else None,
            direction="debit" if entry_type == "expense" else "credit",
            lifecycle_status="needs_review",
            document_id=document["document_id"],
            counterparty_id=counterparty["counterparty_id"],
            source_hash=hashlib.sha256(b"review-packet-transaction").hexdigest(),
        )
        treatment = db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="invoice_review",
            tax_code="unknown",
            jurisdiction="ES",
            taxable_base_minor=10000 if entry_type == "expense" else None,
            vat_minor=2100 if entry_type == "expense" else None,
            notes="Extraction candidate only",
            source_hash=hashlib.sha256(b"review-packet-treatment").hexdigest(),
        )
        issues = [
            db.add_validation_issue(
                period_key=period,
                issue_code="transaction_tax_review",
                severity="warning",
                message="Review IRPF and IVA treatment",
                subject_table="transactions",
                subject_id=transaction["transaction_id"],
                blocking=True,
                source_hash=digest,
            )
        ]
        if country_code == "ZZ":
            issues.append(
                db.add_validation_issue(
                    period_key=period,
                    issue_code="counterparty_tax_profile_review",
                    severity="warning",
                    message="Review supplier country and tax identity",
                    subject_table="counterparties",
                    subject_id=counterparty["counterparty_id"],
                    blocking=True,
                    source_hash=digest,
                )
            )
    return {
        "database": database,
        "source": source,
        "period": period,
        "document_id": document["document_id"],
        "transaction_id": transaction["transaction_id"],
        "counterparty_id": counterparty["counterparty_id"],
        "treatment_id": treatment["treatment_id"],
        "issue_ids": [row["validation_issue_id"] for row in issues],
    }


def _prepare(fixture: dict[str, object], target: Path, capsys) -> dict[str, object]:
    assert main(
        [
            "review",
            "prepare",
            "--db",
            str(fixture["database"]),
            f"transaction:{fixture['transaction_id']}",
            "--out",
            str(target),
        ]
    ) == 0
    capsys.readouterr()
    return json.loads(target.read_text(encoding="utf-8"))


def _resolve_packet_issues(packet: dict[str, object]) -> None:
    decision = packet["decision"]
    assert isinstance(decision, dict)
    resolutions = decision["issue_resolutions"]
    assert isinstance(resolutions, list)
    for resolution in resolutions:
        resolution["action"] = "resolve"
        resolution["reason"] = "Reviewed against the immutable source invoice"


def test_work_item_exposes_structured_supported_requirements(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path)
    with open_ledger_db(fixture["database"], read_only=True) as db:
        work_item = prepare_review_work_item(
            db, f"transaction:{fixture['transaction_id']}"
        )

    assert work_item["supported"] is True
    assert [row["code"] for row in work_item["requirements"]] == [
        "confirm_business_purpose",
        "confirm_irpf_deductible_amount",
        "confirm_iva_treatment",
    ]
    assert all(row["supported"] is True for row in work_item["requirements"])


def _approve_expense(packet: dict[str, object], *, update_country: bool = False) -> None:
    decision = packet["decision"]
    assert isinstance(decision, dict)
    decision.update(
        {
            "outcome": "approve",
            "reason": "Valid business software expense",
            "business_purpose": "Software used exclusively for the professional activity",
            "document_valid": True,
            "asset_decision": "current_expense",
            "asset_id": None,
            "counterparty_changes": (
                {
                    "country_code": "ES",
                    "tax_id": "B12345678",
                    "vat_id": "ESB12345678",
                    "roi_status": "not_registered",
                    "legal_form": "legal_entity",
                    "professional_supplier": False,
                    "retention_expected": False,
                }
                if update_country
                else {}
            ),
            "tax_treatment": {
                "tax_code": "domestic_input",
                "aeat_invoice_type": "F1",
                "aeat_operation_key": "01",
                "aeat_operation_qualification": "S1",
                "aeat_exemption_code": None,
                "aeat_reverse_charge": False,
                "vat_investment_good": False,
                "aeat_expense_concept": "G03",
                "rate_basis_points": 2100,
                "deductible_ratio": 1.0,
                "taxable_base_minor": 10000,
                "vat_minor": 2100,
                "deductible_irpf_minor": 10000,
                "deductible_vat_minor": 2100,
                "withholding_minor": 0,
                "include_modelo130": True,
                "include_modelo303": True,
                "include_modelo347": False,
                "rule_version_id": None,
                "notes": "Reviewed domestic current expense; 100% business use",
            },
        }
    )
    _resolve_packet_issues(packet)


def _approve_income(packet: dict[str, object]) -> None:
    decision = packet["decision"]
    assert isinstance(decision, dict)
    transaction = packet["state"]["transaction"]
    amount_eur = transaction["amount_eur_minor"]
    decision.update(
        {
            "outcome": "approve",
            "reason": "Issued invoice matches the rendered and sent evidence",
            "business_purpose": "Software development services supplied to the customer",
            "document_valid": True,
            "asset_decision": "not_applicable",
            "asset_id": None,
            "counterparty_changes": {},
            "tax_treatment": {
                "tax_code": "outside_scope",
                "aeat_invoice_type": "F1",
                "aeat_operation_key": "01",
                "aeat_operation_qualification": "N2",
                "aeat_exemption_code": None,
                "aeat_reverse_charge": None,
                "vat_investment_good": None,
                "aeat_expense_concept": None,
                "rate_basis_points": None,
                "deductible_ratio": None,
                "taxable_base_minor": amount_eur,
                "vat_minor": 0,
                "deductible_irpf_minor": 0,
                "deductible_vat_minor": 0,
                "withholding_minor": 0,
                "include_modelo130": True,
                "include_modelo303": True,
                "include_modelo347": False,
                "rule_version_id": None,
                "notes": "Reviewed non-EU B2B service income; place of supply outside Spain",
            },
        }
    )
    _resolve_packet_issues(packet)


def _write_packet(path: Path, packet: dict[str, object]) -> None:
    path.write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")


def test_prepare_is_deterministic_and_does_not_expose_paths(
    tmp_path: Path,
    capsys,
) -> None:
    fixture = _invoice_fixture(tmp_path)
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    packet = _prepare(fixture, first, capsys)
    _prepare(fixture, second, capsys)

    assert first.read_bytes() == second.read_bytes()
    serialized = first.read_text(encoding="utf-8")
    assert str(tmp_path) not in serialized
    assert "supplier-invoice.pdf" in serialized
    assert packet["privacy"] == "private_ephemeral_do_not_commit"
    assert packet["decision"]["outcome"] is None


def test_apply_dry_run_then_approves_without_posting(
    tmp_path: Path,
    capsys,
) -> None:
    fixture = _invoice_fixture(tmp_path, country_code="ZZ")
    packet_path = tmp_path / "decision.json"
    packet = _prepare(fixture, packet_path, capsys)
    _approve_expense(packet, update_country=True)
    _write_packet(packet_path, packet)

    assert main(
        [
            "review",
            "apply",
            "--db",
            str(fixture["database"]),
            "--input",
            str(packet_path),
            "--dry-run",
        ]
    ) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["dry_run"] is True
    assert dry_run["transaction"]["lifecycle_status"] == "approved"
    with initialize(fixture["database"]) as db:
        assert db.list_transactions(period_key=str(fixture["period"]))[0]["lifecycle_status"] == "needs_review"
        assert all(row["issue_status"] == "open" for row in db.list_issues(period_key=str(fixture["period"])))

    assert main(
        [
            "review",
            "apply",
            "--db",
            str(fixture["database"]),
            "--input",
            str(packet_path),
        ]
    ) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["posted"] is False
    assert applied["document"]["lifecycle_status"] == "approved"
    assert applied["transaction"]["lifecycle_status"] == "approved"
    with initialize(fixture["database"]) as db:
        treatment = dict(
            db.connection.execute(
                "SELECT * FROM tax_treatments WHERE treatment_id = ?",
                (fixture["treatment_id"],),
            ).fetchone()
        )
        counterparty = dict(
            db.connection.execute(
                "SELECT * FROM counterparties WHERE counterparty_id = ?",
                (fixture["counterparty_id"],),
            ).fetchone()
        )
        assert treatment["tax_code"] == "domestic_input"
        assert treatment["deductible_irpf_minor"] == 10000
        assert "Review reason: Valid business software expense" in treatment["notes"]
        assert "Business purpose: Software used exclusively" in treatment["notes"]
        assert counterparty["country_code"] == "ES"
        assert counterparty["legal_form"] == "legal_entity"
        assert not db.list_issues(period_key=str(fixture["period"]))

    assert main(
        [
            "review",
            "list",
            "--db",
            str(fixture["database"]),
            "--period",
            str(fixture["period"]),
            "--ready-to-post",
        ]
    ) == 0
    ready = json.loads(capsys.readouterr().out)
    assert [row["review_id"] for row in ready] == [
        f"transaction:{fixture['transaction_id']}"
    ]


def test_review_apply_accepts_packet_from_stdin(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _invoice_fixture(tmp_path, country_code="ZZ")
    packet = _prepare(fixture, tmp_path / "decision.json", capsys)
    _approve_expense(packet, update_country=True)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(packet)))

    assert main(
        [
            "review",
            "apply",
            "--db",
            str(fixture["database"]),
            "--input",
            "-",
            "--dry-run",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is True
    assert result["posted"] is False


def test_review_packet_counterparty_update_refreshes_paid_foreign_irnr_review(
    tmp_path: Path,
    capsys,
) -> None:
    fixture = _invoice_fixture(
        tmp_path,
        country_code="ZZ",
        transaction_date="2026-07-15",
    )
    with initialize(fixture["database"]) as db:
        db.add_payment(
            transaction_id=str(fixture["transaction_id"]),
            paid_on="2026-07-20",
            amount_minor=12100,
            currency="EUR",
            source_hash=hashlib.sha256(b"paid-foreign-professional").hexdigest(),
            match_status="exact",
        )

    packet_path = tmp_path / "foreign-professional-decision.json"
    packet = _prepare(fixture, packet_path, capsys)
    _approve_expense(packet)
    decision = packet["decision"]
    assert isinstance(decision, dict)
    decision["counterparty_changes"] = {
        "country_code": "GE",
        "tax_id": None,
        "vat_id": None,
        "roi_status": "not_registered",
        "legal_form": "individual",
        "professional_supplier": True,
        "retention_expected": False,
    }
    _write_packet(packet_path, packet)

    assert main(
        [
            "review",
            "apply",
            "--db",
            str(fixture["database"]),
            "--input",
            str(packet_path),
        ]
    ) == 0
    capsys.readouterr()

    with initialize(fixture["database"]) as db:
        issues = db.list_issues(period_key="2026-Q3")
        transaction = db.connection.execute(
            "SELECT lifecycle_status FROM transactions WHERE transaction_id = ?",
            (fixture["transaction_id"],),
        ).fetchone()

    assert transaction["lifecycle_status"] == "approved"
    assert [row["issue_code"] for row in issues] == [
        "nonresident_professional_irnr_review"
    ]
    assert issues[0]["subject_id"] == fixture["transaction_id"]
    assert issues[0]["blocking"] == 1


def test_personal_invoice_is_rejected_without_tax_classification(
    tmp_path: Path,
    capsys,
) -> None:
    fixture = _invoice_fixture(tmp_path)
    packet_path = tmp_path / "reject.json"
    packet = _prepare(fixture, packet_path, capsys)
    decision = packet["decision"]
    decision.update(
        {
            "outcome": "reject",
            "reason": "Personal purchase unrelated to the professional activity",
            "document_valid": True,
        }
    )
    _resolve_packet_issues(packet)
    _write_packet(packet_path, packet)

    assert main(
        [
            "review",
            "apply",
            "--db",
            str(fixture["database"]),
            "--input",
            str(packet_path),
        ]
    ) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["document"]["lifecycle_status"] == "rejected"
    assert applied["transaction"]["lifecycle_status"] == "rejected"
    with initialize(fixture["database"]) as db:
        treatment = db.connection.execute(
            "SELECT tax_code, deductible_irpf_minor, notes FROM tax_treatments WHERE treatment_id = ?",
            (fixture["treatment_id"],),
        ).fetchone()
        assert treatment["tax_code"] == "unknown"
        assert treatment["deductible_irpf_minor"] is None
        assert "Review outcome: reject" in treatment["notes"]
        assert "Document valid: true" in treatment["notes"]
        assert "Personal purchase unrelated" in treatment["notes"]


def test_modified_archived_evidence_blocks_apply(
    tmp_path: Path,
    capsys,
) -> None:
    fixture = _invoice_fixture(tmp_path)
    packet_path = tmp_path / "tampered-evidence.json"
    packet = _prepare(fixture, packet_path, capsys)
    _approve_expense(packet)
    _write_packet(packet_path, packet)
    source = fixture["source"]
    assert isinstance(source, Path)
    source.write_bytes(b"modified after review preparation")

    with pytest.raises(ReviewPacketError, match="hash no longer matches"):
        main(
            [
                "review",
                "apply",
                "--db",
                str(fixture["database"]),
                "--input",
                str(packet_path),
            ]
        )
    capsys.readouterr()
    with initialize(fixture["database"]) as db:
        transaction = db.connection.execute(
            "SELECT lifecycle_status FROM transactions WHERE transaction_id = ?",
            (fixture["transaction_id"],),
        ).fetchone()
        assert transaction["lifecycle_status"] == "needs_review"


def test_sheet_style_concurrent_treatment_edit_makes_packet_stale(
    tmp_path: Path,
    capsys,
) -> None:
    fixture = _invoice_fixture(tmp_path)
    packet_path = tmp_path / "stale.json"
    packet = _prepare(fixture, packet_path, capsys)
    _approve_expense(packet)
    _write_packet(packet_path, packet)
    with initialize(fixture["database"]) as db:
        current = dict(
            db.connection.execute(
                "SELECT * FROM tax_treatments WHERE treatment_id = ?",
                (fixture["treatment_id"],),
            ).fetchone()
        )
        db.add_detailed_tax_treatment(
            transaction_id=str(fixture["transaction_id"]),
            treatment_type="invoice_review",
            jurisdiction="ES",
            tax_code="unknown",
            taxable_base_minor=10000,
            vat_minor=2100,
            notes="Concurrent Sheet review edit",
            expected_row_version=current["row_version"],
        )

    with pytest.raises(ReviewPacketError, match="stale"):
        main(
            [
                "review",
                "apply",
                "--db",
                str(fixture["database"]),
                "--input",
                str(packet_path),
            ]
        )
    capsys.readouterr()
    with initialize(fixture["database"]) as db:
        transaction = db.connection.execute(
            "SELECT lifecycle_status FROM transactions WHERE transaction_id = ?",
            (fixture["transaction_id"],),
        ).fetchone()
        assert transaction["lifecycle_status"] == "needs_review"


def test_sql_failure_rolls_back_treatment_issues_and_document(
    tmp_path: Path,
    capsys,
) -> None:
    fixture = _invoice_fixture(tmp_path)
    packet_path = tmp_path / "rollback.json"
    packet = _prepare(fixture, packet_path, capsys)
    _approve_expense(packet)
    _write_packet(packet_path, packet)
    with initialize(fixture["database"]) as db:
        db.connection.execute(
            """
            CREATE TRIGGER force_review_failure
            BEFORE UPDATE OF lifecycle_status ON transactions
            BEGIN
                SELECT RAISE(ABORT, 'forced review failure');
            END
            """
        )
        db.connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced review failure"):
        main(
            [
                "review",
                "apply",
                "--db",
                str(fixture["database"]),
                "--input",
                str(packet_path),
            ]
        )
    capsys.readouterr()
    with initialize(fixture["database"]) as db:
        treatment = db.connection.execute(
            "SELECT tax_code, row_version FROM tax_treatments WHERE treatment_id = ?",
            (fixture["treatment_id"],),
        ).fetchone()
        document = db.connection.execute(
            "SELECT lifecycle_status FROM documents WHERE document_id = ?",
            (fixture["document_id"],),
        ).fetchone()
        issue = db.connection.execute(
            "SELECT issue_status FROM validation_issues WHERE validation_issue_id = ?",
            (fixture["issue_ids"][0],),
        ).fetchone()
        assert treatment["tax_code"] == "unknown"
        assert treatment["row_version"] == 1
        assert document["lifecycle_status"] == "needs_review"
        assert issue["issue_status"] == "open"


def test_foreign_currency_requires_sourced_fx_before_approval(
    tmp_path: Path,
    capsys,
) -> None:
    fixture = _invoice_fixture(
        tmp_path,
        entry_type="income",
        currency="USD",
        country_code="US",
    )
    packet_path = tmp_path / "usd.json"
    packet = _prepare(fixture, packet_path, capsys)
    _approve_income(packet)
    _write_packet(packet_path, packet)
    with pytest.raises(ReviewPacketError, match="sourced EUR conversion"):
        main(
            [
                "review",
                "apply",
                "--db",
                str(fixture["database"]),
                "--input",
                str(packet_path),
            ]
        )
    capsys.readouterr()

    with initialize(fixture["database"]) as db:
        rate = db.add_fx_rate(
            rate_date=date.today().isoformat(),
            base_currency="USD",
            quote_currency="EUR",
            rate="0.90",
            rate_source="banco_de_espana",
            source_hash=hashlib.sha256(b"official-rate-evidence").hexdigest(),
        )
        transaction = db.connection.execute(
            "SELECT row_version FROM transactions WHERE transaction_id = ?",
            (fixture["transaction_id"],),
        ).fetchone()
        db.apply_transaction_fx(
            str(fixture["transaction_id"]),
            fx_rate_id=rate["fx_rate_id"],
            expected_row_version=transaction["row_version"],
        )
    packet = _prepare(fixture, packet_path, capsys)
    _approve_income(packet)
    _write_packet(packet_path, packet)
    assert main(
        [
            "review",
            "apply",
            "--db",
            str(fixture["database"]),
            "--input",
            str(packet_path),
        ]
    ) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["transaction"]["lifecycle_status"] == "approved"


def test_income_rejects_input_tax_code_and_expense_deductions(
    tmp_path: Path,
    capsys,
) -> None:
    fixture = _invoice_fixture(
        tmp_path,
        entry_type="income",
        country_code="US",
    )
    packet_path = tmp_path / "invalid-income.json"
    packet = _prepare(fixture, packet_path, capsys)
    _approve_income(packet)
    treatment = packet["decision"]["tax_treatment"]
    treatment["tax_code"] = "domestic_input"
    treatment["deductible_irpf_minor"] = 100
    _write_packet(packet_path, packet)

    with pytest.raises(ReviewPacketError, match="not valid for income"):
        main(
            [
                "review",
                "apply",
                "--db",
                str(fixture["database"]),
                "--input",
                str(packet_path),
            ]
        )
    capsys.readouterr()


def _oss_supplier_changes(**overrides: object) -> dict[str, object]:
    return {
        "country_code": "US",
        "tax_id": None,
        "vat_id": "EU123456789",
        "roi_status": "not_registered",
        "legal_form": "legal_entity",
        "professional_supplier": False,
        "retention_expected": False,
        **overrides,
    }


def _apply(fixture: dict[str, object], packet_path: Path) -> int:
    return main(
        [
            "review",
            "apply",
            "--db",
            str(fixture["database"]),
            "--input",
            str(packet_path),
        ]
    )


def test_oss_non_union_supplier_is_not_an_intra_community_acquisition(
    tmp_path: Path,
    capsys,
) -> None:
    fixture = _invoice_fixture(
        tmp_path,
        country_code="US",
        display_name="Synthetic OSS Supplier",
    )
    packet_path = tmp_path / "oss-supplier.json"
    packet = _prepare(fixture, packet_path, capsys)
    _approve_expense(packet)
    packet["decision"]["counterparty_changes"] = _oss_supplier_changes()
    treatment = packet["decision"]["tax_treatment"]
    treatment.update(
        {"tax_code": "eu_service_expense", "aeat_operation_key": "09", "aeat_reverse_charge": True}
    )
    _write_packet(packet_path, packet)

    with pytest.raises(ReviewPacketError, match="OSS non-Union .* use non_eu_service_expense"):
        _apply(fixture, packet_path)
    capsys.readouterr()

    treatment["tax_code"] = "non_eu_service_expense"
    _write_packet(packet_path, packet)
    assert _apply(fixture, packet_path) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["transaction"]["lifecycle_status"] == "approved"
    with initialize(fixture["database"]) as db:
        stored = db.connection.execute(
            """
            SELECT tt.tax_code, c.vat_id, c.country_code
            FROM tax_treatments tt
            JOIN transactions t ON t.transaction_id = tt.transaction_id
            JOIN counterparties c ON c.counterparty_id = t.counterparty_id
            WHERE tt.treatment_id = ?
            """,
            (fixture["treatment_id"],),
        ).fetchone()
        assert tuple(stored) == ("non_eu_service_expense", "EU123456789", "US")


def test_oss_identifier_is_neither_an_eu_country_nor_roi_registered(
    tmp_path: Path,
    capsys,
) -> None:
    fixture = _invoice_fixture(
        tmp_path,
        country_code="US",
        display_name="Synthetic OSS Supplier",
    )
    packet_path = tmp_path / "oss-identity.json"
    packet = _prepare(fixture, packet_path, capsys)
    _approve_expense(packet)

    packet["decision"]["counterparty_changes"] = _oss_supplier_changes(country_code="EU")
    _write_packet(packet_path, packet)
    with pytest.raises(ReviewPacketError, match="EU is the OSS non-Union prefix, not a country"):
        _apply(fixture, packet_path)

    packet["decision"]["counterparty_changes"] = _oss_supplier_changes(roi_status="registered")
    _write_packet(packet_path, packet)
    with pytest.raises(ReviewPacketError, match="cannot be registered in ROI/VIES"):
        _apply(fixture, packet_path)
    capsys.readouterr()


def test_future_closed_asset_and_unknown_enum_decisions_fail_closed(
    tmp_path: Path,
    capsys,
) -> None:
    future = _invoice_fixture(tmp_path / "future", transaction_date="2099-09-30")
    future_path = tmp_path / "future.json"
    future_packet = _prepare(future, future_path, capsys)
    _approve_expense(future_packet)
    _write_packet(future_path, future_packet)
    with pytest.raises(ReviewPacketError, match="future-dated"):
        main(
            ["review", "apply", "--db", str(future["database"]), "--input", str(future_path)]
        )
    capsys.readouterr()

    closed = _invoice_fixture(tmp_path / "closed")
    with initialize(closed["database"]) as db:
        db.connection.execute(
            "UPDATE periods SET status = 'closed', row_version = row_version + 1 WHERE period_key = ?",
            (closed["period"],),
        )
        db.connection.commit()
    closed_path = tmp_path / "closed.json"
    closed_packet = _prepare(closed, closed_path, capsys)
    _approve_expense(closed_packet)
    _write_packet(closed_path, closed_packet)
    with pytest.raises(ReviewPacketError, match="is not open"):
        main(
            ["review", "apply", "--db", str(closed["database"]), "--input", str(closed_path)]
        )
    capsys.readouterr()

    asset = _invoice_fixture(tmp_path / "asset")
    asset_path = tmp_path / "asset.json"
    asset_packet = _prepare(asset, asset_path, capsys)
    _approve_expense(asset_packet)
    asset_packet["decision"]["asset_decision"] = "asset"
    asset_packet["decision"]["asset_id"] = "missing-asset"
    _write_packet(asset_path, asset_packet)
    with pytest.raises(ReviewPacketError, match="linked, already reviewed asset_id"):
        main(
            ["review", "apply", "--db", str(asset["database"]), "--input", str(asset_path)]
        )
    capsys.readouterr()

    double_deduction = _invoice_fixture(tmp_path / "asset-double-deduction")
    with initialize(double_deduction["database"]) as db:
        linked_asset = db.add_asset(
            asset_code="REVIEW-ASSET-1",
            cost_minor=12100,
            currency="EUR",
            depreciation_method="straight_line",
            source_hash=hashlib.sha256(b"review-asset").hexdigest(),
            document_id=str(double_deduction["document_id"]),
            acquisition_transaction_id=str(double_deduction["transaction_id"]),
            placed_in_service_on=date.today().isoformat(),
            useful_life_months=48,
            amortizable_base_minor=10000,
            iva_treatment="fully_deductible",
            business_use_ratio=1.0,
            annual_rate_basis_points=2500,
        )
    double_path = tmp_path / "asset-double-deduction.json"
    double_packet = _prepare(double_deduction, double_path, capsys)
    _approve_expense(double_packet)
    double_packet["decision"]["asset_decision"] = "asset"
    double_packet["decision"]["asset_id"] = linked_asset["asset_id"]
    _write_packet(double_path, double_packet)
    with pytest.raises(ReviewPacketError, match="cannot also be deducted"):
        main(
            [
                "review",
                "apply",
                "--db",
                str(double_deduction["database"]),
                "--input",
                str(double_path),
            ]
        )
    capsys.readouterr()

    invalid = _invoice_fixture(tmp_path / "enum")
    invalid_path = tmp_path / "enum.json"
    invalid_packet = _prepare(invalid, invalid_path, capsys)
    _approve_expense(invalid_packet)
    invalid_packet["decision"]["tax_treatment"]["tax_code"] = "future_unknown_code"
    _write_packet(invalid_path, invalid_packet)
    with pytest.raises(ReviewPacketError, match="Unsupported production tax_code"):
        main(
            ["review", "apply", "--db", str(invalid["database"]), "--input", str(invalid_path)]
        )


def test_reapplying_committed_packet_is_stale(tmp_path: Path, capsys) -> None:
    fixture = _invoice_fixture(tmp_path)
    packet_path = tmp_path / "once.json"
    packet = _prepare(fixture, packet_path, capsys)
    _approve_expense(packet)
    _write_packet(packet_path, packet)
    command = [
        "review",
        "apply",
        "--db",
        str(fixture["database"]),
        "--input",
        str(packet_path),
    ]
    assert main(command) == 0
    capsys.readouterr()
    with pytest.raises(ReviewPacketError, match="stale"):
        main(command)
