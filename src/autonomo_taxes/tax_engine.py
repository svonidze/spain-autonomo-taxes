from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Iterable

from .modelo130 import Modelo130Result, calculate_modelo130
from .money import cents
from .vat_classification import LEGACY_VAT_CLASSIFICATION_WARNING, is_vat_investment_good


ZERO = Decimal("0.00")
INTRACOMMUNITY_ACQUISITION_CODES = {"eu_service_expense", "eu_goods_expense"}
OTHER_REVERSE_CHARGE_CODES = {
    "domestic_reverse_charge_expense",
    "non_eu_service_expense",
}
EU_ACQUISITION_CODES = INTRACOMMUNITY_ACQUISITION_CODES | OTHER_REVERSE_CHARGE_CODES
EU_349_CODES = {"eu_service_income", "eu_service_expense", "eu_goods_income", "eu_goods_expense"}
MODELO303_DOMESTIC_INPUT_CODES = {"domestic_input", "domestic_expense"}
MODELO303_IMPORT_CODES = {"import_goods_expense", "import_service_expense"}
MODELO303_INFORMATION_CODES = {"outside_scope", "export", "eu_service_income", "eu_goods_income"}
MODELO303_SUPPORTED_CODES = (
    {"domestic_output", "domestic_output_zero"}
    | MODELO303_DOMESTIC_INPUT_CODES
    | MODELO303_IMPORT_CODES
    | EU_ACQUISITION_CODES
    | MODELO303_INFORMATION_CODES
)
MODELO303_RATE_BOXES = {
    Decimal("0.00"): ("150", "151", "152"),
    Decimal("4.00"): ("01", "02", "03"),
    Decimal("10.00"): ("04", "05", "06"),
    Decimal("21.00"): ("07", "08", "09"),
}
MODELO303_RULE_SOURCE = (
    "AEAT Modelo 303 instructions 2026, pages 1-3: "
    "https://sede.agenciatributaria.gob.es/Sede/todas-gestiones/impuestos-tasas/iva/"
    "modelo-303-iva-autoliquidacion_/instrucciones-2026/instrucciones-02-12-2t-4t-2026.html"
)
MODELO390_RULE_SOURCE = (
    "AEAT Modelo 390 instructions 2025, sections 5, 7, 9 and 10: "
    "https://sede.agenciatributaria.gob.es/static_files/Sede/Procedimiento_ayuda/"
    "G412/Instrucciones_modelo_390-2025.pdf"
)
MODELO347_RULE_SOURCE = (
    "AEAT Modelo 347 instructions: operation key A/B, annual threshold EUR 3,005.06, "
    "quarterly breakdown, and amounts net of returns/discounts: "
    "https://sede.agenciatributaria.gob.es/Sede/todas-gestiones/impuestos-tasas/"
    "declaraciones-informativas/modelo-347-decla_____racion-anual-operaciones-personas_/"
    "importe-operaciones.html"
)
WITHHOLDING_TYPE_BY_TAX_CODE = {
    "professional_withholding": "professional",
    "rent_withholding": "rent",
    "nonresident_income": "nonresident",
}


class CalculationBlocked(ValueError):
    pass


@dataclass(frozen=True)
class TaxRow:
    transaction_id: str
    tax_date: date
    kind: str
    amount_eur: Decimal
    taxable_base_eur: Decimal = ZERO
    vat_eur: Decimal = ZERO
    deductible_irpf_eur: Decimal = ZERO
    deductible_vat_eur: Decimal = ZERO
    withholding_eur: Decimal = ZERO
    tax_code: str = "unknown"
    counterparty_id: str = ""
    counterparty_name: str = ""
    country_code: str = ""
    vat_id: str = ""
    include_modelo130: bool = False
    include_modelo303: bool = False
    include_modelo347: bool = False
    withholding_type: str = ""
    asset_id: str = ""
    vat_investment_good: bool | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"income", "expense", "adjustment"}:
            raise ValueError(f"Unsupported transaction kind: {self.kind}")
        if not self.transaction_id:
            raise ValueError("transaction_id is required")
        is_vat_investment_good(self.vat_investment_good, legacy_asset=bool(self.asset_id), tax_code=self.tax_code)


