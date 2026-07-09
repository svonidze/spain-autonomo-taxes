from __future__ import annotations

from collections import Counter, defaultdict
import csv
from pathlib import Path
import re

from .money import format_es, parse_amount


REGISTER_ANSWER_INTAKE_FIELDS = [
    "queue_rank",
    "period",
    "audit_priority",
    "decision_priority",
    "classification",
    "row_label",
    "amount_eur",
    "xolo_id",
    "xolo_url",
    "question",
    "required_evidence",
    "answer_status",
    "xolo_answer",
    "confirmed_included",
    "confirmed_irpf_deductible_eur",
    "confirmed_amortization_eur",
    "confirmed_basis",
    "confirmed_source",
    "source_book_type",
    "source_book_line_id",
    "booking_date",
    "deducted_in_period",
    "original_amount",
    "original_currency",
    "fx_rate",
    "fx_rate_date",
    "fx_source",
    "deductible_base_eur",
    "non_deductible_vat_eur",
    "vat_treatment",
    "reason_code",
    "asset_id",
    "asset_method",
    "asset_coefficient_percent",
    "asset_useful_life_years",
    "asset_amortizable_base_eur",
    "asset_first_period_proration",
    "asset_accumulated_amortization_eur",
    "asset_catch_up_flag",
    "asset_incentive_flag",
    "casilla01_ytd",
    "casilla02_ytd",
    "casilla03",
    "casilla07",
    "application_target",
    "application_notes",
]

SOURCE_BOOK_FIELDS = [
    "source_book_type",
    "source_book_line_id",
    "booking_date",
    "deducted_in_period",
    "original_amount",
    "original_currency",
    "fx_rate",
    "fx_rate_date",
    "fx_source",
    "deductible_base_eur",
    "non_deductible_vat_eur",
    "vat_treatment",
    "reason_code",
    "asset_id",
    "asset_method",
    "asset_coefficient_percent",
    "asset_useful_life_years",
    "asset_amortizable_base_eur",
    "asset_first_period_proration",
    "asset_accumulated_amortization_eur",
    "asset_catch_up_flag",
    "asset_incentive_flag",
    "casilla01_ytd",
    "casilla02_ytd",
    "casilla03",
    "casilla07",
]


def build_register_answer_intake(blocking_queue_csv: Path) -> list[dict[str, str]]:
    rows = _load_rows(blocking_queue_csv)
    return [_intake_row(row) for row in rows]


def write_register_answer_intake_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTER_ANSWER_INTAKE_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in REGISTER_ANSWER_INTAKE_FIELDS})


