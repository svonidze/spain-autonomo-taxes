from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .money import format_es, parse_amount


QUARTER_CLOSURE_FIELDS = [
    "period",
    "closure_status",
    "target_casilla_02_delta",
    "pre_plug_model_minus_target_eur",
    "balancing_adjustment_eur",
    "balancing_adjustment_pct_of_target",
    "balancing_adjustment_materiality",
    "candidate_amortization_delta",
    "excluded_asset_direct_eur",
    "excluded_nearest_subset_eur",
    "has_material_plug",
    "has_asset_decision",
    "has_nearest_exclusion",
    "has_minor_residual",
    "asset_rows",
    "nearest_exclusion_rows",
    "required_xolo_evidence",
    "next_question",
]


def build_quarter_closure_checklist(
    hypothesis_summary_csv: Path,
    row_audit_csv: Path,
) -> list[dict[str, str]]:
    summary_rows = _load_rows(hypothesis_summary_csv)
    audit_by_period = _group_by_period(_load_rows(row_audit_csv))

    output: list[dict[str, str]] = []
    for summary in summary_rows:
        period = summary["period"]
        period_rows = audit_by_period.get(period, [])
        asset_rows = [row for row in period_rows if row["classification"] == "asset_amortization_candidate"]
        nearest_rows = [row for row in period_rows if row["classification"] == "nearest_exclusion_candidate"]
        materiality = summary["balancing_adjustment_materiality"]
        balance = parse_amount(summary["balancing_adjustment_eur"])
        has_material = materiality == "material"
        has_asset = bool(asset_rows) or parse_amount(summary["candidate_amortization_delta"]) != Decimal("0.00")
        has_nearest = bool(nearest_rows)
        has_minor = materiality == "minor" and balance != Decimal("0.00")
        status = _closure_status(has_material, has_asset, has_nearest, has_minor)

        output.append(
            {
                "period": period,
                "closure_status": status,
                "target_casilla_02_delta": summary["target_casilla_02_delta"],
                "pre_plug_model_minus_target_eur": summary["pre_plug_model_minus_target_eur"],
                "balancing_adjustment_eur": summary["balancing_adjustment_eur"],
                "balancing_adjustment_pct_of_target": summary["balancing_adjustment_pct_of_target"],
                "balancing_adjustment_materiality": materiality,
                "candidate_amortization_delta": summary["candidate_amortization_delta"],
                "excluded_asset_direct_eur": summary["excluded_asset_direct_eur"],
                "excluded_nearest_subset_eur": summary["excluded_nearest_subset_eur"],
                "has_material_plug": _yes_no(has_material),
                "has_asset_decision": _yes_no(has_asset),
                "has_nearest_exclusion": _yes_no(has_nearest),
                "has_minor_residual": _yes_no(has_minor),
                "asset_rows": _format_rows(asset_rows),
                "nearest_exclusion_rows": _format_rows(nearest_rows),
                "required_xolo_evidence": _required_evidence(has_material, has_asset, has_nearest, has_minor),
                "next_question": _next_question(summary, has_material, has_asset, has_nearest, has_minor),
            }
        )
    return output


def write_quarter_closure_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUARTER_CLOSURE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_quarter_closure_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 130 Quarter Closure Checklist",
        "",
        "This checklist tracks whether each submitted quarter can be closed from local evidence.",
        "A closed quarter requires Xolo's source books and, where applicable, the confirmed asset amortization schedule.",
        "",
        "| Period | Closure status | Target delta | Pre-plug residual | Balance | Plug % | Amortization | Asset decision | Nearest exclusions |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["closure_status"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["pre_plug_model_minus_target_eur"]),
                    _fmt(row["balancing_adjustment_eur"]),
                    row["balancing_adjustment_pct_of_target"] + "%",
                    _fmt(row["candidate_amortization_delta"]),
                    row["has_asset_decision"],
                    row["has_nearest_exclusion"],
                ]
            )
            + " |"
        )

    lines.extend(["", "## Quarter Details", ""])
    for row in rows:
        lines.extend(
            [
                f"### {row['period']}",
                "",
                f"- Status: `{row['closure_status']}`",
                f"- Required Xolo evidence: {row['required_xolo_evidence']}",
                f"- Next question: {row['next_question']}",
                f"- Asset rows: {row['asset_rows'] or 'none'}",
                f"- Nearest exclusion rows: {row['nearest_exclusion_rows'] or 'none'}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _group_by_period(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["period"], []).append(row)
    return grouped


def _closure_status(has_material: bool, has_asset: bool, has_nearest: bool, has_minor: bool) -> str:
    if has_material:
        return "blocked_material_unexplained_adjustment"
    if has_asset:
        return "pending_asset_schedule_confirmation"
    if has_nearest:
        return "pending_row_exclusion_confirmation"
    if has_minor:
        return "pending_minor_residual_confirmation"
    return "pending_submitted_register_confirmation"


def _required_evidence(has_material: bool, has_asset: bool, has_nearest: bool, has_minor: bool) -> str:
    parts: list[str] = ["source-book export with deductible EUR amount per row"]
    if has_asset:
        parts.append("asset amortization schedule by quarter")
    if has_nearest:
        parts.append("confirmation of excluded/netted candidate rows")
    if has_material:
        parts.append("source of material balancing adjustment")
    elif has_minor:
        parts.append("rounding/basis explanation for minor residual")
    return "; ".join(parts)


def _next_question(
    summary: dict[str, str],
    has_material: bool,
    has_asset: bool,
    has_nearest: bool,
    has_minor: bool,
) -> str:
    period = summary["period"]
    balance = format_es(parse_amount(summary["balancing_adjustment_eur"]))
    pct = summary["balancing_adjustment_pct_of_target"]
    if has_material:
        return (
            f"For {period}, please identify the source-book row(s) or adjustment that explain "
            f"the material balancing amount {balance} EUR ({pct}% of the quarter target delta)."
        )
    if has_asset:
        return f"For {period}, please confirm the asset amortization amount and asset schedule used in Modelo 130."
    if has_nearest:
        return f"For {period}, please confirm whether the nearest-subset rows were excluded, netted, or used on another basis."
    if has_minor:
        return f"For {period}, please confirm whether the {balance} EUR residual is rounding or a tax-basis difference."
    return f"For {period}, please confirm the source books match the listed raw expense rows."


def _format_rows(rows: list[dict[str, str]]) -> str:
    return "; ".join(
        " ".join(
            part
            for part in (
                row.get("date", ""),
                row.get("number", ""),
                row.get("recipient", ""),
                row.get("gross_eur", ""),
            )
            if part
        )
        for row in rows
    )


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _fmt(value: str) -> str:
    return "" if value == "" else format_es(parse_amount(value))
