from autonomo_test_support.tax_engine import row
from datetime import date
from decimal import Decimal

import pytest

from autonomo_taxes.tax_engine import (
    CalculationBlocked,
    CalculationResult,
    TaxRow,
    calculate_modelo100_business_support,
    calculate_modelo130_rows,
    calculate_modelo303_rows,
    calculate_modelo347_rows,
    calculate_modelo349_rows,
    calculate_modelo390,
    calculate_retention_rows,
)




def _modelo303_settlement(
    period: str,
    net_before_compensation: str,
    opening: str,
    applied: str,
    pending: str,
    generated: str,
) -> CalculationResult:
    net = Decimal(net_before_compensation)
    applied_value = Decimal(applied)
    pending_value = Decimal(pending)
    generated_value = Decimal(generated)
    return CalculationResult(
        "303",
        period,
        {
            "27": max(net, Decimal("0.00")),
            "45": max(-net, Decimal("0.00")),
            "64": net,
            "110": Decimal(opening),
            "78": applied_value,
            "87": pending_value,
            "71": net - applied_value,
            "72": generated_value,
            "compensation_carryforward": pending_value + generated_value,
            "result": net - applied_value,
        },
    )


def test_modelo130_has_row_lineage_and_year_rate() -> None:
    rows = [
        row(
            "income",
            kind="income",
            amount_eur=Decimal("10000"),
            taxable_base_eur=Decimal("10000"),
            include_modelo130=True,
        ),
        row("expense", deductible_irpf_eur=Decimal("2000"), include_modelo130=True),
    ]
    result, report = calculate_modelo130_rows(
        rows,
        year=2026,
        quarter=2,
        difficult_expenses_rate=Decimal("0.05"),
    )
    assert result.casilla_01 == Decimal("10000.00")
    assert result.casilla_02 == Decimal("2400.00")
    assert report.lineage["01"] == ("income",)
    assert report.lineage["02"] == ("expense",)


def test_modelo130_preserves_credit_note_sign_and_uses_row_withholding() -> None:
    rows = [
        row(
            "invoice",
            kind="income",
            amount_eur=Decimal("1000.00"),
            taxable_base_eur=Decimal("1000.00"),
            withholding_eur=Decimal("150.00"),
            include_modelo130=True,
        ),
        row(
            "credit-note",
            kind="income",
            amount_eur=Decimal("-100.00"),
            taxable_base_eur=Decimal("-100.00"),
            withholding_eur=Decimal("-15.00"),
            include_modelo130=True,
        ),
    ]
    result, _ = calculate_modelo130_rows(
        rows,
        year=2026,
        quarter=2,
        difficult_expenses_rate=Decimal("0.05"),
    )
    assert result.casilla_01 == Decimal("900.00")
    assert result.casilla_06 == Decimal("135.00")


def test_modelo303_reverse_charge_is_output_and_input() -> None:
    rows = [
        row("sale", kind="income", tax_code="domestic_output", include_modelo303=True),
        row("openai", tax_code="eu_service_expense", include_modelo303=True),
    ]
    report = calculate_modelo303_rows(rows, year=2026, quarter=2)
    assert report.values["domestic_output_vat"] == Decimal("21.00")
    assert report.values["reverse_charge_output_vat"] == Decimal("21.00")
    assert report.values["reverse_charge_input_vat"] == Decimal("21.00")
    assert report.values["result"] == Decimal("21.00")
    assert report.values["07"] == Decimal("100.00")
    assert report.values["08"] == Decimal("21.00")
    assert report.values["09"] == Decimal("21.00")
    assert report.values["10"] == Decimal("100.00")
    assert report.values["11"] == Decimal("21.00")
    assert report.values["36"] == Decimal("100.00")
    assert report.values["37"] == Decimal("21.00")
    assert report.values["27"] == Decimal("42.00")
    assert report.values["45"] == Decimal("21.00")
    assert report.values["46"] == Decimal("21.00")
    assert report.values["71"] == Decimal("21.00")


