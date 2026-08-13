from __future__ import annotations

from datetime import date
from decimal import Decimal

from autonomo_taxes.current_quarter import (
    add_xolo_comparison,
    build_current_quarter_dashboard,
    load_xolo_modelo130_forecast,
    write_current_quarter_dashboard,
)
from autonomo_taxes.tax_engine import TaxRow


def _row(identifier: str, tax_date: date, kind: str, amount: str, **overrides) -> TaxRow:
    values = {
        "transaction_id": identifier,
        "tax_date": tax_date,
        "kind": kind,
        "amount_eur": Decimal(amount),
        "taxable_base_eur": Decimal(amount) if kind == "income" else Decimal("0.00"),
        "deductible_irpf_eur": Decimal("0.00"),
        "include_modelo130": True,
    }
    values.update(overrides)
    return TaxRow(**values)


def test_q3_dashboard_separates_posted_and_approved_and_uses_official_130_rule(tmp_path) -> None:
    prior = [
        _row("prior-income", date(2026, 6, 30), "income", "36770.89"),
        _row(
            "prior-expense",
            date(2026, 6, 30),
            "expense",
            "10280.23",
            deductible_irpf_eur=Decimal("10280.23"),
        ),
    ]
    ipg = _row(
        "ipg",
        date(2026, 7, 1),
        "income",
        "5794.43",
        tax_code="outside_scope",
        include_modelo303=True,
    )
    approved = [
        _row(
            "xolo-fee",
            date(2026, 7, 1),
            "expense",
            "71.39",
            taxable_base_eur=Decimal("59.00"),
            vat_eur=Decimal("12.39"),
            deductible_irpf_eur=Decimal("59.00"),
            deductible_vat_eur=Decimal("12.39"),
            tax_code="domestic_input",
            include_modelo303=True,
        ),
        _row("media", date(2026, 9, 30), "expense", "419.30", deductible_irpf_eur=Decimal("22.53")),
        _row("airpods", date(2026, 9, 30), "expense", "642.95", deductible_irpf_eur=Decimal("31.10")),
        _row("iphone", date(2026, 9, 30), "expense", "1665.00", deductible_irpf_eur=Decimal("89.44")),
        _row("macbook", date(2026, 9, 30), "expense", "1994.00", deductible_irpf_eur=Decimal("107.12")),
    ]
    details = [
        {
            "transaction_id": row.transaction_id,
            "transaction_date": row.tax_date.isoformat(),
            "description": row.transaction_id,
            "counterparty_name": row.transaction_id,
            "amount_eur_minor": int(row.amount_eur * 100),
            "deductible_irpf_minor": int(row.deductible_irpf_eur * 100),
            "tax_code": "domestic_input" if row.transaction_id == "xolo-fee" else "historical_g03",
            "asset_id": "" if row.transaction_id != "xolo-fee" else "",
            "document_id": "source-book",
            "document_type": "gastos_book",
        }
        for row in approved
    ]
    validation = {
        "ready": False,
        "blocking_issues": [],
        "unresolved_obligations": [
            {"obligation_code": "130", "determination": "due", "filing_status": "due"}
        ],
        "review_documents": [
            {"document_id": "document-review", "lifecycle_status": "needs_review"}
        ],
        "review_transactions": [
            {"transaction_id": "transaction-review", "lifecycle_status": "needs_review"}
        ],
        "target_derived_fx": [{"transaction_id": "target-fx"}],
        "invalid_amount_transactions": [{"transaction_id": "invalid-amount"}],
        "out_of_period_transactions": [{"transaction_id": "wrong-period"}],
    }

    dashboard = build_current_quarter_dashboard(
        actual_rows=prior + [ipg],
        projected_rows=prior + [ipg] + approved,
        period_key="2026-Q3",
        as_of=date(2026, 7, 16),
        difficult_expenses_rate=Decimal("0.05"),
        previous_positive_casilla_07=Decimal("5298.13"),
        previous_negative_carry=Decimal("0.00"),
        previous_vat_compensation=Decimal("1383.21"),
        approved_current_rows=details,
        obligations=[
            {
                "obligation_code": "130",
                "determination": "due",
                "filing_status": "due",
                "due_on": "2026-10-20",
            },
            {
                "obligation_code": "111",
                "determination": "not_due",
                "filing_status": "not_due",
                "due_on": None,
            },
        ],
        period_validation=validation,
    )

    assert dashboard["filing_assessment_performed"] is False
    assert dashboard["filing_ready"] is False
    assert dashboard["submission_ready"] is False
    assert {row["obligation_code"] for row in dashboard["obligations"]} == {"111", "130"}
    assert next(
        row for row in dashboard["obligations"] if row["obligation_code"] == "130"
    )["due_on"] == "2026-10-20"
    assert dashboard["posted_actual"]["income_eur"] == Decimal("5794.43")
    assert dashboard["approved_forecast_delta"]["expense_document_gross_eur"] == Decimal("4792.64")
    assert dashboard["approved_forecast_delta"]["expense_irpf_deductible_eur"] == Decimal("309.19")
    projected = dashboard["tax_arithmetic_preview"]["projected_reviewed"]["modelo130"]["values"]
    assert projected["01"] == Decimal("42565.32")
    assert projected["02"] == Decimal("12188.22")
    assert projected["difficult_expenses"] == Decimal("1598.80")
    assert projected["05"] == Decimal("5298.13")
    assert projected["19"] == Decimal("777.29")
    assert dashboard["tax_arithmetic_preview"]["projected_reviewed"]["modelo303"]["values"][
        "110"
    ] == Decimal("1383.21")
    assert any(row["kind"] == "supplier_invoice_missing" for row in dashboard["blocking_items"])
    assert sum(row["kind"] == "amortization_asset_link_missing" for row in dashboard["blocking_items"]) == 4
    assert {row["kind"] for row in dashboard["blocking_items"]}.issuperset(
        {"document_review", "transaction_review", "target_derived_fx", "invalid_amount", "out_of_period"}
    )
    assert any(
        row["kind"] == "obligation_filing_pending"
        and row["reference"] == "130"
        and row["due_on"] == "2026-10-20"
        for row in dashboard["expected_items"]
    )
    assert not any(
        row["kind"] == "obligation" and row["reference"] == "130"
        for row in dashboard["blocking_items"]
    )

    outputs = write_current_quarter_dashboard(dashboard, tmp_path)
    assert outputs["json"].is_file()
    assert "not filing-ready" in outputs["markdown"].read_text(encoding="utf-8")


