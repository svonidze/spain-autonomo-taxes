from __future__ import annotations

from collections import Counter
import csv
from pathlib import Path

from .asset_gap_matrix import ASSET_GAP_SIGNAL_PRIORITY
from .money import format_es, parse_amount


def build_xolo_support_request_short(
    register_answer_intake_csv: Path,
    asset_gap_matrix_csv: Path,
    *,
    max_questions: int = 8,
) -> str:
    intake_rows = sorted(_load_rows(register_answer_intake_csv), key=_queue_sort_key)
    asset_rows = sorted(
        _load_rows(asset_gap_matrix_csv),
        key=lambda row: (
            ASSET_GAP_SIGNAL_PRIORITY.get(row.get("amortization_gap_signal", ""), 99),
            row.get("period", ""),
        ),
    )
    signal_counts = Counter(row["amortization_gap_signal"] for row in asset_rows)
    priority_counts = Counter(row["audit_priority"] for row in intake_rows)

    lines = [
        "# Xolo Support Request - Modelo 130 Registers",
        "",
        "Draft only. Do not send automatically.",
        "",
        "## Context",
        "",
        "I am reconstructing the submitted Modelo 130 declarations from 2023-Q2 through 2026-Q2.",
        "The local audit has checked exported invoices, expenses, Modelo 303 VAT consistency, annual Modelo 100 totals where available, raw Xolo expense API coverage, and candidate amortization chains.",
        "",
        "The remaining blocker is that the Xolo expense UI/export does not show the actual submitted Modelo 130 tax register: row inclusion, deductible IRPF basis, cross-quarter adjustments, and asset amortization used per quarter.",
        "Please answer with source exports if available rather than a narrative explanation. The local target-fitting arithmetic below only routes the questions and should not be treated as confirmed Xolo accounting.",
        "",
        "Local intake files:",
        "",
        "- Local CSV: `runs/modelo130_register_answer_intake.csv`",
        "- Local report: `runs/modelo130_register_answer_intake.md`",
        "- Full request: `runs/xolo_modelo130_closure_request.md`",
        "",
        "## Please Provide",
        "",
        "1. Submitted Modelo 130 expense register for every quarter from 2023-Q2 through 2026-Q2.",
        "2. Per row: date, supplier, invoice number, category, original amount/currency, EUR deductible amount used for casilla 02, and treatment: gross, VAT base, excluded, netted, reversed, adjusted, or amortized.",
        "3. Full asset amortization schedule used for Modelo 130 and annual Modelo 100/Renta: asset, acquisition date, basis, VAT treatment, start date, rate, quarterly amortization, accumulated amortization.",
        "4. Source rows or accounting adjustments for the material gaps and asset/register split below.",
        "",
        "## Why Source Exports Are Needed",
        "",
        f"- Register-answer intake rows waiting for Xolo: `{len(intake_rows)}`.",
        "- Audit priorities: " + "; ".join(f"`{priority}`={priority_counts[priority]}" for priority in sorted(priority_counts)) + ".",
        "- Asset/register split signals: "
        + "; ".join(f"`{signal}`={signal_counts[signal]}" for signal in sorted(signal_counts))
        + ".",
        "- Some quarters have raw non-asset expenses already above the submitted target, so amortization cannot be tuned first.",
        "- Some 2025-2026 quarters need row exclusions or netting alongside the asset schedule.",
        "",
        "## Asset/Register Split",
        "",
        "| Period | Signal | Required amort./catch-up | Annual-constrained amort. | Required answer |",
        "|---|---|---:|---:|---|",
    ]
    for row in asset_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    _cell(row["amortization_gap_signal"]),
                    _fmt(row["required_amortization_or_catchup_delta"]),
                    _fmt(row["annual_constrained_amortization_delta"]),
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Highest Priority Questions",
            "",
            "| Rank | Priority | Period | Target | Amount EUR | Question |",
            "|---:|---|---|---|---:|---|",
        ]
    )
    for row in intake_rows[:max_questions]:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["queue_rank"],
                    row["audit_priority"],
                    row["period"],
                    _cell(row["application_target"]),
                    _fmt(row["amount_eur"]),
                    _cell(_question_with_row(row)),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## How I Will Use The Answer",
            "",
            "I will enter Xolo's response into `runs/modelo130_register_answer_intake.csv` using only explicit confirmations:",
            "",
            "- `confirmed_included`: yes/no only from the submitted register or Xolo confirmation.",
            "- `confirmed_irpf_deductible_eur`: exact deductible EUR amount used in Modelo 130 casilla 02.",
            "- `confirmed_amortization_eur`: exact quarter amortization amount from the asset schedule.",
            "- `confirmed_basis`: gross, VAT-base, FX-rate, excluded, netted, amortized, principal-only, surcharge-excluded, or another precise basis.",
            "",
            "The local model can reproduce or nearly reproduce many quarters arithmetically, but these fits are not proof of Xolo's submitted treatment.",
            "Closing the audit requires the submitted row-level register and asset schedule, not only gross expense-list amounts.",
            "",
        ]
    )
    return "\n".join(lines)


def write_xolo_support_request_short(path: Path, markdown: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")


def _question_with_row(row: dict[str, str]) -> str:
    label = row.get("row_label", "")
    question = row.get("question", "")
    if not label:
        return question
    return f"{question} Row: {label}."


def _queue_sort_key(row: dict[str, str]) -> tuple[int, str]:
    try:
        rank = int(row.get("queue_rank", "0"))
    except ValueError:
        rank = 0
    return (rank, row.get("period", ""))


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _fmt(value: str) -> str:
    return "" if not value else format_es(parse_amount(value))


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
