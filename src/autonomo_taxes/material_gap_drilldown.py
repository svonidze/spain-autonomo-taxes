from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .money import cents, format_es, parse_amount


MATERIAL_GAP_DRILLDOWN_FIELDS = [
    "period",
    "hypothesis_id",
    "hypothesis_status",
    "component",
    "amount_eur",
    "effect_closes_gap_eur",
    "counts_in_best_bridge",
    "hypothesis_total_eur",
    "candidate_balance_to_target",
    "residual_to_candidate_balance_eur",
    "annual_constrained_balance_to_target",
    "residual_to_annual_balance_eur",
    "fit_signal",
    "source_ref",
    "evidence_status",
    "notes",
    "xolo_question",
]


@dataclass(frozen=True)
class HypothesisRow:
    period: str
    hypothesis_id: str
    hypothesis_status: str
    component: str
    amount_eur: Decimal
    effect_closes_gap_eur: Decimal
    counts_in_best_bridge: bool
    source_ref: str
    evidence_status: str
    notes: str
    xolo_question: str


def build_material_gap_drilldown(
    quarter_balance_bridge_csv: Path,
    material_gap_hypotheses_csv: Path,
) -> list[dict[str, str]]:
    balance_by_period = {row["period"]: row for row in _load_rows(quarter_balance_bridge_csv)}
    hypotheses = _load_hypotheses(material_gap_hypotheses_csv)
    total_by_key = _bridge_totals_by_key(hypotheses)
    rows: list[dict[str, str]] = []
    for hypothesis in hypotheses:
        balance = balance_by_period.get(hypothesis.period)
        if balance is None:
            raise ValueError(f"Missing balance bridge row for {hypothesis.period}")
        key = (hypothesis.period, hypothesis.hypothesis_id)
        total = total_by_key[key]
        candidate_balance = parse_amount(balance["candidate_balance_to_target"])
        annual_balance = parse_amount(balance["annual_constrained_balance_to_target"])
        residual_to_candidate = cents(candidate_balance - total)
        residual_to_annual = cents(annual_balance - total)
        rows.append(
            {
                "period": hypothesis.period,
                "hypothesis_id": hypothesis.hypothesis_id,
                "hypothesis_status": hypothesis.hypothesis_status,
                "component": hypothesis.component,
                "amount_eur": _money(hypothesis.amount_eur),
                "effect_closes_gap_eur": _money(hypothesis.effect_closes_gap_eur),
                "counts_in_best_bridge": "yes" if hypothesis.counts_in_best_bridge else "no",
                "hypothesis_total_eur": _money(total),
                "candidate_balance_to_target": _money(candidate_balance),
                "residual_to_candidate_balance_eur": _money(residual_to_candidate),
                "annual_constrained_balance_to_target": _money(annual_balance),
                "residual_to_annual_balance_eur": _money(residual_to_annual),
                "fit_signal": _fit_signal(
                    hypothesis.hypothesis_status,
                    residual_to_candidate,
                    residual_to_annual,
                    total,
                ),
                "source_ref": hypothesis.source_ref,
                "evidence_status": hypothesis.evidence_status,
                "notes": hypothesis.notes,
                "xolo_question": hypothesis.xolo_question,
            }
        )
    return rows


