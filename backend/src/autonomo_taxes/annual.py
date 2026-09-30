from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .history import RawXoloExpense, load_raw_xolo_expenses, _raw_ytd_totals
from .money import cents, format_es, parse_amount


@dataclass(frozen=True)
class Modelo100Summary:
    year: int
    source_report: str
    status: str
    income_0171: Decimal
    total_income_0180: Decimal
    social_security_0186: Decimal
    professional_services_0199: Decimal
    other_external_services_0202: Decimal
    amortization_0208: Decimal
    deductible_expenses_0218: Decimal
    difficult_expenses_0222: Decimal
    total_deductible_0223: Decimal
    notes: str


def load_modelo100_summary(path: Path) -> list[Modelo100Summary]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [_summary_from_row(row) for row in reader]


def compare_annual_to_quarterly(
    modelo100_summary_path: Path,
    history_audit_csv: Path,
    xolo_raw_expenses_csv: Path,
) -> list[dict[str, str]]:
    summaries = load_modelo100_summary(modelo100_summary_path)
    history = _load_history_q4(history_audit_csv)
    raw_expenses = load_raw_xolo_expenses(xolo_raw_expenses_csv)
    rows: list[dict[str, str]] = []
    for summary in summaries:
        q4 = history.get(summary.year)
        usd_fx = Decimal(q4["derived_income_usd_fx"]) if q4 and q4.get("derived_income_usd_fx") else None
        raw = _raw_ytd_totals(raw_expenses, summary.year, 4, usd_fx)
        m130_q4 = parse_amount(q4["target_casilla_02"]) if q4 else Decimal("0.00")
        annual_minus_m130 = cents(summary.deductible_expenses_0218 - m130_q4)
        raw_non_asset = raw["non_asset_gross_eur"]
        annual_minus_raw_non_asset = cents(summary.deductible_expenses_0218 - raw_non_asset)
        amortization_minus_raw_residual = cents(summary.amortization_0208 - annual_minus_raw_non_asset)
        rows.append(
            {
                "year": str(summary.year),
                "status": summary.status,
                "source_report": summary.source_report,
                "m100_income_0171": _money(summary.income_0171),
                "m100_deductible_0218": _money(summary.deductible_expenses_0218),
                "m100_amortization_0208": _money(summary.amortization_0208),
                "m100_difficult_0222": _money(summary.difficult_expenses_0222),
                "m100_total_0223": _money(summary.total_deductible_0223),
                "m130_q4_casilla02": _money(m130_q4),
                "annual_minus_m130_q4": _money(annual_minus_m130),
                "raw_non_asset_gross_ytd": _money(raw_non_asset),
                "raw_asset_gross_ytd": _money(raw["asset_gross_eur"]),
                "annual_minus_raw_non_asset": _money(annual_minus_raw_non_asset),
                "amortization_minus_raw_residual": _money(amortization_minus_raw_residual),
                "raw_estimated_asset_amortization_ytd": _money(raw["asset_amortization_estimate_gross"]),
                "social_security_0186": _money(summary.social_security_0186),
                "professional_services_0199": _money(summary.professional_services_0199),
                "other_external_services_0202": _money(summary.other_external_services_0202),
                "derived_income_usd_fx": "" if usd_fx is None else str(usd_fx),
                "notes": summary.notes,
            }
        )
    return rows


def write_annual_comparison_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_annual_comparison_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 100 Annual Comparison",
        "",
        "This report compares annual Modelo 100 economic-activity expenses with submitted Q4 Modelo 130 `casilla 02` and the raw Xolo expense export.",
        "`M100 0218` is before difficult-to-justify expenses; `M100 0222` shows the annual difficult-expense provision separately.",
        "",
        "| Year | Source | M100 0218 | M100 amort. 0208 | M100 difficult 0222 | M130 Q4 casilla 02 | Annual - M130 | Raw non-asset | Annual - raw non-asset | Amort. - raw residual | Asset candidates |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["year"],
                    row["status"],
                    _fmt(row["m100_deductible_0218"]),
                    _fmt(row["m100_amortization_0208"]),
                    _fmt(row["m100_difficult_0222"]),
                    _fmt(row["m130_q4_casilla02"]),
                    _fmt(row["annual_minus_m130_q4"]),
                    _fmt(row["raw_non_asset_gross_ytd"]),
                    _fmt(row["annual_minus_raw_non_asset"]),
                    _fmt(row["amortization_minus_raw_residual"]),
                    _fmt(row["raw_asset_gross_ytd"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Findings:",
            "",
            "- 2023 and 2024 annual Modelo 100 deductible expenses are higher than Q4 Modelo 130 `casilla 02`, so annual filing added or reclassified expenses after the quarterly return.",
            "- 2025 draft Modelo 100 `0218` equals Q4 Modelo 130 `casilla 02`, and explicitly includes `422.87 EUR` of amortization.",
            "- In 2025, annual `0218` exceeds raw non-asset expenses by `213.59 EUR`, while M100 amortization is `422.87 EUR`; this implies about `209.28 EUR` of raw non-asset rows were excluded, netted, or treated on a different basis.",
            "- Annual difficult-to-justify expenses are present in Modelo 100 (`0222 = 2,000.00`) but are not needed to reproduce quarterly Modelo 130 `casilla 02`.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _summary_from_row(row: dict[str, str]) -> Modelo100Summary:
    return Modelo100Summary(
        year=int(row["year"]),
        source_report=row["source_report"],
        status=row["status"],
        income_0171=parse_amount(row["income_0171"]),
        total_income_0180=parse_amount(row["total_income_0180"]),
        social_security_0186=parse_amount(row["social_security_0186"]),
        professional_services_0199=parse_amount(row["professional_services_0199"]),
        other_external_services_0202=parse_amount(row["other_external_services_0202"]),
        amortization_0208=parse_amount(row["amortization_0208"]),
        deductible_expenses_0218=parse_amount(row["deductible_expenses_0218"]),
        difficult_expenses_0222=parse_amount(row["difficult_expenses_0222"]),
        total_deductible_0223=parse_amount(row["total_deductible_0223"]),
        notes=row.get("notes") or "",
    )


def _load_history_q4(path: Path) -> dict[int, dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return {int(row["year"]): row for row in reader if row["quarter"] == "4"}


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return format_es(Decimal(value))