def test_modelo303_requires_explicit_tax_code_for_zero_rate_output() -> None:
    missing_vat = row(
        "unclassified-zero",
        kind="income",
        tax_code="domestic_output",
        taxable_base_eur=Decimal("100.00"),
        vat_eur=Decimal("0.00"),
        include_modelo303=True,
    )
    with pytest.raises(CalculationBlocked, match="supported output VAT rate"):
        calculate_modelo303_rows([missing_vat], year=2026, quarter=2)

    explicit_zero = row(
        "explicit-zero",
        kind="income",
        tax_code="domestic_output_zero",
        taxable_base_eur=Decimal("100.00"),
        vat_eur=Decimal("0.00"),
        include_modelo303=True,
    )
    report = calculate_modelo303_rows([explicit_zero], year=2026, quarter=2)
    assert report.values["150"] == Decimal("100.00")


def test_modelo347_nets_credit_notes_and_separates_operation_keys_and_quarters() -> None:
    rows = [
        row(
            "sale-q1",
            kind="income",
            tax_date=date(2026, 2, 1),
            amount_eur=Decimal("4000.00"),
            counterparty_id="customer",
            include_modelo347=True,
        ),
        row(
            "credit-q2",
            kind="income",
            tax_date=date(2026, 4, 1),
            amount_eur=Decimal("-500.00"),
            counterparty_id="customer",
            include_modelo347=True,
        ),
        row(
            "purchase",
            kind="expense",
            tax_date=date(2026, 7, 1),
            amount_eur=Decimal("3200.00"),
            counterparty_id="customer",
            include_modelo347=True,
        ),
    ]
    report = calculate_modelo347_rows(rows, year=2026)
    assert report.values["B:customer:annual"] == Decimal("3500.00")
    assert report.values["B:customer:Q1"] == Decimal("4000.00")
    assert report.values["B:customer:Q2"] == Decimal("-500.00")
    assert report.values["A:customer:annual"] == Decimal("3200.00")


def test_modelo303_separates_other_reverse_charge_and_information_boxes() -> None:
    rows = [
        row("non-eu", tax_code="non_eu_service_expense", include_modelo303=True),
        row(
            "eu-sale",
            kind="income",
            amount_eur=Decimal("500.00"),
            taxable_base_eur=Decimal("500.00"),
            vat_eur=Decimal("0.00"),
            deductible_vat_eur=Decimal("0.00"),
            tax_code="eu_service_income",
            include_modelo303=True,
        ),
        row(
            "export",
            kind="income",
            amount_eur=Decimal("300.00"),
            taxable_base_eur=Decimal("300.00"),
            vat_eur=Decimal("0.00"),
            deductible_vat_eur=Decimal("0.00"),
            tax_code="export",
            include_modelo303=True,
        ),
        row(
            "outside",
            kind="income",
            amount_eur=Decimal("200.00"),
            taxable_base_eur=Decimal("200.00"),
            vat_eur=Decimal("0.00"),
            deductible_vat_eur=Decimal("0.00"),
            tax_code="outside_scope",
            include_modelo303=True,
        ),
    ]
    report = calculate_modelo303_rows(rows, year=2026, quarter=2)
    assert report.values["10"] == Decimal("0.00")
    assert report.values["12"] == Decimal("100.00")
    assert report.values["13"] == Decimal("21.00")
    assert report.values["28"] == Decimal("100.00")
    assert report.values["29"] == Decimal("21.00")
    assert report.values["59"] == Decimal("500.00")
    assert report.values["60"] == Decimal("300.00")
    assert report.values["120"] == Decimal("200.00")
    assert report.values["71"] == Decimal("0.00")


