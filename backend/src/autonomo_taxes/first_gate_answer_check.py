from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .money import format_es, parse_amount
from .register_answer_status import build_register_answer_status


FIRST_GATE_ANSWER_CHECK_FIELDS = [
    "check",
    "period",
    "status",
    "expected",
    "actual",
    "evidence",
    "next_action",
]


def build_first_gate_answer_check(
    first_gate_csv: Path,
    answer_intake_csv: Path,
    *,
    tolerance: Decimal = Decimal("0.02"),
) -> list[dict[str, str]]:
    first_gate_rows = _load_rows(first_gate_csv)
    answer_rows = _load_rows(answer_intake_csv)
    status_rows = build_register_answer_status(answer_intake_csv)

    gate = next((row for row in first_gate_rows if row.get("section") == "gate"), {})
    context = next((row for row in first_gate_rows if row.get("section") == "raw_context"), None)
    period = gate.get("period", "")
    if not gate:
        return [
            _row(
                "closure_verdict",
                "",
                "missing_first_gate",
                "first-gate CSV with section=gate",
                "",
                "No first-gate row was found.",
                "Regenerate runs/modelo130_first_gate.csv.",
            )
        ]

    period_answers = [row for row in answer_rows if row.get("period") == period]
    period_statuses = [row for row in status_rows if row.get("period") == period]
    p0_answers = [row for row in period_answers if row.get("audit_priority") == "P0"]
    p0_statuses = [row for row in period_statuses if row.get("audit_priority") == "P0"]
    ready_period_answers = _ready_answer_rows(period_answers, period_statuses)
    target = _amount(gate.get("amount_eur", ""))
    local_non_asset = _amount(context.get("amount_eur", "")) if context else Decimal("0.00")
    residual = _amount(context.get("fit_signal", "")) if context else Decimal("0.00")

    tieout_status, tieout_actual, tieout_evidence = _tieout_status(ready_period_answers, target, tolerance)
    residual_status, residual_actual, residual_evidence = _residual_status(
        p0_answers,
        residual,
        tolerance,
        has_raw_context=bool(context),
    )
    asset_status, asset_actual, asset_evidence = _asset_status(p0_answers, p0_statuses)
    readiness_status, readiness_actual, readiness_evidence = _readiness_status(p0_statuses)
    verdict = _verdict(
        period_answers=period_answers,
        readiness_status=readiness_status,
        tieout_status=tieout_status,
        residual_status=residual_status,
        asset_status=asset_status,
    )

    return [
        _row(
            "first_gate_reference",
            period,
            "reference",
            f"target={_fmt(target)}; local_non_asset={_fmt(local_non_asset)}; residual={_fmt(residual)}",
            "",
            "First chronological gate values from runs/modelo130_first_gate.csv.",
            "Use this as the acceptance target for Xolo's answer.",
        ),
        _row(
            "p0_answer_readiness",
            period,
            readiness_status,
            f"{len(p0_answers)} P0 rows ready_to_apply",
            readiness_actual,
            readiness_evidence,
            _next_for_readiness(readiness_status),
        ),
        _row(
            "casilla02_tieout",
            period,
            tieout_status,
            _fmt(target),
            tieout_actual,
            tieout_evidence,
            _next_for_tieout(tieout_status),
        ),
        _row(
            "residual_explanation",
            period,
            residual_status,
            _fmt(residual),
            residual_actual,
            residual_evidence,
            _next_for_residual(residual_status),
        ),
        _row(
            "asset_treatment",
            period,
            asset_status,
            "source-book treatment for active asset/direct-expense rows",
            asset_actual,
            asset_evidence,
            _next_for_asset(asset_status),
        ),
        _row(
            "closure_verdict",
            period,
            verdict,
            "ready_for_rebuild",
            "",
            _verdict_evidence(readiness_status, tieout_status, residual_status, asset_status),
            _next_for_verdict(verdict),
        ),
    ]


