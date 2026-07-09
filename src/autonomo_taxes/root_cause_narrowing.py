from __future__ import annotations

import csv
from collections import Counter
from decimal import Decimal
from pathlib import Path

from .money import format_es, parse_amount


def build_root_cause_narrowing(
    quarter_closure_csv: Path,
    annual_constrained_assets_csv: Path,
    modelo303_vat_crosscheck_csv: Path,
    annual_categories_csv: Path | None = None,
) -> list[dict[str, str]]:
    closure_rows = _load_rows(quarter_closure_csv)
    annual_by_period = {row["period"]: row for row in _load_rows(annual_constrained_assets_csv)}
    vat_by_period = {row["period"]: row for row in _load_rows(modelo303_vat_crosscheck_csv)}
    annual_category_by_year = _annual_categories_by_year(annual_categories_csv) if annual_categories_csv else {}
    rows: list[dict[str, str]] = []
    for closure in closure_rows:
        period = closure["period"]
        annual = annual_by_period.get(period, {})
        vat = vat_by_period.get(period, {})
        annual_category = annual_category_by_year.get(_period_year(period), {})
        annual_residual = annual.get("annual_constrained_model_minus_target_delta", closure["pre_plug_model_minus_target_eur"])
        eliminated = _eliminated_causes(annual, vat, annual_category)
        remaining = _remaining_causes(closure, annual, vat, annual_category)
        rows.append(
            {
                "period": period,
                "closure_status": closure["closure_status"],
                "target_casilla_02_delta": closure["target_casilla_02_delta"],
                "balancing_adjustment_eur": closure["balancing_adjustment_eur"],
                "annual_constrained_residual": annual_residual,
                "modelo303_vat_status": vat.get("crosscheck_status", "not_available"),
                "annual_constraint_source": annual.get("annual_constraint_source", "not_available"),
                "annual_category_status": annual_category.get("status", "not_available"),
                "annual_professional_gross_diff": annual_category.get("professional_gross_diff", ""),
                "annual_professional_base_diff": annual_category.get("professional_base_diff", ""),
                "annual_category_signal": annual_category.get("category_signal", "not_available"),
                "eliminated_causes": "; ".join(eliminated),
                "remaining_causes": "; ".join(remaining),
                "next_evidence": _next_evidence(closure, annual, vat),
            }
        )
    return rows