def test_modelo303_q2_2026_filed_values_reproduce_from_source_book_categories() -> None:
    rows = [
        row(
            "foreign-sales",
            kind="income",
            amount_eur=Decimal("18038.90"),
            taxable_base_eur=Decimal("18038.90"),
            vat_eur=Decimal("0.00"),
            deductible_vat_eur=Decimal("0.00"),
            tax_code="outside_scope",
            include_modelo303=True,
        ),
        row(
            "non-eu-reverse-charge",
            amount_eur=Decimal("2209.54"),
            taxable_base_eur=Decimal("2209.54"),
            vat_eur=Decimal("464.00"),
            deductible_vat_eur=Decimal("464.00"),
            tax_code="non_eu_service_expense",
            include_modelo303=True,
        ),
        row(
            "domestic-input",
            amount_eur=Decimal("2347.17"),
            taxable_base_eur=Decimal("1939.81"),
            vat_eur=Decimal("407.36"),
            deductible_vat_eur=Decimal("407.36"),
            tax_code="domestic_input",
            include_modelo303=True,
        ),
        row(
            "eu-supplier-with-spanish-vat-not-deducted",
            amount_eur=Decimal("309.00"),
            taxable_base_eur=Decimal("0.00"),
            vat_eur=Decimal("0.00"),
            deductible_vat_eur=Decimal("0.00"),
            tax_code="historical_g19",
            country_code="IE",
            vat_id="IETEST-TAX-ID-004",
            include_modelo303=False,
        ),
    ]

    report = calculate_modelo303_rows(rows, year=2026, quarter=2)
    modelo349 = calculate_modelo349_rows(rows, year=2026, quarter=2)

    assert report.values["12"] == Decimal("2209.54")
    assert report.values["13"] == Decimal("464.00")
    assert report.values["27"] == Decimal("464.00")
    assert report.values["28"] == Decimal("4149.35")
    assert report.values["29"] == Decimal("871.36")
    assert report.values["45"] == Decimal("871.36")
    assert report.values["46"] == Decimal("-407.36")
    assert report.values["120"] == Decimal("18038.90")
    assert report.values["71"] == Decimal("-407.36")
    assert modelo349.values == {}


def test_modelo303_negative_result_preserves_previous_and_adds_current_compensation() -> None:
    rows = [
        row(
            "domestic-input",
            amount_eur=Decimal("121.00"),
            taxable_base_eur=Decimal("100.00"),
            vat_eur=Decimal("21.00"),
            deductible_vat_eur=Decimal("21.00"),
            tax_code="domestic_input",
            include_modelo303=True,
        )
    ]

    report = calculate_modelo303_rows(
        rows,
        year=2026,
        quarter=2,
        previous_compensation=Decimal("1383.21"),
    )

    assert report.values["110"] == Decimal("1383.21")
    assert report.values["78"] == Decimal("0.00")
    assert report.values["87"] == Decimal("1383.21")
    assert report.values["71"] == Decimal("-21.00")
    assert report.values["72"] == Decimal("21.00")
    assert report.values["compensation_carryforward"] == Decimal("1404.21")


def test_modelo303_positive_result_uses_previous_compensation_before_payment() -> None:
    rows = [
        row(
            "domestic-output",
            kind="income",
            amount_eur=Decimal("1210.00"),
            taxable_base_eur=Decimal("1000.00"),
            vat_eur=Decimal("210.00"),
            deductible_vat_eur=Decimal("0.00"),
            tax_code="domestic_output",
            include_modelo303=True,
        )
    ]

    report = calculate_modelo303_rows(
        rows,
        year=2026,
        quarter=2,
        previous_compensation=Decimal("1383.21"),
    )

    assert report.values["78"] == Decimal("210.00")
    assert report.values["87"] == Decimal("1173.21")
    assert report.values["69"] == Decimal("0.00")
    assert report.values["71"] == Decimal("0.00")
    assert report.values["72"] == Decimal("0.00")
    assert report.values["result"] == Decimal("0.00")
    assert report.values["compensation_carryforward"] == Decimal("1173.21")


