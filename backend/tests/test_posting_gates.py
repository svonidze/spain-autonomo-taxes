from __future__ import annotations

import pytest

from autonomo_taxes.ledger_db import LifecycleError, initialize


def _approved_transaction(db, *, key: str, **overrides):
    values = {
        "external_key": key,
        "period_key": "2026-Q3",
        "transaction_date": "2026-07-10",
        "booking_date": "2026-07-10",
        "entry_type": "expense",
        "description": key,
        "amount_minor": 10000,
        "currency": "EUR",
        "lifecycle_status": "approved",
    }
    values.update(overrides)
    return db.add_transaction(**values)


def _treatment(db, transaction_id: str, *, tax_code: str = "domestic_input") -> None:
    db.add_detailed_tax_treatment(
        transaction_id=transaction_id,
        treatment_type="expense",
        tax_code=tax_code,
        taxable_base_minor=10000,
        deductible_irpf_minor=10000,
        include_modelo130=True,
    )


def test_transaction_without_tax_treatment_cannot_be_posted(tmp_path) -> None:
    with initialize(tmp_path / "ledger.sqlite") as db:
        transaction = _approved_transaction(db, key="missing-treatment")

        with pytest.raises(LifecycleError, match="tax treatment is required"):
            db.transition_transaction(
                transaction["transaction_id"],
                lifecycle_status="posted",
                expected_row_version=transaction["row_version"],
            )


def test_unknown_tax_treatment_cannot_be_posted(tmp_path) -> None:
    with initialize(tmp_path / "ledger.sqlite") as db:
        transaction = _approved_transaction(db, key="unknown-treatment")
        _treatment(db, transaction["transaction_id"], tax_code="unknown")

        with pytest.raises(LifecycleError, match="Unknown tax treatment"):
            db.transition_transaction(
                transaction["transaction_id"],
                lifecycle_status="posted",
                expected_row_version=transaction["row_version"],
            )


def test_foreign_currency_requires_sourced_fx_before_posting(tmp_path) -> None:
    with initialize(tmp_path / "ledger.sqlite") as db:
        transaction = _approved_transaction(
            db,
            key="usd-without-source",
            amount_minor=10000,
            currency="USD",
            amount_original_minor=10000,
            original_currency="USD",
            amount_eur_minor=9000,
        )
        _treatment(db, transaction["transaction_id"], tax_code="outside_scope")

        with pytest.raises(LifecycleError, match="requires sourced FX"):
            db.transition_transaction(
                transaction["transaction_id"],
                lifecycle_status="posted",
                expected_row_version=transaction["row_version"],
            )


def test_q3_transaction_rejects_xolo_recorded_fx(tmp_path) -> None:
    with initialize(tmp_path / "ledger.sqlite") as db:
        rate = db.add_fx_rate(
            rate_date="2026-07-10",
            base_currency="USD",
            quote_currency="EUR",
            rate="0.85",
            rate_source="xolo_recorded",
            source_hash="xolo-q3-rate",
        )
        transaction = _approved_transaction(
            db,
            key="usd-with-xolo-source",
            amount_minor=10000,
            currency="USD",
            amount_original_minor=10000,
            original_currency="USD",
            amount_eur_minor=8500,
            fx_rate_id=rate["fx_rate_id"],
        )
        _treatment(db, transaction["transaction_id"], tax_code="outside_scope")

        with pytest.raises(LifecycleError, match="xolo_recorded FX is historical-only"):
            db.transition_transaction(
                transaction["transaction_id"],
                lifecycle_status="posted",
                expected_row_version=transaction["row_version"],
            )


def test_linked_document_must_be_approved_before_posting(tmp_path) -> None:
    with initialize(tmp_path / "ledger.sqlite") as db:
        document = db.upsert_document(
            external_key="draft-document",
            document_type="expense_invoice",
            document_number="INV-1",
            issued_on="2026-07-10",
            period_key="2026-Q3",
            lifecycle_status="extracted",
        )
        transaction = _approved_transaction(
            db,
            key="unapproved-document",
            document_id=document["document_id"],
        )
        _treatment(db, transaction["transaction_id"])

        with pytest.raises(LifecycleError, match="linked document must be approved"):
            db.transition_transaction(
                transaction["transaction_id"],
                lifecycle_status="posted",
                expected_row_version=transaction["row_version"],
            )


