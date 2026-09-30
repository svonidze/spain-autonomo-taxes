from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from .history import (
    RawXoloExpense,
    _row_base_eur,
    _row_gross_eur,
    load_raw_xolo_expenses,
    nearest_excluded_subset,
)
from .money import cents, format_es, parse_amount
from .parsers import quarter_end


ROW_AUDIT_FIELDS = [
    "year",
    "quarter",
    "period",
    "row_kind",
    "classification",
    "xolo_id",
    "date",
    "recipient",
    "type",
    "number",
    "xolo_status",
    "currency",
    "amount_original",
    "gross_eur",
    "base_eur",
    "gross_minus_base_eur",
    "candidate_model_minus_target_delta",
    "nearest_subset_total_eur",
    "nearest_subset_error_eur",
    "notes",
    "xolo_url",
]


def build_row_audit(
    history_audit_csv: Path,
    candidate_quarter_reconciliation_csv: Path,
    xolo_raw_expenses_csv: Path,
) -> list[dict[str, str]]:
    history_rows = _load_rows(history_audit_csv)
    candidate_rows = {row["period"]: row for row in _load_rows(candidate_quarter_reconciliation_csv)}
    raw_expenses = load_raw_xolo_expenses(xolo_raw_expenses_csv)

    output: list[dict[str, str]] = []
    for history in history_rows:
        year = int(history["year"])
        quarter = int(history["quarter"])
        period = f"{year}-Q{quarter}"
        candidate = candidate_rows[period]
        diff = parse_amount(candidate["candidate_model_minus_target_delta"])
        usd_fx = Decimal(history["derived_income_usd_fx"]) if history.get("derived_income_usd_fx") else None
        subset = nearest_excluded_subset(raw_expenses, year, quarter, usd_fx, diff) if diff > Decimal("20.00") else None
        subset_keys = {_row_key(row) for row, _ in subset.rows} if subset else set()
        subset_total = subset.total if subset else Decimal("0.00")
        subset_error = subset.error if subset else Decimal("0.00")

        for raw_row in _raw_rows_for_quarter(raw_expenses, year, quarter):
            output.append(
                _audit_row(
                    raw_row,
                    year,
                    quarter,
                    diff,
                    usd_fx,
                    subset_total,
                    subset_error,
                    _row_key(raw_row) in subset_keys,
                )
            )
        output.extend(_synthetic_gap_rows(year, quarter, diff, subset_total, subset_error))
    return output


def write_row_audit_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ROW_AUDIT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_row_audit_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 130 Row Audit",
        "",
        "This report classifies raw Xolo expense rows by quarter against the candidate Modelo 130 reconciliation.",
        "It highlights asset candidates, nearest excluded-row candidates, VAT base/gross deltas, and synthetic unresolved gap rows.",
        "",
    ]
    for period, period_rows in _group_by_period(rows).items():
        diff = period_rows[0]["candidate_model_minus_target_delta"] if period_rows else "0.00"
        subset_total = period_rows[0]["nearest_subset_total_eur"] if period_rows else "0.00"
        subset_error = period_rows[0]["nearest_subset_error_eur"] if period_rows else "0.00"
        lines.extend(
            [
                f"## {period}",
                "",
                f"- Candidate model minus target: `{_fmt(diff)}`",
                f"- Nearest subset total/error: `{_fmt(subset_total)}` / `{_fmt(subset_error)}`",
                "",
                "| Classification | Date | Xolo ID | Number | Recipient | Gross EUR | Base EUR | Notes |",
                "|---|---|---:|---|---|---:|---:|---|",
            ]
        )
        for row in period_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["classification"],
                        row["date"],
                        _xolo_id_cell(row),
                        _cell(row["number"]),
                        _cell(row["recipient"]),
                        _fmt(row["gross_eur"]),
                        _fmt(row["base_eur"]),
                        _cell(row["notes"]),
                    ]
                )
                + " |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _audit_row(
    row: RawXoloExpense,
    year: int,
    quarter: int,
    diff: Decimal,
    usd_fx: Decimal | None,
    subset_total: Decimal,
    subset_error: Decimal,
    in_nearest_subset: bool,
) -> dict[str, str]:
    gross = _row_gross_eur(row, usd_fx)
    base = _row_base_eur(row, usd_fx)
    classification, notes = _classification(row, diff, in_nearest_subset, gross, base)
    return {
        "year": str(year),
        "quarter": str(quarter),
        "period": f"{year}-Q{quarter}",
        "row_kind": "xolo_expense",
        "classification": classification,
        "xolo_id": row.xolo_id,
        "date": row.date.isoformat(),
        "recipient": row.recipient,
        "type": row.expense_type,
        "number": row.number,
        "xolo_status": row.status,
        "currency": row.currency,
        "amount_original": _money(row.amount_original),
        "gross_eur": _money(gross),
        "base_eur": _money(base),
        "gross_minus_base_eur": _money(_gross_minus_base(gross, base)),
        "candidate_model_minus_target_delta": _money(diff),
        "nearest_subset_total_eur": _money(subset_total),
        "nearest_subset_error_eur": _money(subset_error),
        "notes": notes,
        "xolo_url": row.xolo_url,
    }


