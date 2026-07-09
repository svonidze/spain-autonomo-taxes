from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .money import format_es, parse_amount


REGISTER_REVIEW_FIELDS = [
    "period",
    "priority",
    "review_status",
    "row_kind",
    "source_classification",
    "xolo_id",
    "date",
    "recipient",
    "type",
    "number",
    "currency",
    "amount_original",
    "gross_eur",
    "base_eur",
    "gross_minus_base_eur",
    "candidate_model_amount_eur",
    "hypothesis",
    "confirmation_question",
    "confirmed_included",
    "confirmed_irpf_deductible_eur",
    "confirmed_amortization_eur",
    "confirmed_basis",
    "confirmed_source",
    "notes",
    "xolo_url",
]


def build_register_review_template(row_audit_csv: Path) -> list[dict[str, str]]:
    rows = _load_rows(row_audit_csv)
    return [_review_row(row) for row in rows]


def write_register_review_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTER_REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_register_review_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 130 Submitted Register Review Template",
        "",
        "Fill this template from Xolo's submitted Modelo 130 expense register and asset schedule.",
        "The current hypotheses come from the raw Xolo UI export and candidate amortization model; they are not confirmed tax treatment.",
        "",
        "## Period Summary",
        "",
        "| Period | High | Medium | Low | Total |",
        "|---|---:|---:|---:|---:|",
    ]
    for period, period_rows in _group_by_period(rows).items():
        counts = _priority_counts(period_rows)
        lines.append(
            f"| {period} | {counts['high']} | {counts['medium']} | {counts['low']} | {len(period_rows)} |"
        )
    lines.extend(
        [
            "",
            "## High Priority Rows",
            "",
            "| Period | Hypothesis | Date | Xolo ID | Number | Recipient | Gross EUR | Question |",
            "|---|---|---|---:|---|---|---:|---|",
        ]
    )
    for row in rows:
        if row["priority"] != "high":
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["hypothesis"],
                    row["date"],
                    _xolo_id_cell(row),
                    _cell(row["number"]),
                    _cell(row["recipient"]),
                    _fmt(row["gross_eur"]),
                    _cell(row["confirmation_question"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _review_row(row: dict[str, str]) -> dict[str, str]:
    classification = row["classification"]
    hypothesis = _hypothesis(classification)
    priority = _priority(row)
    question = _question(row, hypothesis)
    return {
        "period": row["period"],
        "priority": priority,
        "review_status": "open",
        "row_kind": row["row_kind"],
        "source_classification": classification,
        "xolo_id": row["xolo_id"],
        "date": row["date"],
        "recipient": row["recipient"],
        "type": row["type"],
        "number": row["number"],
        "currency": row["currency"],
        "amount_original": row["amount_original"],
        "gross_eur": row["gross_eur"],
        "base_eur": row["base_eur"],
        "gross_minus_base_eur": row["gross_minus_base_eur"],
        "candidate_model_amount_eur": _candidate_amount(row),
        "hypothesis": hypothesis,
        "confirmation_question": question,
        "confirmed_included": "",
        "confirmed_irpf_deductible_eur": "",
        "confirmed_amortization_eur": "",
        "confirmed_basis": "",
        "confirmed_source": "",
        "notes": row["notes"],
        "xolo_url": row["xolo_url"],
    }


def _hypothesis(classification: str) -> str:
    if classification == "asset_amortization_candidate":
        return "confirm asset schedule, not direct expense"
    if classification == "nearest_exclusion_candidate":
        return "possibly excluded, netted, or basis-reduced in submitted register"
    if classification == "missing_catch_up_or_reclassification":
        return "missing Xolo adjustment or catch-up row"
    if classification == "unresolved_after_nearest_subset":
        return "nearest subset leaves unresolved residual"
    if classification == "raw_non_asset_context_for_catch_up":
        return "context row while target is above candidate model"
    return "candidate raw non-asset included at gross amount"


def _priority(row: dict[str, str]) -> str:
    classification = row["classification"]
    if classification in {
        "asset_amortization_candidate",
        "nearest_exclusion_candidate",
        "missing_catch_up_or_reclassification",
        "unresolved_after_nearest_subset",
    }:
        return "high"
    gross = _amount(row["gross_eur"])
    gross_minus_base = abs(_amount(row["gross_minus_base_eur"]))
    if row["currency"] != "EUR" or gross_minus_base > Decimal("0.00"):
        return "medium"
    if gross >= Decimal("300.00"):
        return "medium"
    return "low"


def _question(row: dict[str, str], hypothesis: str) -> str:
    classification = row["classification"]
    if classification == "asset_amortization_candidate":
        return (
            "Confirm acquisition basis, VAT basis, amortization rate, start date, and amortization "
            "amount included in each submitted Modelo 130 quarter."
        )
    if classification == "nearest_exclusion_candidate":
        return (
            "Confirm whether this row was excluded, netted, reversed, or deducted on a different basis "
            "in the submitted Modelo 130 register."
        )
    if classification == "missing_catch_up_or_reclassification":
        return "Identify the Xolo adjustment, catch-up, reclassification, or amortization row that makes the submitted target higher."
    if classification == "unresolved_after_nearest_subset":
        return "Identify the remaining adjustment after applying the nearest excluded-row hypothesis."
    if row["currency"] != "EUR":
        return "Confirm FX source/rate and deductible EUR amount used in the submitted register."
    if abs(_amount(row["gross_minus_base_eur"])) > Decimal("0.00"):
        return "Confirm whether IRPF deductible amount used gross, VAT base, or another basis."
    if _amount(row["gross_eur"]) >= Decimal("300.00"):
        return "Confirm this higher-value row was included and the deductible amount used."
    return f"Confirm submitted-register treatment for this row: {hypothesis}."


def _candidate_amount(row: dict[str, str]) -> str:
    classification = row["classification"]
    if classification == "asset_amortization_candidate":
        return "0.00"
    if row["row_kind"] == "synthetic_gap":
        return row["gross_eur"]
    return row["gross_eur"]


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _group_by_period(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["period"], []).append(row)
    return grouped


def _priority_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0}
    for row in rows:
        counts[row["priority"]] += 1
    return counts


def _amount(value: str) -> Decimal:
    return parse_amount(value) if value else Decimal("0.00")


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
