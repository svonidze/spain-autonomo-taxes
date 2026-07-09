from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .history import RawXoloExpense, _row_base_eur, _row_gross_eur, load_raw_xolo_expenses
from .money import cents, format_es, parse_amount
from .parsers import quarter_end


EVIDENCE_FIELDS = [
    "year",
    "quarter",
    "period",
    "report",
    "status",
    "target_casilla_01",
    "target_casilla_02",
    "target_casilla_02_delta",
    "target_casilla_19",
    "raw_non_asset_gross_delta",
    "candidate_amortization_delta",
    "candidate_model_delta",
    "candidate_model_minus_target_delta",
    "candidate_amortization_ytd",
    "ytd_residual_after_candidate",
    "nearest_excluded_subset_eur",
    "nearest_excluded_subset_error_eur",
    "nearest_excluded_subset_rows",
    "quarter_non_asset_rows",
    "quarter_asset_candidate_rows",
    "open_question_topics",
    "recommended_next_evidence",
]


def build_quarter_evidence(
    history_audit_csv: Path,
    candidate_quarter_reconciliation_csv: Path,
    xolo_raw_expenses_csv: Path,
    xolo_questions_csv: Path | None = None,
) -> list[dict[str, str]]:
    history_rows = _load_rows(history_audit_csv)
    candidate_rows = {
        row["period"]: row
        for row in _load_rows(candidate_quarter_reconciliation_csv)
    }
    questions_by_period = _questions_by_period(xolo_questions_csv) if xolo_questions_csv else {}
    raw_expenses = load_raw_xolo_expenses(xolo_raw_expenses_csv)

    evidence: list[dict[str, str]] = []
    for history in history_rows:
        year = int(history["year"])
        quarter = int(history["quarter"])
        period = f"{year}-Q{quarter}"
        candidate = candidate_rows[period]
        usd_fx = Decimal(history["derived_income_usd_fx"]) if history.get("derived_income_usd_fx") else None
        quarter_rows = _raw_rows_for_quarter(raw_expenses, year, quarter)
        non_asset_rows = [row for row in quarter_rows if not row.is_asset_like]
        asset_rows = [row for row in quarter_rows if row.is_asset_like]
        diff = parse_amount(candidate["candidate_model_minus_target_delta"])

        evidence.append(
            {
                "year": history["year"],
                "quarter": history["quarter"],
                "period": period,
                "report": history["report"],
                "status": _status(diff),
                "target_casilla_01": history["target_casilla_01"],
                "target_casilla_02": history["target_casilla_02"],
                "target_casilla_02_delta": candidate["target_casilla_02_delta"],
                "target_casilla_19": history["target_casilla_19"],
                "raw_non_asset_gross_delta": candidate["raw_non_asset_gross_delta"],
                "candidate_amortization_delta": candidate["candidate_amortization_delta"],
                "candidate_model_delta": candidate["candidate_model_delta"],
                "candidate_model_minus_target_delta": candidate["candidate_model_minus_target_delta"],
                "candidate_amortization_ytd": candidate["candidate_amortization_ytd"],
                "ytd_residual_after_candidate": candidate["ytd_residual_after_candidate"],
                "nearest_excluded_subset_eur": candidate["nearest_excluded_subset_eur"],
                "nearest_excluded_subset_error_eur": candidate["nearest_excluded_subset_error_eur"],
                "nearest_excluded_subset_rows": candidate["nearest_excluded_subset_rows"],
                "quarter_non_asset_rows": _format_expense_rows(non_asset_rows, usd_fx),
                "quarter_asset_candidate_rows": _format_expense_rows(asset_rows, usd_fx),
                "open_question_topics": _question_topics(questions_by_period, period),
                "recommended_next_evidence": _recommended_next_evidence(diff),
            }
        )
    return evidence


