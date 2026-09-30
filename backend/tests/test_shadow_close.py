from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.shadow_close import (
    REQUIRED_INVOICE_CHANNEL_CHECKS,
    build_shadow_close_report,
    load_invoice_channel_assessment,
    summarize_operational_acceptance,
    summarize_payment_state,
    summarize_required_tax_settlements,
)


READY_OPERATIONAL_ACCEPTANCE = {
    "status": "ready",
    "ready": True,
    "supplier_expenses": [{"transaction_id": "supplier-proof"}],
    "non_invoice_expenses": [],
    "operational_expenses": [{"transaction_id": "supplier-proof"}],
    "missing_proofs": [],
}


def test_open_quarter_separates_expected_filing_from_data_and_cutover_blockers() -> None:
    report = build_shadow_close_report(
        period={
            "period_key": "2026-Q3",
            "starts_on": "2026-07-01",
            "ends_on": "2026-09-30",
            "status": "open",
        },
        as_of=date(2026, 7, 17),
        dashboard={
            "blocking_items": [
                {
                    "kind": "validation_issue",
                    "reference": "identity-1",
                },
                {
                    "kind": "obligation",
                    "reference": "130",
                    "detail": "due",
                },
                {
                    "kind": "approved_not_posted",
                    "reference": "expense-1",
                },
            ],
            "obligations": [
                {
                    "obligation_code": "130",
                    "determination": "due",
                    "filing_status": "due",
                    "internal_due_on": "2026-10-14",
                    "direct_debit_cutoff_on": "2026-10-15",
                    "due_on": "2026-10-20",
                }
            ],
            "posted_actual": {},
            "approved_forecast_delta": {},
            "projected_total": {},
        },
        aeat_projection={
            "data_projection_ready": False,
            "xlsx_generation_supported": False,
            "counts": {"blockers": 2},
            "blockers": [
                {
                    "code": "approved_not_posted",
                    "reference": "expense-1",
                },
                {
                    "code": "row_mapping_incomplete",
                    "reference": "anthropic-1",
                    "message": "Foreign identity is missing.",
                },
            ],
        },
        payment_state={
            "status": "not_populated",
            "ready": False,
            "unmatched_payment_ids": [],
        },
        offboarding_verification={"ok": True, "category_counts": {}},
        invoice_channel_assessment=None,
    )

    assert report["period_state"]["period_ended_as_of"] is False
    assert report["gates"]["obligations"]["status"] == "expected_pending"
    assert report["gates"]["obligations"]["note"].startswith("Due-but-unfiled")
    assert {row["kind"] for row in report["gates"]["accounting_data"]["items"]} == {
        "validation_issue",
        "aeat_row_mapping_incomplete",
    }
    assert report["gates"]["posting"]["status"] == "in_progress"
    assert report["summary"]["offboarding_archive_ready"] is True
    assert report["summary"]["filing_ready"] is False
    assert report["summary"]["cutover_ready"] is False


