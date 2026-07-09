from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import re

from pypdf import PdfReader

from .history import RawXoloExpense
from .money import cents, format_es, parse_amount


PERCENTAGE_VALUES = {
    Decimal("0.26"),
    Decimal("0.50"),
    Decimal("1.00"),
    Decimal("1.40"),
    Decimal("1.75"),
    Decimal("2.00"),
    Decimal("4.00"),
    Decimal("5.00"),
    Decimal("5.20"),
    Decimal("7.50"),
    Decimal("10.00"),
    Decimal("21.00"),
}


@dataclass(frozen=True)
class Modelo303Report:
    year: int
    quarter: int
    path: Path


@dataclass(frozen=True)
class Modelo303Values:
    output_base: Decimal
    output_vat: Decimal
    deductible_base: Decimal
    deductible_vat: Decimal
    result: Decimal
    extraction_status: str
    monetary_sequence: tuple[Decimal, ...]

    @property
    def net_local_input_base(self) -> Decimal:
        return cents(self.deductible_base - self.output_base)

    @property
    def net_local_input_vat(self) -> Decimal:
        return cents(self.deductible_vat - self.output_vat)


def build_modelo303_vat_crosscheck(
    tax_report_dir: Path,
    xolo_raw_expenses_csv: Path,
) -> list[dict[str, str]]:
    raw_totals = _raw_vat_bearing_totals_from_csv(xolo_raw_expenses_csv)
    rows: list[dict[str, str]] = []
    for report in find_modelo303_reports(tax_report_dir):
        period = f"{report.year}-Q{report.quarter}"
        values = extract_modelo303_values(report.path)
        raw = raw_totals.get(period, {"base": Decimal("0.00"), "vat": Decimal("0.00")})
        base_diff = cents(values.net_local_input_base - raw["base"])
        vat_diff = cents(values.net_local_input_vat - raw["vat"])
        rows.append(
            {
                "year": str(report.year),
                "quarter": str(report.quarter),
                "period": period,
                "report": report.path.name,
                "extraction_status": values.extraction_status,
                "output_base": _money(values.output_base),
                "output_vat": _money(values.output_vat),
                "deductible_base": _money(values.deductible_base),
                "deductible_vat": _money(values.deductible_vat),
                "result": _money(values.result),
                "net_local_input_base": _money(values.net_local_input_base),
                "net_local_input_vat": _money(values.net_local_input_vat),
                "raw_vat_bearing_base": _money(raw["base"]),
                "raw_vat_bearing_vat": _money(raw["vat"]),
                "base_diff": _money(base_diff),
                "vat_diff": _money(vat_diff),
                "crosscheck_status": _crosscheck_status(values.extraction_status, base_diff, vat_diff),
                "monetary_sequence": "; ".join(_money(value) for value in values.monetary_sequence),
            }
        )
    return rows


def find_modelo303_reports(tax_report_dir: Path) -> list[Modelo303Report]:
    reports: list[Modelo303Report] = []
    for path in tax_report_dir.glob("*.pdf"):
        match = re.search(r"(?:M303|MOD 303)\s+([1-4])T\s+(20\d{2})", path.name, re.I)
        if not match:
            continue
        reports.append(Modelo303Report(year=int(match.group(2)), quarter=int(match.group(1)), path=path))
    return sorted(reports, key=lambda item: (item.year, item.quarter, item.path.name.lower()))


def extract_modelo303_values(path: Path) -> Modelo303Values:
    reader = PdfReader(str(path))
    if len(reader.pages) < 2:
        raise ValueError(f"Modelo 303 report has no form page: {path}")
    return values_from_monetary_sequence(_extract_page2_monetary_sequence(reader.pages[1]))


def values_from_monetary_sequence(sequence: list[Decimal] | tuple[Decimal, ...]) -> Modelo303Values:
    values = tuple(cents(value) for value in sequence)
    if len(values) == 0:
        return Modelo303Values(
            output_base=Decimal("0.00"),
            output_vat=Decimal("0.00"),
            deductible_base=Decimal("0.00"),
            deductible_vat=Decimal("0.00"),
            result=Decimal("0.00"),
            extraction_status="no_activity_or_no_page2_vat_values",
            monetary_sequence=values,
        )
    if len(values) == 3:
        deductible_base, deductible_vat, result = values
        return Modelo303Values(
            output_base=Decimal("0.00"),
            output_vat=Decimal("0.00"),
            deductible_base=deductible_base,
            deductible_vat=deductible_vat,
            result=result,
            extraction_status="deductible_only",
            monetary_sequence=values,
        )
    if len(values) == 5:
        output_base, output_vat, deductible_base, deductible_vat, result = values
        return Modelo303Values(
            output_base=output_base,
            output_vat=output_vat,
            deductible_base=deductible_base,
            deductible_vat=deductible_vat,
            result=result,
            extraction_status="output_and_deductible",
            monetary_sequence=values,
        )
    return Modelo303Values(
        output_base=Decimal("0.00"),
        output_vat=Decimal("0.00"),
        deductible_base=Decimal("0.00"),
        deductible_vat=Decimal("0.00"),
        result=Decimal("0.00"),
        extraction_status=f"unexpected_monetary_sequence_len_{len(values)}",
        monetary_sequence=values,
    )


