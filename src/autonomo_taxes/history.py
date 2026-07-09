from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
import re

from .modelo130 import (
    calculate_modelo130,
    extract_modelo130_values,
    previous_positive_payments_with_warnings,
)
from .money import cents, format_es, parse_amount
from .parsers import (
    LedgerEntry,
    derive_single_currency_rate,
    in_ytd,
    quarter_end,
    scan_expense_dir,
    scan_income_dir,
)
from .reports import _deductible_before_from_total


@dataclass(frozen=True)
class Modelo130Report:
    year: int
    quarter: int
    path: Path


@dataclass(frozen=True)
class RawXoloExpense:
    date: date
    recipient: str
    expense_type: str
    number: str
    amount_original: Decimal
    currency: str
    subtotal_amount: Decimal | None

    @property
    def is_asset_like(self) -> bool:
        text = f"{self.recipient} {self.expense_type} {self.number}".lower()
        if "computer hardware" in text:
            return True
        return any(
            marker in text
            for marker in (
                "macbook",
                "media markt",
                "faciletea",
                "rosselli",
                "apple retail",
                "apple retal",
            )
        )


@dataclass(frozen=True)
class ExclusionSubset:
    total: Decimal
    error: Decimal
    rows: tuple[tuple[RawXoloExpense, Decimal], ...]