def test_future_approved_forecast_is_visible_without_blocking_cutover() -> None:
    base = {
        "period": {
            "period_key": "2026-Q3",
            "starts_on": "2026-07-01",
            "ends_on": "2026-09-30",
            "status": "open",
        },
        "payment_state": {"status": "not_populated", "ready": False},
        "offboarding_verification": {"ok": True},
        "invoice_channel_assessment": None,
        "operational_acceptance": READY_OPERATIONAL_ACCEPTANCE,
    }
    pending = {
        "kind": "approved_forecast_pending",
        "reference": "amortization-1",
        "tax_date": "2026-09-30",
    }
    before_due = build_shadow_close_report(
        **base,
        as_of=date(2026, 7, 31),
        dashboard={
            "blocking_items": [],
            "expected_items": [pending],
            "obligations": [],
        },
        aeat_projection={
            "data_projection_ready": False,
            "counts": {"blockers": 1},
            "blockers": [
                {
                    "code": "approved_not_posted",
                    "reference": "amortization-1",
                    "message": "Approved row is not posted.",
                }
            ],
        },
    )
    after_due = build_shadow_close_report(
        **base,
        as_of=date(2026, 10, 1),
        dashboard={
            "blocking_items": [
                {
                    "kind": "approved_not_posted",
                    "reference": "amortization-1",
                    "tax_date": "2026-09-30",
                }
            ],
            "expected_items": [],
            "obligations": [],
        },
        aeat_projection={
            "data_projection_ready": False,
            "counts": {"blockers": 1},
            "blockers": [
                {
                    "code": "approved_not_posted",
                    "reference": "amortization-1",
                    "message": "Approved row is not posted.",
                }
            ],
        },
    )

    assert before_due["summary"]["posting_ready"] is True
    assert before_due["summary"]["aeat_data_projection_ready"] is True
    assert before_due["summary"]["cutover_ready"] is True
    assert before_due["gates"]["posting"]["expected_pending"] == [pending]
    assert after_due["summary"]["posting_ready"] is False
    assert after_due["summary"]["aeat_data_projection_ready"] is False
    assert after_due["summary"]["cutover_ready"] is False


def test_ended_quarter_can_be_filing_and_cutover_ready_before_due_forms_are_submitted() -> None:
    checks = {key: "passed" for key in REQUIRED_INVOICE_CHANNEL_CHECKS}
    report = build_shadow_close_report(
        period={
            "period_key": "2026-Q3",
            "starts_on": "2026-07-01",
            "ends_on": "2026-09-30",
            "status": "open",
        },
        as_of=date(2026, 10, 1),
        dashboard={
            "blocking_items": [
                {
                    "kind": "obligation",
                    "reference": "130",
                    "detail": "due",
                }
            ],
            "obligations": [
                {
                    "obligation_code": "130",
                    "determination": "due",
                    "filing_status": "due",
                    "due_on": "2026-10-20",
                }
            ],
            "posted_actual": {},
            "approved_forecast_delta": {},
            "projected_total": {},
        },
        aeat_projection={
            "data_projection_ready": True,
            "xlsx_generation_supported": False,
            "counts": {"blockers": 0},
            "blockers": [],
        },
        payment_state={
            "status": "ready",
            "ready": True,
            "unmatched_payment_ids": [],
        },
        offboarding_verification={"ok": True},
        invoice_channel_assessment={
            "checked_on": "2026-09-30",
            "checks": checks,
        },
        operational_acceptance=READY_OPERATIONAL_ACCEPTANCE,
    )

    assert report["gates"]["obligations"]["status"] == "action_required"
    assert report["summary"]["filing_ready"] is True
    assert report["summary"]["cutover_ready"] is True


def test_missing_zenmoney_is_optional_and_does_not_block_filing_or_cutover() -> None:
    report = build_shadow_close_report(
        period={
            "period_key": "2026-Q3",
            "starts_on": "2026-07-01",
            "ends_on": "2026-09-30",
            "status": "open",
        },
        as_of=date(2026, 10, 1),
        dashboard={
            "blocking_items": [],
            "obligations": [],
            "posted_actual": {},
            "approved_forecast_delta": {},
            "projected_total": {},
        },
        aeat_projection={
            "data_projection_ready": True,
            "xlsx_generation_supported": False,
            "counts": {"blockers": 0},
            "blockers": [],
        },
        payment_state={
            "status": "not_populated",
            "ready": False,
            "unmatched_payment_ids": [],
        },
        offboarding_verification={"ok": True},
        invoice_channel_assessment=None,
        operational_acceptance=READY_OPERATIONAL_ACCEPTANCE,
    )

    assert report["summary"]["payment_reconciliation_required"] is False
    assert report["summary"]["invoice_channel_required_for_cutover"] is False
    assert report["gates"]["payments"]["required"] is False
    assert report["summary"]["filing_ready"] is True
    assert report["summary"]["cutover_ready"] is True


