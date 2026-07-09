from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .money import format_es, parse_amount


DECISION_CLASSIFICATIONS = {
    "nearest_exclusion_candidate",
    "missing_catch_up_or_reclassification",
    "unresolved_after_nearest_subset",
    "asset_amortization_candidate",
}


def build_row_decision_report(row_audit_csv: Path, root_cause_narrowing_csv: Path) -> list[dict[str, str]]:
    root_by_period = {row["period"]: row for row in _load_rows(root_cause_narrowing_csv)}
    rows: list[dict[str, str]] = []
    for row in _load_rows(row_audit_csv):
        classification = row["classification"]
        if classification not in DECISION_CLASSIFICATIONS:
            continue
        period_root = root_by_period.get(row["period"], {})
        rows.append(_decision_row(row, period_root))
    return sorted(rows, key=lambda row: (row["period"], _priority_sort(row["priority"]), row["decision_kind"], row["date"], row["number"]))


def write_row_decision_report_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_row_decision_report_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_period: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_period.setdefault(row["period"], []).append(row)
    lines = [
        "# Modelo 130 Row Decision Report",
        "",
        "This report lists the concrete row-level decisions still needed to close the sequential Modelo 130 audit.",
        "It is built from `modelo130_row_audit.csv` and `modelo130_root_cause_narrowing.csv`; it does not infer Xolo's source books.",
        "",
        "## Summary",
        "",
        f"- Periods with open row decisions: {len(by_period)}.",
        f"- Row decisions: {len(rows)}.",
        "",
        "| Period | Decisions | High | Medium | Low |",
        "|---|---:|---:|---:|---:|",
    ]
    for period, period_rows in by_period.items():
        counts = _priority_counts(period_rows)
        lines.append(
            f"| {period} | {len(period_rows)} | {counts['high']} | {counts['medium']} | {counts['low']} |"
        )
    for period, period_rows in by_period.items():
        lines.extend(
            [
                "",
                f"## {period}",
                "",
                "| Priority | Decision | Date | Number | Recipient | Amount | Question |",
                "|---|---|---|---|---|---:|---|",
            ]
        )
        for row in period_rows:
            question = row["question"]
            if row.get("root_cause_context"):
                question += " Context: " + row["root_cause_context"]
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["priority"],
                        row["decision_kind"],
                        row["date"],
                        _cell(row["number"]),
                        _cell(row["recipient"]),
                        _fmt(row["amount_eur"]),
                        _cell(question),
                    ]
                )
                + " |"
            )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _decision_row(row: dict[str, str], root: dict[str, str]) -> dict[str, str]:
    classification = row["classification"]
    amount = _decision_amount(row)
    return {
        "period": row["period"],
        "priority": _priority(classification, row, root),
        "decision_kind": _decision_kind(classification, root),
        "classification": classification,
        "date": row["date"],
        "xolo_id": row["xolo_id"],
        "number": row["number"],
        "recipient": row["recipient"],
        "amount_eur": amount,
        "candidate_model_minus_target_delta": row["candidate_model_minus_target_delta"],
        "nearest_subset_total_eur": row["nearest_subset_total_eur"],
        "nearest_subset_error_eur": row["nearest_subset_error_eur"],
        "root_cause_remaining": root.get("remaining_causes", ""),
        "root_cause_context": _root_cause_context(root),
        "question": _question(classification, row, root),
        "xolo_url": row["xolo_url"],
    }


def _priority(classification: str, row: dict[str, str], root: dict[str, str]) -> str:
    if classification in {"missing_catch_up_or_reclassification", "unresolved_after_nearest_subset"}:
        return "high"
    if classification == "nearest_exclusion_candidate":
        error = abs(parse_amount(row["nearest_subset_error_eur"] or "0.00"))
        return "high" if error <= Decimal("1.00") else "medium"
    if classification == "asset_amortization_candidate":
        source = root.get("annual_constraint_source", "")
        return "medium" if source.startswith("m100_0208") else "high"
    return "low"


def _decision_kind(classification: str, root: dict[str, str]) -> str:
    if classification == "nearest_exclusion_candidate":
        return "confirm excluded, netted, or reduced IRPF basis"
    if classification == "missing_catch_up_or_reclassification":
        return "identify missing catch-up or reclassification amount"
    if classification == "unresolved_after_nearest_subset":
        return "explain residual after nearest subset"
    if classification == "asset_amortization_candidate":
        if "asset/direct-expense treatment" in root.get("remaining_causes", ""):
            return "confirm direct expense versus capitalized asset"
        return "confirm asset amortization schedule treatment"
    return classification


def _question(classification: str, row: dict[str, str], root: dict[str, str]) -> str:
    amount = _fmt(_decision_amount(row))
    period = row["period"]
    if classification == "nearest_exclusion_candidate":
        return (
            f"For {period}, was this row included in Modelo 130 at {amount}, excluded, netted, "
            "or deducted on a reduced IRPF basis?"
        )
    if classification == "missing_catch_up_or_reclassification":
        return (
            f"For {period}, submitted casilla 02 is above the local model by {amount}; "
            "which source-book row, catch-up, or reclassification explains it?"
        )
    if classification == "unresolved_after_nearest_subset":
        return (
            f"For {period}, the nearest excluded-row subset {_residual_direction(row)} by {amount}; "
            "which rounding, basis, or extra adjustment explains the residual?"
        )
    if classification == "asset_amortization_candidate":
        if "asset/direct-expense treatment" in root.get("remaining_causes", ""):
            return f"For {period}, was this row treated as a direct expense, capitalized asset, or excluded?"
        return f"For {period}, what asset basis, rate, start date, and amortization amount did Xolo use for this row?"
    return f"For {period}, confirm submitted Modelo 130 treatment for this row."


def _root_cause_context(root: dict[str, str]) -> str:
    contexts: list[str] = []
    eliminated = root.get("eliminated_causes", "")
    remaining = root.get("remaining_causes", "")
    professional_base_diff = root.get("annual_professional_base_diff", "")
    if "annual professional gross-gap row-exclusion inference" in eliminated:
        contexts.append("annual professional gross gap is VAT-shaped, not row-exclusion evidence by itself")
    if professional_base_diff and abs(parse_amount(professional_base_diff)) > Decimal("20.00"):
        contexts.append(f"annual professional VAT-base difference is {format_es(parse_amount(professional_base_diff))} EUR")
    if "annual asset direct-expense or reclassification treatment" in remaining:
        contexts.append("annual Modelo 100 category lens suggests direct expense or reclassification for asset-like rows")
    if "VAT-bearing row completeness" in remaining:
        contexts.append("VAT-bearing row completeness is not locally ruled out for this period")
    return "; ".join(contexts)


def _decision_amount(row: dict[str, str]) -> str:
    raw_amount = row["gross_eur"] or row["amount_original"] or "0.00"
    if row["classification"] == "unresolved_after_nearest_subset":
        return f"{abs(parse_amount(raw_amount)):.2f}"
    return raw_amount


def _residual_direction(row: dict[str, str]) -> str:
    residual = parse_amount(row["gross_eur"] or row["amount_original"] or "0.00")
    if residual < 0:
        return "overshoots the submitted target"
    return "still remains below the submitted target"


def _priority_sort(priority: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}.get(priority, 9)


def _priority_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0}
    for row in rows:
        counts[row["priority"]] = counts.get(row["priority"], 0) + 1
    return counts


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _fmt(value: str) -> str:
    return "" if not value else format_es(parse_amount(value))


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
