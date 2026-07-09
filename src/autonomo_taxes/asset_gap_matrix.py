from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .money import cents, format_es, parse_amount


ASSET_GAP_MATRIX_FIELDS = [
    "year",
    "quarter",
    "period",
    "target_casilla_02_delta",
    "raw_non_asset_gross_delta",
    "required_amortization_or_catchup_delta",
    "candidate_amortization_delta",
    "annual_constrained_amortization_delta",
    "required_minus_candidate_amortization",
    "required_minus_annual_constrained_amortization",
    "candidate_model_minus_target_delta",
    "annual_constrained_model_minus_target_delta",
    "active_asset_count",
    "candidate_included_asset_count",
    "active_asset_base_total",
    "candidate_included_asset_base_total",
    "required_as_pct_of_active_asset_base",
    "required_as_pct_of_candidate_included_asset_base",
    "active_assets",
    "candidate_included_assets",
    "candidate_excluded_assets",
    "amortization_gap_signal",
    "next_action",
]

ASSET_GAP_SIGNAL_ORDER = (
    "ordinary_amortization_too_small",
    "excluded_asset_or_register_adjustment_required",
    "asset_amortization_above_required_row_exclusions_needed",
    "raw_non_asset_above_target",
    "annual_constrained_amortization_near_required",
    "candidate_amortization_near_required",
    "asset_schedule_confirmation_required",
)

ASSET_GAP_SIGNAL_PRIORITY = {signal: index for index, signal in enumerate(ASSET_GAP_SIGNAL_ORDER)}


def build_asset_gap_matrix(
    candidate_quarter_reconciliation_csv: Path,
    annual_constrained_assets_csv: Path,
    asset_schedule_intake_csv: Path,
) -> list[dict[str, str]]:
    candidate_rows = _load_rows(candidate_quarter_reconciliation_csv)
    annual_by_period = {row["period"]: row for row in _load_rows(annual_constrained_assets_csv)}
    intake_by_period: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in _load_rows(asset_schedule_intake_csv):
        intake_by_period[row["period"]].append(row)

    rows: list[dict[str, str]] = []
    for candidate in candidate_rows:
        period = candidate["period"]
        annual = annual_by_period.get(period, {})
        intake_rows = intake_by_period.get(period, [])
        target = parse_amount(candidate["target_casilla_02_delta"])
        raw_non_asset = parse_amount(candidate["raw_non_asset_gross_delta"])
        required = cents(target - raw_non_asset)
        candidate_amortization = parse_amount(candidate["candidate_amortization_delta"])
        annual_constrained = parse_amount(annual.get("annual_constrained_amortization_delta", "0.00"))
        active_base = _base_total(intake_rows)
        included_rows = [row for row in intake_rows if row.get("candidate_included") == "yes"]
        excluded_rows = [row for row in intake_rows if row.get("candidate_included") != "yes"]
        included_base = _base_total(included_rows)
        signal = _gap_signal(required, candidate_amortization, annual_constrained, included_rows)
        rows.append(
            {
                "year": candidate["year"],
                "quarter": candidate["quarter"],
                "period": period,
                "target_casilla_02_delta": _money(target),
                "raw_non_asset_gross_delta": _money(raw_non_asset),
                "required_amortization_or_catchup_delta": _money(required),
                "candidate_amortization_delta": _money(candidate_amortization),
                "annual_constrained_amortization_delta": _money(annual_constrained),
                "required_minus_candidate_amortization": _money(required - candidate_amortization),
                "required_minus_annual_constrained_amortization": _money(required - annual_constrained),
                "candidate_model_minus_target_delta": candidate["candidate_model_minus_target_delta"],
                "annual_constrained_model_minus_target_delta": annual.get(
                    "annual_constrained_model_minus_target_delta", ""
                ),
                "active_asset_count": str(len(intake_rows)),
                "candidate_included_asset_count": str(len(included_rows)),
                "active_asset_base_total": _money(active_base),
                "candidate_included_asset_base_total": _money(included_base),
                "required_as_pct_of_active_asset_base": _pct(required, active_base),
                "required_as_pct_of_candidate_included_asset_base": _pct(required, included_base),
                "active_assets": _asset_labels(intake_rows),
                "candidate_included_assets": _asset_labels(included_rows),
                "candidate_excluded_assets": _asset_labels(excluded_rows),
                "amortization_gap_signal": signal,
                "next_action": _next_action(signal, required, candidate_amortization, annual_constrained),
            }
        )
    return rows