def test_modelo303_payment_remains_after_previous_compensation_is_exhausted() -> None:
    rows = [
        row(
            "domestic-output",
            kind="income",
            amount_eur=Decimal("1210.00"),
            taxable_base_eur=Decimal("1000.00"),
            vat_eur=Decimal("210.00"),
            deductible_vat_eur=Decimal("0.00"),
            tax_code="domestic_output",
            include_modelo303=True,
        )
    ]

    report = calculate_modelo303_rows(
        rows,
        year=2026,
        quarter=2,
        previous_compensation=Decimal("50.00"),
    )

    assert report.values["78"] == Decimal("50.00")
    assert report.values["87"] == Decimal("0.00")
    assert report.values["71"] == Decimal("160.00")
    assert report.values["result"] == Decimal("160.00")
    assert report.values["compensation_carryforward"] == Decimal("0.00")


def test_modelo303_q4_can_request_refund_instead_of_carrying_compensation() -> None:
    report = calculate_modelo303_rows(
        [
            row(
                "domestic-input",
                tax_date=date(2023, 12, 1),
                taxable_base_eur=Decimal("100.00"),
                vat_eur=Decimal("21.00"),
                deductible_vat_eur=Decimal("21.00"),
                tax_code="domestic_input",
                include_modelo303=True,
            )
        ],
        year=2023,
        quarter=4,
        previous_compensation=Decimal("34.65"),
        final_settlement="refund",
    )

    assert report.values["78"] == Decimal("34.65")
    assert report.values["87"] == Decimal("0.00")
    assert report.values["69"] == Decimal("-55.65")
    assert report.values["71"] == Decimal("-55.65")
    assert report.values["72"] == Decimal("0.00")
    assert report.values["73"] == Decimal("55.65")
    assert report.values["compensation_carryforward"] == Decimal("0.00")


def test_modelo303_refund_is_rejected_before_q4() -> None:
    with pytest.raises(CalculationBlocked, match="only for the final period"):
        calculate_modelo303_rows([], year=2026, quarter=3, final_settlement="refund")


def test_modelo303_q1_2026_reproduces_reviewed_anysphere_reverse_charge() -> None:
    rows = [
        row(
            "existing-reverse-charge",
            tax_date=date(2026, 1, 31),
            amount_eur=Decimal("3740.55"),
            taxable_base_eur=Decimal("3740.55"),
            vat_eur=Decimal("785.52"),
            deductible_vat_eur=Decimal("785.52"),
            tax_code="non_eu_service_expense",
            include_modelo303=True,
        ),
        row(
            "existing-domestic-input",
            tax_date=date(2026, 1, 31),
            amount_eur=Decimal("308.74"),
            taxable_base_eur=Decimal("255.16"),
            vat_eur=Decimal("53.58"),
            deductible_vat_eur=Decimal("53.58"),
            tax_code="domestic_input",
            include_modelo303=True,
        ),
        row(
            "00011",
            tax_date=date(2026, 1, 31),
            amount_eur=Decimal("60.91"),
            taxable_base_eur=Decimal("60.91"),
            vat_eur=Decimal("12.79"),
            deductible_vat_eur=Decimal("12.79"),
            tax_code="non_eu_service_expense",
            include_modelo303=True,
        ),
    ]

    report = calculate_modelo303_rows(rows, year=2026, quarter=1)

    assert report.values["12"] == Decimal("3801.46")
    assert report.values["13"] == Decimal("798.31")
    assert report.values["28"] == Decimal("4056.62")
    assert report.values["29"] == Decimal("851.89")
    assert report.values["46"] == Decimal("-53.58")
    assert report.values["71"] == Decimal("-53.58")
    assert "00011" in report.lineage["12"]
    assert "00011" in report.lineage["28"]


def test_modelo303_unknown_treatment_blocks() -> None:
    with pytest.raises(CalculationBlocked, match="unknown tax treatment"):
        calculate_modelo303_rows([row("unknown", tax_code="unknown", include_modelo303=True)], year=2026, quarter=2)


def test_modelo303_unsupported_treatment_blocks() -> None:
    with pytest.raises(CalculationBlocked, match="unsupported tax treatment"):
        calculate_modelo303_rows(
            [row("unsupported", tax_code="special_margin_scheme", include_modelo303=True)],
            year=2026,
            quarter=2,
        )


