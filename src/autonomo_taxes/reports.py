from __future__ import annotations

import csv
from decimal import Decimal
import json
from pathlib import Path

from .modelo130 import Modelo130Result
from .money import format_es, cents
from .parsers import LedgerEntry


def write_ledger(path: Path, entries: list[LedgerEntry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "kind",
        "date",
        "document",
        "counterparty",
        "description",
        "amount_original",
        "currency",
        "amount_eur",
        "deductible_eur",
        "category",
        "confidence",
        "review_required",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for entry in entries:
            writer.writerow(entry.as_row())


def write_compare(path: Path, calculated: Modelo130Result, target: dict[str, Decimal] | None) -> dict[str, object]:
    payload: dict[str, object] = {"target_present": target is not None, "casillas": {}}
    casillas = payload["casillas"]
    assert isinstance(casillas, dict)
    for key, calculated_value in calculated.as_dict().items():
        target_value = target.get(key) if target else None
        casillas[key] = {
            "calculated": f"{cents(calculated_value):.2f}",
            "target": None if target_value is None else f"{cents(target_value):.2f}",
            "diff": None if target_value is None else f"{cents(calculated_value - target_value):.2f}",
        }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def write_markdown_report(
    path: Path,
    year: int,
    quarter: int,
    calculated: Modelo130Result,
    target: dict[str, Decimal] | None,
    entries: list[LedgerEntry],
    manual: list[LedgerEntry],
    warnings: list[str],
    fx_notes: list[str],
    expense_source_summary: dict[str, object] | None = None,
    xolo_reconciliation: list[dict[str, str]] | None = None,
) -> None:
    income_entries = [e for e in entries if e.kind == "income"]
    expense_entries = [e for e in entries if e.kind == "expense" and e.deductible_eur is not None]
    lines: list[str] = []
    lines.append(f"# Modelo 130 {year} Q{quarter} rebuild")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Income YTD: **{format_es(calculated.casilla_01)} EUR**")
    lines.append(f"- Deductible expenses before 5%: **{format_es(calculated.deductible_before_difficult)} EUR**")
    lines.append(f"- Gastos dificil justificacion: **{format_es(calculated.difficult_expenses)} EUR**")
    lines.append(f"- Casilla 02 total expenses: **{format_es(calculated.casilla_02)} EUR**")
    lines.append(f"- Casilla 19 to pay/deduct: **{format_es(calculated.casilla_19)} EUR**")
    if target and "19" in target:
        status = _run_status(calculated, target, xolo_reconciliation)
        lines.append(f"- Run status: **{status}**")
    if target and "01" in target and "02" in target:
        target_before = _deductible_before_from_total(target["01"], target["02"])
        gap = target_before - calculated.deductible_before_difficult
        lines.append(f"- Xolo implied deductible expenses before 5%: **{format_es(target_before)} EUR**")
        lines.append(f"- Unexplained deductible gap before 5%: **{format_es(gap)} EUR**")
    lines.append("")
    if expense_source_summary:
        lines.append("## Expense Source")
        lines.append("")
        lines.append(f"- Final expense source: **{expense_source_summary.get('source')}**")
        lines.append(
            "- Local parsed deductible before 5%: "
            f"**{format_es(expense_source_summary.get('local_parsed_deductible_before_5'))} EUR**"
        )
        lines.append(
            "- Xolo ledger deductible before 5%: "
            f"**{format_es(expense_source_summary.get('xolo_ledger_deductible_before_5'))} EUR**"
        )
        lines.append(f"- Xolo ledger file: `{expense_source_summary.get('xolo_expense_ledger')}`")
        lines.append(f"- Reconciliation CSV: `{Path(str(expense_source_summary.get('reconciliation_csv'))).name}`")
        lines.append("")
    if fx_notes:
        lines.append("## FX Notes")
        lines.append("")
        for note in fx_notes:
            lines.append(f"- {note}")
        lines.append("")
    if target and "01" in target and "02" in target and not expense_source_summary:
        lines.extend(_manual_reconciliation_block(manual, target_before, calculated.deductible_before_difficult))
    if xolo_reconciliation:
        lines.extend(_xolo_reconciliation_block(xolo_reconciliation))
    lines.append("## Casillas")
    lines.append("")
    lines.append("| Casilla | Calculated | Xolo target | Diff |")
    lines.append("|---|---:|---:|---:|")
    for key, value in calculated.as_dict().items():
        target_value = target.get(key) if target else None
        diff = value - target_value if target_value is not None else None
        lines.append(
            f"| {key} | {format_es(value)} | {format_es(target_value)} | {format_es(diff)} |"
        )
    lines.append("")
    lines.append("## Included Income")
    lines.append("")
    lines.append("| Date | Counterparty | Original | EUR | Document |")
    lines.append("|---|---|---:|---:|---|")
    for entry in sorted(income_entries, key=lambda e: (e.date is None, e.date, e.document)):
        lines.append(
            f"| {entry.date or ''} | {entry.counterparty} | {format_es(entry.amount_original)} {entry.currency} | "
            f"{format_es(entry.amount_eur)} | {Path(entry.document).name} |"
        )
    lines.append("")
    lines.append("## Included Expenses")
    lines.append("")
    lines.append("| Date | Counterparty | Category | Deductible EUR | Document |")
    lines.append("|---|---|---|---:|---|")
    for entry in sorted(expense_entries, key=lambda e: (e.date is None, e.date, e.document)):
        lines.append(
            f"| {entry.date or ''} | {entry.counterparty} | {entry.category} | "
            f"{format_es(entry.deductible_eur)} | {Path(entry.document).name} |"
        )
    lines.append("")
    lines.append("## Manual Review Queue")
    lines.append("")
    lines.append("| Document | Original | EUR/deductible candidate | Category | Notes |")
    lines.append("|---|---:|---:|---|---|")
    for entry in manual:
        candidate = entry.deductible_eur or entry.amount_eur or entry.amount_original
        original = (
            f"{format_es(entry.amount_original)} {entry.currency}"
            if entry.amount_original is not None
            else ""
        )
        lines.append(
            f"| {Path(entry.document).name} | {original} | {format_es(candidate)} | "
            f"{entry.category} | {entry.notes} |"
        )
    if not manual:
        lines.append("| - | - | - | - | - |")
    lines.append("")
    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def _deductible_before_from_total(income: Decimal, total_expenses: Decimal) -> Decimal:
    uncapped = (total_expenses - income * Decimal("0.05")) / Decimal("0.95")
    hard_to_justify = income - uncapped
    hard_to_justify = hard_to_justify * Decimal("0.05")
    if hard_to_justify <= Decimal("2000.00"):
        return cents(uncapped)
    return cents(total_expenses - Decimal("2000.00"))


def _manual_reconciliation_block(
    manual: list[LedgerEntry],
    target_before: Decimal,
    calculated_before: Decimal,
) -> list[str]:
    by_category: dict[str, Decimal] = {}
    for entry in manual:
        candidate = entry.deductible_eur or entry.amount_eur or entry.amount_original
        if candidate is None:
            continue
        by_category[entry.category] = by_category.get(entry.category, Decimal("0.00")) + candidate
    known_manual = cents(sum(by_category.values(), Decimal("0.00")))
    gap = cents(target_before - calculated_before)
    lines = ["## Missing Evidence Reconciliation", ""]
    lines.append(f"- Gap before 5% rule: **{format_es(gap)} EUR**")
    lines.append(f"- Manual-review documents with parseable candidate amounts: **{format_es(known_manual)} EUR**")
    lines.append(f"- Remaining gap after candidate amounts: **{format_es(gap - known_manual)} EUR**")
    lines.append("")
    lines.append("| Manual category | Candidate amount |")
    lines.append("|---|---:|")
    for category, amount in sorted(by_category.items()):
        lines.append(f"| {category} | {format_es(amount)} |")
    if not by_category:
        lines.append("| - | - |")
    lines.append("")
    return lines


def _xolo_reconciliation_block(rows: list[dict[str, str]]) -> list[str]:
    status_counts: dict[str, int] = {}
    status_totals: dict[str, Decimal] = {}
    for row in rows:
        status = row["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
        amount = _decimal_from_formatted(row.get("irpf_deductible_eur") or "")
        status_totals[status] = status_totals.get(status, Decimal("0.00")) + amount
    lines = ["## Xolo Expense Reconciliation", ""]
    lines.append("| Status | Rows | Xolo deductible EUR |")
    lines.append("|---|---:|---:|")
    for status in sorted(status_counts):
        lines.append(f"| {status} | {status_counts[status]} | {format_es(status_totals[status])} |")
    lines.append("")
    lines.append("| Date | Recipient | Number | Deductible EUR | Status | Notes |")
    lines.append("|---|---|---|---:|---|---|")
    for row in rows:
        if row["status"] == "parsed_without_xolo_row":
            continue
        notes = (row.get("notes") or "").replace("|", "\\|")
        lines.append(
            f"| {row.get('date', '')} | {row.get('recipient', '')} | {row.get('number', '')} | "
            f"{row.get('irpf_deductible_eur', '')} | {row.get('status', '')} | {notes} |"
        )
    lines.append("")
    return lines


def _run_status(
    calculated: Modelo130Result,
    target: dict[str, Decimal],
    xolo_reconciliation: list[dict[str, str]] | None,
) -> str:
    if calculated.casilla_19 != target["19"]:
        return "recorded mismatch; not filing-ready"
    unresolved = _unresolved_reconciliation_statuses(xolo_reconciliation or [])
    if unresolved:
        return "matched target via unresolved expense rows; not filing-ready"
    return "matched target for casilla 19"


def _unresolved_reconciliation_statuses(rows: list[dict[str, str]]) -> set[str]:
    unresolved = {
        "asset_amortization_decision",
        "deductibility_pending_confirmation",
        "residual_pending_confirmation",
    }
    return {row.get("status", "") for row in rows if row.get("status", "") in unresolved}


def _decimal_from_formatted(value: str) -> Decimal:
    if not value:
        return Decimal("0.00")
    return Decimal(value.replace(".", "").replace(",", "."))
