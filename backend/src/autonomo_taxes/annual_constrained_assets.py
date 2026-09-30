from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .annual import Modelo100Summary, load_modelo100_summary
from .money import cents, format_es, parse_amount


def build_annual_constrained_asset_reconciliation(
    candidate_quarter_reconciliation_csv: Path,
    modelo100_summary_csv: Path,
) -> list[dict[str, str]]:
    candidate_rows = _load_rows(candidate_quarter_reconciliation_csv)
    summaries = {summary.year: summary for summary in load_modelo100_summary(modelo100_summary_csv)}
    allocations = _annual_constrained_allocations(candidate_rows, summaries)
    rows: list[dict[str, str]] = []
    for row in candidate_rows:
        year = int(row["year"])
        period = row["period"]
        candidate_amortization = parse_amount(row["candidate_amortization_delta"])
        constrained_amortization, source = allocations[(year, period)]
        target = parse_amount(row["target_casilla_02_delta"])
        raw_non_asset = parse_amount(row["raw_non_asset_gross_delta"])
        candidate_model_minus_target = parse_amount(row["candidate_model_minus_target_delta"])
        constrained_model_delta = cents(raw_non_asset + constrained_amortization)
        constrained_model_minus_target = cents(constrained_model_delta - target)
        rows.append(
            {
                "year": str(year),
                "quarter": row["quarter"],
                "period": period,
                "annual_constraint_source": source,
                "target_casilla_02_delta": _money(target),
                "raw_non_asset_gross_delta": _money(raw_non_asset),
                "candidate_amortization_delta": _money(candidate_amortization),
                "annual_constrained_amortization_delta": _money(constrained_amortization),
                "amortization_delta_shift": _money(constrained_amortization - candidate_amortization),
                "candidate_model_minus_target_delta": _money(candidate_model_minus_target),
                "annual_constrained_model_delta": _money(constrained_model_delta),
                "annual_constrained_model_minus_target_delta": _money(constrained_model_minus_target),
                "interpretation": _interpretation(source, constrained_model_minus_target),
            }
        )
    return rows


def write_annual_constrained_asset_reconciliation_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_annual_constrained_asset_reconciliation_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Annual-Constrained Asset Reconciliation",
        "",
        "This report constrains the candidate quarterly asset amortization lens to the annual Modelo 100 line `0208` when that annual value exists.",
        "It is still not Xolo's submitted asset schedule: it only proves how much of each quarter can be explained if the annual amortization total is forced to match Modelo 100.",
        "",
        "## Quarter Matrix",
        "",
        "| Period | Constraint source | Target delta | Raw non-asset | Candidate amort. | Constrained amort. | Shift | Constrained model - target | Interpretation |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["annual_constraint_source"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["raw_non_asset_gross_delta"]),
                    _fmt(row["candidate_amortization_delta"]),
                    _fmt(row["annual_constrained_amortization_delta"]),
                    _fmt(row["amortization_delta_shift"]),
                    _fmt(row["annual_constrained_model_minus_target_delta"]),
                    row["interpretation"],
                ]
            )
            + " |"
        )
    lines.extend(["", "## Findings", ""])
    lines.extend(_findings(rows))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _annual_constrained_allocations(
    rows: list[dict[str, str]],
    summaries: dict[int, Modelo100Summary],
) -> dict[tuple[int, str], tuple[Decimal, str]]:
    by_year: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_year[int(row["year"])].append(row)

    allocations: dict[tuple[int, str], tuple[Decimal, str]] = {}
    for year, year_rows in by_year.items():
        year_rows.sort(key=lambda row: int(row["quarter"]))
        summary = summaries.get(year)
        candidate_total = sum((parse_amount(row["candidate_amortization_delta"]) for row in year_rows), Decimal("0.00"))
        if summary is None:
            for row in year_rows:
                value = parse_amount(row["candidate_amortization_delta"])
                allocations[(year, row["period"])] = (value, "no_m100_0208_available_unconstrained")
            continue
        source = f"m100_0208_{summary.status}"
        annual_total = summary.amortization_0208
        if candidate_total == 0:
            for row in year_rows:
                allocations[(year, row["period"])] = (Decimal("0.00"), source)
            continue
        assigned = Decimal("0.00")
        for row in year_rows[:-1]:
            candidate = parse_amount(row["candidate_amortization_delta"])
            value = cents(candidate * annual_total / candidate_total)
            assigned += value
            allocations[(year, row["period"])] = (value, source)
        final_row = year_rows[-1]
        allocations[(year, final_row["period"])] = (cents(annual_total - assigned), source)
    return allocations


def _findings(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["- No rows were available."]
    constrained_rows = [row for row in rows if not row["annual_constraint_source"].startswith("no_m100")]
    max_shift = max((abs(parse_amount(row["amortization_delta_shift"])) for row in constrained_rows), default=Decimal("0.00"))
    material_after_constraint = [
        row
        for row in rows
        if abs(parse_amount(row["annual_constrained_model_minus_target_delta"])) > Decimal("20.00")
    ]
    unconstrained = [row["period"] for row in rows if row["annual_constraint_source"].startswith("no_m100")]
    findings = [
        f"- Applying annual Modelo 100 `0208` shifts quarterly candidate amortization by at most `{format_es(max_shift)}` where annual data exists.",
        f"- `{len(material_after_constraint)}` quarters still remain more than `20,00 EUR` away from submitted `casilla 02` after the annual amortization constraint.",
    ]
    if material_after_constraint:
        worst = max(material_after_constraint, key=lambda row: abs(parse_amount(row["annual_constrained_model_minus_target_delta"])))
        findings.append(
            "- The largest remaining constrained residual is "
            f"`{worst['period']}` at `{_fmt(worst['annual_constrained_model_minus_target_delta'])}`, "
            "so the main problem is still row-set, VAT/base, netting, or catch-up treatment rather than annual amortization alone."
        )
    if unconstrained:
        findings.append(
            "- These periods remain unconstrained by Modelo 100 annual amortization: "
            + ", ".join(f"`{period}`" for period in unconstrained)
            + "."
        )
    return findings


def _interpretation(source: str, model_minus_target: Decimal) -> str:
    if source.startswith("no_m100"):
        return "current-year asset lens only; annual Modelo 100 is not available"
    if model_minus_target > Decimal("20.00"):
        return "annual-constrained asset model remains above target; look for excluded/netted rows"
    if model_minus_target < Decimal("-20.00"):
        return "annual-constrained asset model remains below target; look for catch-up/reclassification"
    return "annual-constrained asset model is near the submitted quarter"


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return format_es(parse_amount(value))
