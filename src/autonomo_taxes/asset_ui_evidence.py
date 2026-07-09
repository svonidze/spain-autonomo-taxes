from __future__ import annotations

from collections import Counter
import csv
from pathlib import Path

from .history import RawXoloExpense, load_raw_xolo_expenses


ASSET_UI_EVIDENCE_FIELDS = [
    "period",
    "date",
    "xolo_id",
    "recipient",
    "type",
    "number",
    "currency",
    "amount_original",
    "gross_eur",
    "vat_base_eur",
    "detail_confidence",
    "ui_depreciable_asset",
    "asset_like_reason",
    "status",
    "evidence",
    "next_action",
]


def build_asset_ui_evidence(xolo_raw_expenses_csv: Path) -> list[dict[str, str]]:
    expenses = load_raw_xolo_expenses(xolo_raw_expenses_csv)
    asset_rows = [row for row in expenses if row.is_asset_like or row.is_depreciable_asset]
    return [_evidence_row(row) for row in sorted(asset_rows, key=lambda item: (item.date, item.recipient, item.number))]


def write_asset_ui_evidence_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ASSET_UI_EVIDENCE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_asset_ui_evidence_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter(row["status"] for row in rows)
    lines = [
        "# Xolo Asset UI Evidence",
        "",
        "This report summarizes Xolo-sourced asset classification fields plus local asset-like candidates.",
        "A Xolo UI depreciable-asset banner confirms classification only; it does not provide the submitted amortization schedule or Modelo 130 deductible amount.",
        "",
        "## Summary",
        "",
        f"- Asset-like rows checked: `{len(rows)}`.",
    ]
    for status in sorted(counts):
        lines.append(f"- `{status}`: `{counts[status]}`.")
    lines.extend(
        [
            "",
            "## Rows",
            "",
            "| Period | Date | Xolo ID | Recipient | Number | Amount | Gross EUR | VAT-base EUR | UI asset? | Status | Next action |",
            "|---|---|---|---|---|---:|---:|---:|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["period"]),
                    _cell(row["date"]),
                    _cell(row["xolo_id"]),
                    _cell(row["recipient"]),
                    _cell(row["number"]),
                    _cell(row["amount_original"] + " " + row["currency"]),
                    _cell(row["gross_eur"]),
                    _cell(row["vat_base_eur"]),
                    _cell(row["ui_depreciable_asset"]),
                    _cell(row["status"]),
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _evidence_row(row: RawXoloExpense) -> dict[str, str]:
    status = _status(row)
    return {
        "period": f"{row.date.year}-Q{((row.date.month - 1) // 3) + 1}",
        "date": row.date.isoformat(),
        "xolo_id": row.xolo_id,
        "recipient": row.recipient,
        "type": row.expense_type,
        "number": row.number,
        "currency": row.currency,
        "amount_original": _money(row.amount_original),
        "gross_eur": _money(row.gross_eur),
        "vat_base_eur": _money(row.vat_base_eur),
        "detail_confidence": row.detail_confidence,
        "ui_depreciable_asset": "yes" if row.is_depreciable_asset else "no",
        "asset_like_reason": _asset_like_reason(row),
        "status": status,
        "evidence": _evidence(row),
        "next_action": _next_action(status),
    }


def _status(row: RawXoloExpense) -> str:
    if row.is_depreciable_asset:
        return "ui_confirms_depreciable_asset"
    if row.is_asset_like:
        return "local_asset_candidate_without_ui_banner"
    return "not_asset_like"


def _asset_like_reason(row: RawXoloExpense) -> str:
    if row.is_depreciable_asset:
        if _has_detail_page_confidence(row):
            return "xolo_detail_depreciable_asset_banner"
        return "xolo_depreciable_asset_flag_without_detail_page"
    text = f"{row.recipient} {row.expense_type} {row.number}".lower()
    if "computer hardware" in text:
        return "computer_hardware_category"
    markers = ["macbook", "media markt", "faciletea", "rosselli", "apple retail", "apple retal"]
    matched = [marker for marker in markers if marker in text]
    if matched:
        return "known_asset_supplier_or_keyword:" + ",".join(matched)
    return "unknown"


def _evidence(row: RawXoloExpense) -> str:
    if row.is_depreciable_asset:
        if _has_detail_page_confidence(row):
            return "Authenticated Xolo expense detail page says this expense is a depreciable asset."
        return "Xolo expense data marks this expense as depreciable, but detail-page confidence is missing."
    return "Local asset-like heuristic selected this row, but the Xolo detail page did not expose a depreciable-asset banner."


def _next_action(status: str) -> str:
    if status == "ui_confirms_depreciable_asset":
        return "Request the submitted asset schedule line: amortizable base, rate/life, period amortization, and accumulated amortization."
    if status == "local_asset_candidate_without_ui_banner":
        return "Ask Xolo whether this row was direct expense, split/multiple treatment, or capitalized in the investment-goods book."
    return ""


def _money(value) -> str:
    return "" if value is None else f"{value:.2f}"


def _has_detail_page_confidence(row: RawXoloExpense) -> bool:
    return row.detail_confidence.startswith("detail_page_")


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
