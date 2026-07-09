from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .annual import load_modelo100_summary
from .history import _row_gross_eur, load_raw_xolo_expenses
from .money import cents, format_es, parse_amount


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
        raw_social = raw["Social security & prof. fees"]
        raw_professional = raw["Professional expenses"]
        raw_transport = raw["Transportation & accommodation"]
        raw_multiple = raw["Multiple"]
        raw_computer = raw["Computer hardware & software"]
        rows.append(
            {
                "year": str(summary.year),
                "status": summary.status,
                "q4_derived_income_usd_fx": "" if summary.year not in fx_by_year else str(fx_by_year[summary.year]),
                "m100_social_security_0186": _money(summary.social_security_0186),
                "raw_social_security": _money(raw_social),
                "social_security_diff": _money(summary.social_security_0186 - raw_social),
                "m100_professional_services_0199": _money(summary.professional_services_0199),
                "raw_professional_expenses": _money(raw_professional),
                "professional_diff": _money(summary.professional_services_0199 - raw_professional),
                "m100_other_external_services_0202": _money(summary.other_external_services_0202),
                "raw_transportation_accommodation": _money(raw_transport),
                "raw_multiple": _money(raw_multiple),
                "other_vs_transport_diff": _money(summary.other_external_services_0202 - raw_transport),
                "m100_amortization_0208": _money(summary.amortization_0208),
                "raw_computer_hardware_software": _money(raw_computer),
                "raw_asset_like_plus_multiple": _money(raw_computer + raw_multiple),
                "m100_deductible_expenses_0218": _money(summary.deductible_expenses_0218),
                "category_signal": _category_signal(
                    summary.amortization_0208,
                    summary.other_external_services_0202,
                    raw_computer,
                    raw_professional - summary.professional_services_0199,
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
        "It is an annual diagnostic lens: quarterly Modelo 130 closure still requires Xolo's submitted row-level register and asset schedule.",
        "USD rows are converted with the corresponding Q4 derived income FX used in the historical Modelo 130 audit.",
        "",
        "| Year | Status | SS diff | Professional diff | Other vs transport diff | M100 amort. | Raw computer/assets | Signal |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["year"],
                    row["status"],
                    _fmt(row["social_security_diff"]),
                    _fmt(row["professional_diff"]),
                    _fmt(row["other_vs_transport_diff"]),
                    _fmt(row["m100_amortization_0208"]),
                    _fmt(row["raw_asset_like_plus_multiple"]),
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


def _raw_category_totals(rows, year: int, usd_fx: Decimal | None) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for row in rows:
        if row.date.year != year:
            continue
        gross = _row_gross_eur(row, usd_fx)
        if gross is None:
            continue
        totals[row.expense_type] = cents(totals[row.expense_type] + gross)
    return totals


def _category_signal(
    m100_amortization: Decimal,
    m100_other: Decimal,
    raw_computer: Decimal,
    raw_professional_excess: Decimal,
) -> str:
    if m100_amortization == 0 and raw_computer > 0 and m100_other > 0:
        return "annual return appears to direct-expense/reclassify software or hardware instead of amortizing it"
    if m100_amortization > 0 and raw_professional_excess > 0:
        return "annual return combines asset amortization with professional-row exclusions or netting"
    if raw_professional_excess > 0:
        return "annual return excludes or nets professional rows"
    return "annual categories are near raw category totals"


def _findings(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["- No rows were available."]
    social_matches = [
        row for row in rows if abs(parse_amount(row["social_security_diff"])) <= Decimal("0.02")
    ]
    professional_exclusions = [
        row for row in rows if parse_amount(row["professional_diff"]) < Decimal("-20.00")
    ]
    findings = [
        f"- Social security annual category matches raw Xolo rows for `{len(social_matches)}` of `{len(rows)}` years, so annual TGSS totals are not the source of the main annual differences.",
        f"- Professional-service raw rows exceed Modelo 100 line `0199` by more than `20,00 EUR` in `{len(professional_exclusions)}` years, pointing to excluded, netted, or reclassified professional rows.",
    ]
    for row in rows:
        if row["category_signal"] != "annual categories are near raw category totals":
            findings.append(f"- `{row['year']}`: {row['category_signal']}.")
    return findings


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return format_es(parse_amount(value))