def test_modelo349_requires_vat_id() -> None:
    item = row("eu", tax_code="eu_service_expense", vat_id="")
    with pytest.raises(CalculationBlocked, match="requires EU VAT IDs"):
        calculate_modelo349_rows([item], year=2026, quarter=2)


def test_modelo349_rejects_oss_non_union_identifier_but_keeps_member_state_vat_id() -> None:
    member_state = row("ie-supplier", tax_code="eu_service_expense", vat_id="IETEST-TAX-ID-004")
    oss_supplier = row("oss-supplier", tax_code="eu_service_expense", vat_id="EU123456789")

    report = calculate_modelo349_rows([member_state], year=2026, quarter=2)

    assert report.values == {"IETEST-TAX-ID-004:I": Decimal("100.00")}
    with pytest.raises(CalculationBlocked, match="OSS non-Union scheme identifiers .* oss-supplier"):
        calculate_modelo349_rows([member_state, oss_supplier], year=2026, quarter=2)


def test_modelo390_uses_inventory_periods_instead_of_fixed_quarter_count() -> None:
    reports = [
        calculate_modelo303_rows([], year=2026, quarter=quarter)
        for quarter in (1, 2, 4)
    ]
    annual = calculate_modelo390(
        reports,
        year=2026,
        expected_periods=("2026-Q1", "2026-Q2", "2026-Q4"),
    )
    assert annual.form == "390"
    assert annual.values["result"] == Decimal("0.00")


def test_modelo390_blocks_when_discovered_period_is_missing() -> None:
    reports = [calculate_modelo303_rows([], year=2026, quarter=1)]
    with pytest.raises(CalculationBlocked, match="period inventory mismatch"):
        calculate_modelo390(
            reports,
            year=2026,
            expected_periods=("2026-Q1", "2026-Q2"),
        )


def test_modelo390_keeps_prior_year_and_current_year_compensation_separate() -> None:
    reports = [
        _modelo303_settlement("2025-Q1", "-37.17", "433.08", "0.00", "433.08", "37.17"),
        _modelo303_settlement("2025-Q2", "-326.14", "470.25", "0.00", "470.25", "326.14"),
        _modelo303_settlement("2025-Q3", "-80.52", "796.39", "0.00", "796.39", "80.52"),
        _modelo303_settlement("2025-Q4", "-45.36", "876.91", "0.00", "876.91", "45.36"),
    ]

    annual = calculate_modelo390(
        reports,
        year=2025,
        expected_periods=tuple(report.period for report in reports),
    )

    assert annual.values["84"] == Decimal("-489.19")
    assert annual.values["85"] == Decimal("0.00")
    assert annual.values["86"] == Decimal("-489.19")
    assert annual.values["95"] == Decimal("0.00")
    assert annual.values["97"] == Decimal("45.36")
    assert annual.values["662"] == Decimal("443.83")
    assert annual.values["closing_compensation"] == Decimal("922.27")


def test_modelo390_counts_only_applied_prior_year_compensation_in_box_85() -> None:
    reports = [
        _modelo303_settlement("2026-Q1", "60.00", "100.00", "60.00", "40.00", "0.00"),
        _modelo303_settlement("2026-Q2", "-20.00", "40.00", "0.00", "40.00", "20.00"),
        _modelo303_settlement("2026-Q3", "10.00", "60.00", "10.00", "50.00", "0.00"),
        _modelo303_settlement("2026-Q4", "-5.00", "50.00", "0.00", "50.00", "5.00"),
    ]

    annual = calculate_modelo390(reports, year=2026)

    assert annual.values["84"] == Decimal("45.00")
    assert annual.values["85"] == Decimal("70.00")
    assert annual.values["86"] == Decimal("-25.00")
    assert annual.values["662"] == Decimal("20.00")
    assert annual.values["97"] == Decimal("5.00")
    assert annual.values["closing_compensation"] == Decimal("55.00")


