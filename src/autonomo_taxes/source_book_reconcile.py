from __future__ import annotations

import csv
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
import re

from .money import cents, format_es, parse_amount
from .parsers import parse_any_date


SOURCE_BOOK_RECONCILIATION_FIELDS = [
    "period",
    "status",
    "target_casilla_02_ytd",
    "target_casilla_02_delta",
    "imported_expense_delta",
    "imported_amortization_delta",
    "imported_total_delta",
    "imported_minus_target_delta",
    "annual_adjustment_delta",
    "adjusted_imported_total_delta",
    "adjusted_minus_target_delta",
    "tieout_casilla02_ytd",
    "tieout_casilla02_ytd_minus_target",
    "tieout_casilla02_delta",
    "tieout_delta_minus_target",
    "expense_row_count",
    "asset_row_count",
    "tieout_row_count",
    "skipped_row_count",
    "amount_parse_error_count",
    "unassigned_row_count",
    "source_files",
    "notes",
]

EXPENSE_BOOK_TYPES = {"gastos_book", "compras_gastos_book"}
ASSET_BOOK_TYPES = {"bienes_inversion_book", "bienes_inversion_or_asset_schedule"}
TIEOUT_BOOK_TYPES = {"modelo130_source_book_tieout"}
QUARTER_PERIOD_RE = re.compile(r"^(20\d{2})-Q([1-4])$", re.IGNORECASE)
ANNUAL_ASSET_MARKER_RE = re.compile(r"^(0A|A|ANUAL|ANNUAL)$", re.IGNORECASE)


def build_source_book_reconciliation(
    history_audit_csv: Path,
    source_book_rows_csv: Path,
    annual_comparison_csv: Path | None = None,
) -> list[dict[str, str]]:
    histories = _history_rows(history_audit_csv)
    source_rows = _load_rows(source_book_rows_csv)
    rows_by_period, tieout_by_period, unassigned_rows, year_issue_notes = _classified_source_rows(source_rows)
    annual_adjustments = _annual_q4_adjustments(annual_comparison_csv)
    first_period = min(histories, key=_period_sort_key) if histories else ""

    output: list[dict[str, str]] = []
    for period in sorted(histories, key=_period_sort_key):
        history = histories[period]
        period_year = _period_year(period)
        period_unassigned_rows = [
            row
            for row in unassigned_rows
            if _unassigned_scope(row) in {period, period_year}
            or (_unassigned_scope(row) == "" and period == first_period)
        ]
        target_ytd = parse_amount(history["target_casilla_02"])
        target_delta = parse_amount(history["target_casilla_02_delta"])
        period_rows = rows_by_period.get(period, [])
        tieout_rows = tieout_by_period.get(period, [])
        period_issue_notes = year_issue_notes.get(period_year, [])
        expense_rows = [row for row in period_rows if row["source_book_type"] in EXPENSE_BOOK_TYPES]
        asset_rows = [row for row in period_rows if row["source_book_type"] in ASSET_BOOK_TYPES]
        expense_total, expense_errors = _sum_row_amounts(expense_rows, "irpf_deductible_eur")
        asset_total, asset_errors = _sum_row_amounts(asset_rows, "amortization_amount_eur")
        amount_errors = [*expense_errors, *asset_errors]
        tieout_ytd, tieout_errors = _single_tieout_amount(tieout_rows, "casilla02_ytd")
        amount_errors.extend(tieout_errors)
        tieout_delta, tieout_delta_errors = _tieout_delta(period, tieout_by_period, histories)
        amount_errors.extend(tieout_delta_errors)
        amount_errors = _dedupe(amount_errors)
        amount_error_count = len(amount_errors)
        imported_total = cents(expense_total + asset_total)
        imported_diff = cents(imported_total - target_delta)
        annual_adjustment = _usable_annual_adjustment(period, imported_diff, annual_adjustments)
        adjusted_imported_total = cents(imported_total - annual_adjustment)
        adjusted_diff = cents(adjusted_imported_total - target_delta)
        tieout_ytd_diff = cents(tieout_ytd - target_ytd) if tieout_ytd is not None else None
        tieout_delta_diff = cents(tieout_delta - target_delta) if tieout_delta is not None else None
        skipped_count = sum(1 for row in period_rows if row.get("import_status", "") != "imported")
        row_counts = Counter(row["source_book_type"] for row in period_rows)
        output.append(
            {
                "period": period,
                "status": _status(
                    period_rows=period_rows,
                    skipped_count=skipped_count,
                    amount_error_count=amount_error_count,
                    unassigned_count=len(period_unassigned_rows),
                    period_issue_count=len(period_issue_notes),
                    imported_diff=imported_diff,
                    annual_adjustment=annual_adjustment,
                    adjusted_diff=adjusted_diff,
                    tieout_ytd_diff=tieout_ytd_diff,
                    tieout_delta_diff=tieout_delta_diff,
                ),
                "target_casilla_02_ytd": _money(target_ytd),
                "target_casilla_02_delta": _money(target_delta),
                "imported_expense_delta": _money(expense_total),
                "imported_amortization_delta": _money(asset_total),
                "imported_total_delta": _money(imported_total),
                "imported_minus_target_delta": _money(imported_diff),
                "annual_adjustment_delta": _money(annual_adjustment),
                "adjusted_imported_total_delta": _money(adjusted_imported_total),
                "adjusted_minus_target_delta": _money(adjusted_diff),
                "tieout_casilla02_ytd": _optional_money(tieout_ytd),
                "tieout_casilla02_ytd_minus_target": _optional_money(tieout_ytd_diff),
                "tieout_casilla02_delta": _optional_money(tieout_delta),
                "tieout_delta_minus_target": _optional_money(tieout_delta_diff),
                "expense_row_count": str(sum(row_counts[source_type] for source_type in EXPENSE_BOOK_TYPES)),
                "asset_row_count": str(sum(row_counts[source_type] for source_type in ASSET_BOOK_TYPES)),
                "tieout_row_count": str(sum(row_counts[source_type] for source_type in TIEOUT_BOOK_TYPES)),
                "skipped_row_count": str(skipped_count),
                "amount_parse_error_count": str(amount_error_count),
                "unassigned_row_count": str(len(period_unassigned_rows)),
                "source_files": "; ".join(sorted({row.get("source_file", "") for row in period_rows if row.get("source_file", "")})),
                "notes": _notes(
                    period_rows,
                    skipped_count,
                    amount_errors,
                    period_unassigned_rows,
                    period_issue_notes,
                    tieout_ytd,
                    tieout_delta,
                    annual_adjustment,
                    adjusted_diff,
                ),
            }
        )
    return output


