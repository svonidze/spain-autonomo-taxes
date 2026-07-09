from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .money import cents, format_es, parse_amount


REGISTER_RECONCILIATION_FIELDS = [
    "period",
    "status",
    "target_casilla_02_delta",
    "confirmed_register_delta",
    "confirmed_minus_target",
    "confirmed_row_count",
    "confirmed_zero_row_count",
    "rows_needing_amount",
    "open_high",
    "open_medium",
    "open_low",
    "open_total",
    "blocking_rows",
    "notes",
]


def build_register_reconciliation(
    history_audit_csv: Path,
    register_review_csv: Path,
) -> list[dict[str, str]]:
    history_by_period = _history_by_period(history_audit_csv)
    review_rows = _load_rows(register_review_csv)
    rows_by_period = _group_by_period(review_rows)

    output: list[dict[str, str]] = []
    for period, history in history_by_period.items():
        rows = rows_by_period.get(period, [])
        target = parse_amount(history["target_casilla_02_delta"])
        confirmed = Decimal("0.00")
        confirmed_row_count = 0
        confirmed_zero_row_count = 0
        rows_needing_amount: list[str] = []
        open_counts = {"high": 0, "medium": 0, "low": 0}
        blocking_rows: list[str] = []

        for row in rows:
            contribution = _confirmed_contribution(row)
            if contribution is not None:
                confirmed += contribution
                confirmed_row_count += 1
                if contribution == Decimal("0.00"):
                    confirmed_zero_row_count += 1
                continue
            if _is_marked_included_without_amount(row):
                rows_needing_amount.append(_row_label(row))
            if _is_open(row):
                priority = row["priority"] if row["priority"] in open_counts else "low"
                open_counts[priority] += 1
                if priority == "high":
                    blocking_rows.append(_row_label(row))

        confirmed = cents(confirmed)
        diff = cents(confirmed - target)
        open_total = sum(open_counts.values())
        output.append(
            {
                "period": period,
                "status": _status(diff, open_total, rows_needing_amount, confirmed_row_count),
                "target_casilla_02_delta": _money(target),
                "confirmed_register_delta": _money(confirmed),
                "confirmed_minus_target": _money(diff),
                "confirmed_row_count": str(confirmed_row_count),
                "confirmed_zero_row_count": str(confirmed_zero_row_count),
                "rows_needing_amount": "; ".join(rows_needing_amount),
                "open_high": str(open_counts["high"]),
                "open_medium": str(open_counts["medium"]),
                "open_low": str(open_counts["low"]),
                "open_total": str(open_total),
                "blocking_rows": "; ".join(blocking_rows),
                "notes": _notes(open_total, rows_needing_amount, diff),
            }
        )
    return output


def write_register_reconciliation_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTER_RECONCILIATION_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_register_reconciliation_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 130 Register Reconciliation",
        "",
        "This report compares filled `confirmed_*` register-review rows against submitted Modelo 130 `casilla 02` quarter movements.",
        "Blank confirmation fields are treated as open work, not as zero.",
        "",
        "| Period | Status | Target 02 delta | Confirmed delta | Diff | Open high | Open total |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["status"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["confirmed_register_delta"]),
                    _fmt(row["confirmed_minus_target"]),
                    row["open_high"],
                    row["open_total"],
                ]
            )
            + " |"
        )
    lines.extend(["", "## Blocking Rows", ""])
    for row in rows:
        if not row["blocking_rows"]:
            continue
        lines.extend([f"### {row['period']}", ""])
        for item in row["blocking_rows"].split("; "):
            lines.append(f"- {item}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _history_by_period(path: Path) -> dict[str, dict[str, str]]:
    rows = _load_rows(path)
    return {f"{row['year']}-Q{row['quarter']}": row for row in rows}


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _group_by_period(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["period"], []).append(row)
    return grouped


def _confirmed_contribution(row: dict[str, str]) -> Decimal | None:
    included = _normalize_bool(row.get("confirmed_included", ""))
    irpf = row.get("confirmed_irpf_deductible_eur", "")
    amortization = row.get("confirmed_amortization_eur", "")
    if irpf or amortization:
        return cents(_amount(irpf) + _amount(amortization))
    if included is False:
        return Decimal("0.00")
    return None


def _is_marked_included_without_amount(row: dict[str, str]) -> bool:
    return _normalize_bool(row.get("confirmed_included", "")) is True and not (
        row.get("confirmed_irpf_deductible_eur") or row.get("confirmed_amortization_eur")
    )


def _is_open(row: dict[str, str]) -> bool:
    status = (row.get("review_status") or "").strip().lower()
    if status in {"closed", "confirmed", "done", "resolved"}:
        return False
    return _confirmed_contribution(row) is None


def _normalize_bool(value: str) -> bool | None:
    text = value.strip().lower()
    if text in {"yes", "y", "true", "1", "included", "include", "si", "sí"}:
        return True
    if text in {"no", "n", "false", "0", "excluded", "exclude"}:
        return False
    return None


def _status(
    diff: Decimal,
    open_total: int,
    rows_needing_amount: list[str],
    confirmed_row_count: int,
) -> str:
    if rows_needing_amount:
        return "included_rows_missing_amounts"
    if open_total:
        return "partial_review_open_rows"
    if confirmed_row_count == 0:
        return "no_confirmed_rows"
    if abs(diff) <= Decimal("0.02"):
        return "matches_target"
    return "review_complete_but_mismatch"


def _notes(open_total: int, rows_needing_amount: list[str], diff: Decimal) -> str:
    if rows_needing_amount:
        return "Rows marked included still need deductible or amortization amounts."
    if open_total:
        return "Open rows remain; confirmed delta is partial."
    if abs(diff) <= Decimal("0.02"):
        return "Confirmed register matches submitted casilla 02 movement."
    return "All rows are closed but confirmed register does not match submitted casilla 02 movement."


def _row_label(row: dict[str, str]) -> str:
    if not any(row.get(key, "") for key in ("date", "xolo_id", "number", "recipient")):
        return " ".join(
            part
            for part in (
                row.get("source_classification", ""),
                row.get("hypothesis", ""),
                row.get("gross_eur", ""),
            )
            if part
        )
    parts = [
        row.get("date", ""),
        row.get("xolo_id", ""),
        row.get("number", ""),
        row.get("recipient", ""),
        row.get("gross_eur", ""),
    ]
    return " ".join(part for part in parts if part)


def _amount(value: str) -> Decimal:
    return parse_amount(value) if value else Decimal("0.00")


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return "" if value == "" else format_es(parse_amount(value))
