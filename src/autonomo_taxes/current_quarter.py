from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Iterable

from .tax_engine import (
    CalculationBlocked,
    TaxRow,
    calculate_modelo130_rows,
    calculate_modelo303_rows,
    calculate_modelo349_rows,
)


MODELO130_KEYS = ("01", "02", "03", "04", "05", "06", "07", "12", "13", "19")
_EXPECTED_PENDING_KINDS = {
    "approved_forecast_pending",
    "obligation_filing_pending",
}


def build_current_quarter_dashboard(
    *,
    actual_rows: Iterable[TaxRow],
    projected_rows: Iterable[TaxRow],
    period_key: str,
    as_of: date,
    difficult_expenses_rate: Decimal,
    previous_positive_casilla_07: Decimal,
    previous_negative_carry: Decimal,
    previous_vat_compensation: Decimal,
    approved_current_rows: list[dict[str, Any]],
    obligations: list[dict[str, Any]],
    period_validation: dict[str, Any],
) -> dict[str, Any]:
    year, quarter = _parse_period(period_key)
    actual = [row for row in actual_rows if row.tax_date <= as_of]
    projected_candidates = list(projected_rows)
    actual_ids = {row.transaction_id for row in actual}
    approved_ids = {row["transaction_id"] for row in approved_current_rows}
    approved_delta = [
        row
        for row in projected_candidates
        if row.transaction_id in approved_ids and _in_quarter(row.tax_date, year, quarter)
    ]
    projected = actual + approved_delta
    unexpected_future = [
        row
        for row in projected_candidates
        if row.transaction_id not in actual_ids
        and row.transaction_id not in approved_ids
        and _in_quarter(row.tax_date, year, quarter)
    ]
    actual_current = [row for row in actual if _in_quarter(row.tax_date, year, quarter)]
    projected_current = [row for row in projected if _in_quarter(row.tax_date, year, quarter)]
    readiness_items = _blocking_items(
        period_validation,
        approved_current_rows,
        obligations=obligations,
        unexpected_future=unexpected_future,
        as_of=as_of,
    )
    expected_items = [row for row in readiness_items if row["kind"] in _EXPECTED_PENDING_KINDS]
    blocking_items = [
        row for row in readiness_items if row["kind"] not in _EXPECTED_PENDING_KINDS
    ]

    dashboard = {
        "schema_version": 2,
        "report_type": "operational_quarter_forecast",
        "period": period_key,
        "as_of": as_of.isoformat(),
        "filing_assessment_performed": False,
        "filing_ready": False,
        "submission_ready": False,
        "posted_actual": _quarter_totals(actual_current),
        "approved_forecast_delta": _quarter_totals(approved_delta),
        "projected_total": _quarter_totals(projected_current),
        "forecast_rows": approved_current_rows,
        "obligations": obligations,
        "period_close_validation": period_validation,
        "blocking_items": blocking_items,
        "expected_items": expected_items,
        "policy": {
            "difficult_expenses": "year_specific_aeat_rule",
            "difficult_expenses_rate": difficult_expenses_rate,
            "previous_positive_casilla_07": previous_positive_casilla_07,
            "previous_negative_carry": previous_negative_carry,
            "previous_vat_compensation": previous_vat_compensation,
        },
        "tax_arithmetic_preview": {
            "posted_actual": _tax_preview(
                actual,
                year=year,
                quarter=quarter,
                difficult_expenses_rate=difficult_expenses_rate,
                previous_positive_casilla_07=previous_positive_casilla_07,
                previous_negative_carry=previous_negative_carry,
                previous_vat_compensation=previous_vat_compensation,
            ),
            "projected_reviewed": _tax_preview(
                projected,
                year=year,
                quarter=quarter,
                difficult_expenses_rate=difficult_expenses_rate,
                previous_positive_casilla_07=previous_positive_casilla_07,
                previous_negative_carry=previous_negative_carry,
                previous_vat_compensation=previous_vat_compensation,
            ),
        },
        "warnings": [
            "Arithmetic previews do not determine whether a form is due and do not relax period-close gates."
        ],
    }
    return dashboard


def load_xolo_modelo130_forecast(path: str | Path, *, period: str) -> dict[str, Any] | None:
    source = Path(path)
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("period") != period:
                continue
            return {
                "source_path": str(source.resolve()),
                "xolo_status": row.get("xolo_status", ""),
                "values": {
                    "01": _optional_decimal(row.get("total_compounded_sales_ytd")),
                    "02": _optional_decimal(row.get("total_compounded_deductible_expenses_ytd")),
                    "03": _optional_decimal(row.get("net_results_ytd")),
                    "04": _optional_decimal(row.get("net_results_20_percent")),
                    "05": _optional_decimal(row.get("previous_quarters_compensation")),
                    "06": _optional_decimal(row.get("withholding_taxes")),
                    "13": _optional_decimal(row.get("article_110_3_reduction")),
                    "19": _optional_decimal(row.get("payable_irpf_for_quarter")),
                },
            }
    return None