def run_history_audit(
    xolo_root: Path,
    xolo_raw_csv: Path | None,
) -> list[dict[str, str]]:
    tax_report_dir = xolo_root / "TAX_REPORT"
    reports = find_modelo130_reports(tax_report_dir)
    income_entries, income_manual = scan_income_dir(xolo_root / "INVOICE")
    expense_entries, expense_manual = scan_expense_dir(xolo_root / "EXPENSE")
    raw_expenses = load_raw_xolo_expenses(xolo_raw_csv) if xolo_raw_csv else []

    rows: list[dict[str, str]] = []
    previous_target_02_by_year: dict[int, Decimal] = {}
    for report in reports:
        target = extract_modelo130_values(report.path, report.year, report.quarter)
        previous_target_02 = previous_target_02_by_year.get(report.year, Decimal("0.00"))
        target_02_delta = cents(target["02"] - previous_target_02)
        previous_target_02_by_year[report.year] = target["02"]
        derived_fx = derive_single_currency_rate(
            income_entries,
            target["01"],
            "USD",
            report.year,
            report.quarter,
        )
        income_ytd = cents(
            sum(
                _entry_amount_eur(entry, derived_fx)
                for entry in income_entries
                if in_ytd(entry.date, report.year, report.quarter)
            )
        )
        local_expense_before_5 = cents(
            sum(
                (entry.deductible_eur or Decimal("0.00"))
                for entry in expense_entries
                if in_ytd(entry.date, report.year, report.quarter)
            )
        )
        local_asset_review = cents(
            sum(
                (entry.amount_eur or entry.amount_original or Decimal("0.00"))
                for entry in expense_manual
                if entry.category == "asset_review" and _manual_relevant_to_period(entry, report.year, report.quarter)
            )
        )
        previous, _ = previous_positive_payments_with_warnings(tax_report_dir, report.year, report.quarter)
        calculated = calculate_modelo130(
            income_ytd,
            local_expense_before_5,
            previous,
            minoracion=target["13"],
            include_difficult_expenses=False,
        )
        raw = _raw_ytd_totals(raw_expenses, report.year, report.quarter, derived_fx)
        quarter_raw = _raw_quarter_totals(raw_expenses, report.year, report.quarter, derived_fx)
        quarter_gap_to_raw_non_asset = cents(quarter_raw["non_asset_gross_eur"] - target_02_delta)
        excluded_subset = (
            _nearest_excluded_subset(raw_expenses, report.year, report.quarter, derived_fx, quarter_gap_to_raw_non_asset)
            if quarter_gap_to_raw_non_asset > Decimal("20.00")
            else None
        )
        provision_before_5 = _deductible_before_from_total(
            target["01"],
            target["02"],
            rate=_difficult_expenses_rate_for_year(report.year),
        )
        provision_difficult = cents(target["02"] - provision_before_5)
        no_provision_gross_model = raw["non_asset_gross_eur"]
        no_provision_base_model = raw["non_asset_base_eur"]
        no_provision_gross_residual = cents(target["02"] - no_provision_gross_model)
        no_provision_base_residual = cents(target["02"] - no_provision_base_model)
        provision_residual = cents(provision_before_5 - no_provision_gross_model)
        after_amortization_residual = cents(
            no_provision_gross_residual - raw["asset_amortization_estimate_gross"]
        )
        best_fit = _best_fit(no_provision_gross_residual, no_provision_base_residual, provision_residual)
        manual_count = sum(
            1
            for entry in income_manual + expense_manual
            if _manual_relevant_to_period(entry, report.year, report.quarter)
        )

        rows.append(
            {
                "year": str(report.year),
                "quarter": str(report.quarter),
                "report": report.path.name,
                "target_casilla_01": _money(target["01"]),
                "calculated_income_ytd": _money(income_ytd),
                "target_casilla_02": _money(target["02"]),
                "target_casilla_02_delta": _money(target_02_delta),
                "provision_before_5_reverse": _money(provision_before_5),
                "provision_difficult_reverse": _money(provision_difficult),
                "no_provision_gross_model": _money(no_provision_gross_model),
                "no_provision_base_model": _money(no_provision_base_model),
                "no_provision_gross_residual": _money(no_provision_gross_residual),
                "no_provision_base_residual": _money(no_provision_base_residual),
                "provision_model_residual": _money(provision_residual),
                "best_fit_model": best_fit,
                "local_parsed_before_5": _money(local_expense_before_5),
                "local_gap_to_target02": _money(target["02"] - local_expense_before_5),
                "target_casilla_07": _money(target["07"]),
                "target_casilla_13": _money(target["13"]),
                "target_casilla_19": _money(target["19"]),
                "calculated_casilla_19": _money(calculated.casilla_19),
                "casilla_19_diff": _money(calculated.casilla_19 - target["19"]),
                "raw_xolo_gross_eur_ytd": _money(raw["gross_eur"]),
                "raw_xolo_base_eur_ytd": _money(raw["base_eur"]),
                "raw_xolo_usd_at_income_fx_ytd": _money(raw["usd_at_fx"]),
                "raw_asset_like_gross_eur_ytd": _money(raw["asset_gross_eur"]),
                "raw_asset_like_base_eur_ytd": _money(raw["asset_base_eur"]),
                "raw_non_asset_gross_eur_ytd": _money(raw["non_asset_gross_eur"]),
                "raw_non_asset_base_eur_ytd": _money(raw["non_asset_base_eur"]),
                "raw_quarter_gross_eur": _money(quarter_raw["gross_eur"] + quarter_raw["usd_at_fx"]),
                "raw_quarter_base_eur": _money(quarter_raw["base_eur"] + quarter_raw["usd_at_fx"]),
                "raw_quarter_asset_gross_eur": _money(quarter_raw["asset_gross_eur"]),
                "raw_quarter_non_asset_gross_eur": _money(quarter_raw["non_asset_gross_eur"]),
                "raw_quarter_non_asset_base_eur": _money(quarter_raw["non_asset_base_eur"]),
                "quarter_gap_to_raw_non_asset_gross": _money(quarter_gap_to_raw_non_asset),
                "nearest_excluded_subset_eur": _money(excluded_subset.total if excluded_subset else Decimal("0.00")),
                "nearest_excluded_subset_error_eur": _money(excluded_subset.error if excluded_subset else Decimal("0.00")),
                "nearest_excluded_subset_rows": _format_subset_rows(excluded_subset),
                "estimated_asset_amortization_gross_ytd": _money(raw["asset_amortization_estimate_gross"]),
                "estimated_asset_amortization_base_ytd": _money(raw["asset_amortization_estimate_base"]),
                "residual_after_estimated_amortization": _money(after_amortization_residual),
                "reconciliation_signal": _reconciliation_signal(no_provision_gross_residual),
                "local_asset_review_eur_ytd": _money(local_asset_review),
                "manual_review_items": str(manual_count),
                "derived_income_usd_fx": "" if derived_fx is None else str(derived_fx),
            }
        )
    return rows


def find_modelo130_reports(tax_report_dir: Path) -> list[Modelo130Report]:
    reports: list[Modelo130Report] = []
    for path in tax_report_dir.glob("*.pdf"):
        match = re.search(r"(?:M130|MOD 130|Mod 130)\s+([1-4])T\s+(20\d{2})", path.name, re.I)
        if not match:
            continue
        reports.append(Modelo130Report(year=int(match.group(2)), quarter=int(match.group(1)), path=path))
    return sorted(reports, key=lambda item: (item.year, item.quarter, item.path.name.lower()))


def load_raw_xolo_expenses(path: Path) -> list[RawXoloExpense]:
    rows: list[RawXoloExpense] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_date = (row.get("date") or "")[:10]
            if not raw_date:
                continue
            amount = _optional_amount(row.get("amount_original"))
            if amount is None:
                continue
            rows.append(
                RawXoloExpense(
                    date=date.fromisoformat(raw_date),
                    recipient=row.get("recipient") or "",
                    expense_type=row.get("type") or "",
                    number=row.get("number") or "",
                    amount_original=amount,
                    currency=(row.get("currency") or "").upper(),
                    subtotal_amount=_optional_amount(row.get("subtotal_amount")),
                )
            )
    return rows