@dataclass(frozen=True)
class CalculationResult:
    form: str
    period: str
    values: dict[str, Decimal | str]
    lineage: dict[str, tuple[str, ...]] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def calculate_modelo130_rows(
    rows: Iterable[TaxRow],
    *,
    year: int,
    quarter: int,
    difficult_expenses_rate: Decimal,
    previous_positive_casilla_07: Decimal = ZERO,
    previous_negative_carry: Decimal = ZERO,
    withholding_and_payments: Decimal | None = None,
    reduction: Decimal = ZERO,
    include_difficult_expenses: bool = True,
) -> tuple[Modelo130Result, CalculationResult]:
    selected = [row for row in rows if _in_ytd(row.tax_date, year, quarter) and row.include_modelo130]
    incomes = [row for row in selected if row.kind == "income"]
    expenses = [row for row in selected if row.kind in {"expense", "adjustment"}]
    income = cents(
        sum(
            (
                row.taxable_base_eur if row.taxable_base_eur != ZERO else row.amount_eur
                for row in incomes
            ),
            ZERO,
        )
    )
    deductible = cents(sum((row.deductible_irpf_eur for row in expenses), ZERO))
    effective_withholding = (
        cents(sum((row.withholding_eur for row in incomes), ZERO))
        if withholding_and_payments is None
        else withholding_and_payments
    )
    result = calculate_modelo130(
        income_ytd=income,
        deductible_before_difficult_ytd=deductible,
        previous_positive_casilla_07=previous_positive_casilla_07,
        previous_negative_results=previous_negative_carry,
        retentions=effective_withholding,
        minoracion=reduction,
        include_difficult_expenses=include_difficult_expenses,
        difficult_expenses_rate=difficult_expenses_rate,
    )
    values = dict(result.as_dict())
    values["difficult_expenses"] = result.difficult_expenses
    lineage = {
        "01": tuple(row.transaction_id for row in incomes),
        "02": tuple(row.transaction_id for row in expenses),
    }
    return result, CalculationResult("130", f"{year}-Q{quarter}", values, lineage)


