from __future__ import annotations

import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path

from .money import cents, format_es, parse_amount


QUARTER_BALANCE_BRIDGE_FIELDS = [
    "period",
    "target_casilla_02_delta",
    "raw_non_asset_delta",
    "candidate_amortization_delta",
    "nearest_excluded_or_netted_eur",
    "candidate_model_after_local_adjustments",
    "candidate_balance_to_target",
    "annual_constraint_source",
    "annual_constrained_amortization_delta",
    "annual_constrained_model_after_local_adjustments",
    "annual_constrained_balance_to_target",
    "amortization_shift_eur",
    "balance_signal",
    "closure_status",
    "required_xolo_evidence",
    "equation",
]


def build_quarter_balance_bridge(
    candidate_quarter_reconciliation_csv: Path,
    quarter_closure_csv: Path,
    annual_constrained_assets_csv: Path | None = None,
) -> list[dict[str, str]]:
    candidate_rows = _load_rows(candidate_quarter_reconciliation_csv)
    closure_by_period = {row["period"]: row for row in _load_rows(quarter_closure_csv)}
    annual_by_period = (
        {row["period"]: row for row in _load_rows(annual_constrained_assets_csv)}
        if annual_constrained_assets_csv
        else {}
    )

    rows: list[dict[str, str]] = []
    for candidate in candidate_rows:
        period = candidate["period"]
        closure = closure_by_period.get(period, {})
        annual = annual_by_period.get(period, {})
        target = _amount(candidate, "target_casilla_02_delta")
        raw = _amount(candidate, "raw_non_asset_gross_delta")
        candidate_amortization = _amount(candidate, "candidate_amortization_delta")
        exclusions = _amount(candidate, "nearest_excluded_subset_eur")
        candidate_model_after_adjustments = cents(raw + candidate_amortization - exclusions)
        candidate_balance = cents(target - candidate_model_after_adjustments)

        annual_amortization = _annual_amortization(annual, candidate_amortization)
        annual_model_after_adjustments = cents(raw + annual_amortization - exclusions)
        annual_balance = cents(target - annual_model_after_adjustments)
        amortization_shift = cents(annual_amortization - candidate_amortization)

        balance_signal = _balance_signal(annual_balance, closure.get("closure_status", ""))
        rows.append(
            {
                "period": period,
                "target_casilla_02_delta": _money(target),
                "raw_non_asset_delta": _money(raw),
                "candidate_amortization_delta": _money(candidate_amortization),
                "nearest_excluded_or_netted_eur": _money(exclusions),
                "candidate_model_after_local_adjustments": _money(candidate_model_after_adjustments),
                "candidate_balance_to_target": _money(candidate_balance),
                "annual_constraint_source": annual.get("annual_constraint_source", "not_available"),
                "annual_constrained_amortization_delta": _money(annual_amortization),
                "annual_constrained_model_after_local_adjustments": _money(annual_model_after_adjustments),
                "annual_constrained_balance_to_target": _money(annual_balance),
                "amortization_shift_eur": _money(amortization_shift),
                "balance_signal": balance_signal,
                "closure_status": closure.get("closure_status", ""),
                "required_xolo_evidence": closure.get("required_xolo_evidence", ""),
                "equation": _equation(raw, annual_amortization, exclusions, annual_balance, target),
            }
        )
    return rows


