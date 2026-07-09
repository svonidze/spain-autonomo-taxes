from __future__ import annotations

import csv
from pathlib import Path

from .money import cents, parse_amount


XOLO_CALCULATION_COMPARE_FIELDS = [
    "period",
    "xolo_status",
    "history_report",
    "xolo_filename",
    "income_ytd_diff",
    "expenses_ytd_diff",
    "payable_diff",
    "history_income_ytd",
    "xolo_income_ytd",
    "history_expenses_ytd",
    "xolo_expenses_ytd",
    "history_payable",
    "xolo_payable",
    "comparison_status",
]


def build_xolo_calculation_compare(history_audit_csv: Path, xolo_calculations_csv: Path) -> list[dict[str, str]]:
    history_by_period = {
        f"{row['year']}-Q{row['quarter']}": row
        for row in _load_rows(history_audit_csv)
    }
    rows: list[dict[str, str]] = []
    for xolo in _load_rows(xolo_calculations_csv):
        period = xolo["period"]
        history = history_by_period.get(period)
        if not history:
            rows.append(_missing_history_row(xolo))
            continue
        income_diff = _diff(history["target_casilla_01"], xolo["total_compounded_sales_ytd"])
        expense_diff = _diff(history["target_casilla_02"], xolo["total_compounded_deductible_expenses_ytd"])
        payable_diff = _diff(history["target_casilla_19"], xolo["payable_irpf_for_quarter"])
        rows.append(
            {
                "period": period,
                "xolo_status": xolo["xolo_status"],
                "history_report": history["report"],
                "xolo_filename": xolo["filename"],
                "income_ytd_diff": _money(income_diff),
                "expenses_ytd_diff": _money(expense_diff),
                "payable_diff": _money(payable_diff),
                "history_income_ytd": history["target_casilla_01"],
                "xolo_income_ytd": xolo["total_compounded_sales_ytd"],
                "history_expenses_ytd": history["target_casilla_02"],
                "xolo_expenses_ytd": xolo["total_compounded_deductible_expenses_ytd"],
                "history_payable": history["target_casilla_19"],
                "xolo_payable": xolo["payable_irpf_for_quarter"],
                "comparison_status": _status(xolo["xolo_status"], income_diff, expense_diff, payable_diff),
            }
        )
    return rows


def write_xolo_calculation_compare_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=XOLO_CALCULATION_COMPARE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_xolo_calculation_compare_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["comparison_status"]] = counts.get(row["comparison_status"], 0) + 1
    lines = [
        "# Xolo Calculation Compare",
        "",
        "Compares Xolo authenticated calculation popups with locally extracted Modelo 130 PDF history values.",
        "This validates quarter-level PDF extraction only; it still does not provide row-level expense register treatment.",
        "",
        "## Summary",
        "",
    ]
    for status, count in sorted(counts.items()):
        lines.append(f"- `{status}`: `{count}`")
    lines.extend(
        [
            "",
            "| Period | Xolo status | Compare | Income diff | Expense diff | Payable diff |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["xolo_status"],
                    row["comparison_status"],
                    row["income_ytd_diff"],
                    row["expenses_ytd_diff"],
                    row["payable_diff"],
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _missing_history_row(xolo: dict[str, str]) -> dict[str, str]:
    return {
        "period": xolo["period"],
        "xolo_status": xolo["xolo_status"],
        "history_report": "",
        "xolo_filename": xolo["filename"],
        "income_ytd_diff": "",
        "expenses_ytd_diff": "",
        "payable_diff": "",
        "history_income_ytd": "",
        "xolo_income_ytd": xolo["total_compounded_sales_ytd"],
        "history_expenses_ytd": "",
        "xolo_expenses_ytd": xolo["total_compounded_deductible_expenses_ytd"],
        "history_payable": "",
        "xolo_payable": xolo["payable_irpf_for_quarter"],
        "comparison_status": "no_local_history_row",
    }


def _diff(left: str, right: str):
    return cents(parse_amount(left) - parse_amount(right))


def _status(xolo_status: str, income_diff, expense_diff, payable_diff) -> str:
    if xolo_status != "submitted":
        return "xolo_forecast_not_in_submitted_history"
    if all(abs(value) <= parse_amount("0.01") for value in (income_diff, expense_diff, payable_diff)):
        return "matched"
    return "mismatch"


def _money(value) -> str:
    return f"{cents(value):.2f}"