def write_history_audit_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_history_audit_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 130 Historical Audit",
        "",
        "This report compares submitted Modelo 130 casillas against the local parser and the raw Xolo expense table.",
        "`best_fit_model` compares forward models. It does not reverse-solve a hidden expense row to make a hypothesis fit.",
        "Asset-like rows include Xolo rows marked `Computer hardware & software` in any currency plus known hardware suppliers.",
        "",
        "| Period | Signal | Target 02 delta | Raw non-asset delta | Quarter gap | Nearest excluded subset | Subset error | Asset delta | YTD residual gross | Target 19 | Diff 19 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        period = f"{row['year']}-Q{row['quarter']}"
        lines.append(
            "| "
            + " | ".join(
                [
                    period,
                    row["reconciliation_signal"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["raw_quarter_non_asset_gross_eur"]),
                    _fmt(row["quarter_gap_to_raw_non_asset_gross"]),
                    _fmt(row["nearest_excluded_subset_eur"]),
                    _fmt(row["nearest_excluded_subset_error_eur"]),
                    _fmt(row["raw_quarter_asset_gross_eur"]),
                    _fmt(row["no_provision_gross_residual"]),
                    _fmt(row["target_casilla_19"]),
                    _fmt(row["casilla_19_diff"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _raw_quarter_totals(
    rows: list[RawXoloExpense],
    year: int,
    quarter: int,
    usd_fx: Decimal | None,
) -> dict[str, Decimal]:
    start = _quarter_start(year, quarter)
    end = quarter_end(year, quarter)
    return _raw_expense_totals(rows, start, end, usd_fx)


def _nearest_excluded_subset(
    rows: list[RawXoloExpense],
    year: int,
    quarter: int,
    usd_fx: Decimal | None,
    target: Decimal,
) -> ExclusionSubset | None:
    if target <= 0:
        return None
    candidates: list[tuple[Decimal, RawXoloExpense]] = []
    start = _quarter_start(year, quarter)
    end = quarter_end(year, quarter)
    for row in rows:
        if row.is_asset_like or not (start <= row.date <= end):
            continue
        gross = _row_gross_eur(row, usd_fx)
        if gross is not None and gross != 0:
            candidates.append((gross, row))
    if not candidates or len(candidates) > 28:
        return None
    split = len(candidates) // 2
    left = candidates[:split]
    right = candidates[split:]
    left_sums = _subset_sums(left)
    right_sums = sorted(_subset_sums(right), key=lambda item: item[0])
    right_values = [item[0] for item in right_sums]

    import bisect

    best: tuple[Decimal, Decimal, int, int] | None = None
    for left_total, left_mask in left_sums:
        needed = target - left_total
        position = bisect.bisect_left(right_values, needed)
        for index in range(max(0, position - 2), min(len(right_sums), position + 3)):
            right_total, right_mask = right_sums[index]
            total = cents(left_total + right_total)
            error = abs(cents(total - target))
            candidate = (error, total, left_mask, right_mask)
            if best is None or candidate < best:
                best = candidate
    if best is None:
        return None
    error, total, left_mask, right_mask = best
    selected: list[tuple[RawXoloExpense, Decimal]] = []
    for index, (amount, row) in enumerate(left):
        if left_mask & (1 << index):
            selected.append((row, amount))
    for index, (amount, row) in enumerate(right):
        if right_mask & (1 << index):
            selected.append((row, amount))
    selected.sort(key=lambda item: (item[0].date, item[0].recipient, item[0].number))
    return ExclusionSubset(total=total, error=error, rows=tuple(selected))


def nearest_excluded_subset(
    rows: list[RawXoloExpense],
    year: int,
    quarter: int,
    usd_fx: Decimal | None,
    target: Decimal,
) -> ExclusionSubset | None:
    return _nearest_excluded_subset(rows, year, quarter, usd_fx, target)


def _subset_sums(candidates: list[tuple[Decimal, RawXoloExpense]]) -> list[tuple[Decimal, int]]:
    output: list[tuple[Decimal, int]] = []
    for mask in range(1 << len(candidates)):
        total = sum((candidates[index][0] for index in range(len(candidates)) if mask & (1 << index)), Decimal("0.00"))
        output.append((cents(total), mask))
    return output


def _format_subset_rows(subset: ExclusionSubset | None) -> str:
    if subset is None:
        return ""
    return "; ".join(
        f"{row.date.isoformat()} {row.number or row.recipient} {amount:.2f}"
        for row, amount in subset.rows
    )


def format_subset_rows(subset: ExclusionSubset | None) -> str:
    return _format_subset_rows(subset)


def _raw_ytd_totals(
    rows: list[RawXoloExpense],
    year: int,
    quarter: int,
    usd_fx: Decimal | None,
) -> dict[str, Decimal]:
    start = date(year, 1, 1)
    end = quarter_end(year, quarter)
    totals = _raw_expense_totals(rows, start, end, usd_fx)
    for row in rows:
        if not row.is_asset_like or row.date > end:
            continue
        gross = _row_gross_eur(row, usd_fx)
        base = _row_base_eur(row, usd_fx)
        if gross is not None:
            totals["asset_amortization_estimate_gross"] += _straight_line_ytd(gross, row.date, end)
        if base is not None:
            totals["asset_amortization_estimate_base"] += _straight_line_ytd(base, row.date, end)
    return {key: cents(value) for key, value in totals.items()}


def _raw_expense_totals(
    rows: list[RawXoloExpense],
    start: date,
    end: date,
    usd_fx: Decimal | None,
) -> dict[str, Decimal]:
    totals = {
        "gross_eur": Decimal("0.00"),
        "base_eur": Decimal("0.00"),
        "usd_at_fx": Decimal("0.00"),
        "asset_gross_eur": Decimal("0.00"),
        "asset_base_eur": Decimal("0.00"),
        "asset_amortization_estimate_gross": Decimal("0.00"),
        "asset_amortization_estimate_base": Decimal("0.00"),
    }
    for row in rows:
        if not (start <= row.date <= end):
            continue
        gross = _row_gross_eur(row, usd_fx)
        base = _row_base_eur(row, usd_fx)
        if gross is None or base is None:
            continue
        if row.currency == "USD":
            totals["usd_at_fx"] += gross
        else:
            totals["gross_eur"] += gross
            totals["base_eur"] += base
        if row.is_asset_like:
            totals["asset_gross_eur"] += gross
            totals["asset_base_eur"] += base
    totals["non_asset_gross_eur"] = totals["gross_eur"] + totals["usd_at_fx"] - totals["asset_gross_eur"]
    totals["non_asset_base_eur"] = totals["base_eur"] + totals["usd_at_fx"] - totals["asset_base_eur"]
    return {key: cents(value) for key, value in totals.items()}


def _row_gross_eur(row: RawXoloExpense, usd_fx: Decimal | None) -> Decimal | None:
    if row.currency == "EUR":
        return row.amount_original
    if row.currency == "USD" and usd_fx is not None:
        return cents(row.amount_original * usd_fx)
    return None


def _row_base_eur(row: RawXoloExpense, usd_fx: Decimal | None) -> Decimal | None:
    if row.currency == "EUR":
        return row.subtotal_amount if row.subtotal_amount is not None else row.amount_original
    if row.currency == "USD" and usd_fx is not None:
        base = row.subtotal_amount if row.subtotal_amount is not None else row.amount_original
        return cents(base * usd_fx)
    return None


def _entry_amount_eur(entry: LedgerEntry, usd_fx: Decimal | None) -> Decimal:
    if entry.amount_eur is not None:
        return entry.amount_eur
    if entry.currency == "USD" and usd_fx is not None and entry.amount_original is not None:
        return cents(entry.amount_original * usd_fx)
    return Decimal("0.00")


def _manual_relevant_to_period(entry: LedgerEntry, year: int, quarter: int) -> bool:
    if in_ytd(entry.date, year, quarter):
        return True
    return str(year) in Path(entry.document).name


def _optional_amount(value: str | None) -> Decimal | None:
    if not value:
        return None
    return parse_amount(value)


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return format_es(Decimal(value))


def _difficult_expenses_rate_for_year(year: int) -> Decimal:
    return Decimal("0.07") if year == 2023 else Decimal("0.05")


def _best_fit(*residuals: Decimal) -> str:
    labels = ("no_provision_gross", "no_provision_base", "provision_reverse")
    return labels[min(range(len(residuals)), key=lambda index: abs(residuals[index]))]


def _reconciliation_signal(residual: Decimal) -> str:
    if abs(residual) <= Decimal("1.00"):
        return "raw_non_asset_near_match"
    if residual > 0:
        return "target_above_raw_non_asset"
    return "target_below_raw_non_asset"


def _quarter_start(year: int, quarter: int) -> date:
    return date(year, 1 + (quarter - 1) * 3, 1)


def _straight_line_ytd(amount: Decimal, purchase_date: date, period_end: date) -> Decimal:
    if purchase_date > period_end:
        return Decimal("0.00")
    year_start = date(period_end.year, 1, 1)
    active_start = max(purchase_date, year_start)
    if active_start > period_end:
        return Decimal("0.00")
    days = Decimal(str((period_end - active_start).days + 1))
    annual = amount * Decimal("0.26")
    return cents(annual * days / Decimal("365"))