def write_root_cause_narrowing_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_root_cause_narrowing_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    status_counts = Counter(row["closure_status"] for row in rows)
    m303_matched = sum(1 for row in rows if row["modelo303_vat_status"] == "matched")
    annual_constrained = sum(1 for row in rows if row["annual_constraint_source"].startswith("m100_0208"))
    annual_category_available = sum(1 for row in rows if row["annual_category_status"] != "not_available")
    lines = [
        "# Modelo 130 Root-Cause Narrowing",
        "",
        "This report narrows the remaining Modelo 130 problem by combining the quarter closure checklist, the annual-constrained asset lens, the annual Modelo 100 category lens, and the submitted Modelo 303 VAT cross-check.",
        "It is not a filed-register substitute. It records which causes are locally ruled out and which causes still require Xolo's submitted Modelo 130 expense register or asset schedule.",
        "",
        "## Evidence Gates",
        "",
        f"- Modelo 303 VAT gate matched: {m303_matched}/{len(rows)} Modelo 130 quarters in scope. Missing periods mean there is no submitted Modelo 303 report in the archive for that quarter.",
        f"- Annual Modelo 100 amortization constraint available: {annual_constrained}/{len(rows)} quarters.",
        f"- Annual Modelo 100 category VAT-base comparison available: {annual_category_available}/{len(rows)} quarters.",
        "",
        "| Closure status | Count |",
        "|---|---:|",
    ]
    for status, count in sorted(status_counts.items()):
        lines.append(f"| {status} | {count} |")
    lines.extend(
        [
            "",
            "## Quarter Matrix",
            "",
            "| Period | Status | Annual-constrained residual | M303 VAT | Annual professional base diff | Eliminated locally | Remaining causes | Next evidence |",
            "|---|---|---:|---|---:|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["closure_status"],
                    _fmt(row["annual_constrained_residual"]),
                    row["modelo303_vat_status"],
                    _fmt(row["annual_professional_base_diff"]),
                    _cell(row["eliminated_causes"]),
                    _cell(row["remaining_causes"]),
                    _cell(row["next_evidence"]),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Findings", ""])
    lines.extend(_findings(rows))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _eliminated_causes(annual: dict[str, str], vat: dict[str, str], annual_category: dict[str, str]) -> list[str]:
    eliminated: list[str] = []
    if vat.get("crosscheck_status") == "matched":
        eliminated.append("missing VAT-bearing EUR expense rows")
    source = annual.get("annual_constraint_source", "")
    if source.startswith("m100_0208"):
        shift = abs(parse_amount(annual.get("amortization_delta_shift") or "0.00"))
        if shift <= Decimal("1.00"):
            eliminated.append("material annual amortization total mismatch")
    if _professional_gross_gap_is_vat_shaped(annual_category):
        eliminated.append("annual professional gross-gap row-exclusion inference")
    return eliminated or ["none locally eliminated"]


def _remaining_causes(
    closure: dict[str, str],
    annual: dict[str, str],
    vat: dict[str, str],
    annual_category: dict[str, str],
) -> list[str]:
    remaining: list[str] = []
    residual = parse_amount(annual.get("annual_constrained_model_minus_target_delta") or closure["pre_plug_model_minus_target_eur"])
    if residual > Decimal("20.00"):
        remaining.append("candidate model above target: excluded/netted rows or IRPF basis reductions")
    elif residual < Decimal("-20.00"):
        remaining.append("candidate model below target: catch-up, reclassification, or missing non-VAT/FX rows")
    else:
        remaining.append("quarter arithmetic near target; exact submitted treatment still unconfirmed")
    if closure.get("has_asset_decision") == "yes" or closure["closure_status"] == "pending_asset_schedule_confirmation":
        remaining.append(_asset_treatment_label(annual))
    if vat.get("crosscheck_status") != "matched":
        remaining.append("VAT-bearing row completeness for this period")
    if closure.get("has_nearest_exclusion") == "yes":
        remaining.append("specific nearest-exclusion row decision")
    if _has_material_professional_base_diff(annual_category):
        remaining.append("annual professional category base difference")
    signal = annual_category.get("category_signal", "")
    if "direct-expense/reclassify" in signal:
        remaining.append("annual asset direct-expense or reclassification treatment")
    return remaining


def _next_evidence(closure: dict[str, str], annual: dict[str, str], vat: dict[str, str]) -> str:
    items = ["submitted Modelo 130 expense register with deductible EUR per row"]
    if closure.get("has_asset_decision") == "yes" or closure["closure_status"] == "pending_asset_schedule_confirmation":
        items.append(_asset_evidence_label(annual))
    if closure.get("has_nearest_exclusion") == "yes":
        items.append("confirmation of excluded/netted rows")
    if annual.get("annual_constraint_source", "").startswith("no_m100"):
        items.append("future Modelo 100 annual amortization line 0208")
    if vat.get("crosscheck_status") not in {"matched", ""}:
        items.append("Modelo 303/VAT-bearing row review")
    return "; ".join(items)


def _findings(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["- No rows were available."]
    vat_matched = [row for row in rows if row["modelo303_vat_status"] == "matched"]
    annual_constrained = [row for row in rows if row["annual_constraint_source"].startswith("m100_0208")]
    annual_category_available = [row for row in rows if row["annual_category_status"] != "not_available"]
    annual_professional_vat = [
        row
        for row in rows
        if row["annual_professional_gross_diff"] and _professional_gross_gap_is_vat_shaped(row)
    ]
    annual_professional_base = [
        row
        for row in rows
        if row["annual_professional_base_diff"] and _has_material_professional_base_diff(row)
    ]
    material_remaining = [
        row
        for row in rows
        if abs(parse_amount(row["annual_constrained_residual"])) > Decimal("20.00")
    ]
    findings = [
        f"- VAT-bearing EUR row loss is locally ruled out for `{len(vat_matched)}` quarters with submitted Modelo 303 coverage.",
        f"- A material annual amortization-total mismatch is locally ruled out for `{len(annual_constrained)}` quarters with Modelo 100 `0208` coverage.",
        f"- Annual Modelo 100 category VAT-base comparison is available for `{len(annual_category_available)}` quarters.",
        f"- Annual professional gross gaps are VAT-shaped rather than row-exclusion evidence for `{len(annual_professional_vat)}` quarters.",
        f"- Annual professional VAT-base differences remain material for `{len(annual_professional_base)}` quarters.",
        f"- `{len(material_remaining)}` quarters still have more than `20,00 EUR` residual after those gates, so the remaining problem is the submitted Modelo 130 row set, IRPF basis treatment, netting, catch-up/reclassification, or the unconfirmed asset schedule.",
    ]
    no_vat = [row["period"] for row in rows if row["modelo303_vat_status"] == "not_available"]
    if no_vat:
        findings.append("- Modelo 303 is not available in the archive for: " + ", ".join(f"`{period}`" for period in no_vat) + ".")
    return findings


def _asset_treatment_label(annual: dict[str, str]) -> str:
    if _annual_amortization_is_zero(annual):
        return "asset/direct-expense treatment confirmation"
    return "asset amortization schedule confirmation"


def _asset_evidence_label(annual: dict[str, str]) -> str:
    if _annual_amortization_is_zero(annual):
        return "confirmation of direct-expense versus capitalized-asset treatment"
    return "asset amortization schedule by asset and quarter"


def _annual_amortization_is_zero(annual: dict[str, str]) -> bool:
    source = annual.get("annual_constraint_source", "")
    if not source.startswith("m100_0208"):
        return False
    value = annual.get("annual_constrained_amortization_delta")
    if value is None:
        return False
    return parse_amount(value) == Decimal("0.00")


def _annual_categories_by_year(path: Path) -> dict[str, dict[str, str]]:
    return {row["year"]: row for row in _load_rows(path)}


def _period_year(period: str) -> str:
    return period.split("-", 1)[0]


def _professional_gross_gap_is_vat_shaped(row: dict[str, str]) -> bool:
    if not row:
        return False
    gross = _professional_diff(row, "gross")
    base = _professional_diff(row, "base")
    if not gross or not base:
        return False
    return parse_amount(gross) < Decimal("-20.00") and abs(parse_amount(base)) <= Decimal("20.00")


def _has_material_professional_base_diff(row: dict[str, str]) -> bool:
    base = _professional_diff(row, "base")
    if not base:
        return False
    return abs(parse_amount(base)) > Decimal("20.00")


def _professional_diff(row: dict[str, str], basis: str) -> str:
    return row.get(f"professional_{basis}_diff") or row.get(f"annual_professional_{basis}_diff") or ""


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _fmt(value: str) -> str:
    if not value:
        return ""
    return format_es(parse_amount(value))


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