def test_modelo390_reports_final_period_refund_in_box_98() -> None:
    reports = [
        _modelo303_settlement("2023-Q2", "-34.65", "0.00", "0.00", "0.00", "34.65"),
        _modelo303_settlement("2023-Q3", "0.00", "34.65", "0.00", "34.65", "0.00"),
        CalculationResult(
            "303",
            "2023-Q4",
            {
                "27": Decimal("0.00"),
                "45": Decimal("136.30"),
                "64": Decimal("-136.30"),
                "110": Decimal("34.65"),
                "78": Decimal("34.65"),
                "87": Decimal("0.00"),
                "71": Decimal("-170.95"),
                "72": Decimal("0.00"),
                "73": Decimal("170.95"),
                "compensation_carryforward": Decimal("0.00"),
            },
        ),
    ]

    annual = calculate_modelo390(reports, year=2023)

    assert annual.values["97"] == Decimal("0.00")
    assert annual.values["98"] == Decimal("170.95")
    assert annual.values["closing_compensation"] == Decimal("0.00")


def test_retention_forms_share_structured_rows() -> None:
    item = row(
        "professional",
        counterparty_id="supplier-1",
        withholding_type="professional",
        withholding_eur=Decimal("15.00"),
    )
    quarterly = calculate_retention_rows([item], year=2026, quarter=2, withholding_type="professional")
    annual = calculate_retention_rows([item], year=2026, quarter=None, withholding_type="professional")
    assert quarterly.form == "111"
    assert annual.form == "190"
    assert quarterly.values["withholding"] == Decimal("15.00")


def test_nonresident_retention_forms_preserve_explicit_zero_withholding_reporting() -> None:
    item = row(
        "nonresident",
        counterparty_id="foreign-supplier-1",
        withholding_type="nonresident",
        taxable_base_eur=Decimal("2002.40"),
        withholding_eur=Decimal("0.00"),
    )

    quarterly = calculate_retention_rows([item], year=2026, quarter=2, withholding_type="nonresident")
    annual = calculate_retention_rows([item], year=2026, quarter=None, withholding_type="nonresident")

    assert quarterly.form == "216"
    assert annual.form == "296"
    assert quarterly.values["base"] == Decimal("2002.40")
    assert quarterly.values["income_count"] == Decimal("1")
    assert quarterly.values["negative_return"] == "yes"
    assert quarterly.warnings
    assert "does not establish treaty relief" in quarterly.warnings[0]
    assert "certificate" not in quarterly.warnings[0].lower()


def test_nonresident_retention_with_tax_withheld_has_no_treaty_warning() -> None:
    item = row(
        "nonresident-withholding",
        counterparty_id="foreign-supplier-1",
        withholding_type="nonresident",
        taxable_base_eur=Decimal("1000.00"),
        withholding_eur=Decimal("240.00"),
    )

    quarterly = calculate_retention_rows([item], year=2026, quarter=2, withholding_type="nonresident")

    assert quarterly.values["negative_return"] == "no"
    assert quarterly.warnings == ()


def test_modelo100_support_fails_closed_on_unknown_family_category() -> None:
    with pytest.raises(CalculationBlocked, match="Renta WEB review required"):
        calculate_modelo100_business_support(
            [],
            year=2026,
            difficult_expenses_rate=Decimal("0.05"),
            unsupported_categories=["foreign_property"],
        )


def test_modelo100_support_uses_2023_seven_percent_when_supplied() -> None:
    rows = [
        row(
            "income",
            tax_date=date(2023, 6, 1),
            kind="income",
            amount_eur=Decimal("1000"),
            taxable_base_eur=Decimal("1000"),
            include_modelo130=True,
        )
    ]
    report = calculate_modelo100_business_support(
        rows,
        year=2023,
        difficult_expenses_rate=Decimal("0.07"),
    )
    assert report.values["difficult_expenses"] == Decimal("70.00")


def _new_activity_support(net_before_difficult: str, *, year: int = 2026, **facts):
    rows = [
        row(
            "income",
            tax_date=date(year, 6, 1),
            kind="income",
            amount_eur=Decimal(net_before_difficult),
            taxable_base_eur=Decimal(net_before_difficult),
            include_modelo130=True,
        )
    ]
    facts.setdefault(
        "business_activities",
        [{"starts_on": "2025-03-01", "ends_on": None, "irpf_method": "estimacion_directa_simplificada"}],
    )
    return calculate_modelo100_business_support(
        rows,
        year=year,
        difficult_expenses_rate=Decimal("0.05"),
        **facts,
    ).values


