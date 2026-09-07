from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import tempfile

import pytest

from autonomo_taxes.ledger_db import LedgerDbError, LifecycleError, initialize


def _template(
    db,
    *,
    template_name: str = "Foreign software services",
    recipient_address_line1: str = "100 Example Street",
    expected_row_version: int | None = None,
):
    counterparty = db.upsert_counterparty(
        external_key="customer-us",
        tax_id="12-3456789",
        display_name="Foreign Customer",
        country_code="US",
        email="billing@example.com",
    )
    template = db.upsert_invoice_template(
        template_key="foreign-software",
        template_name=template_name,
        counterparty_id=counterparty["counterparty_id"],
        currency="USD",
        default_lines=[
            {
                "description": "Software development",
                "quantity": "1",
                "unit_amount_minor": 659580,
                "tax_code": "outside_scope",
                "channel_tax_code": "N2",
                "tax_rate_basis_points": 0,
            }
        ],
        payment_terms_days=30,
        channel_hint="aeat_verifactu",
        delivery_email="billing@example.com",
        recipient_address_line1=recipient_address_line1,
        recipient_postal_code="10001",
        recipient_city="New York",
        recipient_region="NY",
        expected_row_version=expected_row_version,
    )
    return counterparty, template


def test_outgoing_invoice_draft_is_not_income_until_issued_evidence_is_linked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            counterparty, template = _template(db)
            draft = db.create_outgoing_invoice_draft(
                draft_key="foreign-software:2026-07",
                invoice_template_id=template["invoice_template_id"],
                period_key="2026-Q3",
                service_on="2026-06-30",
                planned_issue_on="2026-07-01",
            )

            assert draft["lifecycle_status"] == "draft"
            assert draft["issuance_guard"] == "not_issued_do_not_book_as_income"
            assert draft["total_minor"] == 659580
            assert db.list_transactions(period_key="2026-Q3") == []

            draft = db.review_outgoing_invoice_draft(
                draft["outgoing_invoice_draft_id"],
                expected_row_version=draft["row_version"],
            )
            assert draft["lifecycle_status"] == "reviewed"
            assert db.list_transactions(period_key="2026-Q3") == []
            repeated_review = db.review_outgoing_invoice_draft(
                draft["outgoing_invoice_draft_id"],
                expected_row_version=draft["row_version"] - 1,
            )
            assert repeated_review["row_version"] == draft["row_version"]

            evidence_path = Path(tmp) / "SYNTH-DOCUMENT-010.pdf"
            evidence_path.write_bytes(b"issued invoice evidence")
            evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            document = db.upsert_document(
                external_key="issued:SYNTH-DOCUMENT-010",
                counterparty_id=counterparty["counterparty_id"],
                document_type="income_invoice",
                document_number="SYNTH-DOCUMENT-010",
                issued_on="2026-07-01",
                period_key="2026-Q3",
                currency="USD",
                total_minor=659580,
                lifecycle_status="received",
                source_hash=evidence_hash,
            )
            document = db.set_document_storage(
                document["document_id"],
                source_path=str(evidence_path),
                mime_type="application/pdf",
                expected_row_version=document["row_version"],
            )
            for status in ("extracted", "needs_review", "approved"):
                document = db.transition_document(
                    document["document_id"],
                    lifecycle_status=status,
                    expected_row_version=document["row_version"],
                )
            transaction = db.add_transaction(
                external_key="issued:SYNTH-DOCUMENT-010",
                period_key="2026-Q3",
                transaction_date="2026-07-01",
                booking_date="2026-07-01",
                entry_type="income",
                description="Software development",
                amount_minor=659580,
                currency="USD",
                amount_original_minor=659580,
                original_currency="USD",
                direction="credit",
                lifecycle_status="received",
                document_id=document["document_id"],
                counterparty_id=counterparty["counterparty_id"],
            )
            for status in ("extracted", "needs_review", "approved"):
                transaction = db.transition_transaction(
                    transaction["transaction_id"],
                    lifecycle_status=status,
                    expected_row_version=transaction["row_version"],
                )
            db.add_detailed_tax_treatment(
                transaction_id=transaction["transaction_id"],
                treatment_type="income",
                tax_code="outside_scope",
                taxable_base_minor=579443,
                include_modelo130=True,
                include_modelo303=True,
            )

            issued = db.finalize_outgoing_invoice_draft(
                draft["outgoing_invoice_draft_id"],
                document_id=document["document_id"],
                transaction_id=transaction["transaction_id"],
                external_series="FACT-2026",
                external_number="SYNTH-DOCUMENT-010",
                expected_row_version=draft["row_version"],
            )
            assert issued["lifecycle_status"] == "issued"
            assert issued["issuance_guard"] == "issued_evidence_linked"
            repeated_issue = db.finalize_outgoing_invoice_draft(
                draft["outgoing_invoice_draft_id"],
                document_id=document["document_id"],
                transaction_id=transaction["transaction_id"],
                external_series="FACT-2026",
                external_number="SYNTH-DOCUMENT-010",
                expected_row_version=draft["row_version"],
            )
            assert repeated_issue["row_version"] == issued["row_version"]
            repeated_draft = db.create_outgoing_invoice_draft(
                draft_key="foreign-software:2026-07",
                invoice_template_id=template["invoice_template_id"],
                period_key="2026-Q3",
                service_on="2026-06-30",
                planned_issue_on="2026-07-01",
            )
            assert repeated_draft["lifecycle_status"] == "issued"
            assert repeated_draft["row_version"] == issued["row_version"]