def calculate_modelo303_rows(
    rows: Iterable[TaxRow],
    *,
    year: int,
    quarter: int,
    previous_compensation: Decimal = ZERO,
    final_settlement: str = "compensate",
) -> CalculationResult:
    if final_settlement not in {"compensate", "refund"}:
        raise ValueError("final_settlement must be 'compensate' or 'refund'")
    if final_settlement == "refund" and quarter != 4:
        raise CalculationBlocked("Modelo 303 refund is available only for the final period of the year")
    previous_compensation = cents(max(previous_compensation, ZERO))
    selected = [row for row in rows if _in_quarter(row.tax_date, year, quarter) and row.include_modelo303]
    unknown = [row.transaction_id for row in selected if row.tax_code == "unknown"]
    if unknown:
        raise CalculationBlocked(f"Modelo 303 has unknown tax treatment: {', '.join(unknown)}")
    unsupported = [row.transaction_id for row in selected if row.tax_code not in MODELO303_SUPPORTED_CODES]
    if unsupported:
        raise CalculationBlocked(f"Modelo 303 has unsupported tax treatment: {', '.join(unsupported)}")

    output = [
        row
        for row in selected
        if row.kind == "income" and row.tax_code in {"domestic_output", "domestic_output_zero"}
    ]
    intracommunity = [
        row
        for row in selected
        if row.kind == "expense" and row.tax_code in INTRACOMMUNITY_ACQUISITION_CODES
    ]
    other_reverse = [
        row for row in selected if row.kind == "expense" and row.tax_code in OTHER_REVERSE_CHARGE_CODES
    ]
    domestic_input = [
        row for row in selected if row.kind == "expense" and row.tax_code in MODELO303_DOMESTIC_INPUT_CODES
    ]
    imports = [row for row in selected if row.kind == "expense" and row.tax_code in MODELO303_IMPORT_CODES]
    information = [
        row for row in selected if row.kind == "income" and row.tax_code in MODELO303_INFORMATION_CODES
    ]
    classified_ids = {
        row.transaction_id
        for row in output + intracommunity + other_reverse + domestic_input + imports + information
    }
    wrong_kind = [row.transaction_id for row in selected if row.transaction_id not in classified_ids]
    if wrong_kind:
        raise CalculationBlocked(f"Modelo 303 tax treatment is incompatible with transaction kind: {', '.join(wrong_kind)}")

    output_by_rate: dict[Decimal, list[TaxRow]] = {rate: [] for rate in MODELO303_RATE_BOXES}
    for row in output:
        output_by_rate[_modelo303_rate(row)].append(row)

    values: dict[str, Decimal | str] = {}
    lineage: dict[str, tuple[str, ...]] = {}
    for rate, (base_box, rate_box, vat_box) in MODELO303_RATE_BOXES.items():
        rate_rows = output_by_rate[rate]
        values[base_box] = _sum(rate_rows, "taxable_base_eur")
        values[rate_box] = rate if rate_rows else ZERO
        values[vat_box] = _sum(rate_rows, "vat_eur")
        lineage[base_box] = tuple(row.transaction_id for row in rate_rows)
        lineage[vat_box] = tuple(row.transaction_id for row in rate_rows)

    intracommunity_input = [row for row in intracommunity if row.deductible_vat_eur != ZERO]
    other_reverse_input = [row for row in other_reverse if row.deductible_vat_eur != ZERO]
    domestic_deductible = [row for row in domestic_input if row.deductible_vat_eur != ZERO]
    import_deductible = [row for row in imports if row.deductible_vat_eur != ZERO]
    current_domestic, asset_domestic = _split_current_and_assets(domestic_deductible + other_reverse_input)
    current_imports, asset_imports = _split_current_and_assets(import_deductible)
    current_intracommunity, asset_intracommunity = _split_current_and_assets(intracommunity_input)
    current_eu_goods, asset_eu_goods = _split_current_and_assets(
        row for row in intracommunity_input if row.tax_code == "eu_goods_expense"
    )
    eu_service_input = [
        row for row in intracommunity_input if row.tax_code == "eu_service_expense"
    ]

    box_rows: dict[tuple[str, str], list[TaxRow]] = {
        ("10", "11"): intracommunity,
        ("12", "13"): other_reverse,
        ("28", "29"): current_domestic,
        ("30", "31"): asset_domestic,
        ("32", "33"): current_imports,
        ("34", "35"): asset_imports,
        ("36", "37"): current_intracommunity,
        ("38", "39"): asset_intracommunity,
    }
    for (base_box, vat_box), box_values in box_rows.items():
        values[base_box] = _sum(box_values, "taxable_base_eur")
        vat_field = "vat_eur" if base_box in {"10", "12"} else "deductible_vat_eur"
        values[vat_box] = _sum(box_values, vat_field)
        lineage[base_box] = tuple(row.transaction_id for row in box_values)
        lineage[vat_box] = tuple(row.transaction_id for row in box_values)

    values["27"] = cents(sum((_decimal(values[key]) for key in ("152", "03", "06", "09", "11", "13")), ZERO))
    values["45"] = cents(sum((_decimal(values[key]) for key in ("29", "31", "33", "35", "37", "39")), ZERO))
    values["46"] = cents(_decimal(values["27"]) - _decimal(values["45"]))

    information_boxes = {
        "59": [row for row in information if row.tax_code in {"eu_service_income", "eu_goods_income"}],
        "60": [row for row in information if row.tax_code == "export"],
        "120": [row for row in information if row.tax_code == "outside_scope"],
    }
    for box, box_values in information_boxes.items():
        values[box] = _sum(box_values, "taxable_base_eur")
        lineage[box] = tuple(row.transaction_id for row in box_values)

    # The core engine currently models the common-territory general regime. Settlement
    # adjustments remain explicit future inputs rather than inferred balancing values.
    pre_compensation_result = cents(_decimal(values["46"]))
    compensation_applied = (
        previous_compensation
        if final_settlement == "refund"
        else min(previous_compensation, max(pre_compensation_result, ZERO))
    )
    pending_previous_compensation = cents(previous_compensation - compensation_applied)
    liquidation_result = cents(pre_compensation_result - compensation_applied)
    refundable = cents(max(-liquidation_result, ZERO))
    if final_settlement == "refund" and refundable == ZERO:
        raise CalculationBlocked("Modelo 303 refund requires a negative final-period result")
    current_period_compensation = refundable if final_settlement == "compensate" else ZERO
    refund_requested = refundable if final_settlement == "refund" else ZERO
    compensation_carryforward = cents(
        pending_previous_compensation + current_period_compensation
    )
    values.update(
        {
            "64": values["46"],
            "65": Decimal("100.00"),
            "66": values["46"],
            "69": liquidation_result,
            "110": previous_compensation,
            "78": compensation_applied,
            "87": pending_previous_compensation,
            "71": liquidation_result,
            "72": current_period_compensation,
            "73": refund_requested,
            "compensation_carryforward": compensation_carryforward,
            "domestic_output_base": _sum(output, "taxable_base_eur"),
            "domestic_output_vat": _sum(output, "vat_eur"),
            "reverse_charge_base": _sum(intracommunity + other_reverse, "taxable_base_eur"),
            "reverse_charge_output_vat": _sum(intracommunity + other_reverse, "vat_eur"),
            "domestic_input_base": _sum(current_domestic + asset_domestic, "taxable_base_eur"),
            "domestic_input_vat": _sum(current_domestic + asset_domestic, "deductible_vat_eur"),
            "domestic_current_input_base": _sum(current_domestic, "taxable_base_eur"),
            "domestic_current_input_vat": _sum(current_domestic, "deductible_vat_eur"),
            "domestic_asset_input_base": _sum(asset_domestic, "taxable_base_eur"),
            "domestic_asset_input_vat": _sum(asset_domestic, "deductible_vat_eur"),
            "import_current_input_base": _sum(current_imports, "taxable_base_eur"),
            "import_current_input_vat": _sum(current_imports, "deductible_vat_eur"),
            "import_asset_input_base": _sum(asset_imports, "taxable_base_eur"),
            "import_asset_input_vat": _sum(asset_imports, "deductible_vat_eur"),
            "intracommunity_goods_current_input_base": _sum(
                current_eu_goods,
                "taxable_base_eur",
            ),
            "intracommunity_goods_current_input_vat": _sum(
                current_eu_goods,
                "deductible_vat_eur",
            ),
            "intracommunity_goods_asset_input_base": _sum(
                asset_eu_goods,
                "taxable_base_eur",
            ),
            "intracommunity_goods_asset_input_vat": _sum(
                asset_eu_goods,
                "deductible_vat_eur",
            ),
            "intracommunity_service_input_base": _sum(
                eu_service_input,
                "taxable_base_eur",
            ),
            "intracommunity_service_input_vat": _sum(
                eu_service_input,
                "deductible_vat_eur",
            ),
            "reverse_charge_service_base": _sum(
                [
                    row
                    for row in intracommunity + other_reverse
                    if row.tax_code in {"eu_service_expense", "non_eu_service_expense"}
                ],
                "taxable_base_eur",
            ),
            "reverse_charge_input_vat": _sum(
                intracommunity_input + other_reverse_input,
                "deductible_vat_eur",
            ),
            "outside_scope_base": _sum(information, "taxable_base_eur"),
            "result": liquidation_result,
            "rule_source": MODELO303_RULE_SOURCE,
        }
    )
    lineage.update(
        {
            "27": tuple(row.transaction_id for row in output + intracommunity + other_reverse),
            "45": tuple(
                row.transaction_id
                for row in current_domestic
                + asset_domestic
                + current_imports
                + asset_imports
                + current_intracommunity
                + asset_intracommunity
            ),
        }
    )
    warnings = (
        "Casillas follow the cited AEAT 2026 general-regime structure; year-specific rule review remains required.",
        "Casilla 71 includes the explicit prior-period compensation balance; deferred import VAT, regional allocation, and rectification adjustments remain unsupported.",
    )
    deductible_inputs = domestic_deductible + other_reverse_input + import_deductible + intracommunity_input
    if any(row.vat_investment_good is None for row in deductible_inputs):
        warnings += (LEGACY_VAT_CLASSIFICATION_WARNING,)
    return CalculationResult("303", f"{year}-Q{quarter}", values, lineage, warnings)