def test_required_tax_settlement_blocks_cutover_until_ready() -> None:
    common = {
        "period": {
            "period_key": "2026-Q3",
            "starts_on": "2026-07-01",
            "ends_on": "2026-09-30",
            "status": "open",
        },
        "as_of": date(2026, 7, 18),
        "dashboard": {
            "blocking_items": [],
            "obligations": [],
            "posted_actual": {},
            "approved_forecast_delta": {},
            "projected_total": {},
        },
        "aeat_projection": {
            "data_projection_ready": True,
            "counts": {"blockers": 0},
            "blockers": [],
        },
        "payment_state": {"status": "not_populated", "ready": False},
        "offboarding_verification": {"ok": True},
        "invoice_channel_assessment": None,
        "operational_acceptance": READY_OPERATIONAL_ACCEPTANCE,
    }
    missing = {
        "status": "blocked",
        "ready": False,
        "required": True,
        "requirements": [],
        "missing_settlements": [
            {"selector": "2026-Q2:130", "reason": "payment_missing"}
        ],
    }
    blocked = build_shadow_close_report(
        **common,
        required_tax_settlements=missing,
    )
    ready = build_shadow_close_report(
        **common,
        required_tax_settlements={
            "status": "ready",
            "ready": True,
            "required": True,
            "requirements": [],
            "missing_settlements": [],
        },
    )

    assert blocked["schema_version"] == 3
    assert blocked["summary"]["required_tax_settlements_ready"] is False
    assert blocked["summary"]["cutover_ready"] is False
    assert "2026-Q2:130" in " ".join(blocked["next_actions"])
    assert ready["summary"]["required_tax_settlements_ready"] is True
    assert ready["summary"]["cutover_ready"] is True