ATTESTED = {
    "new_activity_prior_activity": "none",
    "new_activity_former_employer_over_half": False,
}


@pytest.mark.parametrize("first_positive_year", [2025, 2026])
def test_new_activity_reduction_eligible_in_first_positive_year_and_next(first_positive_year) -> None:
    values = _new_activity_support(
        "30000.00", new_activity_first_positive_year=first_positive_year, **ATTESTED
    )

    assert values["business_net_income"] == Decimal("28500.00")
    assert values["new_activity_reduction_status"] == "eligible"
    assert values["new_activity_reduction_window_years"] == [first_positive_year, first_positive_year + 1]
    assert values["new_activity_reduction_base"] == Decimal("28500.00")
    assert values["new_activity_reduction_amount"] == Decimal("5700.00")
    assert values["new_activity_reduction_missing_facts"] == []


def test_new_activity_reduction_caps_base_at_100000() -> None:
    values = _new_activity_support("152000.00", new_activity_first_positive_year=2026, **ATTESTED)

    assert values["business_net_income"] == Decimal("150000.00")
    assert values["new_activity_reduction_base"] == Decimal("100000.00")
    assert values["new_activity_reduction_amount"] == Decimal("20000.00")


def test_new_activity_reduction_derives_first_positive_year_only_in_start_year() -> None:
    activity = [{"starts_on": "2026-02-01", "ends_on": None, "irpf_method": "estimacion_directa_simplificada"}]
    values = _new_activity_support("10000.00", business_activities=activity, **ATTESTED)

    assert values["new_activity_reduction_status"] == "eligible"
    assert values["new_activity_reduction_window_years"] == [2026, 2027]


@pytest.mark.parametrize(
    ("facts", "reason"),
    [
        (
            {
                **ATTESTED,
                "new_activity_first_positive_year": 2024,
                "business_activities": [
                    {"starts_on": "2023-09-01", "ends_on": None, "irpf_method": "estimacion_directa_simplificada"}
                ],
            },
            "outside_reduction_window",
        ),
        (
            {**ATTESTED, "new_activity_former_employer_over_half": True, "new_activity_first_positive_year": 2026},
            "former_employer_income_over_half",
        ),
        ({"new_activity_prior_activity": "yes"}, "prior_activity_before_start"),
        (
            {
                **ATTESTED,
                "business_activities": [
                    {"starts_on": "2025-03-01", "ends_on": None, "irpf_method": "estimacion_objetiva"}
                ],
            },
            "irpf_method_not_direct_estimation",
        ),
    ],
)
def test_new_activity_reduction_not_eligible(facts, reason) -> None:
    values = _new_activity_support("30000.00", **facts)

    assert values["new_activity_reduction_status"] == "not_eligible"
    assert values["new_activity_reduction_reason"] == reason
    assert values["new_activity_reduction_base"] is None
    assert values["new_activity_reduction_amount"] is None
    assert values["business_net_income"] == Decimal("28500.00")


def test_new_activity_reduction_is_unknown_without_attested_facts() -> None:
    values = _new_activity_support("30000.00")

    assert values["new_activity_reduction_status"] == "unknown"
    assert values["new_activity_reduction_reason"] == "missing_facts"
    assert values["new_activity_reduction_missing_facts"] == [
        "prior_activity_before_start",
        "former_employer_income_over_half",
        "first_positive_net_year",
    ]
    assert values["new_activity_reduction_amount"] is None

    no_activity = _new_activity_support(
        "30000.00", business_activities=[], new_activity_first_positive_year=2026, **ATTESTED
    )
    assert no_activity["new_activity_reduction_status"] == "unknown"
    assert no_activity["new_activity_reduction_missing_facts"] == ["business_activity"]

    two_activities = _new_activity_support(
        "30000.00",
        business_activities=[
            {"starts_on": "2025-03-01", "ends_on": None, "irpf_method": "estimacion_directa_simplificada"},
            {"starts_on": "2026-01-01", "ends_on": None, "irpf_method": "estimacion_directa_simplificada"},
        ],
        new_activity_first_positive_year=2026,
        **ATTESTED,
    )
    assert two_activities["new_activity_reduction_status"] == "unknown"
    assert two_activities["new_activity_reduction_reason"] == "multiple_business_activities"