def write_first_gate_answer_check_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIRST_GATE_ANSWER_CHECK_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_first_gate_answer_check_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    verdict = next((row for row in rows if row["check"] == "closure_verdict"), {})
    period = verdict.get("period", "")
    lines = [
        "# Modelo 130 First Gate Answer Check",
        "",
        "This report checks whether the Xolo answer-intake table is structured enough to rebuild the first chronological gate.",
        "It does not close the quarter by itself; a `ready_for_rebuild` verdict means the next step is to apply answers and rerun the quarter audit.",
        "",
        "## Verdict",
        "",
        f"- Period: `{period}`.",
        f"- Status: `{verdict.get('status', 'unknown')}`.",
        f"- Next action: {verdict.get('next_action', '')}",
        "",
        "## Checks",
        "",
        "| Check | Status | Expected | Actual | Evidence | Next action |",
        "|---|---|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["check"]),
                    _cell(row["status"]),
                    _cell(row["expected"]),
                    _cell(row["actual"]),
                    _cell(row["evidence"]),
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _tieout_status(rows: list[dict[str, str]], target: Decimal, tolerance: Decimal) -> tuple[str, str, str]:
    values = [_amount(row.get("casilla02_ytd", "")) for row in rows if row.get("casilla02_ytd")]
    if not values:
        return "missing", "", "No ready source-book answer row has `casilla02_ytd` for the first gate period."
    actual = "; ".join(_fmt(value) for value in values)
    matches = [abs(value - target) <= tolerance for value in values]
    if all(matches):
        return "matches_target", actual, "Every ready source-book answer row with casilla02_ytd ties to filed casilla 02."
    if any(matches):
        return "conflict", actual, "Ready source-book answer rows contain conflicting casilla02_ytd values."
    return (
        "mismatch",
        actual,
        "Entered casilla02_ytd does not match the filed first-gate target within tolerance.",
    )


def _residual_status(
    rows: list[dict[str, str]],
    residual: Decimal,
    tolerance: Decimal,
    *,
    has_raw_context: bool,
) -> tuple[str, str, str]:
    if not has_raw_context:
        return "missing_context", "", "The first-gate CSV has no raw_context row, so the residual target is unknown."
    adjustment_rows = [
        row
        for row in rows
        if row.get("application_target") in {"register_review.new_adjustment_row", "register_review.residual_adjustment"}
    ]
    amounts = [_confirmed_amount(row) for row in adjustment_rows if _has_confirmed_amount(row)]
    if not adjustment_rows and abs(residual) <= tolerance:
        return "not_applicable", "", "No P0 residual/adjustment rows exist for this first gate."
    if not adjustment_rows:
        return "missing", "", "The first gate has a non-zero residual but no P0 residual/adjustment answer row."
    if not amounts:
        return "missing", "", "The P0 residual/adjustment row has no confirmed deductible or amortization amount."
    total = sum(amounts, Decimal("0.00"))
    if abs(total - residual) <= tolerance:
        return "matches_residual", _fmt(total), "Confirmed adjustment amount matches the first-gate residual."
    return "mismatch", _fmt(total), "Confirmed adjustment amount does not match the first-gate residual."


def _asset_status(answer_rows: list[dict[str, str]], status_rows: list[dict[str, str]]) -> tuple[str, str, str]:
    asset_answers = [row for row in answer_rows if row.get("classification") == "asset_amortization_candidate"]
    if not asset_answers:
        return "not_applicable", "", "No P0 asset-treatment rows exist for this first gate."
    asset_statuses = [row for row in status_rows if row.get("classification") == "asset_amortization_candidate"]
    ready = [row for row in asset_statuses if row.get("readiness_status") == "ready_to_apply"]
    if ready:
        labels = "; ".join(row.get("row_label", "") or row.get("xolo_id", "") for row in ready)
        return "ready", labels, "Xolo source-book treatment for the asset/direct-expense candidate is structured."
    open_rows = [row for row in asset_statuses if row.get("readiness_status") == "open"]
    if len(open_rows) == len(asset_statuses):
        return "missing", "", "No structured Xolo answer has been recorded for the asset/direct-expense candidate."
    blockers = "; ".join(row.get("missing_fields", "") for row in asset_statuses if row.get("missing_fields"))
    return "incomplete", blockers, "Asset/direct-expense candidate has a partial answer but is not ready to apply."


def _readiness_status(rows: list[dict[str, str]]) -> tuple[str, str, str]:
    if not rows:
        return "missing_rows", "0/0", "No P0 answer-intake rows exist for the first gate period."
    ready = [row for row in rows if row.get("readiness_status") == "ready_to_apply"]
    actual = f"{len(ready)}/{len(rows)}"
    if len(ready) == len(rows):
        return "all_ready", actual, "All P0 answer-intake rows are structured and ready to apply."
    if not ready:
        return "none_ready", actual, "No P0 answer-intake rows are ready to apply."
    return "partially_ready", actual, "Some P0 answer-intake rows are ready, but at least one remains open or incomplete."


def _verdict(
    *,
    period_answers: list[dict[str, str]],
    readiness_status: str,
    tieout_status: str,
    residual_status: str,
    asset_status: str,
) -> str:
    if not period_answers:
        return "blocked_missing_answer_intake"
    if readiness_status == "none_ready":
        return "blocked_no_xolo_answer"
    if readiness_status != "all_ready":
        return "partial_answer_needs_cleanup"
    tieout_ok = tieout_status == "matches_target"
    residual_ok = residual_status in {"matches_residual", "not_applicable"}
    asset_ok = asset_status in {"ready", "not_applicable"}
    if tieout_ok and residual_ok and asset_ok:
        return "ready_for_rebuild"
    return "structured_but_unresolved"


def _verdict_evidence(readiness: str, tieout: str, residual: str, asset: str) -> str:
    return f"readiness={readiness}; casilla02_tieout={tieout}; residual={residual}; asset_treatment={asset}"


def _ready_answer_rows(answer_rows: list[dict[str, str]], status_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    ready_keys = {_answer_key(row) for row in status_rows if row.get("readiness_status") == "ready_to_apply"}
    return [row for row in answer_rows if _answer_key(row) in ready_keys]


def _answer_key(row: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        row.get("queue_rank", ""),
        row.get("classification", ""),
        row.get("xolo_id", ""),
        row.get("row_label", ""),
        row.get("application_target", ""),
    )


def _next_for_readiness(status: str) -> str:
    if status == "all_ready":
        return "Apply answers to register review."
    return "Fill P0 answer rows with confirmed source, basis, and amount/treatment fields from Xolo source books."


def _next_for_tieout(status: str) -> str:
    if status == "matches_target":
        return "Use the tie-out as evidence for the rebuild."
    return "Enter Xolo's filed casilla02_ytd from the quarterly source-book tie-out."


def _next_for_residual(status: str) -> str:
    if status in {"matches_residual", "not_applicable"}:
        return "No residual amount action needed before rebuild."
    return "Record the Xolo source-book row, correction, or adjustment that explains the first-gate residual."


def _next_for_asset(status: str) -> str:
    if status in {"ready", "not_applicable"}:
        return "Use the confirmed asset/direct-expense treatment in the rebuild."
    return "Record whether the candidate row was direct-expensed, capitalized/amortized, excluded, or booked elsewhere."


def _next_for_verdict(status: str) -> str:
    if status == "ready_for_rebuild":
        return "Run register-answer apply, rerun first-gate/quarter audit, and compare the rebuilt casilla 02."
    if status == "partial_answer_needs_cleanup":
        return "Move partial/free-text Xolo answers into structured confirmed_* and source-book fields."
    if status == "structured_but_unresolved":
        return "Resolve mismatch rows before applying the answer set."
    return "Ask Xolo for the source-book tie-out and asset schedule, then fill the answer-intake rows."


def _has_confirmed_amount(row: dict[str, str]) -> bool:
    return bool(row.get("confirmed_irpf_deductible_eur") or row.get("confirmed_amortization_eur"))


def _confirmed_amount(row: dict[str, str]) -> Decimal:
    total = Decimal("0.00")
    for field in ("confirmed_irpf_deductible_eur", "confirmed_amortization_eur"):
        value = row.get(field, "")
        if value:
            total += _amount(value)
    return total


def _amount(value: str) -> Decimal:
    return parse_amount(value) if value else Decimal("0.00")


def _row(
    check: str,
    period: str,
    status: str,
    expected: str,
    actual: str,
    evidence: str,
    next_action: str,
) -> dict[str, str]:
    return {
        "check": check,
        "period": period,
        "status": status,
        "expected": expected,
        "actual": actual,
        "evidence": evidence,
        "next_action": next_action,
    }


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _fmt(value: Decimal) -> str:
    return format_es(value)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