def test_operational_acceptance_accepts_any_independent_posted_expense_path(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        _add_expense_proof(
            db,
            key="xolo-fee",
            transaction_date="2026-07-01",
            document_type="expense_invoice",
            source_path="Xolo evidence archive/expense_evidence/INV-XOLO.pdf",
            source_name="xolo_source_book_rows",
            treatment_notes="source_book_line_id=gastos_book:53",
            lifecycle_status="posted",
        )
        _add_expense_proof(
            db,
            key="reviewed-not-posted",
            transaction_date="2026-07-08",
            document_type="expense_invoice",
            source_path="Accounting system/Evidence/reviewed.pdf",
            source_name="Inbox/2026-Q3/expense_invoice/reviewed.pdf",
            lifecycle_status="approved",
        )
        _add_expense_proof(
            db,
            key="supplier-live",
            transaction_date="2026-07-10",
            document_type="expense_invoice",
            source_path="Accounting system/Evidence/supplier-live.pdf",
            source_name="Inbox/2026-Q3/expense_invoice/supplier-live.pdf",
            lifecycle_status="posted",
        )

        supplier_only = summarize_operational_acceptance(
            db,
            proof_since=date(2026, 7, 1),
            as_of=date(2026, 7, 14),
        )
        db.connection.execute(
            "UPDATE transactions SET lifecycle_status = 'rejected' "
            "WHERE transaction_id = ?",
            (_proof_transaction_id("supplier-live"),),
        )
        db.connection.commit()
        _add_expense_proof(
            db,
            key="tgss-live",
            transaction_date="2026-07-15",
            document_type="social_security_evidence",
            source_path="Accounting system/Evidence/tgss-live.pdf",
            source_name="expense-record:social-security",
            lifecycle_status="posted",
        )
        complete = summarize_operational_acceptance(
            db,
            proof_since=date(2026, 7, 1),
            as_of=date(2026, 7, 18),
        )

    assert supplier_only["ready"] is True
    assert supplier_only["missing_proofs"] == []
    assert supplier_only["excluded_xolo_derived_count"] == 1
    assert [row["transaction_id"] for row in supplier_only["supplier_expenses"]] == [
        _proof_transaction_id("supplier-live")
    ]
    assert complete["ready"] is True
    assert complete["missing_proofs"] == []
    assert complete["supplier_expenses"] == []
    assert [row["document_type"] for row in complete["non_invoice_expenses"]] == [
        "social_security_evidence"
    ]


def test_required_tax_settlement_requires_hash_archived_payment_evidence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        obligation = db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            filed_at="2026-07-08",
            determination="due",
            blocking=False,
        )
        db.create_filing_snapshot(
            "2026-Q2",
            payload={"form": "130", "filed_values": {"19": "2639.12"}},
            filed_on="2026-07-08",
            status="baseline",
            form_code="130",
        )
        missing = summarize_required_tax_settlements(
            db,
            selectors=[("2026-Q2", "130")],
        )
        db.add_payment(
            paid_on="2026-07-20",
            amount_minor=263912,
            currency="EUR",
            source_hash="legacy-payment-without-evidence",
            obligation_id=obligation["obligation_id"],
            match_status="manual",
            source_system="bank",
            external_id="legacy-operation",
        )
        evidence_missing = summarize_required_tax_settlements(
            db,
            selectors=[("2026-Q2", "130")],
        )
        db.add_payment(
            paid_on="2026-07-21",
            amount_minor=263912,
            currency="EUR",
            source_hash="archived-payment",
            obligation_id=obligation["obligation_id"],
            match_status="manual",
            source_system="revolut",
            external_id="tax-debit-2026-q2-130",
            source_row_json=json.dumps(
                {
                    "schema_version": 1,
                    "evidence_sha256": "a" * 64,
                    "evidence_locator": "2026-Q2/tax_payment_evidence/a.pdf",
                    "source_system": "revolut",
                    "external_id": "tax-debit-2026-q2-130",
                }
            ),
        )
        ready = summarize_required_tax_settlements(
            db,
            selectors=[("2026-Q2", "130"), ("2026-Q2", "130")],
        )

    assert missing["missing_settlements"] == [
        {"selector": "2026-Q2:130", "reason": "payment_missing"}
    ]
    assert missing["requirements"][0]["amount_check_status"] == "pending_payment"
    assert evidence_missing["missing_settlements"] == [
        {"selector": "2026-Q2:130", "reason": "payment_evidence_missing"}
    ]
    assert ready["ready"] is True
    assert len(ready["requirements"]) == 1
    assert ready["requirements"][0]["expected_amount_minor"] == 263912
    assert ready["requirements"][0]["evidenced_amount_minor"] == 263912
    assert ready["requirements"][0]["amount_check_status"] == "matched"
    assert any(
        payment["evidence_ready"]
        for payment in ready["requirements"][0]["payments"]
    )


def test_required_tax_settlement_rejects_payment_amount_mismatch(
    tmp_path: Path,
) -> None:
    database = tmp_path / "amount-mismatch.sqlite"
    with initialize(database) as db:
        obligation = db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            determination="due",
            blocking=False,
        )
        db.create_filing_snapshot(
            "2026-Q2",
            payload={"form": "130", "filed_values": {"19": "2639.12"}},
            filed_on="2026-07-08",
            status="baseline",
            form_code="130",
        )
        db.add_payment(
            paid_on="2026-07-20",
            amount_minor=100,
            currency="EUR",
            source_hash="wrong-amount-payment",
            obligation_id=obligation["obligation_id"],
            match_status="manual",
            source_system="revolut",
            external_id="wrong-amount",
            source_row_json=json.dumps(
                {
                    "schema_version": 1,
                    "evidence_sha256": "b" * 64,
                    "evidence_locator": "2026-Q2/tax_payment_evidence/b.pdf",
                    "source_system": "revolut",
                    "external_id": "wrong-amount",
                }
            ),
        )
        state = summarize_required_tax_settlements(
            db,
            selectors=[("2026-Q2", "130")],
        )

    requirement = state["requirements"][0]
    assert state["ready"] is False
    assert state["missing_settlements"] == [
        {"selector": "2026-Q2:130", "reason": "payment_amount_mismatch"}
    ]
    assert requirement["expected_amount_minor"] == 263912
    assert requirement["evidenced_amount_minor"] == 100
    assert requirement["amount_check_status"] == "mismatch"