def test_new_activity_reduction_rejects_first_positive_year_after_positive_year() -> None:
    with pytest.raises(ValueError, match="is after 2026"):
        _new_activity_support("30000.00", new_activity_first_positive_year=2027, **ATTESTED)


@pytest.mark.parametrize("prior_activity", ["none", "yes"])
def test_new_activity_reduction_rejects_first_positive_year_before_start(prior_activity) -> None:
    with pytest.raises(ValueError, match="precedes the activity start"):
        _new_activity_support(
            "30000.00",
            new_activity_first_positive_year=2024,
            new_activity_prior_activity=prior_activity,
            new_activity_former_employer_over_half=True,
        )


@pytest.mark.parametrize("net_before_difficult", ["0.00", "-500.00"])
def test_new_activity_reduction_needs_positive_net_income(net_before_difficult) -> None:
    values = _new_activity_support(net_before_difficult, new_activity_first_positive_year=2025, **ATTESTED)

    assert values["new_activity_reduction_status"] == "not_eligible"
    assert values["new_activity_reduction_reason"] == "net_income_not_positive"
    assert values["new_activity_reduction_amount"] is None


def test_new_activity_reduction_ignores_activities_outside_the_year() -> None:
    values = _new_activity_support(
        "30000.00",
        business_activities=[
            {"starts_on": "2020-01-01", "ends_on": "2023-06-30", "irpf_method": "estimacion_objetiva"},
            {"starts_on": "2025-03-01", "ends_on": None, "irpf_method": "estimacion_directa_simplificada"},
            {"starts_on": "2027-01-01", "ends_on": None, "irpf_method": "estimacion_objetiva"},
        ],
        new_activity_first_positive_year=2025,
        **ATTESTED,
    )

    assert values["new_activity_reduction_status"] == "eligible"


@pytest.mark.parametrize(
    ("previous_ends_on", "starts_on", "status"),
    [
        ("2025-10-31", "2026-02-01", "unknown"),
        ("2025-01-31", "2026-02-01", "eligible"),
        ("2027-02-28", "2028-02-29", "unknown"),
        ("2027-02-27", "2028-02-29", "eligible"),
    ],
)
def test_new_activity_reduction_flags_ledger_activity_in_the_year_before_start(
    previous_ends_on, starts_on, status
) -> None:
    year = int(starts_on[:4])
    activities = [
        {"starts_on": "2020-01-01", "ends_on": previous_ends_on, "irpf_method": "estimacion_directa_simplificada"},
        {"starts_on": starts_on, "ends_on": None, "irpf_method": "estimacion_directa_simplificada"},
    ]
    values = _new_activity_support("30000.00", year=year, business_activities=activities, **ATTESTED)

    assert values["new_activity_reduction_status"] == status
    if status == "unknown":
        assert values["new_activity_reduction_reason"] == "prior_activity_conflicts_with_ledger"
        never_positive = _new_activity_support(
            "30000.00",
            year=year,
            business_activities=activities,
            **{**ATTESTED, "new_activity_prior_activity": "never_positive"},
        )
        assert never_positive["new_activity_reduction_status"] == "eligible"


def test_new_activity_reduction_rounds_amount_to_cents() -> None:
    values = _new_activity_support("10526.35", new_activity_first_positive_year=2026, **ATTESTED)

    assert values["business_net_income"] == Decimal("10000.03")
    assert values["new_activity_reduction_base"] == Decimal("10000.03")
    assert values["new_activity_reduction_amount"] == Decimal("2000.01")
