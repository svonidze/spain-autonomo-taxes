from __future__ import annotations

import csv
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from .money import format_es, parse_amount
from .source_book_wording import source_book_wording


QUARTER_ACCEPTANCE_FIELDS = [
    "period",
    "review_order",
    "acceptance_status",
    "priority",
    "target_casilla_02_delta",
    "annual_constrained_balance_to_target",
    "balance_signal",
    "closure_status",
    "material_gap_focus",
    "material_hypotheses",
    "required_evidence",
    "next_action",
    "packet_path",
]

_ACTIONABLE_MATERIAL_STATUSES = {
    "unresolved_required_evidence",
    "strong_candidate_pending_confirmation",
}


def build_quarter_acceptance_matrix(
    quarter_closure_csv: Path,
    quarter_balance_bridge_csv: Path,
    material_gap_drilldown_csv: Path | None = None,
    packets_dir: Path | None = None,
) -> list[dict[str, str]]:
    closure_rows = _load_rows(quarter_closure_csv)
    balance_by_period = {row["period"]: row for row in _load_rows(quarter_balance_bridge_csv)}
    material_by_period = _material_by_period(_load_rows(material_gap_drilldown_csv) if material_gap_drilldown_csv else [])

    rows: list[dict[str, str]] = []
    for index, closure in enumerate(closure_rows, start=1):
        period = closure["period"]
        balance = balance_by_period.get(period)
        if balance is None:
            raise ValueError(f"Missing quarter balance bridge row for {period}")

        material = material_by_period.get(period, [])
        annual_balance = parse_amount(balance["annual_constrained_balance_to_target"])
        acceptance_status, priority = _acceptance_gate(closure, balance, material, annual_balance)
        material_focus = _yes_no(any(_is_actionable_material(row) for row in material))

        rows.append(
            {
                "period": period,
                "review_order": f"{index:03d}",
                "acceptance_status": acceptance_status,
                "priority": priority,
                "target_casilla_02_delta": balance["target_casilla_02_delta"],
                "annual_constrained_balance_to_target": balance["annual_constrained_balance_to_target"],
                "balance_signal": balance["balance_signal"],
                "closure_status": closure["closure_status"],
                "material_gap_focus": material_focus,
                "material_hypotheses": _material_summary(material),
                "required_evidence": _required_evidence(closure, material),
                "next_action": _next_action(closure, material, acceptance_status),
                "packet_path": _packet_path(packets_dir, period),
            }
        )
    return rows


