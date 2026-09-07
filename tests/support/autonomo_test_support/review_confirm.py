"""Reusable synthetic fixtures shared by backend test suites."""

from __future__ import annotations
from datetime import date, timedelta
import hashlib
from decimal import Decimal
from pathlib import Path
import pytest
from autonomo_taxes.ledger_db import initialize, open as open_ledger_db
from autonomo_taxes.fx_reference import ECBRateObservation
from autonomo_taxes.review_packet import (
    ReviewPacketError,
    build_fx_suggestion,
    classify_review_packet_failure,
    confirm_review_packet,
    prepare_review_work_item,
)

def _period_key(value: date) -> str:
    return f"{value.year}-Q{((value.month - 1) // 3) + 1}"

def _invoice_fixture(
    tmp_path: Path,
    *,
    currency: str = "EUR",
    transaction_date: str | None = None,
) -> dict[str, object]:
    database = tmp_path / "ledger.sqlite"
    source = tmp_path / "private" / "supplier-invoice.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"immutable invoice fixture")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    tax_date = transaction_date or date.today().isoformat()
    period = _period_key(date.fromisoformat(tax_date))
    original_minor = 12100
    with initialize(database) as db:
        counterparty = db.upsert_counterparty(
            external_key="confirm-counterparty",
            display_name="Supplier Example SL",
            country_code="ES",
        )
        document = db.upsert_document(
            external_key=f"sha256:{digest}",
            counterparty_id=counterparty["counterparty_id"],
            document_type="expense_invoice",
            document_number="INV-CONFIRM-1",
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
            external_key="confirm-transaction",
            period_key=period,
            transaction_date=tax_date,
            booking_date=tax_date,
            entry_type="expense",
            description="Confirmed invoice fixture",
            amount_minor=original_minor,
            currency=currency,
            amount_original_minor=original_minor,
            original_currency=currency,
            amount_eur_minor=original_minor if currency == "EUR" else None,
            direction="debit",
            lifecycle_status="needs_review",
            document_id=document["document_id"],
            counterparty_id=counterparty["counterparty_id"],
            source_hash=hashlib.sha256(b"confirm-transaction").hexdigest(),
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="invoice_review",
            tax_code="unknown",
            jurisdiction="ES",
            taxable_base_minor=10000,
            vat_minor=2100,
            notes="Extraction candidate only",
            source_hash=hashlib.sha256(b"confirm-treatment").hexdigest(),
        )
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
    return {
        "database": database,
        "period": period,
        "document_id": document["document_id"],
        "transaction_id": transaction["transaction_id"],
        "counterparty_id": counterparty["counterparty_id"],
    }

def _packet(fixture: dict[str, object]) -> dict[str, object]:
    with open_ledger_db(fixture["database"], read_only=True) as db:
        work_item = prepare_review_work_item(db, f"transaction:{fixture['transaction_id']}")
    return dict(work_item["packet"])

def _approve_decision(packet: dict[str, object]) -> None:
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
            "counterparty_changes": {},
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
    for resolution in decision["issue_resolutions"]:
        resolution["action"] = "resolve"
        resolution["reason"] = "Reviewed against the immutable source invoice"

def _reject_decision(packet: dict[str, object]) -> None:
    decision = packet["decision"]
    assert isinstance(decision, dict)
    decision.update(
        {
            "outcome": "reject",
            "reason": "Document does not describe the contracted service",
            "business_purpose": None,
            "document_valid": False,
            "asset_decision": None,
            "asset_id": None,
            "counterparty_changes": {},
            "tax_treatment": None,
        }
    )
    for resolution in decision["issue_resolutions"]:
        resolution["action"] = "resolve"
        resolution["reason"] = "Closed by the rejection decision"

def _ecb_fx_spec(rate: str = "0.9216") -> dict[str, object]:
    return {
        "rate_date": date.today().isoformat(),
        "rate": rate,
        "rate_source": "ecb",
        "source_reference": None,
        "raw_observation": None,
        "raw_observation_hash": None,
        "supersedes_rate_id": None,
    }

def _ecb_observation(rate: str, currency: str, rate_date: date) -> ECBRateObservation:
    eur_per_unit = Decimal(rate)
    units_per_eur = Decimal(1) / eur_per_unit
    raw_observation = '{"currency":"' + currency + '","date":"' + rate_date.isoformat() + '","value":"' + format(units_per_eur, "f") + '"}'
    return ECBRateObservation(currency=currency, rate_date=rate_date, units_per_eur=units_per_eur, eur_per_unit=eur_per_unit, source_url="https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A", raw_observation=raw_observation, raw_observation_hash=hashlib.sha256(raw_observation.encode("utf-8")).hexdigest())