def write_quarter_balance_bridge_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUARTER_BALANCE_BRIDGE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_quarter_balance_bridge_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    signal_counts = Counter(row["balance_signal"] for row in rows)
    material_rows = [
        row
        for row in rows
        if abs(parse_amount(row["annual_constrained_balance_to_target"])) > Decimal("20.00")
    ]
    max_shift = max((abs(parse_amount(row["amortization_shift_eur"])) for row in rows), default=Decimal("0.00"))
    lines = [
        "# Modelo 130 Quarter Balance Bridge",
        "",
        "This report rewrites each quarter as one audit equation:",
        "",
        "`raw non-asset expenses + amortization - excluded/netted rows + balancing amount = submitted casilla 02 delta`",
        "",
        "The balancing amount is not a confirmed filing row. It is the remaining amount that still needs Xolo's submitted Modelo 130 register or asset schedule.",
        "",
        "## Summary",
        "",
        f"- Quarters bridged: `{len(rows)}`.",
        f"- Quarters still over `20,00 EUR` after local row/amortization adjustments: `{len(material_rows)}`.",
        f"- Largest annual amortization constraint shift versus candidate schedule: `{format_es(max_shift)}`.",
        "",
        "| Balance signal | Count |",
        "|---|---:|",
    ]
    for signal, count in sorted(signal_counts.items()):
        lines.append(f"| {signal} | {count} |")
    lines.extend(
        [
            "",
            "## Quarter Matrix",
            "",
            "| Period | Target | Raw non-asset | Amort. | Excluded/netted | Balance | Annual balance | Signal | Status |",
            "|---|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["raw_non_asset_delta"]),
                    _fmt(row["annual_constrained_amortization_delta"]),
                    _fmt(row["nearest_excluded_or_netted_eur"]),
                    _fmt(row["candidate_balance_to_target"]),
                    _fmt(row["annual_constrained_balance_to_target"]),
                    row["balance_signal"],
                    row["closure_status"],
                ]
            )
            + " |"
        )
    lines.extend(["", "## Quarter Equations", ""])
    for row in rows:
        lines.extend(
            [
                f"### {row['period']}",
                "",
                f"- Equation: `{row['equation']}`",
                f"- Annual constraint source: `{row['annual_constraint_source']}`",
                f"- Required evidence: {_cell(row['required_xolo_evidence']) or '`not recorded`'}",
                "",
            ]
        )
    lines.extend(["## Findings", ""])
    lines.extend(_findings(rows, material_rows, max_shift))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _findings(
    rows: list[dict[str, str]],
    material_rows: list[dict[str, str]],
    max_shift: Decimal,
) -> list[str]:
    if not rows:
        return ["- No quarter rows were available."]
    target_above = [
        row for row in rows if row["balance_signal"] == "submitted_target_above_local_model"
    ]
    target_below = [
        row for row in rows if row["balance_signal"] == "submitted_target_below_local_model"
    ]
    near_target = [
        row for row in rows if row["balance_signal"] == "near_target_pending_confirmation"
    ]
    findings = [
        "- Annual-constrained amortization changes the candidate quarterly equation by at most "
        f"`{format_es(max_shift)}`, so large residuals are not caused by annual amortization totals alone.",
        "- Submitted target remains above the local model for: "
        + _period_list(target_above)
        + ".",
        "- Submitted target remains below the local model for: "
        + _period_list(target_below)
        + ".",
        "- Near-target quarters still need register confirmation because excluded/netted row sets can be ambiguous: "
        + _period_list(near_target)
        + ".",
    ]
    if material_rows:
        findings.append(
            "- Material post-bridge balances remain in: "
            + _period_list(material_rows)
            + "; these are the first quarters to ask Xolo about in the submitted register."
        )
    return findings


def _balance_signal(balance_to_target: Decimal, closure_status: str) -> str:
    if abs(balance_to_target) <= Decimal("1.00"):
        return "near_target_pending_confirmation"
    if balance_to_target > Decimal("20.00"):
        return "submitted_target_above_local_model"
    if balance_to_target < Decimal("-20.00"):
        return "submitted_target_below_local_model"
    if "blocked" in closure_status:
        return "minor_amount_but_material_by_context"
    return "minor_residual_pending_confirmation"


def _annual_amortization(annual: dict[str, str], fallback: Decimal) -> Decimal:
    value = annual.get("annual_constrained_amortization_delta", "")
    if not value:
        return fallback
    return parse_amount(value)


def _equation(raw: Decimal, amortization: Decimal, exclusions: Decimal, balance: Decimal, target: Decimal) -> str:
    return (
        f"{_money(raw)} + {_money(amortization)} - {_money(exclusions)} "
        f"+ {_money(balance)} = {_money(target)}"
    )


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _amount(row: dict[str, str], key: str) -> Decimal:
    return parse_amount(row[key])


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return format_es(parse_amount(value)) if value else ""


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _period_list(rows: list[dict[str, str]]) -> str:
    if not rows:
        return "`none`"
    return ", ".join(f"`{row['period']}`" for row in rows)
