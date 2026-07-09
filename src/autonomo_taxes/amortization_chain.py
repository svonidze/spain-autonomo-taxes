from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .annual import load_modelo100_summary
from .money import format_es, parse_amount


AMORTIZATION_CHAIN_FIELDS = [
    "year",
    "quarter",
    "period",
    "annual_constraint_source",
    "modelo100_amortization_0208",
    "candidate_amortization_delta",
    "annual_constrained_amortization_delta",
    "candidate_amortization_ytd",
    "annual_constrained_amortization_ytd",
    "amortization_delta_shift",
    "annual_constrained_model_minus_target_delta",
    "closure_status",
    "asset_rows",
    "quarter_asset_candidates",
    "asset_candidates_seen_ytd",
    "amortization_chain_signal",
    "required_xolo_evidence",
    "next_amortization_question",
]


YEAR_SUMMARY_FIELDS = [
    "year",
    "modelo100_status",
    "modelo100_amortization_0208",
    "candidate_amortization_total",
    "annual_constrained_amortization_total",
    "max_abs_quarter_shift",
    "max_abs_constrained_residual",
    "periods",
    "year_signal",
]


def build_amortization_chain(
    candidate_quarter_reconciliation_csv: Path,
    annual_constrained_assets_csv: Path,
    quarter_closure_csv: Path,
    asset_candidates_csv: Path,
    modelo100_summary_csv: Path,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    candidate_by_period = {row["period"]: row for row in _load_rows(candidate_quarter_reconciliation_csv)}
    annual_rows = _load_rows(annual_constrained_assets_csv)
    closure_by_period = {row["period"]: row for row in _load_rows(quarter_closure_csv)}
    asset_candidates = _load_rows(asset_candidates_csv)
    modelo100_by_year = {str(summary.year): summary for summary in load_modelo100_summary(modelo100_summary_csv)}

    chain_rows: list[dict[str, str]] = []
    constrained_ytd_by_year: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for annual in sorted(annual_rows, key=lambda row: (int(row["year"]), int(row["quarter"]))):
        period = annual["period"]
        candidate = candidate_by_period[period]
        closure = closure_by_period[period]
        year = annual["year"]
        constrained_delta = parse_amount(annual["annual_constrained_amortization_delta"])
        constrained_ytd_by_year[year] += constrained_delta
        summary = modelo100_by_year.get(year)
        quarter_assets = _asset_labels(
            asset for asset in asset_candidates if asset.get("purchase_period") == period
        )
        active_assets = _asset_labels(
            asset for asset in asset_candidates if _period_key(asset.get("purchase_period", "9999-Q9")) <= _period_key(period)
        )
        chain_rows.append(
            {
                "year": year,
                "quarter": annual["quarter"],
                "period": period,
                "annual_constraint_source": annual["annual_constraint_source"],
                "modelo100_amortization_0208": _money(summary.amortization_0208) if summary else "",
                "candidate_amortization_delta": annual["candidate_amortization_delta"],
                "annual_constrained_amortization_delta": annual["annual_constrained_amortization_delta"],
                "candidate_amortization_ytd": candidate["candidate_amortization_ytd"],
                "annual_constrained_amortization_ytd": _money(constrained_ytd_by_year[year]),
                "amortization_delta_shift": annual["amortization_delta_shift"],
                "annual_constrained_model_minus_target_delta": annual[
                    "annual_constrained_model_minus_target_delta"
                ],
                "closure_status": closure["closure_status"],
                "asset_rows": closure["asset_rows"],
                "quarter_asset_candidates": quarter_assets,
                "asset_candidates_seen_ytd": active_assets,
                "amortization_chain_signal": _chain_signal(annual, closure, summary is not None),
                "required_xolo_evidence": closure["required_xolo_evidence"],
                "next_amortization_question": _next_question(annual, closure, summary is not None),
            }
        )
    return chain_rows, _year_summary(chain_rows, modelo100_by_year)


def write_amortization_chain_csvs(
    rows_path: Path,
    years_path: Path,
    rows: list[dict[str, str]],
    years: list[dict[str, str]],
) -> None:
    _write_csv(rows_path, AMORTIZATION_CHAIN_FIELDS, rows)
    _write_csv(years_path, YEAR_SUMMARY_FIELDS, years)


def write_amortization_chain_markdown(
    path: Path,
    rows: list[dict[str, str]],
    years: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    signals = defaultdict(int)
    for row in rows:
        signals[row["amortization_chain_signal"]] += 1
    max_shift = _max_abs(row["amortization_delta_shift"] for row in rows if row["modelo100_amortization_0208"])
    lines = [
        "# Modelo 130 Amortization Chain Audit",
        "",
        "This report follows the asset amortization lens quarter by quarter.",
        "It does not confirm Xolo's submitted asset schedule; it shows what the local candidate schedule and annual Modelo 100 constraint can and cannot prove.",
        "",
        "## Summary",
        "",
        f"- Quarter rows: `{len(rows)}`.",
        f"- Year rows: `{len(years)}`.",
        f"- Max annual-constraint shift where Modelo 100 exists: `{_fmt_decimal(max_shift)}`.",
        "- Signals: "
        + "; ".join(f"`{signal}`={count}" for signal, count in sorted(signals.items()))
        + ".",
        "",
        "## Year Summary",
        "",
        "| Year | M100 status | M100 0208 | Candidate amort. | Annual-constrained amort. | Max shift | Max residual | Signal |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in years:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["year"],
                    row["modelo100_status"],
                    _fmt(row["modelo100_amortization_0208"]),
                    _fmt(row["candidate_amortization_total"]),
                    _fmt(row["annual_constrained_amortization_total"]),
                    _fmt(row["max_abs_quarter_shift"]),
                    _fmt(row["max_abs_constrained_residual"]),
                    row["year_signal"],
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Quarter Chain",
            "",
            "| Period | Source | M100 0208 | Candidate delta | Constrained delta | Constrained YTD | Residual | Signal | Required evidence |",
            "|---|---|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["annual_constraint_source"],
                    _fmt(row["modelo100_amortization_0208"]),
                    _fmt(row["candidate_amortization_delta"]),
                    _fmt(row["annual_constrained_amortization_delta"]),
                    _fmt(row["annual_constrained_amortization_ytd"]),
                    _fmt(row["annual_constrained_model_minus_target_delta"]),
                    row["amortization_chain_signal"],
                    _cell(row["required_xolo_evidence"]),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Asset Candidates By Quarter",
            "",
            "| Period | Quarter asset candidates | Asset candidates seen YTD | Submitted asset rows in closure checklist | Next amortization question |",
            "|---|---|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    _cell(row["quarter_asset_candidates"] or "none"),
                    _cell(row["asset_candidates_seen_ytd"] or "none"),
                    _cell(row["asset_rows"] or "none"),
                    _cell(row["next_amortization_question"]),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Findings", ""])
    lines.extend(_findings(rows, years))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _year_summary(
    rows: list[dict[str, str]],
    modelo100_by_year: dict[str, object],
) -> list[dict[str, str]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["year"]].append(row)
    summaries: list[dict[str, str]] = []
    for year, year_rows in sorted(grouped.items()):
        summary = modelo100_by_year.get(year)
        candidate_total = sum((parse_amount(row["candidate_amortization_delta"]) for row in year_rows), Decimal("0.00"))
        constrained_total = sum(
            (parse_amount(row["annual_constrained_amortization_delta"]) for row in year_rows),
            Decimal("0.00"),
        )
        max_shift = _max_abs(row["amortization_delta_shift"] for row in year_rows)
        max_residual = _max_abs(row["annual_constrained_model_minus_target_delta"] for row in year_rows)
        m100_value = summary.amortization_0208 if summary else None
        summaries.append(
            {
                "year": year,
                "modelo100_status": summary.status if summary else "not_available",
                "modelo100_amortization_0208": _money(m100_value) if m100_value is not None else "",
                "candidate_amortization_total": _money(candidate_total),
                "annual_constrained_amortization_total": _money(constrained_total),
                "max_abs_quarter_shift": _money(max_shift),
                "max_abs_constrained_residual": _money(max_residual),
                "periods": "; ".join(row["period"] for row in year_rows),
                "year_signal": _year_signal(summary is not None, m100_value, candidate_total, constrained_total),
            }
        )
    return summaries


def _chain_signal(annual: dict[str, str], closure: dict[str, str], has_modelo100: bool) -> str:
    source = annual["annual_constraint_source"]
    m100_value = annual.get("annual_constrained_amortization_delta", "")
    residual = parse_amount(annual["annual_constrained_model_minus_target_delta"])
    shift = abs(parse_amount(annual["amortization_delta_shift"]))
    has_asset_rows = closure.get("has_asset_decision") == "yes" or bool(closure.get("asset_rows"))
    if source.startswith("no_m100"):
        return "current_year_unconstrained_asset_schedule_required"
    if has_modelo100 and parse_amount(m100_value) == Decimal("0.00") and has_asset_rows:
        return "m100_zero_asset_like_row_reclassification_question"
    if shift <= Decimal("1.00") and abs(residual) > Decimal("20.00"):
        return "annual_amortization_not_main_gap"
    if abs(residual) <= Decimal("2.00"):
        return "near_target_asset_schedule_confirmation"
    return "asset_schedule_and_register_required"


def _year_signal(
    has_modelo100: bool,
    m100_value: Decimal | None,
    candidate_total: Decimal,
    constrained_total: Decimal,
) -> str:
    if not has_modelo100:
        return "no_modelo100_available"
    if m100_value == Decimal("0.00") and candidate_total == Decimal("0.00"):
        return "annual_zero_amortization_consistent_with_candidate_filter"
    if abs(constrained_total - (m100_value or Decimal("0.00"))) <= Decimal("0.02"):
        return "annual_constraint_matches_modelo100_0208"
    return "annual_constraint_mismatch"


def _next_question(annual: dict[str, str], closure: dict[str, str], has_modelo100: bool) -> str:
    source = annual["annual_constraint_source"]
    period = annual["period"]
    if source.startswith("no_m100"):
        return (
            f"For {period}, provide Xolo's submitted asset schedule because no annual Modelo 100 "
            "amortization total is available yet."
        )
    if annual["annual_constrained_amortization_delta"] == "0.00" and closure.get("asset_rows"):
        return (
            f"For {period}, confirm whether the asset-like row was excluded, direct-expensed, or "
            "reclassified; annual Modelo 100 line 0208 is zero for that year."
        )
    if abs(parse_amount(annual["annual_constrained_model_minus_target_delta"])) > Decimal("20.00"):
        return (
            f"For {period}, confirm source-book row inclusion/netting first; annual amortization "
            f"changes the local candidate by only {format_es(abs(parse_amount(annual['amortization_delta_shift'])))}."
        )
    return f"For {period}, confirm the exact asset schedule amount and row basis used by Xolo."


def _findings(rows: list[dict[str, str]], years: list[dict[str, str]]) -> list[str]:
    findings = []
    zero_years = [
        row["year"]
        for row in years
        if row["year_signal"] == "annual_zero_amortization_consistent_with_candidate_filter"
    ]
    if zero_years:
        findings.append(
            "- Annual Modelo 100 line `0208` is zero for "
            + ", ".join(f"`{year}`" for year in zero_years)
            + "; asset-like rows in those years need direct-expense, exclusion, or reclassification confirmation rather than an amortization assumption."
        )
    low_shift_rows = [
        row
        for row in rows
        if row["amortization_chain_signal"] == "annual_amortization_not_main_gap"
    ]
    if low_shift_rows:
        findings.append(
            "- For "
            + ", ".join(f"`{row['period']}`" for row in low_shift_rows)
            + ", forcing annual Modelo 100 amortization changes the candidate schedule by less than `1,00 EUR`; the remaining issue is submitted row-set/treatment."
        )
    unconstrained = [
        row["period"]
        for row in rows
        if row["amortization_chain_signal"] == "current_year_unconstrained_asset_schedule_required"
    ]
    if unconstrained:
        findings.append(
            "- "
            + ", ".join(f"`{period}`" for period in unconstrained)
            + " remain current-year asset schedule questions because annual Modelo 100 data is not available."
        )
    if not findings:
        findings.append("- No amortization-chain findings were generated.")
    return findings


def _asset_labels(assets) -> str:
    labels = []
    for asset in assets:
        labels.append(
            " ".join(
                part
                for part in [
                    asset.get("date", ""),
                    asset.get("number", ""),
                    asset.get("recipient", ""),
                    asset.get("base_basis_eur", ""),
                ]
                if part
            )
        )
    return "; ".join(labels)


def _period_key(period: str) -> tuple[int, int]:
    year, quarter = period.split("-Q", 1)
    return int(year), int(quarter)


def _max_abs(values) -> Decimal:
    max_value = Decimal("0.00")
    for value in values:
        if value == "":
            continue
        amount = abs(parse_amount(value))
        if amount > max_value:
            max_value = amount
    return max_value


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _money(value: Decimal | None) -> str:
    return "" if value is None else f"{value.quantize(Decimal('0.01')):.2f}"


def _fmt(value: str) -> str:
    return format_es(parse_amount(value)) if value else ""


def _fmt_decimal(value: Decimal) -> str:
    return format_es(value)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
