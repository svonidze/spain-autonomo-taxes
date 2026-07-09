from __future__ import annotations

from collections import Counter
import csv
from pathlib import Path

from .asset_gap_matrix import ASSET_GAP_SIGNAL_PRIORITY
from .money import format_es, parse_amount


SOURCE_BOOK_FIELD_ROWS = [
    ("xolo_expense_id", "Stable join key to Xolo expense rows and local audit rows."),
    ("source_book_type", "`compras_gastos`, `bienes_inversion`, or quarterly tie-out source."),
    ("source_book_line_id", "Book line, folio, or export row id that can be cited later."),
    ("date / booking_date / deducted_in_period", "Separates document date from the quarter where Xolo deducted the row."),
    ("supplier / invoice_number / category", "Human reconciliation fields."),
    ("original_amount / original_currency", "Original row economics before Xolo conversion."),
    ("fx_rate / fx_rate_date / fx_source", "Required for USD/CZK and sub-euro residuals."),
    ("gross_eur / deductible_base_eur / non_deductible_vat_eur", "Separates gross, VAT base, and non-deductible VAT treatment."),
    ("vat_treatment", "For example gross, VAT-base, reverse-charge, outside-scope, or non-deductible VAT."),
    ("irpf_deductible_eur", "Amount used for Modelo 130 casilla 02."),
    (
        "reason_code",
        "Controlled value: included, excluded, netted, reversed, reclassified, deferred, duplicate, corrected, personal-adjusted, non-deductible, amortized.",
    ),
    ("asset_id / method / coefficient_% / useful_life", "Investment-goods book derivation fields."),
    (
        "asset_amortizable_base_eur / quarterly_amortization_eur / accumulated_amortization_eur",
        "Quarterly and cumulative asset tie-out.",
    ),
    ("catch_up_flag / incentive_flag", "Marks catch-up, accelerated, or special treatment instead of ordinary amortization."),
    ("casilla01_ytd / casilla02_ytd / casilla03 / casilla07", "Quarterly filed tie-out values."),
]


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
        "# Xolo Support Request - Modelo 130 Source Books",
        "",
        "Draft only. Do not send automatically.",
        "",
        "## Context",
        "",
        "I am reconstructing the submitted Modelo 130 declarations from 2023-Q2 through 2026-Q2.",
        "The local audit has checked exported invoices, expenses, Modelo 303 VAT consistency, annual Modelo 100 totals where available, raw Xolo expense API coverage, and candidate amortization chains.",
        "",
        "The remaining blocker is that the Xolo expense UI/export does not show the source bookkeeping used to compute Modelo 130: row inclusion, deductible IRPF basis, cross-quarter adjustments, FX/VAT treatment, and asset amortization.",
        "Please answer with source exports if available rather than a narrative explanation: the `libro registro de compras y gastos`, the `libro registro de bienes de inversión`, and quarterly tie-outs to the filed Modelo 130 casillas.",
        "The local arithmetic appendix only routes questions and should not be treated as confirmed Xolo accounting.",
        "",
        "Local intake files:",
        "",
        "- Local CSV: `runs/modelo130_register_answer_intake.csv`",
        "- Local report: `runs/modelo130_register_answer_intake.md`",
        "- Full request: `runs/xolo_modelo130_closure_request.md`",
        "",
        "## Please Provide",
        "",
        "1. `Libro registro de compras y gastos` for 2023, 2024, 2025, and 2026 through 2T, preferably CSV/Excel plus PDF export if available.",
        "2. `Libro registro de bienes de inversión` / asset amortization schedule used for Modelo 130 and annual Renta/Modelo 100.",
        "3. Quarterly tie-out for each Modelo 130 from 2023-2T through 2026-2T: filed casillas 01, 02, 03, and 07, with the book totals that feed them.",
        "4. Source rows or accounting adjustments for catch-up, deferral, correction, reversal, netting, personal-use, non-deductible, and amortization decisions.",
        "",
        "## Machine-Import Field Spec",
        "",
        "If Xolo can export CSV/Excel, please include these fields or equivalent column names:",
        "",
        "| Field(s) | Why needed |",
        "|---|---|",
    ]
    for field, why in SOURCE_BOOK_FIELD_ROWS:
        lines.append(f"| `{field}` | {_cell(why)} |")

    lines.extend(
        [
            "",
            "## Prioritized Reconciliation Prompts",
            "",
            "Please let the books speak first. These prompts name the rows or quarters that the local audit cannot close without source-book evidence; the arithmetic amounts are kept in the appendix below to avoid anchoring the answer.",
            "",
            "| Rank | Priority | Period | Prompt |",
            "|---:|---|---|---|",
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
                    _cell(_deanchored_question(row)),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Treatment Questions That Apply Across Quarters",
            "",
            "- For foreign-currency rows, which FX rate, rate date, and source did Xolo use?",
            "- For VAT-bearing rows, did Xolo use gross, VAT base, non-deductible VAT, reverse-charge, or another IRPF basis?",
            "- Which rows were deducted in a quarter different from the document date, and what booking date/period was used?",
            "- Which rows were excluded, netted, reversed, corrected, duplicated, or reclassified?",
            "- Which low-value items were direct-expensed instead of amortized, and under what source-book treatment?",
            "- For TGSS/RETA apremio rows, which part was deductible principal and which part was non-deductible surcharge or interest?",
            "- Was the difficult-justification expense allowance applied in quarterly Modelo 130, only in annual Renta/Modelo 100, or not at all in these calculations?",
            "",
            "## Why Source Exports Are Needed",
            "",
            f"- Register-answer intake rows waiting for Xolo: `{len(intake_rows)}`.",
            "- Audit priorities: "
            + "; ".join(f"`{priority}`={priority_counts[priority]}" for priority in sorted(priority_counts))
            + ".",
            "- Asset/register split signals: "
            + "; ".join(f"`{signal}`={signal_counts[signal]}" for signal in sorted(signal_counts))
            + ".",
            "- Some quarters have raw non-asset expenses already above the submitted target, so amortization cannot be tuned first.",
            "- Some 2025-2026 quarters need row exclusions or netting alongside the asset schedule.",
            "- Modelo 130 casilla 02 is cumulative year-to-date, so a quarter delta derived from two submitted forms is not enough to prove row-level treatment.",
            "",
            "## Appendix A - Local Asset/Register Split",
            "",
            "This appendix is local routing evidence only. Please do not confirm these numbers without checking the source books.",
            "",
            "| Period | Signal | Required amort./catch-up | Annual-constrained amort. | Required answer |",
            "|---|---|---:|---:|---|",
        ]
    )
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
            "## Appendix B - Local Arithmetic Queue",
            "",
            "These amounts are local hypothesis-routing values, not confirmed Xolo treatment.",
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
            "- `confirmed_included`: yes/no only from source-book rows or explicit Xolo confirmation.",
            "- `confirmed_irpf_deductible_eur`: exact deductible EUR amount used in Modelo 130 casilla 02.",
            "- `confirmed_amortization_eur`: exact quarter amortization amount from the asset schedule.",
            "- `confirmed_basis`: gross, VAT-base, FX-rate, excluded, netted, amortized, principal-only, surcharge-excluded, or another precise basis.",
            "- `source_book_type` and `source_book_line_id`: the book and line/folio that support the confirmation.",
            "- FX, VAT, booking-period, reason-code, asset-derivation, and casilla tie-out fields when present in the export.",
            "",
            "The local model can reproduce or nearly reproduce many quarters arithmetically, but these fits are not proof of Xolo's submitted treatment.",
            "Closing the audit requires source-book lines, asset schedule lines, and tie-outs to the filed casillas, not only gross expense-list amounts.",
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


def _deanchored_question(row: dict[str, str]) -> str:
    label = row.get("row_label", "")
    classification = row.get("classification", "")
    period = row.get("period", "")
    if classification == "missing_catch_up_or_reclassification":
        return f"For {period}, which source-book rows, booking-period decisions, or adjustments make up filed casilla 02?"
    if classification == "asset_amortization_candidate":
        row_part = f" for row {label}" if label else ""
        return f"For {period}, confirm source-book treatment{row_part}: direct expense, low-value item, capitalized asset, amortized, or excluded."
    if classification == "nearest_exclusion_candidate":
        row_part = f" for row {label}" if label else ""
        return f"For {period}, confirm source-book inclusion and deductible IRPF basis{row_part}: included, excluded, netted, reversed, deferred, or reduced."
    if classification == "unresolved_after_nearest_subset":
        return f"For {period}, identify the source-book basis, FX/VAT treatment, rounding, or adjustment that reconciles the filed casilla 02."
    return _question_with_row(row)


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
