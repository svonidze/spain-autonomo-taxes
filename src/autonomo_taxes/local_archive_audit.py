from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
import re

from .history import RawXoloExpense, _row_gross_eur, load_raw_xolo_expenses
from .money import cents, format_es, parse_amount
from .parsers import LedgerEntry, scan_expense_dir


LOCAL_ARCHIVE_AUDIT_FIELDS = [
    "period",
    "status",
    "date",
    "local_document",
    "counterparty",
    "description",
    "parser_kind",
    "category",
    "amount_original",
    "currency",
    "amount_eur",
    "deductible_eur",
    "confidence",
    "review_required",
    "matched_xolo_ids",
    "matched_xolo_numbers",
    "matched_xolo_recipients",
    "matched_xolo_amounts",
    "match_reasons",
    "notes",
]

ATTENTION_STATUSES = {
    "local_parsed_without_xolo_raw",
    "local_manual_review_without_xolo_raw",
    "amount_mismatch_potential_xolo_raw",
    "ambiguous_xolo_raw_matches",
}


def build_local_archive_audit(xolo_root: Path, xolo_raw_expenses_csv: Path) -> list[dict[str, str]]:
    entries, manual = scan_expense_dir(xolo_root / "EXPENSE")
    raw_rows = load_raw_xolo_expenses(xolo_raw_expenses_csv)
    return audit_local_entries(entries + manual, raw_rows)