def calculate_modelo349_rows(rows: Iterable[TaxRow], *, year: int, quarter: int) -> CalculationResult:
    selected = [
        row
        for row in rows
        if _in_quarter(row.tax_date, year, quarter) and row.tax_code in EU_349_CODES
    ]
    missing_vat_id = [row.transaction_id for row in selected if not row.vat_id]
    if missing_vat_id:
        raise CalculationBlocked(f"Modelo 349 requires EU VAT IDs: {', '.join(missing_vat_id)}")

    aggregates: dict[tuple[str, str], Decimal] = {}
    lineage: dict[str, list[str]] = {}
    for row in selected:
        operation_key = {
            "eu_service_income": "S",
            "eu_service_expense": "I",
            "eu_goods_income": "E",
            "eu_goods_expense": "A",
        }[row.tax_code]
        key = (row.vat_id, operation_key)
        aggregates[key] = cents(aggregates.get(key, ZERO) + row.taxable_base_eur)
        lineage.setdefault(f"{row.vat_id}:{operation_key}", []).append(row.transaction_id)
    values = {f"{vat_id}:{key}": amount for (vat_id, key), amount in sorted(aggregates.items())}
    return CalculationResult(
        "349",
        f"{year}-Q{quarter}",
        values,
        {key: tuple(ids) for key, ids in lineage.items()},
    )