def write_asset_gap_matrix_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ASSET_GAP_MATRIX_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_asset_gap_matrix_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_signal = defaultdict(int)
    for row in rows:
        by_signal[row["amortization_gap_signal"]] += 1
    lines = [
        "# Asset Gap Matrix",
        "",
        "This report asks one narrow question per quarter: before row exclusions or netting, how much amortization or catch-up would be needed to make raw non-asset expenses equal the submitted Modelo 130 expense delta?",
        "It does not confirm Xolo's asset schedule. It separates asset-schedule-sized gaps from gaps that require submitted-register row treatment.",
        "",
        "## Summary",
        "",
        f"- Quarter rows: `{len(rows)}`.",
        "- Signals: "
        + "; ".join(f"`{signal}`={count}" for signal, count in sorted(by_signal.items()))
        + ".",
        "",
        "## Quarter Matrix",
        "",
        "| Period | Target | Raw non-asset | Required amort./catch-up | Candidate amort. | Annual-constrained amort. | Required - annual | Active assets | Signal | Next action |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["raw_non_asset_gross_delta"]),
                    _fmt(row["required_amortization_or_catchup_delta"]),
                    _fmt(row["candidate_amortization_delta"]),
                    _fmt(row["annual_constrained_amortization_delta"]),
                    _fmt(row["required_minus_annual_constrained_amortization"]),
                    row["active_asset_count"],
                    row["amortization_gap_signal"],
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Asset Basis Lens",
            "",
            "| Period | Required / active base | Required / included base | Active assets | Included assets | Candidate-excluded assets |",
            "|---|---:|---:|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["required_as_pct_of_active_asset_base"],
                    row["required_as_pct_of_candidate_included_asset_base"],
                    _cell(row["active_assets"] or "none"),
                    _cell(row["candidate_included_assets"] or "none"),
                    _cell(row["candidate_excluded_assets"] or "none"),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Findings", ""])
    lines.extend(_findings(rows))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _findings(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["- No rows were available."]
    p0_like = [row for row in rows if row["amortization_gap_signal"] == "ordinary_amortization_too_small"]
    raw_above = [row for row in rows if row["amortization_gap_signal"] == "raw_non_asset_above_target"]
    amortization_above = [
        row
        for row in rows
        if row["amortization_gap_signal"] == "asset_amortization_above_required_row_exclusions_needed"
    ]
    near = [
        row
        for row in rows
        if row["amortization_gap_signal"]
        in {"candidate_amortization_near_required", "annual_constrained_amortization_near_required"}
    ]
    findings = [
        f"- `{len(raw_above)}` quarters have raw non-asset expenses above the submitted target before amortization, so they require row exclusions, netting, VAT/base changes, or deferrals before an asset schedule can close them.",
        f"- `{len(p0_like)}` quarters require more positive amortization/catch-up than the current annual-constrained asset lens can plausibly explain.",
        f"- `{len(amortization_above)}` quarters have positive required amortization/catch-up, but the current asset lens is higher than that required amount by more than `20,00 EUR`; those need row exclusions alongside the asset schedule.",
        f"- `{len(near)}` quarters have candidate or annual-constrained amortization within `2,00 EUR` of the required pre-exclusion amount.",
    ]
    if p0_like:
        findings.append(
            "- Positive catch-up pressure is concentrated in: "
            + ", ".join(f"`{row['period']}` ({_fmt(row['required_minus_annual_constrained_amortization'])})" for row in p0_like)
            + "."
        )
    return findings


def _gap_signal(
    required: Decimal,
    candidate_amortization: Decimal,
    annual_constrained: Decimal,
    included_assets: list[dict[str, str]],
) -> str:
    if required < Decimal("0.00"):
        return "raw_non_asset_above_target"
    if abs(required - annual_constrained) <= Decimal("2.00"):
        return "annual_constrained_amortization_near_required"
    if abs(required - candidate_amortization) <= Decimal("2.00"):
        return "candidate_amortization_near_required"
    if not included_assets and required > Decimal("0.00"):
        return "excluded_asset_or_register_adjustment_required"
    if required - annual_constrained > Decimal("20.00"):
        return "ordinary_amortization_too_small"
    if annual_constrained - required > Decimal("20.00"):
        return "asset_amortization_above_required_row_exclusions_needed"
    return "asset_schedule_confirmation_required"


def _next_action(
    signal: str,
    required: Decimal,
    candidate_amortization: Decimal,
    annual_constrained: Decimal,
) -> str:
    if signal == "raw_non_asset_above_target":
        return "Do not tune amortization first; identify submitted-register exclusions, netting, VAT/base treatment, or deferrals."
    if signal == "ordinary_amortization_too_small":
        return (
            "Ask Xolo whether this quarter has catch-up, direct asset deduction, or row reclassification; "
            f"required amortization/catch-up is {format_es(required)} versus annual-constrained {format_es(annual_constrained)}."
        )
    if signal == "excluded_asset_or_register_adjustment_required":
        return "The candidate schedule excludes active asset rows; confirm whether the submitted register direct-expensed, capitalized, or excluded them."
    if signal == "asset_amortization_above_required_row_exclusions_needed":
        return (
            "The asset schedule alone would push expenses above target; confirm submitted-register exclusions, VAT/base treatment, or netting alongside the asset schedule."
        )
    if signal.endswith("_near_required"):
        return (
            "Asset schedule can arithmetically explain this pre-exclusion gap; confirm per-asset basis, rate, start date, quarter amount, and YTD amount."
        )
    return (
        "Confirm the submitted asset schedule and row register; candidate amortization "
        f"{format_es(candidate_amortization)} and annual-constrained {format_es(annual_constrained)} are not independently verified."
    )


def _base_total(rows: list[dict[str, str]]) -> Decimal:
    return sum((parse_amount(row.get("base_basis_eur", "0.00")) for row in rows), Decimal("0.00"))


def _asset_labels(rows: list[dict[str, str]]) -> str:
    labels = []
    for row in rows:
        labels.append(
            f"{row['asset_date']} {row['number']} {row['recipient']} base {format_es(parse_amount(row['base_basis_eur']))}"
        )
    return "; ".join(labels)


def _pct(numerator: Decimal, denominator: Decimal) -> str:
    if denominator == 0:
        return ""
    return f"{cents(numerator * Decimal('100') / denominator):.2f}%"


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return format_es(parse_amount(value))


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