def test_required_modelo130_settlement_requires_filed_payable_amount(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing-filed-amount.sqlite"
    with initialize(database) as db:
        obligation = db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            determination="due",
            blocking=False,
        )
        db.add_payment(
            paid_on="2026-07-20",
            amount_minor=263912,
            currency="EUR",
            source_hash="payment-without-filed-snapshot",
            obligation_id=obligation["obligation_id"],
            match_status="manual",
            source_system="revolut",
            external_id="missing-filed-snapshot",
            source_row_json=json.dumps(
                {
                    "schema_version": 1,
                    "evidence_sha256": "c" * 64,
                    "evidence_locator": "2026-Q2/tax_payment_evidence/c.pdf",
                    "source_system": "revolut",
                    "external_id": "missing-filed-snapshot",
                }
            ),
        )
        state = summarize_required_tax_settlements(
            db,
            selectors=[("2026-Q2", "130")],
        )

    assert state["ready"] is False
    assert state["missing_settlements"] == [
        {"selector": "2026-Q2:130", "reason": "filed_amount_missing"}
    ]
    assert state["requirements"][0]["amount_check_status"] == "missing"


@pytest.mark.parametrize(
    ("determination", "filing_status", "expected_reason"),
    [
        ("not_due", "waived", "obligation_not_due"),
        ("due", "due", "obligation_not_filed"),
    ],
)
def test_required_tax_settlement_rejects_inapplicable_or_unfiled_obligation(
    tmp_path: Path,
    determination: str,
    filing_status: str,
    expected_reason: str,
) -> None:
    database = tmp_path / f"{expected_reason}.sqlite"
    with initialize(database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status=filing_status,
            determination=determination,
            blocking=False,
        )
        state = summarize_required_tax_settlements(
            db,
            selectors=[("2026-Q2", "130")],
        )

    assert state["ready"] is False
    assert state["missing_settlements"] == [
        {"selector": "2026-Q2:130", "reason": expected_reason}
    ]


def _add_expense_proof(
    db,
    *,
    key: str,
    transaction_date: str,
    document_type: str,
    source_path: str,
    source_name: str,
    lifecycle_status: str,
    treatment_notes: str = "independent live review",
) -> None:
    batch = db.add_import_batch(
        source_name=source_name,
        source_hash=f"batch-{key}",
        batch_key=f"batch:{key}",
    )
    document = db.upsert_document(
        external_key=f"document-{key}",
        import_batch_id=batch["import_batch_id"],
        document_type=document_type,
        document_number=key,
        issued_on=transaction_date,
        period_key="2026-Q3",
        lifecycle_status="approved",
        source_hash=f"document-hash-{key}",
    )
    db.connection.execute(
        "UPDATE documents SET source_path = ? WHERE document_id = ?",
        (source_path, document["document_id"]),
    )
    transaction = db.add_transaction(
        transaction_id=_proof_transaction_id(key),
        external_key=f"transaction-{key}",
        period_key="2026-Q3",
        transaction_date=transaction_date,
        booking_date=transaction_date,
        entry_type="expense",
        description=key,
        amount_minor=1000,
        lifecycle_status=lifecycle_status,
        document_id=document["document_id"],
        source_hash=f"transaction-hash-{key}",
    )
    db.add_detailed_tax_treatment(
        transaction_id=transaction["transaction_id"],
        treatment_type="expense",
        tax_code="deductible_expense",
        deductible_irpf_minor=1000,
        include_modelo130=True,
        notes=treatment_notes,
    )