def write_quarter_acceptance_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUARTER_ACCEPTANCE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_quarter_acceptance_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    priority_counts = Counter(row["priority"] for row in rows)
    status_counts = Counter(row["acceptance_status"] for row in rows)
    p0_rows = [row for row in rows if row["priority"] == "P0"]

    lines = [
        "# Modelo 130 Quarter Acceptance Matrix",
        "",
        "This matrix is the evidence gate for the sequential Modelo 130 audit.",
        "No quarter is accepted from local arithmetic alone; acceptance requires Xolo's source books and any applicable asset schedule.",
        "",
        "## Summary",
        "",
        f"- Quarters reviewed: `{len(rows)}`.",
        "- Accepted/closed from current local evidence: `0`.",
        "- Highest-priority external confirmations: " + _period_list(p0_rows) + ".",
        "",
        "| Priority | Count |",
        "|---|---:|",
    ]
    for priority, count in sorted(priority_counts.items()):
        lines.append(f"| {priority} | {count} |")

    lines.extend(["", "| Acceptance status | Count |", "|---|---:|"])
    for status, count in sorted(status_counts.items()):
        lines.append(f"| {status} | {count} |")

    lines.extend(
        [
            "",
            "## Quarter Matrix",
            "",
            "| Order | Period | Priority | Acceptance status | Target 02 delta | Annual balance | Signal | Material focus |",
            "|---:|---|---|---|---:|---:|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["review_order"],
                    row["period"],
                    row["priority"],
                    row["acceptance_status"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["annual_constrained_balance_to_target"]),
                    _cell(row["balance_signal"]),
                    row["material_gap_focus"],
                ]
            )
            + " |"
        )

    lines.extend(["", "## Quarter Gates", ""])
    for row in rows:
        lines.extend(
            [
                f"### {row['period']}",
                "",
                f"- Priority: `{row['priority']}`",
                f"- Acceptance status: `{row['acceptance_status']}`",
                f"- Required evidence: {_cell(row['required_evidence'])}",
                f"- Next action: {_cell(row['next_action'])}",
                f"- Material hypotheses: {_cell(row['material_hypotheses']) or 'none'}",
                f"- Packet: {_cell(row['packet_path']) or 'not supplied'}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _acceptance_gate(
    closure: dict[str, str],
    balance: dict[str, str],
    material: list[dict[str, str]],
    annual_balance: Decimal,
) -> tuple[str, str]:
    closure_status = closure["closure_status"]
    if any(_is_actionable_material(row) for row in material):
        return "not_closed_material_gap_requires_xolo_register", "P0"
    if closure_status == "blocked_material_unexplained_adjustment":
        return "not_closed_material_status_near_fit_requires_register", "P1"
    if closure_status == "pending_row_exclusion_confirmation":
        return "not_closed_row_exclusion_requires_register", "P1"
    if abs(annual_balance) <= Decimal("1.00"):
        return "not_closed_near_target_requires_asset_schedule", "P2"
    if "asset" in closure_status:
        return "not_closed_minor_residual_requires_asset_schedule", "P2"
    if "minor" in closure_status or balance["balance_signal"] == "minor_residual_pending_confirmation":
        return "not_closed_minor_residual_requires_register", "P2"
    return "not_closed_requires_xolo_evidence", "P2"


def _required_evidence(closure: dict[str, str], material: list[dict[str, str]]) -> str:
    evidence = closure.get("required_xolo_evidence", "").strip()
    material_questions = _material_questions(material)
    if material_questions:
        return "; ".join(part for part in [evidence, "material-gap row basis from source books"] if part)
    return evidence or "source-book export with deductible EUR amount per row"


def _next_action(
    closure: dict[str, str],
    material: list[dict[str, str]],
    acceptance_status: str,
) -> str:
    questions = _material_questions(material)
    if acceptance_status == "not_closed_material_gap_requires_xolo_register" and questions:
        return "Resolve material-gap question(s): " + " | ".join(questions)
    if acceptance_status == "not_closed_material_status_near_fit_requires_register":
        return (
            "Confirm the source books and asset schedule before treating this near-fit material-status "
            "quarter as closed."
        )
    if acceptance_status == "not_closed_row_exclusion_requires_register":
        return "Confirm whether the candidate rows were excluded, netted, deferred, or used on another tax basis."
    if acceptance_status.startswith("not_closed_near_target") or acceptance_status.startswith("not_closed_minor"):
        return (
            "Confirm Xolo's source books and asset amortization schedule; the small residual is not "
            "enough to prove row-level treatment."
        )
    return closure.get("next_question", "") or "Confirm the source books used for Modelo 130."


def _material_by_period(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("hypothesis_status") == "ruled_out_local_hypothesis":
            continue
        grouped[row["period"]].append(row)
    return dict(grouped)


def _material_summary(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["hypothesis_id"]].append(row)
    summaries = []
    for hypothesis_id, group in sorted(grouped.items()):
        first = group[0]
        components = [
            f"{row['component']} {_fmt(row['effect_closes_gap_eur'])} counts={row['counts_in_best_bridge']}"
            for row in group
        ]
        summaries.append(
            f"{hypothesis_id} [{first['hypothesis_status']}; total {_fmt(first['hypothesis_total_eur'])}; "
            + "; ".join(components)
            + "]"
        )
    return " | ".join(summaries)


def _material_questions(rows: list[dict[str, str]]) -> list[str]:
    questions = []
    seen = set()
    for row in rows:
        if not _is_actionable_material(row):
            continue
        question = row.get("xolo_question", "").strip()
        if not question or question in seen:
            continue
        seen.add(question)
        questions.append(question)
    return questions


def _is_actionable_material(row: dict[str, str]) -> bool:
    return row.get("hypothesis_status", "") in _ACTIONABLE_MATERIAL_STATUSES


def _packet_path(packets_dir: Path | None, period: str) -> str:
    if packets_dir is None:
        return ""
    return str(packets_dir / f"{period}.md")


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _fmt(value: str) -> str:
    return format_es(parse_amount(value)) if value else ""


def _cell(value: str) -> str:
    return source_book_wording(value).replace("|", "\\|").replace("\n", " ")


def _period_list(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "`none`"
    return ", ".join(f"`{row['period']}`" for row in rows)


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"