def test_outgoing_invoice_finalize_rejects_amount_mismatch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            counterparty, template = _template(db)
            draft = db.create_outgoing_invoice_draft(
                draft_key="foreign-software:2026-08",
                invoice_template_id=template["invoice_template_id"],
                period_key="2026-Q3",
                service_on="2026-07-31",
                planned_issue_on="2026-08-01",
            )
            draft = db.review_outgoing_invoice_draft(
                draft["outgoing_invoice_draft_id"],
                expected_row_version=draft["row_version"],
            )
            evidence_path = Path(tmp) / "FACT-2026-177919007411.pdf"
            evidence_path.write_bytes(b"issued invoice evidence with wrong amount")
            evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            document = db.upsert_document(
                external_key="issued:FACT-2026-177919007411",
                counterparty_id=counterparty["counterparty_id"],
                document_type="income_invoice",
                document_number="FACT-2026-177919007411",
                issued_on="2026-08-01",
                period_key="2026-Q3",
                currency="USD",
                total_minor=1,
                lifecycle_status="received",
                source_hash=evidence_hash,
            )
            document = db.set_document_storage(
                document["document_id"],
                source_path=str(evidence_path),
                mime_type="application/pdf",
                expected_row_version=document["row_version"],
            )
            for status in ("extracted", "needs_review", "approved"):
                document = db.transition_document(
                    document["document_id"],
                    lifecycle_status=status,
                    expected_row_version=document["row_version"],
                )
            transaction = db.add_transaction(
                period_key="2026-Q3",
                transaction_date="2026-08-01",
                booking_date="2026-08-01",
                entry_type="income",
                description="Wrong amount",
                amount_minor=1,
                currency="USD",
                amount_original_minor=1,
                original_currency="USD",
                direction="credit",
                lifecycle_status="approved",
                document_id=document["document_id"],
                counterparty_id=counterparty["counterparty_id"],
            )
            db.add_detailed_tax_treatment(
                transaction_id=transaction["transaction_id"],
                treatment_type="income",
                tax_code="outside_scope",
                taxable_base_minor=1,
                include_modelo130=True,
                include_modelo303=True,
            )

            with pytest.raises(LedgerDbError, match="document_total"):
                db.finalize_outgoing_invoice_draft(
                    draft["outgoing_invoice_draft_id"],
                    document_id=document["document_id"],
                    transaction_id=transaction["transaction_id"],
                    external_number="FACT-2026-177919007411",
                    expected_row_version=draft["row_version"],
                )