def _proof_transaction_id(key: str) -> str:
    return f"proof-{key}"


def test_unknown_obligation_takes_precedence_over_due_unfiled_label() -> None:
    report = build_shadow_close_report(
        period={
            "period_key": "2026-Q3",
            "starts_on": "2026-07-01",
            "ends_on": "2026-09-30",
            "status": "open",
        },
        as_of=date(2026, 7, 17),
        dashboard={
            "blocking_items": [
                {"kind": "obligation", "reference": "130", "detail": "due"},
                {"kind": "obligation", "reference": "216", "detail": "unknown"},
            ],
            "obligations": [
                {
                    "obligation_code": "130",
                    "determination": "due",
                    "filing_status": "due",
                },
                {
                    "obligation_code": "216",
                    "determination": "unknown",
                    "filing_status": "unknown",
                },
            ],
            "posted_actual": {},
            "approved_forecast_delta": {},
            "projected_total": {},
        },
        aeat_projection={
            "data_projection_ready": True,
            "counts": {"blockers": 0},
            "blockers": [],
        },
        payment_state={
            "status": "ready",
            "ready": True,
            "unmatched_payment_ids": [],
        },
        offboarding_verification={"ok": True},
        invoice_channel_assessment={
            "checks": {
                key: "passed" for key in REQUIRED_INVOICE_CHANNEL_CHECKS
            }
        },
    )

    assert report["gates"]["obligations"]["status"] == "blocked"
    assert report["summary"]["accounting_data_ready"] is False
    assert report["summary"]["filing_ready"] is False