def add_xolo_comparison(dashboard: dict[str, Any], comparison: dict[str, Any] | None) -> None:
    if comparison is None:
        return
    projected = dashboard["tax_arithmetic_preview"]["projected_reviewed"]["modelo130"]["values"]
    differences: dict[str, Decimal | None] = {}
    for key, xolo_value in comparison["values"].items():
        differences[key] = None if xolo_value is None else projected[key] - xolo_value
    comparison["difference_projected_minus_xolo"] = differences
    dashboard["xolo_forecast"] = comparison

    xolo_c05 = comparison["values"].get("05")
    expected_c05 = dashboard["policy"]["previous_positive_casilla_07"]
    if xolo_c05 is not None and xolo_c05 != expected_c05:
        dashboard["warnings"].append(
            "The Xolo UI forecast does not include every positive casilla 07 from prior filed quarters."
        )


def write_current_quarter_dashboard(
    dashboard: dict[str, Any], output_dir: str | Path
) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "dashboard.json"
    markdown_path = root / "dashboard.md"
    json_path.write_text(
        json.dumps(_jsonable(dashboard), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_render_markdown(dashboard), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def _tax_preview(
    rows: list[TaxRow],
    *,
    year: int,
    quarter: int,
    difficult_expenses_rate: Decimal,
    previous_positive_casilla_07: Decimal,
    previous_negative_carry: Decimal,
    previous_vat_compensation: Decimal,
) -> dict[str, Any]:
    _, modelo130 = calculate_modelo130_rows(
        rows,
        year=year,
        quarter=quarter,
        difficult_expenses_rate=difficult_expenses_rate,
        previous_positive_casilla_07=previous_positive_casilla_07,
        previous_negative_carry=previous_negative_carry,
        include_difficult_expenses=True,
    )
    return {
        "modelo130": {"values": modelo130.values, "lineage": modelo130.lineage},
        "modelo303": _optional_calculation(
            calculate_modelo303_rows,
            rows,
            year,
            quarter,
            previous_compensation=previous_vat_compensation,
        ),
        "modelo349": _optional_calculation(calculate_modelo349_rows, rows, year, quarter),
    }


def _optional_calculation(
    calculator,
    rows: list[TaxRow],
    year: int,
    quarter: int,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        result = calculator(rows, year=year, quarter=quarter, **kwargs)
    except CalculationBlocked as exc:
        return {"blocked": True, "reason": str(exc)}
    return {
        "blocked": False,
        "values": result.values,
        "lineage": result.lineage,
        "warnings": result.warnings,
    }


def _quarter_totals(rows: list[TaxRow]) -> dict[str, Any]:
    incomes = [row for row in rows if row.kind == "income"]
    expenses = [row for row in rows if row.kind in {"expense", "adjustment"}]
    return {
        "transaction_count": len(rows),
        "income_eur": _sum(row.amount_eur for row in incomes),
        "expense_document_gross_eur": _sum(row.amount_eur for row in expenses),
        "expense_irpf_deductible_eur": _sum(row.deductible_irpf_eur for row in expenses),
        "deductible_vat_eur": _sum(row.deductible_vat_eur for row in expenses),
        "transaction_ids": tuple(row.transaction_id for row in rows),
    }


def _blocking_items(
    validation: dict[str, Any],
    approved_current_rows: list[dict[str, Any]],
    *,
    obligations: list[dict[str, Any]],
    unexpected_future: list[TaxRow],
    as_of: date,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    obligations_by_code = {
        str(row.get("obligation_code") or ""): row for row in obligations
    }
    for issue in validation["blocking_issues"]:
        blockers.append({"kind": "validation_issue", "reference": issue["validation_issue_id"]})
    for obligation in validation["unresolved_obligations"]:
        obligation_code = str(obligation["obligation_code"])
        scheduled = obligations_by_code.get(obligation_code, {})
        raw_due_on = (
            obligation.get("due_on")
            or obligation.get("obligation_due_on")
            or scheduled.get("due_on")
            or scheduled.get("obligation_due_on")
        )
        due_on = date.fromisoformat(str(raw_due_on)) if raw_due_on else None
        is_expected_filing = (
            obligation.get("determination") == "due"
            and due_on is not None
            and as_of <= due_on
        )
        item = {
            "kind": "obligation_filing_pending" if is_expected_filing else "obligation",
            "reference": obligation_code,
            "detail": obligation["determination"],
        }
        if due_on is not None:
            item["due_on"] = due_on.isoformat()
        blockers.append(item)
    for obligation in obligations:
        unresolved_due = obligation.get("determination") == "due" and obligation.get(
            "filing_status"
        ) not in {"filed", "waived"}
        if unresolved_due and obligation.get("calendar_deadline_mismatch"):
            blockers.append(
                {
                    "kind": "calendar_deadline_mismatch",
                    "reference": obligation["obligation_code"],
                    "detail": (
                        f"obligation={obligation.get('obligation_due_on')} "
                        f"calendar={obligation.get('calendar_statutory_due_on')}"
                    ),
                }
            )
        if unresolved_due and not obligation.get("due_on"):
            blockers.append(
                {
                    "kind": "confirmed_deadline_missing",
                    "reference": obligation["obligation_code"],
                    "detail": obligation.get("deadline_status") or "calendar entry missing",
                }
            )
    for document in validation.get("review_documents", []):
        blockers.append(
            {
                "kind": "document_review",
                "reference": document["document_id"],
                "detail": document["lifecycle_status"],
            }
        )
    approved_ids = {row["transaction_id"] for row in approved_current_rows}
    for transaction in validation.get("review_transactions", []):
        if transaction["transaction_id"] in approved_ids:
            continue
        blockers.append(
            {
                "kind": "transaction_review",
                "reference": transaction["transaction_id"],
                "detail": transaction["lifecycle_status"],
            }
        )
    for row in validation.get("target_derived_fx", []):
        blockers.append(
            {
                "kind": "target_derived_fx",
                "reference": row["transaction_id"],
            }
        )
    for row in validation.get("xolo_recorded_fx_after_cutover", []):
        blockers.append(
            {
                "kind": "xolo_recorded_fx_after_cutover",
                "reference": row["transaction_id"],
            }
        )
    for row in validation.get("invalid_amount_transactions", []):
        blockers.append(
            {
                "kind": "invalid_amount",
                "reference": row["transaction_id"],
            }
        )
    for row in validation.get("out_of_period_transactions", []):
        blockers.append(
            {
                "kind": "out_of_period",
                "reference": row["transaction_id"],
            }
        )
    for row in unexpected_future:
        blockers.append(
            {
                "kind": "future_posted_after_as_of",
                "reference": row.transaction_id,
                "detail": row.tax_date.isoformat(),
            }
        )
    for row in approved_current_rows:
        raw_tax_date = row.get("transaction_date")
        try:
            tax_date = date.fromisoformat(str(raw_tax_date)) if raw_tax_date else None
        except ValueError:
            tax_date = None
        item = {
            "kind": (
                "approved_forecast_pending"
                if tax_date is not None and tax_date > as_of
                else "approved_not_posted"
            ),
            "reference": row["transaction_id"],
            "detail": row["description"],
        }
        if tax_date is not None:
            item["tax_date"] = tax_date.isoformat()
        blockers.append(
            item
        )
        if row.get("tax_code") == "historical_g03" and not row.get("asset_id"):
            blockers.append(
                {
                    "kind": "amortization_asset_link_missing",
                    "reference": row["transaction_id"],
                    "detail": row["description"],
                }
            )
        if row.get("tax_code") != "historical_g03" and row.get("document_type") != "expense_invoice":
            blockers.append(
                {
                    "kind": "supplier_invoice_missing",
                    "reference": row["transaction_id"],
                    "detail": row["description"],
                }
            )
    return blockers


def _render_markdown(dashboard: dict[str, Any]) -> str:
    posted = dashboard["posted_actual"]
    delta = dashboard["approved_forecast_delta"]
    projected_total = dashboard["projected_total"]
    posted_130 = dashboard["tax_arithmetic_preview"]["posted_actual"]["modelo130"]["values"]
    projected_130 = dashboard["tax_arithmetic_preview"]["projected_reviewed"]["modelo130"]["values"]
    lines = [
        f"# {dashboard['period']} operational dashboard",
        "",
        f"As of: `{dashboard['as_of']}`.",
        "",
        "Status: **draft operational forecast; not filing-ready**.",
        "",
        "## Current-quarter accounting",
        "",
        "| Scope | Income | Expense document gross | IRPF deductible | Deductible VAT | Rows |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        _totals_row("Posted actual", posted),
        _totals_row("Approved forecast delta", delta),
        _totals_row("Projected reviewed total", projected_total),
        "",
        "## Modelo 130 arithmetic preview",
        "",
        "| Casilla | Posted actual YTD | Projected reviewed YTD |",
        "| --- | ---: | ---: |",
    ]
    for key in MODELO130_KEYS:
        lines.append(f"| {key} | {_money(posted_130[key])} | {_money(projected_130[key])} |")
    lines.append(
        f"| difficult expenses | {_money(posted_130['difficult_expenses'])} | "
        f"{_money(projected_130['difficult_expenses'])} |"
    )
    lines.append("")

    lines.extend(
        [
            "## Obligations and deadlines",
            "",
            "| Form | Determination | Filing status | Internal target | Direct debit | Effective deadline | Calendar status |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for obligation in dashboard["obligations"]:
        lines.append(
            f"| {obligation['obligation_code']} | {obligation.get('determination', '')} | "
            f"{obligation.get('filing_status', '')} | {obligation.get('internal_due_on') or ''} | "
            f"{obligation.get('direct_debit_cutoff_on') or ''} | "
            f"{obligation.get('due_on') or ''} | "
            f"{obligation.get('deadline_status') or 'legacy/manual'} |"
        )
    lines.append("")

    comparison = dashboard.get("xolo_forecast")
    if comparison:
        lines.extend(
            [
                "## Xolo UI comparison",
                "",
                f"Xolo status: `{comparison.get('xolo_status', '')}`.",
                "",
                "| Casilla | Projected official-rule | Xolo UI | Difference |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for key in ("01", "02", "03", "04", "05", "06", "13", "19"):
            xolo_value = comparison["values"].get(key)
            difference = comparison["difference_projected_minus_xolo"].get(key)
            lines.append(
                f"| {key} | {_money(projected_130[key])} | {_money(xolo_value)} | {_money(difference)} |"
            )
        lines.append("")

    lines.extend(["## Approved forecast rows", ""])
    forecast_rows = dashboard["forecast_rows"]
    if forecast_rows:
        lines.extend(
            [
                "| Date | Counterparty | Description | EUR gross | IRPF deductible | Tax code |",
                "| --- | --- | --- | ---: | ---: | --- |",
            ]
        )
        for row in forecast_rows:
            lines.append(
                f"| {row['transaction_date']} | {row.get('counterparty_name', '')} | "
                f"{row['description']} | {_minor_money(row.get('amount_eur_minor'))} | "
                f"{_minor_money(row.get('deductible_irpf_minor'))} | {row.get('tax_code', '')} |"
            )
    else:
        lines.append("None.")
    lines.append("")

    lines.extend(["## Blocking items", ""])
    if dashboard["blocking_items"]:
        for blocker in dashboard["blocking_items"]:
            detail = f": {blocker['detail']}" if blocker.get("detail") else ""
            lines.append(f"- `{blocker['kind']}` `{blocker['reference']}`{detail}")
    else:
        lines.append("None.")
    lines.append("")

    lines.extend(["## Expected pending items", ""])
    if dashboard.get("expected_items"):
        for item in dashboard["expected_items"]:
            tax_date = f" on {item['tax_date']}" if item.get("tax_date") else ""
            due_on = f" by {item['due_on']}" if item.get("due_on") else ""
            lines.append(
                f"- `{item['kind']}` `{item['reference']}`{tax_date}{due_on}: "
                f"{item.get('detail', '')}".rstrip()
            )
    else:
        lines.append("None.")
    lines.append("")

    lines.extend(["## Warnings", ""])
    lines.extend(f"- {warning}" for warning in dashboard["warnings"])
    lines.append("")
    return "\n".join(lines)


def _totals_row(label: str, totals: dict[str, Any]) -> str:
    return (
        f"| {label} | {_money(totals['income_eur'])} | "
        f"{_money(totals['expense_document_gross_eur'])} | "
        f"{_money(totals['expense_irpf_deductible_eur'])} | "
        f"{_money(totals['deductible_vat_eur'])} | {totals['transaction_count']} |"
    )


def _parse_period(period_key: str) -> tuple[int, int]:
    parts = period_key.split("-Q")
    if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in {"1", "2", "3", "4"}:
        raise ValueError("Dashboard period must use YYYY-QN format")
    return int(parts[0]), int(parts[1])


def _in_quarter(value: date, year: int, quarter: int) -> bool:
    return value.year == year and ((value.month - 1) // 3) + 1 == quarter


def _sum(values: Iterable[Decimal]) -> Decimal:
    return sum(values, Decimal("0.00")).quantize(Decimal("0.01"))


def _optional_decimal(value: str | None) -> Decimal | None:
    text = (value or "").strip()
    return Decimal(text) if text else None


def _minor_money(value: int | str | None) -> str:
    if value in {None, ""}:
        return ""
    return _money(Decimal(str(value)) / Decimal("100"))


def _money(value: Any) -> str:
    if value is None or value == "":
        return ""
    return f"{Decimal(str(value)):.2f}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