def test_dashboard_as_of_excludes_future_posted_rows_from_actual() -> None:
    posted = _row("posted", date(2026, 7, 1), "income", "100.00")
    future_posted = _row("future-posted", date(2026, 8, 1), "income", "200.00")
    validation = {
        "ready": False,
        "blocking_issues": [],
        "unresolved_obligations": [],
        "review_documents": [],
        "review_transactions": [],
        "target_derived_fx": [],
        "invalid_amount_transactions": [],
        "out_of_period_transactions": [],
    }

    dashboard = build_current_quarter_dashboard(
        actual_rows=[posted, future_posted],
        projected_rows=[posted, future_posted],
        period_key="2026-Q3",
        as_of=date(2026, 7, 16),
        difficult_expenses_rate=Decimal("0.05"),
        previous_positive_casilla_07=Decimal("0.00"),
        previous_negative_carry=Decimal("0.00"),
        previous_vat_compensation=Decimal("0.00"),
        approved_current_rows=[],
        obligations=[],
        period_validation=validation,
    )

    assert dashboard["posted_actual"]["income_eur"] == Decimal("100.00")
    assert dashboard["approved_forecast_delta"]["income_eur"] == Decimal("0.00")
    assert any(
        row["kind"] == "future_posted_after_as_of"
        and row["reference"] == "future-posted"
        for row in dashboard["blocking_items"]
    )