def test_reviewed_transaction_with_approved_document_can_be_posted(tmp_path) -> None:
    with initialize(tmp_path / "ledger.sqlite") as db:
        document = db.upsert_document(
            external_key="approved-document",
            document_type="expense_invoice",
            document_number="INV-2",
            issued_on="2026-07-10",
            period_key="2026-Q3",
            lifecycle_status="approved",
        )
        transaction = _approved_transaction(
            db,
            key="ready-to-post",
            document_id=document["document_id"],
        )
        _treatment(db, transaction["transaction_id"])

        posted = db.transition_transaction(
            transaction["transaction_id"],
            lifecycle_status="posted",
            expected_row_version=transaction["row_version"],
        )

    assert posted["lifecycle_status"] == "posted"


def test_open_subject_review_issue_blocks_posting(tmp_path) -> None:
    with initialize(tmp_path / "ledger.sqlite") as db:
        transaction = _approved_transaction(db, key="open-review-issue")
        _treatment(db, transaction["transaction_id"])
        db.add_validation_issue(
            period_key="2026-Q3",
            issue_code="transaction_tax_review",
            severity="warning",
            message="Deductibility review is incomplete.",
            subject_table="transactions",
            subject_id=transaction["transaction_id"],
            blocking=True,
        )

        with pytest.raises(LifecycleError, match="transaction_tax_review"):
            db.transition_transaction(
                transaction["transaction_id"],
                lifecycle_status="posted",
                expected_row_version=transaction["row_version"],
            )


def test_future_nonresident_professional_invoice_waits_for_payment_before_blocking(
    tmp_path,
) -> None:
    with initialize(tmp_path / "ledger.sqlite") as db:
        counterparty = db.upsert_counterparty(
            external_key="future-foreign-professional",
            display_name="Future Foreign Professional",
            country_code="GE",
        )
        db.update_counterparty_review(
            counterparty["counterparty_id"],
            display_name=counterparty["display_name"],
            tax_id=None,
            country_code="GE",
            vat_id=None,
            roi_status="unknown",
            professional_supplier=True,
            retention_expected=False,
            email=None,
            phone=None,
            legal_form="individual",
            expected_row_version=counterparty["row_version"],
        )
        transaction = _approved_transaction(
            db,
            key="future-foreign-professional-expense",
            counterparty_id=counterparty["counterparty_id"],
        )
        _treatment(db, transaction["transaction_id"], tax_code="outside_scope")

        posted = db.transition_transaction(
            transaction["transaction_id"],
            lifecycle_status="posted",
            expected_row_version=transaction["row_version"],
        )
        issues_before_payment = db.list_issues(
            period_key="2026-Q3",
            blocking_only=True,
        )
        db.add_payment(
            transaction_id=transaction["transaction_id"],
            paid_on="2026-07-15",
            amount_minor=10000,
            currency="EUR",
            source_hash="future-foreign-professional-payment",
            match_status="exact",
        )
        issues_after_payment = db.list_issues(
            period_key="2026-Q3",
            blocking_only=True,
        )

    assert posted["lifecycle_status"] == "posted"
    assert issues_before_payment == []
    assert [row["issue_code"] for row in issues_after_payment] == [
        "nonresident_professional_irnr_review"
    ]
    assert issues_after_payment[0]["subject_table"] == "transactions"
    assert issues_after_payment[0]["subject_id"] == transaction["transaction_id"]
    assert "Expense deductibility is independent" in issues_after_payment[0]["message"]


def test_historical_invoice_paid_in_q3_creates_review_in_payment_period(tmp_path) -> None:
    with initialize(tmp_path / "ledger.sqlite") as db:
        counterparty = db.upsert_counterparty(
            external_key="historical-foreign-professional",
            display_name="Historical Foreign Professional",
            country_code="GE",
        )
        db.update_counterparty_review(
            counterparty["counterparty_id"],
            display_name=counterparty["display_name"],
            tax_id=None,
            country_code="GE",
            vat_id=None,
            roi_status="unknown",
            professional_supplier=True,
            retention_expected=False,
            email=None,
            phone=None,
            legal_form="individual",
            expected_row_version=counterparty["row_version"],
        )
        transaction = _approved_transaction(
            db,
            key="historical-foreign-professional-expense",
            period_key="2026-Q2",
            transaction_date="2026-06-30",
            booking_date="2026-06-30",
            counterparty_id=counterparty["counterparty_id"],
        )
        _treatment(db, transaction["transaction_id"], tax_code="outside_scope")

        posted = db.transition_transaction(
            transaction["transaction_id"],
            lifecycle_status="posted",
            expected_row_version=transaction["row_version"],
        )
        db.add_payment(
            transaction_id=transaction["transaction_id"],
            paid_on="2026-06-30",
            amount_minor=10000,
            currency="EUR",
            source_hash="historical-invoice-q2-payment",
            match_status="exact",
        )
        q2_issues = db.list_issues(period_key="2026-Q2", blocking_only=True)
        db.add_payment(
            transaction_id=transaction["transaction_id"],
            paid_on="2026-07-15",
            amount_minor=10000,
            currency="EUR",
            source_hash="historical-invoice-q3-payment",
            match_status="exact",
        )
        q3_issues = db.list_issues(period_key="2026-Q3", blocking_only=True)

    assert posted["lifecycle_status"] == "posted"
    assert q2_issues == []
    assert [row["issue_code"] for row in q3_issues] == [
        "nonresident_professional_irnr_review"
    ]