def test_reviewed_draft_cannot_be_changed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            _, template = _template(db)
            draft = db.create_outgoing_invoice_draft(
                draft_key="foreign-software:future",
                invoice_template_id=template["invoice_template_id"],
                period_key="2026-Q3",
                service_on="2026-08-31",
                planned_issue_on="2026-09-01",
            )
            reviewed = db.review_outgoing_invoice_draft(
                draft["outgoing_invoice_draft_id"],
                expected_row_version=draft["row_version"],
            )
            with pytest.raises(LifecycleError, match="Only a draft"):
                db.create_outgoing_invoice_draft(
                    outgoing_invoice_draft_id=reviewed["outgoing_invoice_draft_id"],
                    draft_key=reviewed["draft_key"],
                    invoice_template_id=template["invoice_template_id"],
                    period_key="2026-Q3",
                    service_on="2026-08-31",
                    planned_issue_on="2026-09-02",
                    expected_row_version=reviewed["row_version"],
                )


def test_invoice_template_update_creates_new_version_without_rewriting_draft() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            _, template_v1 = _template(db)
            draft = db.create_outgoing_invoice_draft(
                draft_key="foreign-software:version-snapshot",
                invoice_template_id=template_v1["invoice_template_id"],
                period_key="2026-Q3",
                service_on="2026-08-31",
                planned_issue_on="2026-09-01",
            )
            _, template_v2 = _template(
                db,
                template_name="Foreign software services revised",
                recipient_address_line1="200 Revised Street",
                expected_row_version=template_v1["row_version"],
            )

            unchanged = db.get_outgoing_invoice_draft(draft["outgoing_invoice_draft_id"])
            assert template_v1["invoice_template_id"] != template_v2["invoice_template_id"]
            assert template_v2["template_version"] == 2
            assert template_v2["supersedes_invoice_template_id"] == template_v1["invoice_template_id"]
            assert unchanged["template_name"] == "Foreign software services"
            assert unchanged["template_version"] == 1
            assert unchanged["recipient_address_line1"] == "100 Example Street"


def test_non_issued_draft_cannot_carry_issuance_links_at_sqlite_boundary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            _, template = _template(db)
            draft = db.create_outgoing_invoice_draft(
                draft_key="foreign-software:sql-invariant",
                invoice_template_id=template["invoice_template_id"],
                period_key="2026-Q3",
                service_on="2026-08-31",
                planned_issue_on="2026-09-01",
            )
            with pytest.raises(sqlite3.IntegrityError):
                db.connection.execute(
                    "UPDATE outgoing_invoice_drafts SET external_number = 'FACT-X' "
                    "WHERE outgoing_invoice_draft_id = ?",
                    (draft["outgoing_invoice_draft_id"],),
                )


def test_void_outgoing_invoice_draft_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            _, template = _template(db)
            draft = db.create_outgoing_invoice_draft(
                draft_key="foreign-software:void-retry",
                invoice_template_id=template["invoice_template_id"],
                period_key="2026-Q3",
                service_on="2026-08-31",
                planned_issue_on="2026-09-01",
            )
            voided = db.void_outgoing_invoice_draft(
                draft["outgoing_invoice_draft_id"],
                reason="Customer cancelled before issue",
                expected_row_version=draft["row_version"],
            )
            repeated = db.void_outgoing_invoice_draft(
                draft["outgoing_invoice_draft_id"],
                reason="Customer cancelled before issue",
                expected_row_version=draft["row_version"],
            )
            assert repeated["lifecycle_status"] == "void"
            assert repeated["row_version"] == voided["row_version"]