def write_register_answer_intake_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    priority_counts = Counter(row["audit_priority"] for row in rows)
    target_counts = Counter(row["application_target"] for row in rows)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["period"]].append(row)

    lines = [
        "# Xolo Register Answer Intake Template",
        "",
        "Fill this from Xolo's source books: `libro registro de compras y gastos` and `libro registro de bienes de inversión`.",
        "It is an intake surface only: blank confirmation fields are open work, not zero.",
        "",
        "## Summary",
        "",
        f"- Intake rows: `{len(rows)}`.",
        "- Audit priorities: "
        + "; ".join(f"`{priority}`={priority_counts[priority]}" for priority in sorted(priority_counts)),
        "- Application targets: "
        + "; ".join(f"`{target}`={target_counts[target]}" for target in sorted(target_counts)),
        "",
        "## How To Fill",
        "",
        "- `confirmed_included`: use `yes` only if Xolo confirms the row is in the source books for Modelo 130; use `no` only if Xolo confirms exclusion, netting, reversal, or non-deductible treatment.",
        "- `confirmed_irpf_deductible_eur`: the row amount used in Modelo 130 casilla 02, excluding separate amortization if Xolo reports it separately.",
        "- `confirmed_amortization_eur`: quarter amortization amount confirmed by Xolo for an asset row or synthetic amortization adjustment.",
        "- `confirmed_basis`: gross, VAT-base, FX-rate, excluded, netted, amortized, principal-only, surcharge-excluded, or other precise basis.",
        "- `confirmed_source`: source-book export, asset schedule, Xolo support answer, or local evidence path.",
        "- `source_book_type` / `source_book_line_id`: cite the source book and stable line/folio used for the confirmation.",
        "- FX/VAT fields: fill `fx_rate`, `fx_rate_date`, `fx_source`, `deductible_base_eur`, `non_deductible_vat_eur`, and `vat_treatment` when Xolo provides them.",
        "- Asset fields: fill asset id, method, coefficient, amortizable base, accumulated amortization, and catch-up/incentive flags from the investment-goods book.",
        "- Casilla fields: fill filed YTD `casilla01_ytd`, `casilla02_ytd`, `casilla03`, and `casilla07` when the source export includes the quarterly tie-out.",
        "",
        "## First Open Rows",
        "",
        "| Rank | Period | Priority | Target | Row | Amount | Question |",
        "|---:|---|---|---|---|---:|---|",
    ]
    for row in rows[:20]:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["queue_rank"],
                    row["period"],
                    row["audit_priority"],
                    row["application_target"],
                    _cell(row["row_label"] or "synthetic adjustment"),
                    _fmt(row["amount_eur"]),
                    _cell(row["question"]),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Period Counts", "", "| Period | Rows | High priority rows |", "|---|---:|---:|"])
    for period in sorted(grouped):
        period_rows = grouped[period]
        high = sum(1 for row in period_rows if row["decision_priority"] == "high")
        lines.append(f"| {period} | {len(period_rows)} | {high} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _intake_row(row: dict[str, str]) -> dict[str, str]:
    classification = row.get("classification", "")
    target, notes = _application_target(classification)
    return {
        "queue_rank": row.get("queue_rank", ""),
        "period": row.get("period", ""),
        "audit_priority": row.get("audit_priority", ""),
        "decision_priority": row.get("decision_priority", ""),
        "classification": classification,
        "row_label": row.get("row_label", ""),
        "amount_eur": row.get("amount_eur", ""),
        "xolo_id": _xolo_id(row),
        "xolo_url": row.get("xolo_url", ""),
        "question": row.get("question", ""),
        "required_evidence": row.get("required_evidence", ""),
        "answer_status": "open",
        "xolo_answer": "",
        "confirmed_included": "",
        "confirmed_irpf_deductible_eur": "",
        "confirmed_amortization_eur": "",
        "confirmed_basis": "",
        "confirmed_source": "",
        **{field: "" for field in SOURCE_BOOK_FIELDS},
        "application_target": target,
        "application_notes": notes,
    }


def _application_target(classification: str) -> tuple[str, str]:
    if classification == "asset_amortization_candidate":
        return (
            "register_review.confirmed_amortization_eur",
            "If Xolo says direct expense instead of asset, record amount in confirmed_irpf_deductible_eur and basis in confirmed_basis.",
        )
    if classification == "missing_catch_up_or_reclassification":
        return (
            "register_review.new_adjustment_row",
            "Create or identify the missing adjustment/catch-up/reclassification row before marking the quarter closed.",
        )
    if classification == "unresolved_after_nearest_subset":
        return (
            "register_review.residual_adjustment",
            "Record the residual rounding, basis, or extra source-book adjustment explained by Xolo.",
        )
    if classification == "nearest_exclusion_candidate":
        return (
            "register_review.confirmed_included",
            "Use confirmed_included=no only for confirmed excluded/netted/reversed/non-deductible treatment.",
        )
    return (
        "register_review.confirmed_irpf_deductible_eur",
        "Record the exact deductible EUR amount used by Xolo for this row.",
    )


def _xolo_id(row: dict[str, str]) -> str:
    url = row.get("xolo_url", "")
    match = re.search(r"/invoice/(\d+)/", url)
    if match:
        return match.group(1)
    label = row.get("row_label", "")
    match = re.search(r"\b(\d{6,})\b", label)
    return match.group(1) if match else ""


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _fmt(value: str) -> str:
    return "" if not value else format_es(parse_amount(value))


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