def test_repeated_professional_payment_keeps_one_irnr_review_issue(tmp_path) -> None:
    with initialize(tmp_path / "ledger.sqlite") as db:
        counterparty = db.upsert_counterparty(
            external_key="future-foreign-professional-failure",
            display_name="Future Foreign Professional",
            country_code="GE",
        )
        db.update_counterparty_review(
            counterparty["counterparty_id"],
            display_name=counterparty["display_name"],
            tax_id=None,
            country_code="GE",
            vat_id=None,
            roi_status="unknown",
            professional_supplier=True,
            retention_expected=False,
            email=None,
            phone=None,
            legal_form="individual",
            expected_row_version=counterparty["row_version"],
        )
        transaction = _approved_transaction(
            db,
            key="future-foreign-professional-idempotent-payment",
            counterparty_id=counterparty["counterparty_id"],
        )
        _treatment(db, transaction["transaction_id"], tax_code="outside_scope")
        db.transition_transaction(
            transaction["transaction_id"],
            lifecycle_status="posted",
            expected_row_version=transaction["row_version"],
        )
        payment_args = {
            "transaction_id": transaction["transaction_id"],
            "paid_on": "2026-07-15",
            "amount_minor": 10000,
            "currency": "EUR",
            "source_hash": "idempotent-professional-payment",
            "match_status": "exact",
        }
        first = db.add_payment(**payment_args)
        second = db.add_payment(**payment_args)
        issues = db.list_issues(period_key="2026-Q3", blocking_only=True)

    assert first["payment_id"] == second["payment_id"]
    assert [row["issue_code"] for row in issues] == [
        "nonresident_professional_irnr_review"
    ]