def calculate_modelo390(
    quarters: Iterable[CalculationResult],
    *,
    year: int,
    expected_periods: Iterable[str] | None = None,
) -> CalculationResult:
    reports = [report for report in quarters if report.form == "303" and report.period.startswith(f"{year}-Q")]
    if not reports:
        raise CalculationBlocked("Modelo 390 requires Modelo 303 periods from the obligation inventory")
    actual_periods = [report.period for report in reports]
    if len(actual_periods) != len(set(actual_periods)):
        raise CalculationBlocked("Modelo 390 received duplicate Modelo 303 periods")
    expected = set(expected_periods) if expected_periods is not None else set(actual_periods)
    actual = set(actual_periods)
    if actual != expected:
        missing = ", ".join(sorted(expected - actual)) or "none"
        unexpected = ", ".join(sorted(actual - expected)) or "none"
        raise CalculationBlocked(
            f"Modelo 390 period inventory mismatch; missing: {missing}; unexpected: {unexpected}"
        )
    reports.sort(key=lambda report: report.period)
    prior_year_balance = _decimal(reports[0].values.get("110", ZERO))
    current_year_balance = ZERO
    prior_year_applied = ZERO
    total_payable = ZERO
    final_period_compensation = ZERO
    final_period_refund = ZERO
    previous_carry: Decimal | None = None
    last_index = len(reports) - 1
    for index, report in enumerate(reports):
        opening = _decimal(report.values.get("110", ZERO))
        applied = _decimal(report.values.get("78", ZERO))
        pending_previous = _decimal(report.values.get("87", ZERO))
        current_generated = _decimal(report.values.get("72", ZERO))
        current_refund = _decimal(report.values.get("73", ZERO))
        liquidation = _decimal(report.values.get("71", report.values.get("result", ZERO)))
        if any(value < ZERO for value in (opening, applied, pending_previous, current_generated, current_refund)):
            raise CalculationBlocked(f"Modelo 390 received a negative compensation casilla in {report.period}")
        if current_generated and current_refund:
            raise CalculationBlocked(
                f"Modelo 390 cannot treat {report.period} as both compensation and refund"
            )
        if current_refund and index != last_index:
            raise CalculationBlocked(
                f"Modelo 390 received a refund outside the final period in {report.period}"
            )
        if previous_carry is not None and abs(opening - previous_carry) > Decimal("0.02"):
            raise CalculationBlocked(
                f"Modelo 390 compensation chain breaks at {report.period}: "
                f"opening {opening:.2f}, expected {previous_carry:.2f}"
            )
        if applied - opening > Decimal("0.02"):
            raise CalculationBlocked(f"Modelo 390 applies more compensation than available in {report.period}")

        applied_to_prior_year = min(prior_year_balance, applied)
        prior_year_balance = cents(prior_year_balance - applied_to_prior_year)
        prior_year_applied = cents(prior_year_applied + applied_to_prior_year)
        applied_to_current_year = cents(applied - applied_to_prior_year)
        if applied_to_current_year - current_year_balance > Decimal("0.02"):
            raise CalculationBlocked(
                f"Modelo 390 cannot attribute applied compensation in {report.period}"
            )
        current_year_balance = cents(current_year_balance - applied_to_current_year)
        expected_pending = cents(prior_year_balance + current_year_balance)
        if abs(pending_previous - expected_pending) > Decimal("0.02"):
            raise CalculationBlocked(
                f"Modelo 390 pending compensation does not reconcile in {report.period}: "
                f"casilla 87 is {pending_previous:.2f}, expected {expected_pending:.2f}"
            )

        if index == last_index:
            final_period_compensation = current_generated
            final_period_refund = current_refund
        else:
            current_year_balance = cents(current_year_balance + current_generated)
        previous_carry = cents(pending_previous + current_generated)
        total_payable = cents(total_payable + max(liquidation, ZERO))

    total_output_vat = _sum_report_values(reports, "27")
    total_deductible_vat = _sum_report_values(reports, "45")
    annual_general_result = cents(total_output_vat - total_deductible_vat)
    liquidation_result = cents(annual_general_result - prior_year_applied)
    closing_compensation = cents(
        prior_year_balance + current_year_balance + final_period_compensation
    )
    if previous_carry is not None and abs(closing_compensation - previous_carry) > Decimal("0.02"):
        raise CalculationBlocked("Modelo 390 closing compensation does not match the final Modelo 303")

    domestic_operations = _sum_report_values(reports, "domestic_output_base")
    eu_supplies = _sum_report_values(reports, "59")
    exports = _sum_report_values(reports, "60")
    outside_scope = _sum_report_values(reports, "120")
    values: dict[str, Decimal | str] = {
        "47": total_output_vat,
        "48": _sum_report_values(reports, "domestic_current_input_base"),
        "49": _sum_report_values(reports, "domestic_current_input_vat"),
        "50": _sum_report_values(reports, "domestic_asset_input_base"),
        "51": _sum_report_values(reports, "domestic_asset_input_vat"),
        "52": _sum_report_values(reports, "import_current_input_base"),
        "53": _sum_report_values(reports, "import_current_input_vat"),
        "54": _sum_report_values(reports, "import_asset_input_base"),
        "55": _sum_report_values(reports, "import_asset_input_vat"),
        "56": _sum_report_values(reports, "intracommunity_goods_current_input_base"),
        "57": _sum_report_values(reports, "intracommunity_goods_current_input_vat"),
        "58": _sum_report_values(reports, "intracommunity_goods_asset_input_base"),
        "59": _sum_report_values(reports, "intracommunity_goods_asset_input_vat"),
        "597": _sum_report_values(reports, "intracommunity_service_input_base"),
        "598": _sum_report_values(reports, "intracommunity_service_input_vat"),
        "64": total_deductible_vat,
        "65": annual_general_result,
        "84": annual_general_result,
        "85": prior_year_applied,
        "86": liquidation_result,
        "95": total_payable,
        "97": final_period_compensation,
        "98": final_period_refund,
        "662": current_year_balance,
        "99": domestic_operations,
        "103": eu_supplies,
        "104": exports,
        "110": outside_scope,
        "108": cents(domestic_operations + eu_supplies + exports + outside_scope),
        "523": _sum_report_values(reports, "reverse_charge_service_base"),
        "closing_compensation": closing_compensation,
        "result": liquidation_result,
        "rule_source": MODELO390_RULE_SOURCE,
    }
    return CalculationResult(
        "390",
        str(year),
        values,
        warnings=(
            "Casillas 84/85/86/95/97/662 follow the cited AEAT annual settlement rules and preserve the sequential Modelo 303 compensation chain.",
            "The output covers the common general-regime categories modeled by the ledger; special regimes, prorrata, rectifications, regional allocation, and rare statistical boxes still require explicit review before filing.",
        ) + ((LEGACY_VAT_CLASSIFICATION_WARNING,) if any(
            LEGACY_VAT_CLASSIFICATION_WARNING in report.warnings for report in reports
        ) else ()),
    )


