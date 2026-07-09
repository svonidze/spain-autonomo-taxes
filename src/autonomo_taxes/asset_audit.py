from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

from .annual import load_modelo100_summary
from .history import (
    RawXoloExpense,
    format_subset_rows,
    load_raw_xolo_expenses,
    nearest_excluded_subset,
)
from .money import cents, format_es, parse_amount
from .parsers import quarter_end


ANNUAL_AMORTIZATION_RATE = Decimal("0.26")
CANDIDATE_QUARTER_SCENARIO = "exclude_2023_usd_low_value_or_subscription_vat_base_25pct"


def build_asset_audit(
    history_audit_csv: Path,
    xolo_raw_expenses_csv: Path,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    history_rows = _load_history_rows(history_audit_csv)
    rates_by_period = _rates_by_period(history_rows)
    raw_assets = [row for row in load_raw_xolo_expenses(xolo_raw_expenses_csv) if row.is_asset_like]
    assets = [_asset_row(row, rates_by_period) for row in sorted(raw_assets, key=lambda item: (item.date, item.recipient, item.number))]
    quarter_rows = _quarter_rows(history_rows, assets)
    return assets, quarter_rows


def build_candidate_quarter_reconciliation(
    history_audit_csv: Path,
    xolo_raw_expenses_csv: Path,
) -> list[dict[str, str]]:
    history_rows = _load_history_rows(history_audit_csv)
    rates_by_period = _rates_by_period(history_rows)
    raw_expenses = load_raw_xolo_expenses(xolo_raw_expenses_csv)
    raw_assets = [row for row in raw_expenses if row.is_asset_like]
    assets = [_asset_row(row, rates_by_period) for row in sorted(raw_assets, key=lambda item: (item.date, item.recipient, item.number))]
    included_assets = [asset for asset in assets if not _is_2023_usd_low_value_or_subscription(asset)]
    rows: list[dict[str, str]] = []
    previous_ytd_by_year: dict[int, Decimal] = {}
    for history in history_rows:
        year = int(history["year"])
        quarter = int(history["quarter"])
        end = quarter_end(year, quarter)
        amortization_ytd = _amortization_ytd(included_assets, end, "base_basis_eur", Decimal("0.25"))
        amortization_delta = cents(amortization_ytd - previous_ytd_by_year.get(year, Decimal("0.00")))
        previous_ytd_by_year[year] = amortization_ytd
        target_delta = parse_amount(history["target_casilla_02_delta"])
        raw_non_asset_delta = parse_amount(history["raw_quarter_non_asset_gross_eur"])
        model_delta = cents(raw_non_asset_delta + amortization_delta)
        model_minus_target = cents(model_delta - target_delta)
        ytd_residual_after_candidate = cents(parse_amount(history["no_provision_gross_residual"]) - amortization_ytd)
        derived_fx = Decimal(history["derived_income_usd_fx"]) if history.get("derived_income_usd_fx") else None
        subset = (
            nearest_excluded_subset(raw_expenses, year, quarter, derived_fx, model_minus_target)
            if model_minus_target > Decimal("20.00")
            else None
        )
        rows.append(
            {
                "year": str(year),
                "quarter": str(quarter),
                "period": f"{year}-Q{quarter}",
                "scenario": CANDIDATE_QUARTER_SCENARIO,
                "target_casilla_02_delta": _money(target_delta),
                "raw_non_asset_gross_delta": _money(raw_non_asset_delta),
                "candidate_amortization_delta": _money(amortization_delta),
                "candidate_model_delta": _money(model_delta),
                "candidate_model_minus_target_delta": _money(model_minus_target),
                "candidate_amortization_ytd": _money(amortization_ytd),
                "ytd_residual_after_candidate": _money(ytd_residual_after_candidate),
                "nearest_excluded_subset_eur": _money(subset.total if subset else Decimal("0.00")),
                "nearest_excluded_subset_error_eur": _money(subset.error if subset else Decimal("0.00")),
                "nearest_excluded_subset_rows": format_subset_rows(subset),
                "interpretation": _quarter_candidate_interpretation(model_minus_target, ytd_residual_after_candidate),
            }
        )
    return rows


def write_asset_audit_csvs(
    assets_path: Path,
    quarters_path: Path,
    assets: list[dict[str, str]],
    quarters: list[dict[str, str]],
) -> None:
    _write_csv(assets_path, assets)
    _write_csv(quarters_path, quarters)


def build_asset_scenarios(
    assets: list[dict[str, str]],
    modelo100_summary_path: Path,
) -> list[dict[str, str]]:
    scenarios: list[dict[str, str]] = []
    summaries = load_modelo100_summary(modelo100_summary_path)
    for summary in summaries:
        end = date(summary.year, 12, 31)
        eligible_assets = [asset for asset in assets if date.fromisoformat(asset["date"]) <= end]
        for filter_name, filter_fn in _asset_filters():
            included = [asset for asset in eligible_assets if filter_fn(asset)]
            excluded = [asset for asset in eligible_assets if not filter_fn(asset)]
            for basis in ("gross_basis_eur", "base_basis_eur"):
                for rate in (Decimal("0.26"), Decimal("0.25")):
                    estimate = _amortization_ytd(included, end, basis, rate)
                    scenarios.append(
                        {
                            "year": str(summary.year),
                            "status": summary.status,
                            "m100_amortization_0208": _money(summary.amortization_0208),
                            "scenario": filter_name,
                            "basis": "gross" if basis == "gross_basis_eur" else "vat_base",
                            "rate": str(rate),
                            "estimate_ytd": _money(estimate),
                            "estimate_minus_m100_0208": _money(estimate - summary.amortization_0208),
                            "included_assets": _asset_names(included),
                            "excluded_assets": _asset_names(excluded),
                        }
                    )
    return scenarios


def write_asset_scenarios_csv(path: Path, scenarios: list[dict[str, str]]) -> None:
    _write_csv(path, scenarios)


def write_candidate_quarter_reconciliation_csv(path: Path, rows: list[dict[str, str]]) -> None:
    _write_csv(path, rows)


def write_asset_audit_markdown(
    path: Path,
    assets: list[dict[str, str]],
    quarters: list[dict[str, str]],
    scenarios: list[dict[str, str]] | None = None,
    candidate_quarters: list[dict[str, str]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 130 Asset Amortization Audit",
        "",
        "This report makes the amortization estimate used in the historical Modelo 130 audit explicit.",
        "It is an audit estimate, not Xolo's confirmed asset schedule.",
        "USD asset candidates are converted once at the derived income FX rate for the purchase quarter; the historical audit's report-FX estimate is shown separately for comparison.",
        "",
        "## Asset Candidates",
        "",
        "| Date | Period | Recipient | Number | Currency | Original | Gross basis EUR | VAT-base basis EUR | Basis source |",
        "|---|---|---|---|---:|---:|---:|---:|---|",
    ]
    for row in assets:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["date"],
                    row["purchase_period"],
                    row["recipient"],
                    row["number"],
                    row["currency"],
                    _fmt(row["amount_original"]),
                    _fmt(row["gross_basis_eur"]),
                    _fmt(row["base_basis_eur"]),
                    row["basis_source"],
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Quarter Comparison",
            "",
            "| Period | Target residual before amort. | Fixed gross amort. YTD | Residual after fixed gross | Fixed base amort. YTD | Residual after fixed base | History report-FX amort. YTD |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in quarters:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    _fmt(row["no_provision_gross_residual"]),
                    _fmt(row["asset_amortization_gross_ytd_fixed_purchase_fx"]),
                    _fmt(row["residual_after_fixed_gross_amortization"]),
                    _fmt(row["asset_amortization_base_ytd_fixed_purchase_fx"]),
                    _fmt(row["residual_after_fixed_base_amortization"]),
                    _fmt(row["history_asset_amortization_gross_ytd_report_fx"]),
                ]
            )
            + " |"
        )
    if scenarios:
        lines.extend(
            [
                "",
                "## Annual Scenario Sweep",
                "",
                "These scenarios compare candidate amortization schedules with Modelo 100 line `0208`.",
                "`exclude_2023_usd_low_value_or_subscription` removes GitHub and LINQPad from the capitalized-asset scenario because filed 2023 `0208 = 0.00` and those rows look like service or low-value software purchases.",
                "",
                "| Year | Source | Scenario | Basis | Rate | M100 0208 | Estimate | Estimate - M100 |",
                "|---|---|---|---|---:|---:|---:|---:|",
            ]
        )
        for row in scenarios:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["year"],
                        row["status"],
                        row["scenario"],
                        row["basis"],
                        row["rate"],
                        _fmt(row["m100_amortization_0208"]),
                        _fmt(row["estimate_ytd"]),
                        _fmt(row["estimate_minus_m100_0208"]),
                    ]
                )
                + " |"
            )
        lines.extend(
            [
                "",
                "Closest annual scenario by absolute difference:",
                "",
                "| Year | Scenario | Basis | Rate | Estimate - M100 |",
                "|---|---|---|---:|---:|",
            ]
        )
        for row in _closest_scenarios(scenarios):
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["year"],
                        row["scenario"],
                        row["basis"],
                        row["rate"],
                        _fmt(row["estimate_minus_m100_0208"]),
                    ]
                )
                + " |"
            )
    if candidate_quarters:
        lines.extend(
            [
                "",
                "## Candidate Quarterly Reconciliation",
                "",
                f"Scenario: `{CANDIDATE_QUARTER_SCENARIO}`.",
                "Positive `model - target` values mean the candidate model is above the submitted quarter and row exclusions/netting are needed; negative values mean the submitted quarter is above the candidate model.",
                "",
                "| Period | Target delta | Raw non-asset delta | Candidate amort. delta | Model - target | Nearest excluded subset | Subset error | Interpretation |",
                "|---|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in candidate_quarters:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["period"],
                        _fmt(row["target_casilla_02_delta"]),
                        _fmt(row["raw_non_asset_gross_delta"]),
                        _fmt(row["candidate_amortization_delta"]),
                        _fmt(row["candidate_model_minus_target_delta"]),
                        _fmt(row["nearest_excluded_subset_eur"]),
                        _fmt(row["nearest_excluded_subset_error_eur"]),
                        row["interpretation"],
                    ]
                )
                + " |"
            )
        lines.append("")
        lines.append("Nearest subset row details are written to the candidate quarterly reconciliation CSV.")
    lines.extend(
        [
            "",
            "Interpretation:",
            "",
            "- Positive residuals after subtracting raw non-asset expenses can be consistent with amortization, but this table does not prove Xolo's exact schedule.",
            "- Negative residuals point to row exclusion, netting, VAT/base treatment, or later annual true-up rather than missing asset amortization alone.",
            "- The fixed-purchase-FX columns avoid revaluing old USD assets with each later quarter's derived income FX.",
            "- The scenario sweep is a candidate reconciliation against annual Modelo 100 `0208`; it is not confirmed Xolo tax treatment.",
            "- Candidate quarterly reconciliation uses the closest annual scenario only as a diagnostic lens; exact quarterly treatment still requires Xolo's submitted per-row register.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _load_history_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _rates_by_period(history_rows: list[dict[str, str]]) -> dict[tuple[int, int], Decimal]:
    rates: dict[tuple[int, int], Decimal] = {}
    for row in history_rows:
        raw_rate = row.get("derived_income_usd_fx") or ""
        if not raw_rate:
            continue
        rates[(int(row["year"]), int(row["quarter"]))] = Decimal(raw_rate)
    return rates


def _asset_row(row: RawXoloExpense, rates_by_period: dict[tuple[int, int], Decimal]) -> dict[str, str]:
    period = _period_for_date(row.date)
    gross_basis, gross_source = _basis_eur(row, rates_by_period, use_base=False)
    base_basis, base_source = _basis_eur(row, rates_by_period, use_base=True)
    basis_source = gross_source if gross_source == base_source else f"gross: {gross_source}; base: {base_source}"
    return {
        "date": row.date.isoformat(),
        "purchase_period": f"{period[0]}-Q{period[1]}",
        "recipient": row.recipient,
        "type": row.expense_type,
        "number": row.number,
        "currency": row.currency,
        "amount_original": _money(row.amount_original),
        "subtotal_amount": _money(row.subtotal_amount),
        "gross_basis_eur": _money(gross_basis),
        "base_basis_eur": _money(base_basis),
        "annual_rate": str(ANNUAL_AMORTIZATION_RATE),
        "basis_source": basis_source,
    }


def _quarter_rows(history_rows: list[dict[str, str]], assets: list[dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    previous_gross_by_year: dict[int, Decimal] = {}
    previous_base_by_year: dict[int, Decimal] = {}
    for history in history_rows:
        year = int(history["year"])
        quarter = int(history["quarter"])
        end = quarter_end(year, quarter)
        gross_ytd = _amortization_ytd(assets, end, "gross_basis_eur")
        base_ytd = _amortization_ytd(assets, end, "base_basis_eur")
        gross_delta = cents(gross_ytd - previous_gross_by_year.get(year, Decimal("0.00")))
        base_delta = cents(base_ytd - previous_base_by_year.get(year, Decimal("0.00")))
        previous_gross_by_year[year] = gross_ytd
        previous_base_by_year[year] = base_ytd
        residual = parse_amount(history["no_provision_gross_residual"])
        rows.append(
            {
                "year": str(year),
                "quarter": str(quarter),
                "period": f"{year}-Q{quarter}",
                "report": history["report"],
                "no_provision_gross_residual": _money(residual),
                "asset_amortization_gross_ytd_fixed_purchase_fx": _money(gross_ytd),
                "asset_amortization_gross_delta_fixed_purchase_fx": _money(gross_delta),
                "residual_after_fixed_gross_amortization": _money(residual - gross_ytd),
                "asset_amortization_base_ytd_fixed_purchase_fx": _money(base_ytd),
                "asset_amortization_base_delta_fixed_purchase_fx": _money(base_delta),
                "residual_after_fixed_base_amortization": _money(residual - base_ytd),
                "history_asset_amortization_gross_ytd_report_fx": history.get("estimated_asset_amortization_gross_ytd", "0.00"),
                "history_asset_amortization_base_ytd_report_fx": history.get("estimated_asset_amortization_base_ytd", "0.00"),
            }
        )
    return rows


def _basis_eur(
    row: RawXoloExpense,
    rates_by_period: dict[tuple[int, int], Decimal],
    *,
    use_base: bool,
) -> tuple[Decimal | None, str]:
    amount = row.subtotal_amount if use_base and row.subtotal_amount is not None else row.amount_original
    if row.currency == "EUR":
        return amount, "EUR source amount"
    if row.currency == "USD":
        period = _period_for_date(row.date)
        rate = rates_by_period.get(period)
        if rate is None:
            return None, f"missing purchase-period FX for {period[0]}-Q{period[1]}"
        return cents(amount * rate), f"USD converted at purchase-quarter derived_income_usd_fx={rate}"
    return None, f"unsupported currency {row.currency}"


def _amortization_ytd(
    assets: list[dict[str, str]],
    end: date,
    key: str,
    rate: Decimal = ANNUAL_AMORTIZATION_RATE,
) -> Decimal:
    total = Decimal("0.00")
    for asset in assets:
        purchase_date = date.fromisoformat(asset["date"])
        amount = parse_amount(asset[key]) if asset.get(key) else Decimal("0.00")
        total += _straight_line_ytd(amount, purchase_date, end, rate)
    return cents(total)


def _straight_line_ytd(amount: Decimal, purchase_date: date, period_end: date, rate: Decimal) -> Decimal:
    if purchase_date > period_end:
        return Decimal("0.00")
    year_start = date(period_end.year, 1, 1)
    active_start = max(purchase_date, year_start)
    if active_start > period_end:
        return Decimal("0.00")
    days = Decimal(str((period_end - active_start).days + 1))
    annual = amount * rate
    return cents(annual * days / Decimal("365"))


def _asset_filters():
    return (
        ("all_candidates", lambda asset: True),
        ("exclude_2023_usd_low_value_or_subscription", lambda asset: not _is_2023_usd_low_value_or_subscription(asset)),
    )


def _is_2023_usd_low_value_or_subscription(asset: dict[str, str]) -> bool:
    if not asset["date"].startswith("2023-") or asset["currency"] != "USD":
        return False
    text = f"{asset['recipient']} {asset['number']}".lower()
    if "github" in text or "linqpad" in text:
        return True
    gross = parse_amount(asset["gross_basis_eur"]) if asset.get("gross_basis_eur") else Decimal("0.00")
    return gross < Decimal("300.00")


def _asset_names(assets: list[dict[str, str]]) -> str:
    return "; ".join(f"{asset['date']} {asset['recipient']} {asset['number']}" for asset in assets)


def _closest_scenarios(scenarios: list[dict[str, str]]) -> list[dict[str, str]]:
    by_year: dict[str, list[dict[str, str]]] = {}
    for row in scenarios:
        by_year.setdefault(row["year"], []).append(row)
    return [
        min(rows, key=lambda row: abs(parse_amount(row["estimate_minus_m100_0208"])))
        for _, rows in sorted(by_year.items())
    ]


def _quarter_candidate_interpretation(model_minus_target: Decimal, ytd_residual_after_candidate: Decimal) -> str:
    if model_minus_target > Decimal("20.00"):
        return "candidate model above target; look for excluded/netted rows"
    if model_minus_target < Decimal("-20.00"):
        return "target above candidate model; look for catch-up/reclassification"
    if abs(ytd_residual_after_candidate) > Decimal("20.00"):
        return "quarter near match but YTD residual remains"
    return "candidate quarter near match"


def _period_for_date(value: date) -> tuple[int, int]:
    return value.year, ((value.month - 1) // 3) + 1


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _money(value: Decimal | None) -> str:
    return "" if value is None else f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return "" if value == "" else format_es(Decimal(value))