def _classification(
    row: RawXoloExpense,
    diff: Decimal,
    in_nearest_subset: bool,
    gross: Decimal | None,
    base: Decimal | None,
) -> tuple[str, str]:
    notes: list[str] = []
    if row.is_asset_like:
        notes.append("excluded from raw non-asset model; candidate amortization schedule must confirm basis/rate/start")
        return "asset_amortization_candidate", "; ".join(notes)
    if in_nearest_subset:
        notes.append("nearest subset that would lower candidate model toward submitted casilla 02")
    elif diff < Decimal("-20.00"):
        notes.append("raw row does not explain target-above-model gap; look for catch-up or reclassification")
    else:
        notes.append("ordinary raw non-asset row in candidate model")
    if gross is not None and base is not None and gross != base:
        notes.append(f"gross/base delta {_money(gross - base)} requires VAT-basis confirmation")
    if row.currency != "EUR":
        notes.append("foreign-currency row; FX source should be confirmed")
    if in_nearest_subset:
        return "nearest_exclusion_candidate", "; ".join(notes)
    if diff < Decimal("-20.00"):
        return "raw_non_asset_context_for_catch_up", "; ".join(notes)
    return "raw_non_asset_candidate_model", "; ".join(notes)


def _synthetic_gap_rows(
    year: int,
    quarter: int,
    diff: Decimal,
    subset_total: Decimal,
    subset_error: Decimal,
) -> list[dict[str, str]]:
    if abs(diff) <= Decimal("1.00"):
        return []
    if diff > 0:
        unresolved = cents(diff - subset_total)
        if abs(unresolved) <= Decimal("1.00"):
            return []
        classification = "unresolved_after_nearest_subset"
        notes = "nearest subset does not fully explain candidate model above target"
        amount = unresolved
    else:
        classification = "missing_catch_up_or_reclassification"
        notes = "submitted target is above raw rows plus candidate amortization; needs Xolo adjustment row or treatment"
        amount = abs(diff)
    period = f"{year}-Q{quarter}"
    return [
        {
            "year": str(year),
            "quarter": str(quarter),
            "period": period,
            "row_kind": "synthetic_gap",
            "classification": classification,
            "xolo_id": "",
            "date": "",
            "recipient": "",
            "type": "",
            "number": "",
            "xolo_status": "",
            "currency": "EUR",
            "amount_original": _money(amount),
            "gross_eur": _money(amount),
            "base_eur": _money(amount),
            "gross_minus_base_eur": "0.00",
            "candidate_model_minus_target_delta": _money(diff),
            "nearest_subset_total_eur": _money(subset_total),
            "nearest_subset_error_eur": _money(subset_error),
            "notes": notes,
            "xolo_url": "",
        }
    ]


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _raw_rows_for_quarter(rows: list[RawXoloExpense], year: int, quarter: int) -> list[RawXoloExpense]:
    start = date(year, 1 + (quarter - 1) * 3, 1)
    end = quarter_end(year, quarter)
    return sorted(
        (row for row in rows if start <= row.date <= end),
        key=lambda row: (row.date, row.recipient, row.number, row.xolo_id),
    )


def _row_key(row: RawXoloExpense) -> tuple[str, str, str, str, str]:
    return (
        row.date.isoformat(),
        row.recipient,
        row.number,
        _money(row.amount_original),
        row.currency,
    )


def _group_by_period(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["period"], []).append(row)
    return grouped


def _gross_minus_base(gross: Decimal | None, base: Decimal | None) -> Decimal | None:
    if gross is None or base is None:
        return None
    return gross - base


def _money(value: Decimal | None) -> str:
    return "" if value is None else f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return "" if value == "" else format_es(parse_amount(value))


def _cell(value: str) -> str:
    return value.replace("|", "\\|")


def _xolo_id_cell(row: dict[str, str]) -> str:
    xolo_id = row["xolo_id"]
    if not xolo_id:
        return ""
    url = row.get("xolo_url", "")
    if not url:
        return xolo_id
    return f"[{xolo_id}]({url})"