def calculate_modelo347_rows(
    rows: Iterable[TaxRow],
    *,
    year: int,
    threshold: Decimal = Decimal("3005.06"),
) -> CalculationResult:
    selected = [row for row in rows if row.tax_date.year == year and row.include_modelo347]
    missing_counterparty = [row.transaction_id for row in selected if not row.counterparty_id]
    if missing_counterparty:
        raise CalculationBlocked(f"Modelo 347 requires counterparties: {', '.join(missing_counterparty)}")
    aggregates: dict[tuple[str, str], Decimal] = {}
    quarterly: dict[tuple[str, str, int], Decimal] = {}
    lineage_rows: dict[tuple[str, str], list[str]] = {}
    quarterly_lineage: dict[tuple[str, str, int], list[str]] = {}
    for row in selected:
        operation_key = "B" if row.kind == "income" else "A"
        aggregate_key = (row.counterparty_id, operation_key)
        quarter = ((row.tax_date.month - 1) // 3) + 1
        quarter_key = (row.counterparty_id, operation_key, quarter)
        aggregates[aggregate_key] = cents(aggregates.get(aggregate_key, ZERO) + row.amount_eur)
        quarterly[quarter_key] = cents(quarterly.get(quarter_key, ZERO) + row.amount_eur)
        lineage_rows.setdefault(aggregate_key, []).append(row.transaction_id)
        quarterly_lineage.setdefault(quarter_key, []).append(row.transaction_id)

    values: dict[str, Decimal | str] = {"rule_source": MODELO347_RULE_SOURCE}
    lineage: dict[str, tuple[str, ...]] = {}
    for (counterparty_id, operation_key), annual_amount in sorted(aggregates.items()):
        if annual_amount <= threshold:
            continue
        prefix = f"{operation_key}:{counterparty_id}"
        values[f"{prefix}:annual"] = annual_amount
        lineage[f"{prefix}:annual"] = tuple(lineage_rows[(counterparty_id, operation_key)])
        for quarter in range(1, 5):
            key = (counterparty_id, operation_key, quarter)
            values[f"{prefix}:Q{quarter}"] = quarterly.get(key, ZERO)
            lineage[f"{prefix}:Q{quarter}"] = tuple(quarterly_lineage.get(key, ()))
    return CalculationResult(
        "347",
        str(year),
        values,
        lineage,
        (
            "Ordinary acquisition (A) and supply (B) records are separated; special 347 record keys require explicit review.",
        ),
    )


def calculate_retention_rows(
    rows: Iterable[TaxRow],
    *,
    year: int,
    quarter: int | None,
    withholding_type: str,
) -> CalculationResult:
    if withholding_type not in {"professional", "rent", "nonresident"}:
        raise ValueError(f"Unsupported withholding type: {withholding_type}")
    selected = [
        row
        for row in rows
        if row.tax_date.year == year
        and (quarter is None or _in_quarter(row.tax_date, year, quarter))
        and row.withholding_type == withholding_type
    ]
    form = "111" if withholding_type == "professional" and quarter else "190"
    if withholding_type == "rent":
        form = "115" if quarter else "180"
    if withholding_type == "nonresident":
        form = "216" if quarter else "296"
    period = f"{year}-Q{quarter}" if quarter else str(year)
    values = {
        "recipient_count": Decimal(len({row.counterparty_id for row in selected})),
        "base": _sum(selected, "taxable_base_eur"),
        "withholding": _sum(selected, "withholding_eur"),
    }
    warnings: tuple[str, ...] = ()
    if withholding_type == "nonresident":
        values["income_count"] = Decimal(len(selected))
        values["negative_return"] = "yes" if selected and values["withholding"] == ZERO else "no"
        if any(row.withholding_eur == ZERO for row in selected):
            warnings = (
                "Zero withholding on an explicitly classified IRNR row does not establish treaty relief. "
                "This calculator does not decide treaty applicability; retain residence evidence only "
                "when the reviewed tax treatment relies on a treaty.",
            )
    return CalculationResult(
        form,
        period,
        values,
        {"withholding": tuple(row.transaction_id for row in selected)},
        warnings,
    )


def calculate_modelo100_business_support(
    rows: Iterable[TaxRow],
    *,
    year: int,
    difficult_expenses_rate: Decimal,
    difficult_expenses_cap: Decimal = Decimal("2000.00"),
    unsupported_categories: Iterable[str] = (),
) -> CalculationResult:
    unsupported = tuple(sorted({value for value in unsupported_categories if value}))
    if unsupported:
        raise CalculationBlocked(f"Renta WEB review required for unsupported categories: {', '.join(unsupported)}")
    selected = [row for row in rows if row.tax_date.year == year and row.include_modelo130]
    incomes = [row for row in selected if row.kind == "income"]
    expenses = [row for row in selected if row.kind in {"expense", "adjustment"}]
    income = cents(
        sum(
            (
                row.taxable_base_eur if row.taxable_base_eur != ZERO else row.amount_eur
                for row in incomes
            ),
            ZERO,
        )
    )
    expenses_total = _sum(expenses, "deductible_irpf_eur")
    net_before = cents(income - expenses_total)
    difficult = cents(min(max(net_before, ZERO) * difficult_expenses_rate, difficult_expenses_cap))
    values = {
        "business_income": income,
        "business_expenses_before_difficult": expenses_total,
        "difficult_expenses": difficult,
        "business_net_income": cents(net_before - difficult),
        "status": "decision_support_requires_renta_web",
    }
    return CalculationResult(
        "100-business-support",
        str(year),
        values,
        {
            "business_income": tuple(row.transaction_id for row in incomes),
            "business_expenses_before_difficult": tuple(row.transaction_id for row in expenses),
        },
        ("This output is decision support and must be compared with Renta WEB.",),
    )


def _in_ytd(value: date, year: int, quarter: int) -> bool:
    return value.year == year and value.month <= quarter * 3


def _in_quarter(value: date, year: int, quarter: int) -> bool:
    return value.year == year and (value.month - 1) // 3 + 1 == quarter


def _sum(rows: Iterable[TaxRow], field_name: str) -> Decimal:
    return cents(sum((getattr(row, field_name) for row in rows), ZERO))


def _sum_report_values(reports: Iterable[CalculationResult], key: str) -> Decimal:
    return cents(sum((_decimal(report.values.get(key, ZERO)) for report in reports), ZERO))


def _modelo303_rate(row: TaxRow) -> Decimal:
    if row.tax_code == "domestic_output_zero":
        if abs(row.vat_eur) <= Decimal("0.02"):
            return Decimal("0.00")
        raise CalculationBlocked(
            f"Modelo 303 explicit zero-rate output {row.transaction_id} has non-zero VAT"
        )
    for rate in (Decimal("4.00"), Decimal("10.00"), Decimal("21.00")):
        expected_vat = cents(row.taxable_base_eur * rate / Decimal("100"))
        if abs(expected_vat - row.vat_eur) <= Decimal("0.02"):
            return rate
    raise CalculationBlocked(
        f"Modelo 303 cannot map {row.transaction_id} to a supported output VAT rate"
    )


def _split_current_and_assets(rows: Iterable[TaxRow]) -> tuple[list[TaxRow], list[TaxRow]]:
    current: list[TaxRow] = []
    assets: list[TaxRow] = []
    for row in rows:
        investment = is_vat_investment_good(row.vat_investment_good, legacy_asset=bool(row.asset_id))
        (assets if investment else current).append(row)
    return current, assets


def _decimal(value: Decimal | str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"Expected Decimal, got {type(value).__name__}")
    return value