def write_quarter_evidence_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=EVIDENCE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_quarter_evidence_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 130 Quarter Evidence",
        "",
        "This file packages every submitted Modelo 130 quarter with the raw Xolo rows, candidate amortization movement, and generated Xolo questions.",
        "It is audit evidence, not a confirmed Xolo register. The row-level submitted register and asset schedule are still required for exact closure.",
        "",
        "| Period | Status | Target 02 delta | Raw non-asset delta | Candidate amort. delta | Model - target | Nearest subset | Questions |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["status"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["raw_non_asset_gross_delta"]),
                    _fmt(row["candidate_amortization_delta"]),
                    _fmt(row["candidate_model_minus_target_delta"]),
                    _cell(row["nearest_excluded_subset_rows"]),
                    _cell(row["open_question_topics"]),
                ]
            )
            + " |"
        )

    for row in rows:
        lines.extend(
            [
                "",
                f"## {row['period']}",
                "",
                f"- Report: `{row['report']}`",
                f"- Submitted: casilla 01 `{_fmt(row['target_casilla_01'])}`, casilla 02 `{_fmt(row['target_casilla_02'])}`, casilla 19 `{_fmt(row['target_casilla_19'])}`",
                f"- Quarter movement: target 02 delta `{_fmt(row['target_casilla_02_delta'])}`, raw non-asset `{_fmt(row['raw_non_asset_gross_delta'])}`, candidate amortization `{_fmt(row['candidate_amortization_delta'])}`, model-target `{_fmt(row['candidate_model_minus_target_delta'])}`",
                f"- Candidate amortization YTD: `{_fmt(row['candidate_amortization_ytd'])}`; YTD residual after candidate: `{_fmt(row['ytd_residual_after_candidate'])}`",
                f"- Next evidence needed: {row['recommended_next_evidence']}",
                "",
                "Non-asset raw Xolo rows:",
            ]
        )
        lines.extend(_bullet_rows(row["quarter_non_asset_rows"]))
        lines.extend(["", "Asset candidate rows:"])
        lines.extend(_bullet_rows(row["quarter_asset_candidate_rows"]))
        lines.extend(["", "Open Xolo question topics:"])
        lines.extend(_bullet_rows(row["open_question_topics"]))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _questions_by_period(path: Path) -> dict[str, list[str]]:
    questions: dict[str, list[str]] = {}
    for row in _load_rows(path):
        period = row.get("period", "")
        topic = row.get("topic", "")
        priority = row.get("priority", "")
        if not topic:
            continue
        entry = f"{priority}:{topic}" if priority else topic
        questions.setdefault(period, []).append(entry)
    return questions


def _question_topics(questions_by_period: dict[str, list[str]], period: str) -> str:
    topics = list(questions_by_period.get("2023-Q2..2026-Q2", []))
    topics.extend(questions_by_period.get(period, []))
    return "; ".join(dict.fromkeys(topics))


def _raw_rows_for_quarter(rows: list[RawXoloExpense], year: int, quarter: int) -> list[RawXoloExpense]:
    start_month = 1 + (quarter - 1) * 3
    end = quarter_end(year, quarter)
    return sorted(
        (
            row
            for row in rows
            if row.date.year == year and start_month <= row.date.month <= end.month
        ),
        key=lambda row: (row.date, row.recipient, row.number),
    )


def _format_expense_rows(rows: list[RawXoloExpense], usd_fx: Decimal | None) -> str:
    return "; ".join(_format_expense_row(row, usd_fx) for row in rows)


def _format_expense_row(row: RawXoloExpense, usd_fx: Decimal | None) -> str:
    gross = _row_gross_eur(row, usd_fx)
    base = _row_base_eur(row, usd_fx)
    amount = f"{gross:.2f}" if gross is not None else f"{row.amount_original:.2f} {row.currency}"
    basis = "" if base is None or base == gross else f" base {base:.2f}"
    number = row.number or row.recipient
    return f"{row.date.isoformat()} {number} {row.recipient} {amount}{basis}"


def _status(diff: Decimal) -> str:
    amount = abs(cents(diff))
    if amount <= Decimal("1.00"):
        return "candidate_near_match"
    if amount <= Decimal("20.00"):
        return "small_residual"
    if diff > 0:
        return "needs_exclusion_or_basis_reduction"
    return "needs_catch_up_or_reclassification"


def _recommended_next_evidence(diff: Decimal) -> str:
    if abs(diff) <= Decimal("1.00"):
        return "Confirm that the submitted expense register uses the same rows and asset schedule."
    if diff > 0:
        return "Get Xolo row-level exclusions, VAT-base/gross basis decisions, or reversal rows for this quarter."
    return "Get Xolo catch-up, reclassification, amortization, or annual adjustment rows for this quarter."


def _bullet_rows(value: str) -> list[str]:
    if not value:
        return ["- none"]
    return [f"- {item}" for item in value.split("; ")]


def _cell(value: str) -> str:
    return value.replace("|", "\\|")


def _fmt(value: str) -> str:
    return "" if value == "" else format_es(parse_amount(value))
