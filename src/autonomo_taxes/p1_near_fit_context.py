from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .money import cents, format_es, parse_amount


P1_NEAR_FIT_CONTEXT_FIELDS = [
    "period",
    "priority",
    "acceptance_status",
    "hypothesis_id",
    "hypothesis_status",
    "target_delta_eur",
    "model_amount_eur",
    "diff_to_target_eur",
    "annual_constrained_balance_to_target",
    "fit_signal",
    "components",
    "source_ref",
    "evidence_status",
    "notes",
    "xolo_question",
    "context_signal",
]


@dataclass(frozen=True)
class P1Hypothesis:
    period: str
    hypothesis_id: str
    hypothesis_status: str
    target_delta_eur: Decimal
    model_amount_eur: Decimal
    diff_to_target_eur: Decimal
    fit_signal: str
    components: str
    source_ref: str
    evidence_status: str
    notes: str
    xolo_question: str


def build_p1_near_fit_context(
    quarter_acceptance_csv: Path,
    quarter_balance_bridge_csv: Path,
    hypotheses_csv: Path,
) -> list[dict[str, str]]:
    p1_acceptance = {
        row["period"]: row
        for row in _load_rows(quarter_acceptance_csv)
        if row.get("priority") == "P1"
    }
    bridge_by_period = {row["period"]: row for row in _load_rows(quarter_balance_bridge_csv)}
    hypotheses = _load_hypotheses(hypotheses_csv)

    rows: list[dict[str, str]] = []
    for hypothesis in hypotheses:
        acceptance = p1_acceptance.get(hypothesis.period)
        if acceptance is None:
            continue
        bridge = bridge_by_period.get(hypothesis.period)
        if bridge is None:
            raise ValueError(f"Missing quarter balance bridge row for {hypothesis.period}")
        annual_balance = parse_amount(bridge["annual_constrained_balance_to_target"])
        rows.append(
            {
                "period": hypothesis.period,
                "priority": acceptance["priority"],
                "acceptance_status": acceptance["acceptance_status"],
                "hypothesis_id": hypothesis.hypothesis_id,
                "hypothesis_status": hypothesis.hypothesis_status,
                "target_delta_eur": _money(hypothesis.target_delta_eur),
                "model_amount_eur": _money(hypothesis.model_amount_eur),
                "diff_to_target_eur": _money(hypothesis.diff_to_target_eur),
                "annual_constrained_balance_to_target": _money(annual_balance),
                "fit_signal": hypothesis.fit_signal,
                "components": hypothesis.components,
                "source_ref": hypothesis.source_ref,
                "evidence_status": hypothesis.evidence_status,
                "notes": hypothesis.notes,
                "xolo_question": hypothesis.xolo_question,
                "context_signal": _context_signal(hypothesis),
            }
        )
    return sorted(rows, key=lambda row: row["period"])


def write_p1_near_fit_context_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=P1_NEAR_FIT_CONTEXT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_p1_near_fit_context_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 130 P1 Near-Fit Context",
        "",
        "This report summarizes the strongest local arithmetic forks for P1 quarters.",
        "These forks are not closure evidence; they identify the next submitted-register or asset-schedule questions after the P0 material gaps.",
        "",
        "## Summary",
        "",
        f"- P1 near-fit periods covered: `{len(rows)}`.",
        "- Confirmed closed from this context alone: `0`.",
        "",
        "| Period | Status | Target | Model | Diff | Annual balance | Signal |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    _cell(row["acceptance_status"]),
                    _fmt(row["target_delta_eur"]),
                    _fmt(row["model_amount_eur"]),
                    _fmt(row["diff_to_target_eur"]),
                    _fmt(row["annual_constrained_balance_to_target"]),
                    _cell(row["context_signal"]),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Period Questions", ""])
    for row in rows:
        lines.extend(
            [
                f"### {row['period']}",
                "",
                f"- Hypothesis: `{row['hypothesis_id']}`",
                f"- Fit: `{row['fit_signal']}`, diff `{_fmt(row['diff_to_target_eur'])}`",
                f"- Components: {_cell(row['components'])}",
                f"- Evidence status: `{row['evidence_status']}`",
                f"- Notes: {_cell(row['notes'])}",
                f"- Next Xolo question: {_cell(row['xolo_question'])}",
                f"- Source: `{row['source_ref']}`",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _load_hypotheses(path: Path) -> list[P1Hypothesis]:
    rows: list[P1Hypothesis] = []
    required = {
        "period",
        "hypothesis_id",
        "hypothesis_status",
        "target_delta_eur",
        "model_amount_eur",
        "diff_to_target_eur",
        "fit_signal",
        "components",
        "source_ref",
        "evidence_status",
        "notes",
        "xolo_question",
    }
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"P1 near-fit hypotheses missing required columns: {', '.join(missing)}")
        for row_number, row in enumerate(reader, start=2):
            if row.get(None):
                raise ValueError(
                    f"Malformed P1 near-fit hypotheses CSV row {row_number}: extra fields {row[None]}"
                )
            rows.append(
                P1Hypothesis(
                    period=row["period"],
                    hypothesis_id=row["hypothesis_id"],
                    hypothesis_status=row["hypothesis_status"],
                    target_delta_eur=parse_amount(row["target_delta_eur"]),
                    model_amount_eur=parse_amount(row["model_amount_eur"]),
                    diff_to_target_eur=parse_amount(row["diff_to_target_eur"]),
                    fit_signal=row["fit_signal"],
                    components=row["components"],
                    source_ref=row["source_ref"],
                    evidence_status=row["evidence_status"],
                    notes=row["notes"],
                    xolo_question=row["xolo_question"],
                )
            )
    return rows


def _context_signal(hypothesis: P1Hypothesis) -> str:
    if abs(hypothesis.diff_to_target_eur) <= Decimal("0.02"):
        return "exact_arithmetic_fit_requires_xolo_confirmation"
    if abs(hypothesis.diff_to_target_eur) <= Decimal("1.00"):
        return "near_fit_requires_xolo_confirmation"
    return "weak_fit_kept_for_context"


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return format_es(parse_amount(value)) if value else ""


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
