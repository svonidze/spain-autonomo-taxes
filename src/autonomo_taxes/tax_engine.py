from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Iterable

from .modelo130 import Modelo130Result, calculate_modelo130
from .money import cents


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
MODELO303_NON_ADDITIVE_BOXES = {"02", "05", "08", "151", "65"}
MODELO303_RULE_SOURCE = (
    "AEAT Modelo 303 instructions 2026, pages 1-3: "
    "https://sede.agenciatributaria.gob.es/Sede/todas-gestiones/impuestos-tasas/iva/"
    "modelo-303-iva-autoliquidacion_/instrucciones-2026/instrucciones-02-12-2t-4t-2026.html"
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

    def __post_init__(self) -> None:
        if self.kind not in {"income", "expense", "adjustment"}:
            raise ValueError(f"Unsupported transaction kind: {self.kind}")
        if not self.transaction_id:
            raise ValueError("transaction_id is required")


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
) -> CalculationResult:
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
    compensation_applied = min(previous_compensation, max(pre_compensation_result, ZERO))
    pending_previous_compensation = cents(previous_compensation - compensation_applied)
    liquidation_result = cents(pre_compensation_result - compensation_applied)
    current_period_compensation = cents(max(-liquidation_result, ZERO))
    compensation_carryforward = cents(
        pending_previous_compensation + current_period_compensation
    )
    values.update(
        {
            "64": values["46"],
            "65": Decimal("100.00"),
            "66": values["46"],
            "69": values["46"],
            "110": previous_compensation,
            "78": compensation_applied,
            "87": pending_previous_compensation,
            "71": liquidation_result,
            "72": current_period_compensation,
            "compensation_carryforward": compensation_carryforward,
            "domestic_output_base": _sum(output, "taxable_base_eur"),
            "domestic_output_vat": _sum(output, "vat_eur"),
            "reverse_charge_base": _sum(intracommunity + other_reverse, "taxable_base_eur"),
            "reverse_charge_output_vat": _sum(intracommunity + other_reverse, "vat_eur"),
            "domestic_input_base": _sum(current_domestic + asset_domestic, "taxable_base_eur"),
            "domestic_input_vat": _sum(current_domestic + asset_domestic, "deductible_vat_eur"),
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
    keys = sorted(
        {
            key
            for report in reports
            for key, value in report.values.items()
            if isinstance(value, Decimal) and key not in MODELO303_NON_ADDITIVE_BOXES
        }
    )
    values = {
        key: cents(sum((_decimal(report.values.get(key, ZERO)) for report in reports), ZERO))
        for key in keys
    }
    for key in MODELO303_NON_ADDITIVE_BOXES:
        nonzero = {_decimal(report.values.get(key, ZERO)) for report in reports} - {ZERO}
        if len(nonzero) > 1:
            raise CalculationBlocked(f"Modelo 390 found inconsistent non-additive casilla {key}")
        values[key] = next(iter(nonzero), ZERO)
    return CalculationResult(
        "390",
        str(year),
        values,
        warnings=(
            "This is an inventory-driven annual reconciliation of Modelo 303 casillas, not an AEAT-ready Modelo 390 submission file.",
        ),
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
        (assets if row.asset_id else current).append(row)
    return current, assets


def _decimal(value: Decimal | str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"Expected Decimal, got {type(value).__name__}")
    return value
