from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .money import format_es, parse_amount


QUESTION_FIELDS = [
    "status",
    "priority",
    "period",
    "topic",
    "question",
    "why_it_matters",
    "related_rows",
    "evidence",
    "owner",
    "answer",
]


def build_xolo_questions(candidate_quarter_reconciliation_csv: Path) -> list[dict[str, str]]:
    rows = _load_rows(candidate_quarter_reconciliation_csv)
    questions = [
        _question(
            priority="high",
            period="2023-Q2..2026-Q2",
            topic="Modelo 130 source books",
            question=(
                "Please export the `libro registro de compras y gastos` for 2023-Q2 through 2026-Q2, "
                "with row-level inclusion, IRPF deductible amount, VAT-base/gross basis, FX rate/date/source, "
                "booking period, and any adjustment, correction, deferral, or reversal rows."
            ),
            why_it_matters=(
                "The raw Xolo expense UI list is not the same evidence as the source books used for Modelo 130; "
                "quarterly reconciliation still depends on row-level inclusion and basis decisions."
            ),
            related_rows="all Modelo 130 quarters",
            evidence=f"{len(rows)} candidate quarterly reconciliation rows",
        ),
        _question(
            priority="high",
            period="2023-Q2..2026-Q2",
            topic="asset amortization schedule",
            question=(
                "Please export or confirm the full amortization schedule: asset, acquisition date, taxable basis, "
                "rate, start date, accumulated amortization by quarter, and whether GitHub 2023-06-30 and "
                "LINQPad SYNTH-DOCUMENT-029 were capitalized or expensed immediately."
            ),
            why_it_matters=(
                "Annual Modelo 100 is closest to a 25% VAT-base schedule excluding the 2023 USD GitHub/LINQPad rows, "
                "but this is still only a candidate schedule until Xolo confirms it."
            ),
            related_rows="GitHub 2023-06-30; LINQPad SYNTH-DOCUMENT-029; MediaMarkt; Apple SYNTH-DOCUMENT-026; Faciletea; Apple SYNTH-DOCUMENT-031",
            evidence="annual closest scenario: exclude_2023_usd_low_value_or_subscription + vat_base + 0.25",
        ),
    ]

    for row in rows:
        diff = parse_amount(row["candidate_model_minus_target_delta"])
        if abs(diff) < Decimal("20.00"):
            continue
        questions.append(_period_question(row, diff))
        if row["period"] == "2026-Q2" and "SYNTH-DOCUMENT-019" in row.get("nearest_excluded_subset_rows", ""):
            questions.append(_q2_2026_tgss_conflict_question(row))
    return questions


def write_questions_csv(path: Path, questions: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUESTION_FIELDS)
        writer.writeheader()
        writer.writerows(questions)


def write_questions_markdown(path: Path, questions: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Xolo Questions From Modelo 130 Audit",
        "",
        "These questions are generated from the candidate quarterly reconciliation.",
        "They are audit questions, not confirmed tax conclusions.",
        "",
        "| Priority | Period | Topic | Question | Related rows | Evidence |",
        "|---|---|---|---|---|---|",
    ]
    for row in questions:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["priority"],
                    row["period"],
                    row["topic"],
                    row["question"],
                    row["related_rows"],
                    row["evidence"],
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _period_question(row: dict[str, str], diff: Decimal) -> dict[str, str]:
    period = row["period"]
    evidence = (
        f"target delta {row['target_casilla_02_delta']}; "
        f"raw non-asset delta {row['raw_non_asset_gross_delta']}; "
        f"candidate amortization delta {row['candidate_amortization_delta']}; "
        f"model - target {row['candidate_model_minus_target_delta']}"
    )
    related = row.get("nearest_excluded_subset_rows") or period
    if diff > 0:
        question = (
            f"For {period}, the candidate model is {format_es(diff)} EUR above the submitted Modelo 130 "
            "casilla 02 movement. Please confirm whether the listed rows were excluded, netted, reversed, "
            "or used on a different deductible basis in the source books."
        )
        why = (
            "The candidate amortization schedule does not reconcile the quarter unless row-level exclusions or "
            "basis differences are applied."
        )
        topic = "row exclusions or netting"
    else:
        question = (
            f"For {period}, the submitted Modelo 130 casilla 02 movement is {format_es(abs(diff))} EUR above "
            "the candidate model. Please identify any catch-up, reclassification, amortization, or other "
            "adjustment rows included in the source books."
        )
        why = (
            "The submitted quarter contains more deductible expense than raw non-asset rows plus the candidate "
            "amortization schedule explain."
        )
        topic = "catch-up or reclassification"
    return _question(
        priority=_priority(period, diff),
        period=period,
        topic=topic,
        question=question,
        why_it_matters=why,
        related_rows=related,
        evidence=evidence,
    )


def _q2_2026_tgss_conflict_question(row: dict[str, str]) -> dict[str, str]:
    return _question(
        priority="high",
        period="2026-Q2",
        topic="TGSS debt row inclusion conflict",
        question=(
            "Was TGSS debt row SYNTH-DOCUMENT-019 for 189.01 EUR included in the submitted Modelo 130 2T 2026 "
            "source books? If yes, please provide the offsetting row or amortization treatment; if no, "
            "please confirm why it appears in the Xolo expense UI list but not in the submitted quarter."
        ),
        why_it_matters=(
            "The annual-best quarterly amortization scenario puts the model 188.36 EUR above Xolo, and the "
            "nearest excluded subset is this single 189.01 EUR TGSS row."
        ),
        related_rows="2026-05-10 SYNTH-DOCUMENT-019 189.01",
        evidence=(
            f"model - target {row['candidate_model_minus_target_delta']}; "
            f"nearest subset {row['nearest_excluded_subset_eur']} with error {row['nearest_excluded_subset_error_eur']}"
        ),
    )


def _priority(period: str, diff: Decimal) -> str:
    if period in {"2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4", "2026-Q2"}:
        return "high"
    if abs(diff) >= Decimal("75.00"):
        return "high"
    if abs(diff) >= Decimal("40.00"):
        return "medium"
    return "low"


def _question(
    *,
    priority: str,
    period: str,
    topic: str,
    question: str,
    why_it_matters: str,
    related_rows: str,
    evidence: str,
    status: str = "open",
    owner: str = "Xolo",
    answer: str = "",
) -> dict[str, str]:
    return {
        "status": status,
        "priority": priority,
        "period": period,
        "topic": topic,
        "question": question,
        "why_it_matters": why_it_matters,
        "related_rows": related_rows,
        "evidence": evidence,
        "owner": owner,
        "answer": answer,
    }