def write_source_book_reconciliation_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_BOOK_RECONCILIATION_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_source_book_reconciliation_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter(row["status"] for row in rows)
    lines = [
        "# Xolo Source-Book Quarter Reconciliation",
        "",
        "This report compares imported Xolo source-book rows against filed Modelo 130 `casilla 02` quarter movements.",
        "It is not a tax-treatment decision. Quarters close only after imported official-register rows match the filed values; Modelo 130 tie-outs are used when Xolo provides them.",
        "",
        "## Summary",
        "",
    ]
    for status in sorted(counts):
        lines.append(f"- `{status}`: `{counts[status]}`.")
    lines.extend(
        [
            "",
            "## Quarters",
            "",
            "| Period | Status | Target 02 delta | Imported rows delta | Annual adj. | Adjusted delta | Adjusted diff | Tie-out 02 delta | Rows | Notes |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in rows:
        row_count = int(row["expense_row_count"]) + int(row["asset_row_count"]) + int(row["tieout_row_count"])
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["status"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["imported_total_delta"]),
                    _fmt(row["annual_adjustment_delta"]),
                    _fmt(row["adjusted_imported_total_delta"]),
                    _fmt(row["adjusted_minus_target_delta"]),
                    _fmt(row["tieout_casilla02_delta"]),
                    str(row_count),
                    _cell(row["notes"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _history_rows(path: Path) -> dict[str, dict[str, str]]:
    rows = _load_rows(path)
    return {f"{row['year']}-Q{row['quarter']}": row for row in rows}


def _classified_source_rows(
    rows: list[dict[str, str]],
) -> tuple[
    dict[str, list[dict[str, str]]],
    dict[str, list[dict[str, str]]],
    list[dict[str, str]],
    dict[str, list[str]],
]:
    by_period: dict[str, list[dict[str, str]]] = {}
    tieout_by_period: dict[str, list[dict[str, str]]] = {}
    unassigned_rows: list[dict[str, str]] = []
    annual_asset_rows_by_year: dict[str, list[dict[str, str]]] = defaultdict(list)
    quarterly_asset_rows_by_key: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    year_issue_notes: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        source_type = row.get("source_book_type", "")
        if source_type in ASSET_BOOK_TYPES:
            period_kind, period_value, issue_note = _classify_asset_row_period(row)
            if period_kind == "quarter":
                by_period.setdefault(period_value, []).append(row)
                quarterly_asset_rows_by_key[_asset_year_key(row, _period_year(period_value))].append(row)
                continue
            if period_kind == "annual":
                annual_asset_rows_by_year[period_value].append(row)
                continue
            if period_value:
                year_issue_notes[period_value].append(issue_note)
            else:
                unassigned_rows.append(row)
            continue
        period = row.get("period", "").strip()
        if _is_quarter_period(period):
            by_period.setdefault(period, []).append(row)
            if source_type in TIEOUT_BOOK_TYPES:
                tieout_by_period.setdefault(period, []).append(row)
            continue
        unassigned_rows.append(row)
    _append_annual_asset_issues(annual_asset_rows_by_year, quarterly_asset_rows_by_key, year_issue_notes)
    return by_period, tieout_by_period, unassigned_rows, {year: _dedupe(notes) for year, notes in year_issue_notes.items()}


def _unassigned_scope(row: dict[str, str]) -> str:
    for field in ("date", "booking_date"):
        parsed = parse_any_date(row.get(field, ""))
        if parsed is not None:
            return f"{parsed.year}-Q{((parsed.month - 1) // 3) + 1}"
    source_scope = row.get("source_scope", "").strip()
    if re.fullmatch(r"20\d{2}", source_scope):
        return source_scope
    for field in ("period", "amortization_period", "source_file"):
        match = re.search(r"20\d{2}", row.get(field, ""))
        if match:
            return match.group(0)
    return ""


def _classify_asset_row_period(row: dict[str, str]) -> tuple[str, str, str]:
    raw_period = row.get("amortization_period", "").strip()
    if _is_quarter_period(raw_period):
        return "quarter", raw_period, ""
    if not raw_period:
        return "invalid", "", ""
    if _is_annual_asset_marker(raw_period):
        annual_year = _annual_asset_year(row)
        if annual_year:
            return "annual", annual_year, ""
        return (
            "invalid",
            "",
            "Annual asset marker "
            f"{_row_period_marker(row)} cannot be mapped to a single asset/year from source_scope={row.get('source_scope', '')!r}.",
        )
    annual_year = _annual_asset_year(row)
    if annual_year:
        return (
            "invalid",
            annual_year,
            "Unsupported annual asset period "
            f"{_row_period_marker(row)} for {_asset_year_label(row, annual_year)}.",
        )
    return "invalid", "", ""


def _append_annual_asset_issues(
    annual_asset_rows_by_year: dict[str, list[dict[str, str]]],
    quarterly_asset_rows_by_key: dict[tuple[str, str, str, str], list[dict[str, str]]],
    year_issue_notes: dict[str, list[str]],
) -> None:
    quarterly_totals_by_key: dict[tuple[str, str, str, str], Decimal] = {}
    quarterly_error_keys: set[tuple[str, str, str, str]] = set()
    for key, rows in quarterly_asset_rows_by_key.items():
        total, errors = _sum_row_amounts(rows, "amortization_amount_eur")
        if errors:
            quarterly_error_keys.add(key)
            year_issue_notes[key[0]].append(
                "Quarterly amortization rows contain malformed amounts and cannot cross-check annual asset rows: "
                + "; ".join(errors[:5])
            )
            continue
        quarterly_totals_by_key[key] = total
    for year, annual_rows in annual_asset_rows_by_year.items():
        annual_rows_by_key: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
        for row in annual_rows:
            annual_rows_by_key[_asset_year_key(row, year)].append(row)
        for key, key_rows in annual_rows_by_key.items():
            annual_total, annual_errors = _sum_row_amounts(key_rows, "amortization_amount_eur")
            if annual_errors:
                year_issue_notes[year].append(
                    "Annual asset rows contain malformed amortization amounts and cannot be cross-checked: "
                    + "; ".join(annual_errors[:5])
                )
                continue
            label = _asset_year_label(key_rows[0], year)
            if key in quarterly_error_keys:
                year_issue_notes[year].append(
                    f"Annual asset row(s) for {label} cannot be cross-checked because matching quarterly amortization rows have malformed amounts."
                )
                continue
            quarterly_total = quarterly_totals_by_key.get(key)
            if quarterly_total is None:
                year_issue_notes[year].append(
                    f"Annual asset row(s) for {label} use {_row_period_marker(key_rows[0])}, "
                    "but no quarterly amortization row matches this asset/year."
                )
                continue
            if abs(cents(quarterly_total - annual_total)) > Decimal("0.02"):
                year_issue_notes[year].append(
                    f"Annual asset row(s) for {label} total {_money(annual_total)}, "
                    f"but matching quarterly amortization totals {_money(quarterly_total)}."
                )


def _single_tieout_amount(rows: list[dict[str, str]], field: str) -> tuple[Decimal | None, list[str]]:
    amounts: list[Decimal] = []
    errors: list[str] = []
    for row in rows:
        value = row.get(field, "")
        if not value:
            continue
        amount, error = _amount_from_row(row, field)
        if error:
            errors.append(error)
            continue
        if amount is not None:
            amounts.append(amount)
    if not amounts:
        return None, errors
    return cents(amounts[-1]), errors


def _tieout_delta(
    period: str,
    tieout_by_period: dict[str, list[dict[str, str]]],
    histories: dict[str, dict[str, str]],
) -> tuple[Decimal | None, list[str]]:
    current, errors = _single_tieout_amount(tieout_by_period.get(period, []), "casilla02_ytd")
    if current is None:
        return None, errors
    previous_period = _previous_period(period)
    if previous_period and previous_period in tieout_by_period:
        previous, previous_errors = _single_tieout_amount(tieout_by_period.get(previous_period, []), "casilla02_ytd")
        errors.extend(previous_errors)
        if previous_errors:
            return None, errors
        if previous is not None:
            return cents(current - previous), errors
    if previous_period and previous_period in histories:
        return None, errors
    return cents(current), errors


def _previous_period(period: str) -> str:
    year, quarter = period.split("-Q", 1)
    quarter_number = int(quarter)
    if quarter_number <= 1:
        return ""
    return f"{year}-Q{quarter_number - 1}"


def _status(
    *,
    period_rows: list[dict[str, str]],
    skipped_count: int,
    amount_error_count: int,
    unassigned_count: int,
    period_issue_count: int,
    imported_diff: Decimal,
    annual_adjustment: Decimal,
    adjusted_diff: Decimal,
    tieout_ytd_diff: Decimal | None,
    tieout_delta_diff: Decimal | None,
) -> str:
    if unassigned_count or period_issue_count:
        return "import_attention"
    if not period_rows:
        return "no_imported_source_book_rows"
    if skipped_count or amount_error_count:
        return "import_attention"
    imported_matches = abs(imported_diff) <= Decimal("0.02")
    adjusted_matches = annual_adjustment != Decimal("0.00") and abs(adjusted_diff) <= Decimal("0.02")
    tieout_ytd_matches = tieout_ytd_diff is not None and abs(tieout_ytd_diff) <= Decimal("0.02")
    tieout_delta_matches = tieout_delta_diff is not None and abs(tieout_delta_diff) <= Decimal("0.02")
    if imported_matches and tieout_ytd_matches and tieout_delta_matches:
        return "rows_and_tieout_match_target"
    if imported_matches and tieout_ytd_matches and tieout_delta_diff is None:
        return "rows_and_ytd_tieout_match_target_delta_missing"
    if imported_matches and tieout_ytd_diff is None:
        return "rows_match_target_no_tieout"
    if adjusted_matches and tieout_ytd_diff is None:
        return "rows_match_target_after_annual_adjustment_no_tieout"
    if (tieout_ytd_matches or tieout_delta_matches) and not imported_matches:
        return "tieout_matches_rows_mismatch"
    return "source_book_rows_mismatch"


def _notes(
    period_rows: list[dict[str, str]],
    skipped_count: int,
    amount_errors: list[str],
    unassigned_rows: list[dict[str, str]],
    period_issue_notes: list[str],
    tieout_ytd: Decimal | None,
    tieout_delta: Decimal | None,
    annual_adjustment: Decimal,
    adjusted_diff: Decimal,
) -> str:
    if unassigned_rows:
        refs = "; ".join(f"{_row_ref(row)} {_row_period_marker(row)}" for row in unassigned_rows[:5])
        return "Imported source-book row(s) have no valid quarter period and cannot be assigned: " + refs
    if period_issue_notes:
        return period_issue_notes[0]
    if not period_rows:
        return "No imported source-book rows for this quarter."
    if skipped_count:
        return "At least one content-ready source-book file could not be imported cleanly."
    if amount_errors:
        return "Malformed amount cell(s): " + "; ".join(amount_errors[:5])
    if annual_adjustment != Decimal("0.00") and abs(adjusted_diff) <= Decimal("0.02"):
        return (
            "Official annual Modelo 100 comparison explains annual source-book rows above filed Q4 Modelo 130: "
            f"annual_adjustment_delta={_money(annual_adjustment)}; adjusted_diff={_money(adjusted_diff)}."
        )
    if tieout_ytd is None:
        return "No Modelo 130 tie-out row imported for this period."
    if tieout_delta is None:
        return "Tie-out YTD is present, but previous-quarter tie-out is missing so delta cannot be derived."
    return "Imported source-book rows and tie-out rows are available for comparison."


def _sum_row_amounts(rows: list[dict[str, str]], field: str) -> tuple[Decimal, list[str]]:
    total = Decimal("0.00")
    errors: list[str] = []
    for row in rows:
        amount, error = _amount_from_row(row, field)
        if error:
            errors.append(error)
            continue
        if amount is not None:
            total += amount
    return cents(total), errors


def _amount_from_row(row: dict[str, str], field: str) -> tuple[Decimal | None, str]:
    value = row.get(field, "")
    if not value:
        return None, ""
    try:
        return _amount(value), ""
    except ValueError:
        return None, f"{_row_ref(row)} {field}={value!r}"


def _row_ref(row: dict[str, str]) -> str:
    return row.get("source_book_line_id") or row.get("source_file") or row.get("source_book_type", "source row")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _amount(value: str) -> Decimal:
    return parse_amount(value) if value else Decimal("0.00")


def _annual_asset_year(row: dict[str, str]) -> str:
    years = sorted(set(re.findall(r"20\d{2}", row.get("source_scope", ""))))
    if len(years) == 1:
        return years[0]
    return ""


def _asset_year_key(row: dict[str, str], year: str) -> tuple[str, str, str, str]:
    return (
        year,
        _asset_key_part(row.get("asset_id", "")) or _asset_key_part(row.get("source_book_line_id", "")),
        _asset_key_part(row.get("date", "")),
        _asset_key_part(row.get("amortizable_base_eur", "")),
    )


def _asset_key_part(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _asset_year_label(row: dict[str, str], year: str) -> str:
    asset_name = row.get("asset_id") or _row_ref(row)
    return f"{asset_name!r} in {year}"


def _is_quarter_period(period: str) -> bool:
    return bool(QUARTER_PERIOD_RE.match((period or "").strip()))


def _is_annual_asset_marker(period: str) -> bool:
    return bool(ANNUAL_ASSET_MARKER_RE.match((period or "").strip()))


def _row_period_marker(row: dict[str, str]) -> str:
    if row.get("source_book_type") in ASSET_BOOK_TYPES:
        return f"amortization_period={row.get('amortization_period', '')!r}"
    return f"period={row.get('period', '')!r}"


def _annual_q4_adjustments(path: Path | None) -> dict[str, Decimal]:
    if path is None or not path.exists():
        return {}
    adjustments: dict[str, Decimal] = {}
    for row in _load_rows(path):
        year = row.get("year", "").strip()
        amount_text = row.get("annual_minus_m130_q4", "").strip()
        status = row.get("status", "").strip().lower()
        if not status.startswith("filed_"):
            continue
        if not year or not amount_text:
            continue
        try:
            amount = parse_amount(amount_text)
        except ValueError:
            continue
        if amount != Decimal("0.00"):
            adjustments[f"{year}-Q4"] = cents(amount)
    return adjustments


def _usable_annual_adjustment(period: str, imported_diff: Decimal, annual_adjustments: dict[str, Decimal]) -> Decimal:
    annual_adjustment = annual_adjustments.get(period, Decimal("0.00"))
    if annual_adjustment <= Decimal("0.00"):
        return Decimal("0.00")
    if abs(imported_diff) <= Decimal("0.02"):
        return Decimal("0.00")
    if abs(imported_diff - annual_adjustment) <= Decimal("0.02"):
        return annual_adjustment
    return Decimal("0.00")


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _optional_money(value: Decimal | None) -> str:
    return "" if value is None else _money(value)


def _fmt(value: str) -> str:
    return "" if not value else format_es(parse_amount(value))


def _period_sort_key(period: str) -> tuple[int, int]:
    year, quarter = period.split("-Q", 1)
    return int(year), int(quarter)


def _period_year(period: str) -> str:
    return period.split("-", 1)[0]


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
