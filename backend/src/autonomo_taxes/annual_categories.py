from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .annual import load_modelo100_summary
from .history import _row_base_eur, _row_gross_eur, load_raw_xolo_expenses
from .money import cents, format_es, parse_amount


TRACKED_CATEGORIES = (
    "Social security & prof. fees",
    "Professional expenses",
    "Transportation & accommodation",
    "Multiple",
    "Computer hardware & software",
)

NEAR_ZERO = Decimal("20.00")


@dataclass(frozen=True)
class CategoryTotals:
    gross: dict[str, Decimal]
    base: dict[str, Decimal]
    missing_fx_rows: int
    unknown_categories: tuple[str, ...]


def build_annual_category_reconciliation(
    modelo100_summary_csv: Path,
    history_audit_csv: Path,
    xolo_raw_expenses_csv: Path,
) -> list[dict[str, str]]:
    summaries = load_modelo100_summary(modelo100_summary_csv)
    fx_by_year = _q4_fx_by_year(history_audit_csv)
    raw_rows = load_raw_xolo_expenses(xolo_raw_expenses_csv)
    rows: list[dict[str, str]] = []
    for summary in summaries:
        raw = _raw_category_totals(raw_rows, summary.year, fx_by_year.get(summary.year))
        gross = raw.gross
        base = raw.base
        raw_social_gross = gross["Social security & prof. fees"]
        raw_social_base = base["Social security & prof. fees"]
        raw_professional_gross = gross["Professional expenses"]
        raw_professional_base = base["Professional expenses"]
        raw_transport_gross = gross["Transportation & accommodation"]
        raw_transport_base = base["Transportation & accommodation"]
        raw_multiple_gross = gross["Multiple"]
        raw_multiple_base = base["Multiple"]
        raw_computer_gross = gross["Computer hardware & software"]
        raw_computer_base = base["Computer hardware & software"]
        professional_gross_diff = summary.professional_services_0199 - raw_professional_gross
        professional_base_diff = summary.professional_services_0199 - raw_professional_base
        rows.append(
            {
                "year": str(summary.year),
                "status": summary.status,
                "q4_derived_income_usd_fx": "" if summary.year not in fx_by_year else str(fx_by_year[summary.year]),
                "m100_social_security_0186": _money(summary.social_security_0186),
                "raw_social_security_gross": _money(raw_social_gross),
                "raw_social_security_base": _money(raw_social_base),
                "social_security_base_diff": _money(summary.social_security_0186 - raw_social_base),
                "m100_professional_services_0199": _money(summary.professional_services_0199),
                "raw_professional_expenses_gross": _money(raw_professional_gross),
                "raw_professional_expenses_base": _money(raw_professional_base),
                "professional_gross_diff": _money(professional_gross_diff),
                "professional_base_diff": _money(professional_base_diff),
                "m100_other_external_services_0202": _money(summary.other_external_services_0202),
                "raw_transportation_accommodation_gross": _money(raw_transport_gross),
                "raw_transportation_accommodation_base": _money(raw_transport_base),
                "raw_multiple_gross": _money(raw_multiple_gross),
                "raw_multiple_base": _money(raw_multiple_base),
                "other_vs_transport_gross_diff": _money(summary.other_external_services_0202 - raw_transport_gross),
                "other_vs_transport_base_diff": _money(summary.other_external_services_0202 - raw_transport_base),
                "m100_amortization_0208": _money(summary.amortization_0208),
                "raw_computer_hardware_software_gross": _money(raw_computer_gross),
                "raw_computer_hardware_software_base": _money(raw_computer_base),
                "raw_asset_like_plus_multiple_gross": _money(raw_computer_gross + raw_multiple_gross),
                "raw_asset_like_plus_multiple_base": _money(raw_computer_base + raw_multiple_base),
                "m100_deductible_expenses_0218": _money(summary.deductible_expenses_0218),
                "missing_fx_expense_rows": str(raw.missing_fx_rows),
                "unmapped_expense_types": "; ".join(raw.unknown_categories),
                "category_signal": _category_signal(
                    summary.amortization_0208,
                    summary.other_external_services_0202,
                    raw_computer_gross,
                    professional_gross_diff,
                    professional_base_diff,
                ),
            }
        )
    return rows


def write_annual_category_reconciliation_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_annual_category_reconciliation_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 100 Annual Category Reconciliation",
        "",
        "This report compares annual Modelo 100 expense categories with raw Xolo expense categories.",
        "It is an annual diagnostic lens: quarterly Modelo 130 closure still requires Xolo's source-book row evidence and asset schedule.",
        "USD rows are converted with the corresponding Q4 derived income FX used in the historical Modelo 130 audit.",
        "Modelo 100 service categories are compared primarily against raw Xolo VAT-base/subtotal amounts; gross amounts remain as a VAT diagnostic.",
        "",
        "| Year | Status | SS base diff | Professional gross diff | Professional base diff | Other base diff | M100 amort. | Raw asset base | Signal |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["year"],
                    row["status"],
                    _fmt(row["social_security_base_diff"]),
                    _fmt(row["professional_gross_diff"]),
                    _fmt(row["professional_base_diff"]),
                    _fmt(row["other_vs_transport_base_diff"]),
                    _fmt(row["m100_amortization_0208"]),
                    _fmt(row["raw_asset_like_plus_multiple_base"]),
                    row["category_signal"],
                ]
            )
            + " |"
        )
    lines.extend(["", "## Findings", ""])
    lines.extend(_findings(rows))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _q4_fx_by_year(path: Path) -> dict[int, Decimal]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return {
            int(row["year"]): Decimal(row["derived_income_usd_fx"])
            for row in csv.DictReader(handle)
            if row.get("quarter") == "4" and row.get("derived_income_usd_fx")
        }