def write_modelo303_vat_crosscheck_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_modelo303_vat_crosscheck_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 303 VAT Cross-Check",
        "",
        "This report compares submitted Modelo 303 VAT totals with VAT-bearing EUR rows from the raw Xolo expense export.",
        "For reverse-charge quarters, Modelo 303 includes the reverse-charge VAT on both output and deductible sides; the comparable local input VAT is therefore `deductible - output`.",
        "This is a VAT-basis cross-check only. It does not prove the Modelo 130 IRPF expense register or asset amortization schedule.",
        "",
        "| Period | Status | Output VAT | Deductible VAT | Net local input VAT | Raw VAT rows | VAT diff | Base diff | Extraction |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["crosscheck_status"],
                    _fmt(row["output_vat"]),
                    _fmt(row["deductible_vat"]),
                    _fmt(row["net_local_input_vat"]),
                    _fmt(row["raw_vat_bearing_vat"]),
                    _fmt(row["vat_diff"]),
                    _fmt(row["base_diff"]),
                    row["extraction_status"],
                ]
            )
            + " |"
        )
    lines.extend(["", "## Findings", ""])
    lines.extend(_findings(rows))
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _extract_page2_monetary_sequence(page) -> list[Decimal]:
    values: list[tuple[Decimal, Decimal, Decimal]] = []

    def visitor(fragment: str, _cm, tm, _font_dict, font_size: float) -> None:
        value = fragment.strip()
        if font_size < 5 or not re.fullmatch(r"-?[0-9.]+,[0-9]{2}", value):
            return
        amount = parse_amount(value)
        if amount in PERCENTAGE_VALUES:
            return
        x = Decimal(str(tm[4]))
        if x <= Decimal("10"):
            return
        y = Decimal(str(tm[5]))
        values.append((y, x, amount))

    page.extract_text(visitor_text=visitor)
    return [amount for _y, _x, amount in sorted(values, key=lambda item: (-item[0], item[1]))]


def _raw_vat_bearing_totals_by_period(rows: list[RawXoloExpense]) -> dict[str, dict[str, Decimal]]:
    totals: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        if row.currency != "EUR":
            continue
        base = row.subtotal_amount if row.subtotal_amount is not None else row.amount_original
        vat = cents(row.amount_original - base)
        if vat <= 0:
            continue
        period = f"{row.date.year}-Q{((row.date.month - 1) // 3) + 1}"
        bucket = totals.setdefault(period, {"base": Decimal("0.00"), "vat": Decimal("0.00")})
        bucket["base"] = cents(bucket["base"] + base)
        bucket["vat"] = cents(bucket["vat"] + vat)
    return totals


def _raw_vat_bearing_totals_from_csv(path: Path) -> dict[str, dict[str, Decimal]]:
    totals: dict[str, dict[str, Decimal]] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if (row.get("currency") or "").upper() != "EUR":
                continue
            raw_date = (row.get("date") or "")[:10]
            if not raw_date:
                continue
            vat = _optional_amount(row.get("vat_amount"))
            if vat is None:
                gross = _optional_amount(row.get("amount_original"))
                subtotal = _optional_amount(row.get("subtotal_amount"))
                if gross is None or subtotal is None:
                    continue
                vat = cents(gross - subtotal)
            if vat <= 0:
                continue
            base = _taxable_base_from_vat(row, vat)
            year = int(raw_date[:4])
            month = int(raw_date[5:7])
            period = f"{year}-Q{((month - 1) // 3) + 1}"
            bucket = totals.setdefault(period, {"base": Decimal("0.00"), "vat": Decimal("0.00")})
            bucket["base"] = cents(bucket["base"] + base)
            bucket["vat"] = cents(bucket["vat"] + vat)
    return totals


def _taxable_base_from_vat(row: dict[str, str], vat: Decimal) -> Decimal:
    subtotal = _optional_amount(row.get("subtotal_amount"))
    rate = _first_percentage(row.get("vat_percentages") or "")
    if rate and rate > 0:
        if subtotal is not None and abs(cents(subtotal * rate / Decimal("100")) - vat) <= Decimal("0.02"):
            return subtotal
        return cents(vat * Decimal("100") / rate)
    if subtotal is not None:
        return subtotal
    return Decimal("0.00")


def _first_percentage(value: str) -> Decimal | None:
    match = re.search(r"\d+(?:[.,]\d+)?", value)
    if not match:
        return None
    return parse_amount(match.group(0))


def _optional_amount(value: str | None) -> Decimal | None:
    if value in (None, ""):
        return None
    return parse_amount(value)


def _crosscheck_status(extraction_status: str, base_diff: Decimal, vat_diff: Decimal) -> str:
    if extraction_status.startswith("unexpected"):
        return "needs_manual_review"
    if abs(base_diff) <= Decimal("0.02") and abs(vat_diff) <= Decimal("0.02"):
        return "matched"
    return "mismatch"


def _findings(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["- No Modelo 303 rows were available."]
    matched = [row for row in rows if row["crosscheck_status"] == "matched"]
    mismatches = [row for row in rows if row["crosscheck_status"] != "matched"]
    reverse_charge = [row for row in rows if parse_amount(row["output_vat"]) > 0]
    findings = [
        f"- `{len(matched)}` of `{len(rows)}` Modelo 303 quarters match raw VAT-bearing EUR expense rows within `0,02 EUR` after netting reverse-charge output VAT.",
        f"- `{len(reverse_charge)}` quarters include reverse-charge output VAT; those amounts should not be mistaken for cash VAT-bearing expense rows.",
    ]
    if mismatches:
        findings.append(
            "- Manual review is still needed for: "
            + ", ".join(f"`{row['period']}`" for row in mismatches)
            + "."
        )
    else:
        findings.append(
            "- This supports the raw Xolo VAT/base fields for VAT-bearing rows, but it does not resolve non-VAT row inclusion or IRPF amortization."
        )
    return findings


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return format_es(parse_amount(value))