def write_material_gap_drilldown_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATERIAL_GAP_DRILLDOWN_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_material_gap_drilldown_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    grouped = _group_by_period(rows)
    status_counts = Counter(row["hypothesis_status"] for row in rows)
    lines = [
        "# Modelo 130 Material Gap Drilldown",
        "",
        "This report focuses only on material balances that remain after the quarter balance bridge.",
        "It separates locally ruled-out ideas, partial candidates, strong but unconfirmed bridges, and side components that still need Xolo's submitted register.",
        "",
        "## Summary",
        "",
        f"- Periods covered: `{len(grouped)}`.",
        f"- Hypothesis components: `{len(rows)}`.",
        "",
        "| Hypothesis status | Components |",
        "|---|---:|",
    ]
    for status, count in sorted(status_counts.items()):
        lines.append(f"| {status} | {count} |")

    lines.extend(["", "## Period Findings", ""])
    for period, period_rows in grouped.items():
        lines.extend(_period_markdown(period, period_rows))

    lines.extend(["## Xolo Questions", ""])
    for row in rows:
        if not row["xolo_question"]:
            continue
        lines.append(
            "- "
            + f"`{row['period']}` `{row['hypothesis_id']}`: "
            + _cell(row["xolo_question"])
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _period_markdown(period: str, rows: list[dict[str, str]]) -> list[str]:
    by_hypothesis = _group_by_hypothesis(rows)
    first = rows[0]
    lines = [
        f"### {period}",
        "",
        f"- Candidate balance to target: `{_fmt(first['candidate_balance_to_target'])}`",
        f"- Annual-constrained balance to target: `{_fmt(first['annual_constrained_balance_to_target'])}`",
        "",
        "| Hypothesis | Status | Bridge total | Residual to candidate | Residual to annual | Fit |",
        "|---|---|---:|---:|---:|---|",
    ]
    for hypothesis_id, hypothesis_rows in by_hypothesis.items():
        row = hypothesis_rows[0]
        lines.append(
            "| "
            + " | ".join(
                [
                    hypothesis_id,
                    row["hypothesis_status"],
                    _fmt(row["hypothesis_total_eur"]),
                    _fmt(row["residual_to_candidate_balance_eur"]),
                    _fmt(row["residual_to_annual_balance_eur"]),
                    row["fit_signal"],
                ]
            )
            + " |"
        )
    lines.extend(["", "| Component | Effect | Counts | Evidence | Notes |", "|---|---:|---|---|---|"])
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["component"]),
                    _fmt(row["effect_closes_gap_eur"]),
                    row["counts_in_best_bridge"],
                    _cell(row["evidence_status"]),
                    _cell(row["notes"]),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def _load_hypotheses(path: Path) -> list[HypothesisRow]:
    rows: list[HypothesisRow] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {
            "period",
            "hypothesis_id",
            "hypothesis_status",
            "component",
            "amount_eur",
            "effect_closes_gap_eur",
            "counts_in_best_bridge",
            "source_ref",
            "evidence_status",
            "notes",
            "xolo_question",
        }
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"Material gap hypotheses missing required columns: {', '.join(missing)}")
        for row_number, row in enumerate(reader, start=2):
            if row.get(None):
                raise ValueError(
                    "Malformed material gap hypotheses CSV row "
                    f"{row_number}: extra fields {row[None]}"
                )
            rows.append(
                HypothesisRow(
                    period=row["period"],
                    hypothesis_id=row["hypothesis_id"],
                    hypothesis_status=row["hypothesis_status"],
                    component=row["component"],
                    amount_eur=parse_amount(row["amount_eur"]),
                    effect_closes_gap_eur=parse_amount(row["effect_closes_gap_eur"]),
                    counts_in_best_bridge=_truthy(row["counts_in_best_bridge"]),
                    source_ref=row["source_ref"],
                    evidence_status=row["evidence_status"],
                    notes=row["notes"],
                    xolo_question=row["xolo_question"],
                )
            )
    return rows


def _bridge_totals_by_key(rows: list[HypothesisRow]) -> dict[tuple[str, str], Decimal]:
    totals: dict[tuple[str, str], Decimal] = {}
    for row in rows:
        key = (row.period, row.hypothesis_id)
        totals.setdefault(key, Decimal("0.00"))
        if row.counts_in_best_bridge:
            totals[key] += row.effect_closes_gap_eur
    return {key: cents(total) for key, total in totals.items()}


def _fit_signal(
    status: str,
    residual_to_candidate: Decimal,
    residual_to_annual: Decimal,
    total: Decimal,
) -> str:
    if status.startswith("ruled_out"):
        return "ruled_out"
    if status.startswith("partial"):
        return "partial_only"
    if total == Decimal("0.00"):
        return "side_component_not_gap_closure"
    if abs(residual_to_candidate) <= Decimal("0.50") or abs(residual_to_annual) <= Decimal("0.50"):
        return "near_exact_pending_confirmation"
    if abs(residual_to_candidate) <= Decimal("2.00") or abs(residual_to_annual) <= Decimal("2.00"):
        return "close_pending_asset_schedule"
    return "unresolved_required_evidence"


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _group_by_period(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["period"], []).append(row)
    return grouped


def _group_by_hypothesis(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["hypothesis_id"], []).append(row)
    return grouped


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "si", "sí"}


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return format_es(parse_amount(value)) if value else ""


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