def test_payment_state_requires_structured_export_coverage_through_as_of(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        period = db.ensure_period("2026-Q3")
        db.add_import_batch(
            source_name="partial.csv",
            source_hash="partial-hash",
            batch_key="zenmoney:partial-hash",
            notes=json.dumps(
                {
                    "period_key": "2026-Q3",
                    "source_starts_on": "2026-07-01",
                    "source_ends_on": "2026-07-10",
                    "business_accounts": ["Business EUR"],
                }
            ),
        )
        partial = summarize_payment_state(
            db,
            period=period,
            as_of=date(2026, 7, 17),
        )
        db.add_import_batch(
            source_name="full.csv",
            source_hash="full-hash",
            batch_key="zenmoney:full-hash",
            notes=json.dumps(
                {
                    "period_key": "2026-Q3",
                    "source_starts_on": "2023-01-01",
                    "source_ends_on": "2026-07-17",
                    "business_accounts": ["Business EUR"],
                }
            ),
        )
        complete = summarize_payment_state(
            db,
            period=period,
            as_of=date(2026, 7, 17),
        )
        db.add_payment(
            paid_on="2026-07-05",
            amount_minor=10000,
            currency="EUR",
            source_hash="payment-hash",
            match_status="manual",
            source_system="zenmoney",
        )
        blocked = summarize_payment_state(
            db,
            period=period,
            as_of=date(2026, 7, 17),
        )

    assert partial["status"] == "coverage_incomplete"
    assert partial["ready"] is False
    assert complete["status"] == "ready"
    assert complete["payment_count"] == 0
    assert blocked["status"] == "blocked"
    assert len(blocked["unmatched_payment_ids"]) == 1


def test_invoice_channel_and_offboarding_gates_fail_closed(tmp_path: Path) -> None:
    assessment_path = tmp_path / "channel.json"
    assessment_path.write_text(
        json.dumps(
            {
                "checked_on": "2026-07-17",
                "checks": {"foreign_business_recipient": "passed"},
            }
        ),
        encoding="utf-8",
    )
    assessment = load_invoice_channel_assessment(assessment_path)
    report = build_shadow_close_report(
        period={
            "period_key": "2026-Q3",
            "starts_on": "2026-07-01",
            "ends_on": "2026-09-30",
            "status": "open",
        },
        as_of=date(2026, 7, 17),
        dashboard={
            "blocking_items": [],
            "obligations": [],
            "posted_actual": {},
            "approved_forecast_delta": {},
            "projected_total": {},
        },
        aeat_projection={
            "data_projection_ready": True,
            "counts": {"blockers": 0},
            "blockers": [],
        },
        payment_state={
            "status": "ready",
            "ready": True,
            "unmatched_payment_ids": [],
        },
        offboarding_verification={
            "ok": False,
            "status": "forged-ready",
            "ready": True,
        },
        invoice_channel_assessment=assessment,
    )

    assert report["gates"]["offboarding_archive"]["status"] == "blocked"
    assert report["gates"]["offboarding_archive"]["ready"] is False
    assert report["gates"]["invoice_channel"]["status"] == "blocked"
    assert "usd_amount" in report["gates"]["invoice_channel"]["missing_checks"]
    assert report["summary"]["cutover_ready"] is False


def test_invoice_channel_loader_rejects_non_object_json(tmp_path: Path) -> None:
    assessment_path = tmp_path / "channel.json"
    assessment_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        load_invoice_channel_assessment(assessment_path)


def test_shadow_close_cli_rejects_malformed_quarter(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="YYYY-QN"):
        main(
            [
                "period",
                "shadow-close",
                "--db",
                str(tmp_path / "ledger.sqlite"),
                "2026-3",
                "--out-dir",
                str(tmp_path / "shadow"),
            ]
        )


def test_shadow_close_cli_rejects_malformed_required_settlement(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="PERIOD:FORM"):
        main(
            [
                "period",
                "shadow-close",
                "--db",
                str(tmp_path / "ledger.sqlite"),
                "2026-Q3",
                "--required-settled-obligation",
                "2026-Q2",
                "--out-dir",
                str(tmp_path / "shadow"),
            ]
        )


def test_shadow_close_cli_writes_fail_closed_report_when_prerequisites_are_missing(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    output = tmp_path / "shadow"
    assert main(["db", "init", "--db", str(database)]) == 0
    capsys.readouterr()
    with initialize(database) as db:
        db.ensure_period("2026-Q3")

    assert (
        main(
            [
                "period",
                "shadow-close",
                "--db",
                str(database),
                "2026-Q3",
                "--as-of",
                "2026-07-17",
                "--operational-proof-since",
                "2026-07-01",
                "--required-settled-obligation",
                "2026-Q2:130",
                "--out-dir",
                str(output),
            ]
        )
        == 0
    )
    emitted = json.loads(capsys.readouterr().out)
    report = json.loads(
        (output / "shadow-close.json").read_text(encoding="utf-8")
    )

    assert emitted["summary"]["filing_ready"] is False
    assert report["gates"]["aeat_books"]["status"] == "blocked"
    assert report["gates"]["payments"]["status"] == "not_populated"
    assert report["gates"]["offboarding_archive"]["status"] == "not_checked"
    assert report["gates"]["operational_acceptance"]["status"] == "in_progress"
    assert report["gates"]["required_tax_settlements"]["status"] == "blocked"
    assert report["gates"]["required_tax_settlements"]["missing_settlements"] == [
        {"selector": "2026-Q2:130", "reason": "obligation_not_found"}
    ]
    assert report["summary"]["operational_acceptance_ready"] is False
    assert report["summary"]["required_tax_settlements_ready"] is False
    assert report["summary"]["cutover_ready"] is False
    markdown = (output / "shadow-close.md").read_text(encoding="utf-8")
    assert "| Filing | no | informational |" in markdown
    assert "| Live operating proof | no | required |" in markdown
    assert "| Required tax settlements | no | required |" in markdown
    assert "| Replacement invoice channel | no | informational |" in markdown
    assert (output / "dashboard.json").is_file()
    assert (output / "aeat-preview.json").is_file()
