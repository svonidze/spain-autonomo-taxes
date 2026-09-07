from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .money import cents, format_es, parse_amount
from .xolo_ledger import XoloExpenseRow, load_xolo_expense_ledger, rows_for_modelo130


LEDGER_PROJECTION_FIELDS = [
    "period",
    "status",
    "target_casilla_02_ytd",
    "ledger_ytd",
    "ledger_minus_target_ytd",
    "target_casilla_02_delta",
    "ledger_delta",
    "ledger_minus_target_delta",
    "ledger_rows_ytd",
    "ledger_rows_delta",
    "pending_or_inferred_rows_ytd",
    "pending_or_inferred_rows_delta",
    "pending_or_inferred_delta_eur",
    "pending_or_inferred_rows",
    "notes",
]


def build_ledger_projection(
    history_audit_csv: Path,
    xolo_expense_ledger_csv: Path,
) -> list[dict[str, str]]:
    history_rows = _load_rows(history_audit_csv)
    ledger_rows = load_xolo_expense_ledger(xolo_expense_ledger_csv)
    output: list[dict[str, str]] = []
    previous_ledger_ytd_by_year: dict[int, Decimal] = {}
    previous_row_keys_by_year: dict[int, set[tuple[str, str, str]]] = {}

    for history in history_rows:
        year = int(history["year"])
        quarter = int(history["quarter"])
        period = f"{year}-Q{quarter}"
        ytd_rows = rows_for_modelo130(ledger_rows, year, quarter)
        current_keys = {_row_key(row) for row in ytd_rows}
        previous_keys = previous_row_keys_by_year.get(year, set())
        delta_rows = [row for row in ytd_rows if _row_key(row) not in previous_keys]
        previous_row_keys_by_year[year] = current_keys

        ledger_ytd = cents(sum((row.irpf_deductible_eur or Decimal("0.00")) for row in ytd_rows))
        previous_ledger_ytd = previous_ledger_ytd_by_year.get(year, Decimal("0.00"))
        ledger_delta = cents(ledger_ytd - previous_ledger_ytd)
        previous_ledger_ytd_by_year[year] = ledger_ytd

        target_ytd = parse_amount(history["target_casilla_02"])
        target_delta = parse_amount(history["target_casilla_02_delta"])
        diff_ytd = cents(ledger_ytd - target_ytd)
        diff_delta = cents(ledger_delta - target_delta)
        pending_delta_rows = [row for row in delta_rows if _is_pending_or_inferred(row)]
        pending_ytd_rows = [row for row in ytd_rows if _is_pending_or_inferred(row)]
        pending_delta = cents(sum((row.irpf_deductible_eur or Decimal("0.00")) for row in pending_delta_rows))

        output.append(
            {
                "period": period,
                "status": _status(ytd_rows, diff_ytd, pending_ytd_rows),
                "target_casilla_02_ytd": _money(target_ytd),
                "ledger_ytd": _money(ledger_ytd),
                "ledger_minus_target_ytd": _money(diff_ytd),
                "target_casilla_02_delta": _money(target_delta),
                "ledger_delta": _money(ledger_delta),
                "ledger_minus_target_delta": _money(diff_delta),
                "ledger_rows_ytd": str(len(ytd_rows)),
                "ledger_rows_delta": str(len(delta_rows)),
                "pending_or_inferred_rows_ytd": str(len(pending_ytd_rows)),
                "pending_or_inferred_rows_delta": str(len(pending_delta_rows)),
                "pending_or_inferred_delta_eur": _money(pending_delta),
                "pending_or_inferred_rows": _format_rows(pending_delta_rows),
                "notes": _notes(ytd_rows, diff_ytd, pending_ytd_rows),
            }
        )
    return output


def write_ledger_projection_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_PROJECTION_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_ledger_projection_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 130 Ledger Projection",
        "",
        "This report compares a Xolo-format expense ledger against submitted Modelo 130 `casilla 02` values.",
        "A match here proves the ledger arithmetic, not Xolo's row-level submitted tax basis.",
        "",
        "| Period | Status | Target 02 YTD | Ledger YTD | Diff YTD | Target delta | Ledger delta | Pending/inferred delta |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["status"],
                    _fmt(row["target_casilla_02_ytd"]),
                    _fmt(row["ledger_ytd"]),
                    _fmt(row["ledger_minus_target_ytd"]),
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["ledger_delta"]),
                    _fmt(row["pending_or_inferred_delta_eur"]),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Pending Or Inferred Delta Rows", ""])
    for row in rows:
        if not row["pending_or_inferred_rows"]:
            continue
        lines.extend([f"### {row['period']}", ""])
        for item in row["pending_or_inferred_rows"].split("; "):
            lines.append(f"- {item}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _status(rows: list[XoloExpenseRow], diff_ytd: Decimal, pending_rows: list[XoloExpenseRow]) -> str:
    if not rows:
        return "no_ledger_rows_for_period"
    if abs(diff_ytd) <= Decimal("0.02") and pending_rows:
        return "matches_target_with_unconfirmed_rows"
    if abs(diff_ytd) <= Decimal("0.02"):
        return "matches_target"
    if pending_rows:
        return "mismatch_with_unconfirmed_rows"
    return "mismatch"


def _notes(rows: list[XoloExpenseRow], diff_ytd: Decimal, pending_rows: list[XoloExpenseRow]) -> str:
    if not rows:
        return "Curated ledger has no rows for this period."
    if abs(diff_ytd) <= Decimal("0.02") and pending_rows:
        return "Ledger matches submitted casilla 02 arithmetically, but some rows are pending/inferred and need Xolo confirmation."
    if abs(diff_ytd) <= Decimal("0.02"):
        return "Ledger matches submitted casilla 02 arithmetically."
    if pending_rows:
        return "Ledger mismatch remains and pending/inferred rows need confirmation."
    return "Ledger mismatch remains."


def _is_pending_or_inferred(row: XoloExpenseRow) -> bool:
    text = f"{row.confidence} {row.notes}".lower()
    return any(marker in text for marker in ("pending", "inferred", "verify", "likely", "provisional"))


def _format_rows(rows: list[XoloExpenseRow]) -> str:
    return "; ".join(
        " ".join(
            part
            for part in (
                row.date.isoformat(),
                row.xolo_id,
                row.number,
                row.recipient,
                _money(row.irpf_deductible_eur or Decimal("0.00")),
                row.confidence,
            )
            if part
        )
        for row in rows
    )


def _row_key(row: XoloExpenseRow) -> tuple[str, str, str]:
    return (row.date.isoformat(), row.xolo_id, row.number)


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return "" if value == "" else format_es(parse_amount(value))