def _raw_category_totals(rows, year: int, usd_fx: Decimal | None) -> CategoryTotals:
    gross_totals: dict[str, Decimal] = defaultdict(Decimal)
    base_totals: dict[str, Decimal] = defaultdict(Decimal)
    missing_fx_rows = 0
    unknown_categories: set[str] = set()
    for row in rows:
        if row.date.year != year:
            continue
        if row.expense_type not in TRACKED_CATEGORIES:
            unknown_categories.add(row.expense_type or "<blank>")
        if row.currency == "USD" and usd_fx is None:
            missing_fx_rows += 1
            continue
        gross = _row_gross_eur(row, usd_fx)
        base = _row_base_eur(row, usd_fx)
        if gross is not None:
            gross_totals[row.expense_type] = cents(gross_totals[row.expense_type] + gross)
        if base is not None:
            base_totals[row.expense_type] = cents(base_totals[row.expense_type] + base)
    return CategoryTotals(
        gross=gross_totals,
        base=base_totals,
        missing_fx_rows=missing_fx_rows,
        unknown_categories=tuple(sorted(unknown_categories)),
    )


def _category_signal(
    m100_amortization: Decimal,
    m100_other: Decimal,
    raw_computer_gross: Decimal,
    professional_gross_diff: Decimal,
    professional_base_diff: Decimal,
) -> str:
    if m100_amortization == 0 and raw_computer_gross > 0 and m100_other > 0:
        return "annual return appears to direct-expense/reclassify software or hardware instead of amortizing it"
    if professional_base_diff < -NEAR_ZERO:
        return "M100 professional services are below raw VAT-base rows; confirm excluded or netted professional rows"
    if professional_base_diff > NEAR_ZERO:
        return "M100 professional services exceed raw VAT-base rows; confirm category reclassification or catch-up"
    if professional_gross_diff < -NEAR_ZERO:
        if m100_amortization > 0:
            return "professional gross gap is mostly recoverable VAT; asset schedule still needs confirmation"
        return "professional gross gap is mostly recoverable VAT; line 0199 aligns with raw VAT-base rows"
    if m100_amortization > 0:
        return "annual amortization exists; asset schedule still needs row-level confirmation"
    return "annual categories are near raw category totals"


def _findings(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["- No rows were available."]
    social_matches = [row for row in rows if abs(parse_amount(row["social_security_base_diff"])) <= Decimal("0.02")]
    professional_vat_shaped = [
        row
        for row in rows
        if parse_amount(row["professional_gross_diff"]) < -NEAR_ZERO
        and abs(parse_amount(row["professional_base_diff"])) <= NEAR_ZERO
    ]
    professional_base_differences = [
        row for row in rows if abs(parse_amount(row["professional_base_diff"])) > NEAR_ZERO
    ]
    findings = [
        f"- Social security annual category matches raw Xolo rows for `{len(social_matches)}` of `{len(rows)}` years, so annual TGSS totals are not the source of the main annual differences.",
        f"- Professional-service gross gaps collapse to within `20,00 EUR` when compared with raw VAT-base amounts in `{len(professional_vat_shaped)}` of `{len(rows)}` years; those gross gaps are likely recoverable VAT, not row exclusions.",
        f"- Professional-service VAT-base differences remain material in `{len(professional_base_differences)}` of `{len(rows)}` years, pointing to category reclassification, catch-up, exclusion, or netting that still needs row-level confirmation.",
    ]
    missing_fx_years = [row for row in rows if row["missing_fx_expense_rows"] != "0"]
    unknown_category_years = [row for row in rows if row["unmapped_expense_types"]]
    if missing_fx_years:
        findings.append(
            "- Some years skipped USD expense rows because no Q4 derived FX was available: "
            + ", ".join(f"`{row['year']}` ({row['missing_fx_expense_rows']} rows)" for row in missing_fx_years)
            + "."
        )
    if unknown_category_years:
        findings.append(
            "- Some years contain raw Xolo categories outside the tracked category map: "
            + ", ".join(f"`{row['year']}` ({row['unmapped_expense_types']})" for row in unknown_category_years)
            + "."
        )
    for row in rows:
        if row["category_signal"] != "annual categories are near raw category totals":
            findings.append(f"- `{row['year']}`: {row['category_signal']}.")
    return findings


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return format_es(parse_amount(value))