def test_foreign_legal_entity_payment_does_not_create_individual_irnr_review(
    tmp_path,
) -> None:
    with initialize(tmp_path / "ledger.sqlite") as db:
        counterparty = db.upsert_counterparty(
            external_key="foreign-professional-company",
            display_name="Foreign Professional Company",
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
        transaction = _approved_transaction(
            db,
            key="foreign-professional-company-expense",
            counterparty_id=counterparty["counterparty_id"],
        )
        _treatment(db, transaction["transaction_id"], tax_code="outside_scope")
        db.transition_transaction(
            transaction["transaction_id"],
            lifecycle_status="posted",
            expected_row_version=transaction["row_version"],
        )

        db.add_payment(
            transaction_id=transaction["transaction_id"],
            paid_on="2026-07-15",
            amount_minor=10000,
            currency="EUR",
            source_hash="foreign-professional-company-payment",
            match_status="exact",
        )
        issues = db.list_issues(period_key="2026-Q3", blocking_only=True)

    assert issues == []


def test_unknown_foreign_professional_legal_form_blocks_classification(tmp_path) -> None:
    with initialize(tmp_path / "ledger.sqlite") as db:
        counterparty = db.upsert_counterparty(
            external_key="foreign-professional-unknown-form",
            display_name="Foreign Professional",
            country_code="GE",
        )
        db.update_counterparty_review(
            counterparty["counterparty_id"],
            display_name=counterparty["display_name"],
            tax_id=None,
            country_code="GE",
            vat_id=None,
            roi_status="unknown",
            professional_supplier=True,
            retention_expected=False,
            email=None,
            phone=None,
            legal_form="unknown",
            expected_row_version=counterparty["row_version"],
        )
        transaction = _approved_transaction(
            db,
            key="foreign-professional-unknown-form-expense",
            counterparty_id=counterparty["counterparty_id"],
        )
        _treatment(db, transaction["transaction_id"], tax_code="outside_scope")
        db.transition_transaction(
            transaction["transaction_id"],
            lifecycle_status="posted",
            expected_row_version=transaction["row_version"],
        )

        db.add_payment(
            transaction_id=transaction["transaction_id"],
            paid_on="2026-07-15",
            amount_minor=10000,
            currency="EUR",
            source_hash="foreign-professional-unknown-form-payment",
            match_status="exact",
        )
        issues = db.list_issues(period_key="2026-Q3", blocking_only=True)

    assert [row["issue_code"] for row in issues] == [
        "nonresident_payee_legal_form_review"
    ]


def test_reviewed_modelo216_decision_resolves_payment_review_issue(tmp_path) -> None:
    with initialize(tmp_path / "ledger.sqlite") as db:
        counterparty = db.upsert_counterparty(
            external_key="foreign-individual-reviewed-216",
            display_name="Foreign Individual",
            country_code="GE",
        )
        counterparty = db.update_counterparty_review(
            counterparty["counterparty_id"],
            display_name=counterparty["display_name"],
            tax_id=None,
            country_code="GE",
            vat_id=None,
            roi_status="unknown",
            professional_supplier=True,
            retention_expected=False,
            email=None,
            phone=None,
            legal_form="individual",
            expected_row_version=counterparty["row_version"],
        )
        transaction = _approved_transaction(
            db,
            key="foreign-individual-reviewed-216-expense",
            counterparty_id=counterparty["counterparty_id"],
        )
        _treatment(db, transaction["transaction_id"], tax_code="outside_scope")
        db.transition_transaction(
            transaction["transaction_id"],
            lifecycle_status="posted",
            expected_row_version=transaction["row_version"],
        )
        db.add_payment(
            transaction_id=transaction["transaction_id"],
            paid_on="2026-07-15",
            amount_minor=10000,
            currency="EUR",
            source_hash="foreign-individual-reviewed-216-payment",
            match_status="exact",
        )
        assert [
            row["issue_code"]
            for row in db.list_issues(period_key="2026-Q3", blocking_only=True)
        ] == ["nonresident_professional_irnr_review"]

        db.add_obligation(
            period_key="2026-Q3",
            obligation_code="216",
            determination="not_due",
            filing_status="waived",
            blocking=False,
            explanation="Reviewed payment is not reportable under IRNR.",
        )
        open_issues = db.list_issues(
            period_key="2026-Q3",
            blocking_only=True,
        )
        all_issues = db.list_issues(
            period_key="2026-Q3",
            blocking_only=True,
            include_resolved=True,
        )

    assert open_issues == []
    assert all_issues[0]["issue_status"] == "resolved"
    assert (
        all_issues[0]["resolution_reason"]
        == "Modelo 216 applicability was reviewed for the period."
    )


def test_reviewed_foreign_legal_entity_resolves_legal_form_issue(tmp_path) -> None:
    with initialize(tmp_path / "ledger.sqlite") as db:
        counterparty = db.upsert_counterparty(
            external_key="foreign-company-reviewed-after-payment",
            display_name="Foreign Company",
            country_code="US",
        )
        counterparty = db.update_counterparty_review(
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
            legal_form="unknown",
            expected_row_version=counterparty["row_version"],
        )
        transaction = _approved_transaction(
            db,
            key="foreign-company-reviewed-after-payment-expense",
            counterparty_id=counterparty["counterparty_id"],
        )
        _treatment(db, transaction["transaction_id"], tax_code="outside_scope")
        db.transition_transaction(
            transaction["transaction_id"],
            lifecycle_status="posted",
            expected_row_version=transaction["row_version"],
        )
        db.add_payment(
            transaction_id=transaction["transaction_id"],
            paid_on="2026-07-15",
            amount_minor=10000,
            currency="EUR",
            source_hash="foreign-company-reviewed-after-payment",
            match_status="exact",
        )
        assert [
            row["issue_code"]
            for row in db.list_issues(period_key="2026-Q3", blocking_only=True)
        ] == ["nonresident_payee_legal_form_review"]

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
        open_issues = db.list_issues(
            period_key="2026-Q3",
            blocking_only=True,
        )
        all_issues = db.list_issues(
            period_key="2026-Q3",
            blocking_only=True,
            include_resolved=True,
        )

    assert open_issues == []
    assert all_issues[0]["issue_status"] == "resolved"