def audit_local_entries(
    local_entries: list[LedgerEntry],
    raw_rows: list[RawXoloExpense],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for entry in sorted(local_entries, key=lambda item: (item.date or date.min, Path(item.document).name.lower())):
        matches = _matches(entry, raw_rows)
        rows.append(_audit_row(entry, matches))
    return rows


def write_local_archive_audit_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOCAL_ARCHIVE_AUDIT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_local_archive_audit_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_status = _counts(rows, "status")
    attention_rows = [
        row
        for row in rows
        if row["status"] in ATTENTION_STATUSES and _focus_period(row["period"])
    ]
    lines = [
        "# Modelo 130 Local Archive Coverage Audit",
        "",
        "This report compares locally parsed `EXPENSE` archive documents against the raw Xolo expense API rows.",
        "It is a coverage diagnostic only: a local document can support evidence, but Xolo's source books are still required for row inclusion and deductible basis.",
        "",
        "## Summary",
        "",
        f"- Local documents checked: `{len(rows)}`.",
        f"- 2023/2024 local attention candidates: `{len(attention_rows)}`.",
        "",
        "| Status | Count |",
        "|---|---:|",
    ]
    for status, count in by_status:
        lines.append(f"| {status} | {count} |")

    lines.extend(
        [
            "",
            "## 2023/2024 Local Attention Candidates",
            "",
            "| Period | Status | Date | Amount | Document | Notes |",
            "|---|---|---|---:|---|---|",
        ]
    )
    if not attention_rows:
        lines.append("|  | none |  |  |  |  |")
    for row in attention_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["status"],
                    row["date"],
                    _fmt_optional(row["amount_eur"] or row["amount_original"]),
                    _cell(row["local_document"]),
                    _cell(row["notes"]),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## All Local Rows",
            "",
            "| Period | Status | Date | Amount | Document | Xolo match | Reasons |",
            "|---|---|---|---:|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["status"],
                    row["date"],
                    _fmt_optional(row["amount_eur"] or row["amount_original"]),
                    _cell(row["local_document"]),
                    _cell(row["matched_xolo_numbers"] or row["matched_xolo_ids"]),
                    _cell(row["match_reasons"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _audit_row(entry: LedgerEntry, matches: list[tuple[RawXoloExpense, str]]) -> dict[str, str]:
    status = _status(entry, matches)
    raw_rows = [row for row, _ in matches]
    return {
        "period": _period(entry.date),
        "status": status,
        "date": entry.date.isoformat() if entry.date else "",
        "local_document": _local_document(entry.document),
        "counterparty": entry.counterparty,
        "description": entry.description,
        "parser_kind": entry.kind,
        "category": entry.category,
        "amount_original": _amount(entry.amount_original),
        "currency": entry.currency,
        "amount_eur": _amount(entry.amount_eur),
        "deductible_eur": _amount(entry.deductible_eur),
        "confidence": entry.confidence,
        "review_required": "yes" if entry.review_required else "no",
        "matched_xolo_ids": "; ".join(row.xolo_id for row in raw_rows if row.xolo_id),
        "matched_xolo_numbers": "; ".join(row.number for row in raw_rows if row.number),
        "matched_xolo_recipients": "; ".join(row.recipient for row in raw_rows if row.recipient),
        "matched_xolo_amounts": "; ".join(_raw_amount(row) for row in raw_rows),
        "match_reasons": "; ".join(reason for _, reason in matches),
        "notes": _notes(entry, status),
    }


def _matches(entry: LedgerEntry, raw_rows: list[RawXoloExpense]) -> list[tuple[RawXoloExpense, str]]:
    matches: list[tuple[RawXoloExpense, str]] = []
    local_text = _normalize(f"{entry.document} {entry.description}")
    for row in raw_rows:
        reason = _match_reason(entry, row, local_text)
        if reason:
            matches.append((row, reason))
    return _dedupe_matches(matches)


def _match_reason(entry: LedgerEntry, row: RawXoloExpense, local_text: str) -> str:
    row_number = _strong_number(row.number)
    if row_number and row_number in local_text:
        return "number_in_local_document"
    if entry.date and entry.date == row.date and _same_amount(entry, row) and _counterparty_overlap(entry.counterparty, row.recipient):
        return "same_date_amount_counterparty"
    if entry.date and entry.date == row.date and _same_amount(entry, row):
        return "same_date_amount"
    if entry.date and entry.date == row.date and _counterparty_overlap(entry.counterparty, row.recipient):
        return "same_date_counterparty_amount_mismatch"
    return ""


def _same_amount(entry: LedgerEntry, row: RawXoloExpense) -> bool:
    local_amounts = [entry.amount_original, entry.amount_eur, entry.deductible_eur]
    raw_amounts = [row.amount_original, row.subtotal_amount, _row_gross_eur(row, None)]
    for left in local_amounts:
        if left is None:
            continue
        for right in raw_amounts:
            if right is not None and abs(cents(left) - cents(right)) <= Decimal("0.01"):
                return True
    return False


def _counterparty_overlap(left: str, right: str) -> bool:
    left_tokens = {token for token in re.split(r"\W+", left.lower()) if len(token) >= 4}
    right_tokens = {token for token in re.split(r"\W+", right.lower()) if len(token) >= 4}
    return bool(left_tokens & right_tokens)


def _dedupe_matches(matches: list[tuple[RawXoloExpense, str]]) -> list[tuple[RawXoloExpense, str]]:
    by_key: dict[tuple[str, str, str], tuple[RawXoloExpense, set[str]]] = {}
    for row, reason in matches:
        key = (row.xolo_id, row.number, row.date.isoformat())
        if key not in by_key:
            by_key[key] = (row, set())
        by_key[key][1].add(reason)
    return [(row, "+".join(sorted(reasons))) for row, reasons in by_key.values()]


def _status(entry: LedgerEntry, matches: list[tuple[RawXoloExpense, str]]) -> str:
    if len(matches) == 1:
        if "amount_mismatch" in matches[0][1]:
            return "amount_mismatch_potential_xolo_raw"
        return "matched_to_xolo_raw"
    if len(matches) > 1:
        return "ambiguous_xolo_raw_matches"
    if entry.kind == "manual_review" or entry.review_required:
        if entry.date is None and entry.amount_eur is None and entry.amount_original is None:
            return "local_manual_review_without_date_amount"
        return "local_manual_review_without_xolo_raw"
    return "local_parsed_without_xolo_raw"


def _notes(entry: LedgerEntry, status: str) -> str:
    parts = []
    if entry.notes:
        parts.append(entry.notes)
    if status.startswith("local_"):
        parts.append("Not matched to the raw Xolo expense API snapshot; confirm whether this file was evidence-only, ignored, or manually entered into the source books.")
    if status == "ambiguous_xolo_raw_matches":
        parts.append("Matched more than one Xolo raw row; inspect manually before using as evidence.")
    return " ".join(parts)


def _period(value: date | None) -> str:
    if value is None:
        return "undated"
    quarter = (value.month - 1) // 3 + 1
    return f"{value.year}-Q{quarter}"


def _focus_period(period: str) -> bool:
    return period.startswith("2023-") or period.startswith("2024-") or period == "undated"


def _raw_amount(row: RawXoloExpense) -> str:
    gross = _row_gross_eur(row, None)
    return _amount(gross if gross is not None else row.amount_original)


def _local_document(value: str) -> str:
    path = Path(value)
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.name


def _amount(value: Decimal | None) -> str:
    return "" if value is None else f"{cents(value):.2f}"


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", value.lower())


def _strong_number(value: str) -> str:
    normalized = _normalize(value)
    if len(normalized) >= 7:
        return normalized
    if len(normalized) >= 4 and re.search(r"[a-z]", normalized):
        return normalized
    return ""


def _fmt_optional(value: str) -> str:
    return "" if not value else format_es(parse_amount(value))


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _counts(rows: list[dict[str, str]], field: str) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row[field]] = counts.get(row[field], 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))