def test_dashboard_separates_future_approved_forecasts_from_overdue_rows() -> None:
    validation = {
        "ready": False,
        "blocking_issues": [],
        "unresolved_obligations": [],
        "review_documents": [],
        "review_transactions": [],
        "target_derived_fx": [],
        "xolo_recorded_fx_after_cutover": [],
        "invalid_amount_transactions": [],
        "out_of_period_transactions": [],
    }
    approved_rows = [
        {
            "transaction_id": "future-amortization",
            "transaction_date": "2026-09-30",
            "description": "Scheduled Q3 amortization",
            "tax_code": "historical_g03",
            "asset_id": "asset-1",
            "document_type": "gastos_book",
        },
        {
            "transaction_id": "overdue-expense",
            "transaction_date": "2026-07-10",
            "description": "Reviewed expense waiting for posting",
            "tax_code": "domestic_input",
            "document_type": "expense_invoice",
        },
        {
            "transaction_id": "missing-date",
            "description": "Malformed reviewed row",
            "tax_code": "domestic_input",
            "document_type": "expense_invoice",
        },
    ]

    dashboard = build_current_quarter_dashboard(
        actual_rows=[],
        projected_rows=[],
        period_key="2026-Q3",
        as_of=date(2026, 7, 18),
        difficult_expenses_rate=Decimal("0.05"),
        previous_positive_casilla_07=Decimal("0.00"),
        previous_negative_carry=Decimal("0.00"),
        previous_vat_compensation=Decimal("0.00"),
        approved_current_rows=approved_rows,
        obligations=[],
        period_validation=validation,
    )

    assert dashboard["expected_items"] == [
        {
            "kind": "approved_forecast_pending",
            "reference": "future-amortization",
            "detail": "Scheduled Q3 amortization",
            "tax_date": "2026-09-30",
        }
    ]
    blockers = {
        (row["kind"], row["reference"], row.get("tax_date"))
        for row in dashboard["blocking_items"]
    }
    assert ("approved_not_posted", "overdue-expense", "2026-07-10") in blockers
    assert ("approved_not_posted", "missing-date", None) in blockers
    assert not any(reference == "future-amortization" for _, reference, _ in blockers)


def test_dashboard_blocks_deadline_mismatch_and_missing_confirmed_deadline(tmp_path) -> None:
    validation = {
        "ready": False,
        "blocking_issues": [],
        "unresolved_obligations": [],
        "review_documents": [],
        "review_transactions": [],
        "target_derived_fx": [],
        "invalid_amount_transactions": [],
        "out_of_period_transactions": [],
    }
    obligations = [
        {
            "obligation_code": "130",
            "determination": "due",
            "filing_status": "due",
            "due_on": "2026-10-19",
            "obligation_due_on": "2026-10-19",
            "calendar_statutory_due_on": "2026-10-20",
            "internal_due_on": "2026-10-14",
            "direct_debit_cutoff_on": "2026-10-15",
            "deadline_status": "confirmed",
            "calendar_deadline_mismatch": True,
        },
        {
            "obligation_code": "303",
            "determination": "due",
            "filing_status": "due",
            "due_on": None,
            "calendar_statutory_due_on": "2027-01-30",
            "deadline_status": "provisional",
            "calendar_deadline_mismatch": False,
        },
        {
            "obligation_code": "111",
            "determination": "due",
            "filing_status": "filed",
            "due_on": None,
            "deadline_status": None,
            "calendar_deadline_mismatch": False,
        },
        {
            "obligation_code": "115",
            "determination": "due",
            "filing_status": "filed",
            "due_on": "2026-10-19",
            "obligation_due_on": "2026-10-19",
            "calendar_statutory_due_on": "2026-10-20",
            "deadline_status": "confirmed",
            "calendar_deadline_mismatch": True,
        },
    ]

    dashboard = build_current_quarter_dashboard(
        actual_rows=[],
        projected_rows=[],
        period_key="2026-Q3",
        as_of=date(2026, 7, 17),
        difficult_expenses_rate=Decimal("0.05"),
        previous_positive_casilla_07=Decimal("0.00"),
        previous_negative_carry=Decimal("0.00"),
        previous_vat_compensation=Decimal("0.00"),
        approved_current_rows=[],
        obligations=obligations,
        period_validation=validation,
    )

    blockers = {(row["kind"], row["reference"]) for row in dashboard["blocking_items"]}
    assert ("calendar_deadline_mismatch", "130") in blockers
    assert ("confirmed_deadline_missing", "303") in blockers
    assert ("confirmed_deadline_missing", "111") not in blockers
    assert ("calendar_deadline_mismatch", "115") not in blockers
    outputs = write_current_quarter_dashboard(dashboard, tmp_path)
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "2026-10-14" in markdown
    assert "2026-10-15" in markdown
    assert "2026-10-20" in markdown
    assert "2027-01-30" not in markdown


