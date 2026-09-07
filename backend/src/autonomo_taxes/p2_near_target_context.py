from __future__ import annotations

import csv
from pathlib import Path

from .money import format_es, parse_amount
from .source_book_wording import source_book_wording


P2_NEAR_TARGET_CONTEXT_FIELDS = [
    "period",
    "priority",
    "acceptance_status",
    "row_ref",
    "source_status",
    "target_casilla_02_delta",
    "annual_constrained_balance_to_target",
    "annual_constrained_amortization_delta",
    "nearest_excluded_or_netted_eur",
    "finding",
    "impact",
    "context_signal",
]


def build_p2_near_target_context(
    quarter_acceptance_csv: Path,
    quarter_balance_bridge_csv: Path,
    source_findings_csv: Path,
) -> list[dict[str, str]]:
    p2_acceptance = {
        row["period"]: row
        for row in _load_rows(quarter_acceptance_csv)
        if row.get("priority") == "P2"
    }
    bridge_by_period = {row["period"]: row for row in _load_rows(quarter_balance_bridge_csv)}
    source_rows = [
        row for row in _load_rows(source_findings_csv) if _is_p2_context_row(row, p2_acceptance)
    ]

    rows: list[dict[str, str]] = []
    for source in source_rows:
        period = source["period"]
        acceptance = p2_acceptance[period]
        bridge = bridge_by_period.get(period)
        if bridge is None:
            raise ValueError(f"Missing quarter balance bridge row for {period}")
        rows.append(
            {
                "period": period,
                "priority": acceptance["priority"],
                "acceptance_status": acceptance["acceptance_status"],
                "row_ref": source["row_ref"],
                "source_status": source["source_status"],
                "target_casilla_02_delta": bridge["target_casilla_02_delta"],
                "annual_constrained_balance_to_target": bridge["annual_constrained_balance_to_target"],
                "annual_constrained_amortization_delta": bridge["annual_constrained_amortization_delta"],
                "nearest_excluded_or_netted_eur": bridge["nearest_excluded_or_netted_eur"],
                "finding": source_book_wording(source["finding"]),
                "impact": source_book_wording(source["impact"]),
                "context_signal": _context_signal(source),
            }
        )
    return sorted(rows, key=lambda row: (row["period"], _signal_order(row["context_signal"]), row["row_ref"]))


def write_p2_near_target_context_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=P2_NEAR_TARGET_CONTEXT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_p2_near_target_context_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 130 P2 Near-Target Context",
        "",
        "This report summarizes near-target forks for P2 quarters.",
        "These are not closure evidence: the source books and asset schedule are still required even when residuals are small.",
        "",
        "## Summary",
        "",
        f"- P2 context rows: `{len(rows)}`.",
        f"- P2 periods covered: `{len({row['period'] for row in rows})}`.",
        "- Confirmed closed from this context alone: `0`.",
        "",
        "| Period | Context rows | Annual balance | Amortization | Excluded/netted |",
        "|---|---:|---:|---:|---:|",
    ]
    for period, period_rows in _group_by_period(rows).items():
        first = period_rows[0]
        lines.append(
            "| "
            + " | ".join(
                [
                    period,
                    str(len(period_rows)),
                    _fmt(first["annual_constrained_balance_to_target"]),
                    _fmt(first["annual_constrained_amortization_delta"]),
                    _fmt(first["nearest_excluded_or_netted_eur"]),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Period Forks", ""])
    for period, period_rows in _group_by_period(rows).items():
        lines.extend(
            [
                f"### {period}",
                "",
                "| Row | Signal | Finding | Impact |",
                "|---|---|---|---|",
            ]
        )
        for row in period_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(row["row_ref"]),
                        _cell(row["context_signal"]),
                        _cell(row["finding"]),
                        _cell(row["impact"]),
                    ]
                )
                + " |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _is_p2_context_row(row: dict[str, str], p2_acceptance: dict[str, dict[str, str]]) -> bool:
    if row.get("period") not in p2_acceptance:
        return False
    status = row.get("source_status", "")
    row_ref = row.get("row_ref", "")
    return (
        row_ref.startswith("scenario_")
        or status.startswith("unconfirmed")
        or status == "secondary_arithmetic_hypothesis"
    )


def _context_signal(row: dict[str, str]) -> str:
    status = row.get("source_status", "")
    row_ref = row.get("row_ref", "")
    if status == "unconfirmed_asset_amortization_row":
        return "asset_placeholder_requires_schedule"
    if "RETA" in row_ref or "reta" in row_ref.lower():
        return "reta_treatment_requires_xolo_confirmation"
    if status == "secondary_arithmetic_hypothesis":
        return "secondary_near_target_fork"
    if row_ref.startswith("scenario_q2_2026"):
        return "q2_2026_original_gap_fork"
    return "primary_near_target_fork_requires_source_books"


def _signal_order(signal: str) -> int:
    order = {
        "primary_near_target_fork_requires_source_books": 0,
        "asset_placeholder_requires_schedule": 1,
        "q2_2026_original_gap_fork": 2,
        "reta_treatment_requires_xolo_confirmation": 3,
        "secondary_near_target_fork": 4,
    }
    return order.get(signal, 9)


def _group_by_period(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["period"], []).append(row)
    return grouped


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _fmt(value: str) -> str:
    return format_es(parse_amount(value)) if value else ""


def _cell(value: str) -> str:
    return source_book_wording(value).replace("|", "\\|").replace("\n", " ")