def test_dashboard_due_form_is_expected_through_deadline_and_blocking_after(tmp_path) -> None:
    validation = {
        "ready": False,
        "blocking_issues": [],
        "unresolved_obligations": [
            {
                "obligation_code": "130",
                "determination": "due",
                "filing_status": "due",
            }
        ],
        "review_documents": [],
        "review_transactions": [],
        "target_derived_fx": [],
        "invalid_amount_transactions": [],
        "out_of_period_transactions": [],
    }
    obligations = [
        {
            "obligation_code": "130",
            "determination": "due",
            "filing_status": "due",
            "due_on": "2026-10-20",
            "calendar_deadline_mismatch": False,
        }
    ]
    common = {
        "actual_rows": [],
        "projected_rows": [],
        "period_key": "2026-Q3",
        "difficult_expenses_rate": Decimal("0.05"),
        "previous_positive_casilla_07": Decimal("0.00"),
        "previous_negative_carry": Decimal("0.00"),
        "previous_vat_compensation": Decimal("0.00"),
        "approved_current_rows": [],
        "obligations": obligations,
        "period_validation": validation,
    }

    on_deadline = build_current_quarter_dashboard(
        **common,
        as_of=date(2026, 10, 20),
    )
    overdue = build_current_quarter_dashboard(
        **common,
        as_of=date(2026, 10, 21),
    )

    assert on_deadline["blocking_items"] == []
    assert on_deadline["expected_items"] == [
        {
            "kind": "obligation_filing_pending",
            "reference": "130",
            "detail": "due",
            "due_on": "2026-10-20",
        }
    ]
    outputs = write_current_quarter_dashboard(on_deadline, tmp_path)
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "## Blocking items\n\nNone." in markdown
    assert "Quarter-end completeness review is still required" not in markdown
    assert overdue["expected_items"] == []
    assert overdue["blocking_items"] == [
        {
            "kind": "obligation",
            "reference": "130",
            "detail": "due",
            "due_on": "2026-10-20",
        }
    ]


def test_xolo_comparison_exposes_omitted_allowance_and_prior_quarter(tmp_path) -> None:
    source = tmp_path / "xolo.csv"
    source.write_text(
        "period,xolo_status,amount_due,total_compounded_sales_ytd,"
        "total_compounded_deductible_expenses_ytd,net_results_ytd,net_results_20_percent,"
        "previous_quarters_compensation,withholding_taxes,article_110_3_reduction,"
        "payable_irpf_for_quarter\n"
        "2026-Q3,forecast_or_unsubmitted,3736.17,42565.32,10589.42,31975.90,"
        "6395.18,2659.01,0.00,,3736.17\n",
        encoding="utf-8",
    )
    dashboard = {
        "policy": {"previous_positive_casilla_07": Decimal("5298.13")},
        "tax_arithmetic_preview": {
            "projected_reviewed": {
                "modelo130": {
                    "values": {
                        "01": Decimal("42565.32"),
                        "02": Decimal("12188.22"),
                        "03": Decimal("30377.10"),
                        "04": Decimal("6075.42"),
                        "05": Decimal("5298.13"),
                        "06": Decimal("0.00"),
                        "13": Decimal("0.00"),
                        "19": Decimal("777.29"),
                    }
                }
            }
        },
        "warnings": [],
    }

    add_xolo_comparison(
        dashboard,
        load_xolo_modelo130_forecast(source, period="2026-Q3"),
    )

    assert dashboard["xolo_forecast"]["difference_projected_minus_xolo"]["02"] == Decimal("1598.80")
    assert dashboard["xolo_forecast"]["difference_projected_minus_xolo"]["05"] == Decimal("2639.12")
    assert dashboard["xolo_forecast"]["difference_projected_minus_xolo"]["19"] == Decimal("-2958.88")
    assert "prior filed quarters" in dashboard["warnings"][0]
