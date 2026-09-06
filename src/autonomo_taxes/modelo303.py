from __future__ import annotations

import csv
from dataclasses import dataclass, replace
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

_NUMBER_PATTERN = r"(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}"
_AMOUNT_PATTERN = rf"-?{_NUMBER_PATTERN}"
STRUCTURAL_BOXES = ("12", "13", "27", "28", "29", "30", "31", "45", "46")
STRUCTURAL_PAIRS = (("12", "13"), ("28", "29"), ("30", "31"))
REVERSE_CHARGE_BOXES = STRUCTURAL_BOXES + (
    "150", "01", "04", "07", "10", "11", "32", "33", "34", "35", "36", "37", "38", "39",
)
REVERSE_CHARGE_PAIRS = STRUCTURAL_PAIRS + (("10", "11"), ("32", "33"), ("34", "35"), ("36", "37"), ("38", "39"))
STRUCTURAL_SINGLE_CONTEXT = {
    "27": "total cuota devengada",
    "45": "total a deducir",
    "46": "resultado régimen general",
}

PositionedFragment = tuple[str, Decimal, Decimal, Decimal]


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
    structural_casillas: tuple[tuple[str, Decimal], ...] = ()
    structural_extraction_status: str = "not_extracted"
    settlement_casillas: tuple[tuple[str, Decimal], ...] = ()
    settlement_extraction_status: str = "not_extracted"
    blank_casillas: tuple[str, ...] = ()
    value_sources: tuple[tuple[str, str], ...] = ()

    @property
    def net_local_input_base(self) -> Decimal:
        return cents(self.deductible_base - self.output_base)

    @property
    def net_local_input_vat(self) -> Decimal:
        return cents(self.deductible_vat - self.output_vat)

    @property
    def settlement_values(self) -> dict[str, Decimal]:
        return dict(self.settlement_casillas)

    @property
    def casilla_values(self) -> dict[str, Decimal]:
        return dict(self.structural_casillas + self.settlement_casillas)

    @property
    def compensation_carryforward(self) -> Decimal | None:
        if self.settlement_extraction_status != "casillas_extracted":
            return None
        values = self.settlement_values
        return cents(max(values["87"], Decimal("0.00")) + max(values["72"], Decimal("0.00")))


@dataclass(frozen=True)
class Modelo303CasillaEvidence:
    casillas: tuple[tuple[str, Decimal], ...]
    blank_casillas: tuple[str, ...]
    value_sources: tuple[tuple[str, str], ...]
    status: str


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
    sequence = _extract_page2_monetary_sequence(reader.pages[1])
    layout_pages, positioned_pages = _extract_page_evidence(reader.pages)
    extended = len(sequence) in {6, 7}
    structural = _structural_casilla_evidence_from_pages(
        layout_pages, positioned_pages, extended=extended,
    )
    settlement = _settlement_casilla_evidence_from_fragments(positioned_pages)
    values = values_from_monetary_sequence(sequence, structural_evidence=structural if extended else None)
    if values.extraction_status.startswith("reverse_charge_"):
        if settlement.status == "casillas_extracted":
            values = replace(values, result=dict(settlement.casillas)["71"])
    return replace(
        values,
        structural_casillas=structural.casillas,
        structural_extraction_status=structural.status,
        settlement_casillas=settlement.casillas,
        settlement_extraction_status=settlement.status,
        blank_casillas=structural.blank_casillas + settlement.blank_casillas,
        value_sources=structural.value_sources + settlement.value_sources,
    )


def values_from_monetary_sequence(
    sequence: list[Decimal] | tuple[Decimal, ...],
    *,
    structural_evidence: Modelo303CasillaEvidence | None = None,
) -> Modelo303Values:
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
    corroborated = _reverse_charge_sequence_is_corroborated(values, structural_evidence)
    if len(values) == 6 and corroborated:
        # Reverse-charge services quarter (casillas 10/11 populated, no other
        # output rows): [rc base (10), total output VAT (27), domestic
        # deductible base (28), rc deductible base (36), total deductible
        # VAT (45), result (46/71)].
        rc_base, output_vat, deductible_domestic, deductible_rc, deductible_vat, result = values
        return Modelo303Values(
            output_base=rc_base,
            output_vat=output_vat,
            deductible_base=cents(deductible_domestic + deductible_rc),
            deductible_vat=deductible_vat,
            result=result,
            extraction_status="reverse_charge_services",
            monetary_sequence=values,
        )
    if len(values) == 7 and corroborated:
        # Reverse-charge services plus other reverse-charge output (casillas
        # 10/11 and 12/13): [rc service base (10), other rc base (12), total
        # output VAT (27), domestic deductible base (28), rc deductible
        # base (36), total deductible VAT (45), result (46/71)].
        (
            rc_service_base,
            other_rc_base,
            output_vat,
            deductible_domestic,
            deductible_rc,
            deductible_vat,
            result,
        ) = values
        return Modelo303Values(
            output_base=cents(rc_service_base + other_rc_base),
            output_vat=output_vat,
            deductible_base=cents(deductible_domestic + deductible_rc),
            deductible_vat=deductible_vat,
            result=result,
            extraction_status="reverse_charge_with_other_output",
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


SETTLEMENT_BOXES = ("64", "110", "78", "87", "69", "71", "72", "73")


def _reverse_charge_sequence_is_corroborated(
    values: tuple[Decimal, ...], evidence: Modelo303CasillaEvidence | None,
) -> bool:
    if len(values) not in {6, 7} or evidence is None or evidence.status != "casillas_extracted":
        return False
    boxes = dict(evidence.casillas)
    if not set(REVERSE_CHARGE_BOXES) <= boxes.keys():
        return False
    if any(boxes[key] != 0 for key in ("150", "01", "04", "07", "30", "32", "34", "38")):
        return False
    if boxes["10"] == 0 or (len(values) == 6 and (boxes["12"] != 0 or boxes["13"] != 0)):
        return False
    if boxes["27"] != cents(boxes["11"] + boxes["13"]):
        return False
    if boxes["45"] != cents(sum((boxes[k] for k in ("29", "31", "33", "35", "37", "39")), Decimal(0))):
        return False
    if boxes["46"] != cents(boxes["27"] - boxes["45"]):
        return False
    keys = ("10", "27", "28", "36", "45", "46") if len(values) == 6 else ("10", "12", "27", "28", "36", "45", "46")
    return values == tuple(boxes[key] for key in keys)


def _extract_settlement_casillas(pages) -> tuple[tuple[tuple[str, Decimal], ...], str]:
    _layouts, positioned_pages = _extract_page_evidence(pages)
    evidence = _settlement_casilla_evidence_from_fragments(positioned_pages)
    return evidence.casillas, evidence.status


def _extract_page_evidence(
    pages,
) -> tuple[list[str], list[list[PositionedFragment]]]:
    layouts: list[str] = []
    positioned_pages: list[list[PositionedFragment]] = []
    for page in pages:
        fragments: list[PositionedFragment] = []

        def visitor(fragment: str, _cm, tm, _font_dict, font_size: float) -> None:
            value = " ".join(fragment.split())
            if not value:
                return
            fragments.append(
                (
                    value,
                    Decimal(str(tm[4])),
                    Decimal(str(tm[5])),
                    Decimal(str(font_size)),
                )
            )

        page.extract_text(visitor_text=visitor)
        layouts.append(page.extract_text(extraction_mode="layout") or "")
        positioned_pages.append(fragments)
    return layouts, positioned_pages


def _structural_casilla_evidence_from_pages(
    layout_pages: list[str] | tuple[str, ...],
    positioned_pages: list[list[PositionedFragment]]
    | tuple[tuple[PositionedFragment, ...], ...],
    *,
    extended: bool = False,
) -> Modelo303CasillaEvidence:
    boxes = REVERSE_CHARGE_BOXES if extended else STRUCTURAL_BOXES
    pairs = REVERSE_CHARGE_PAIRS if extended else STRUCTURAL_PAIRS
    recognized: set[str] = set()
    candidates: dict[str, set[Decimal]] = {box: set() for box in boxes}
    sources: dict[str, set[str]] = {box: set() for box in boxes}

    for page in layout_pages:
        for line in page.splitlines():
            for left, right in pairs:
                match = re.search(
                    rf"(?<!\d){left}(?!\d)[ \t]*(?P<left>{_AMOUNT_PATTERN})?"
                    rf"[ \t]*{right}(?!\d)[ \t]*(?P<right>{_AMOUNT_PATTERN})?",
                    line,
                )
                if match is None:
                    continue
                recognized.update((left, right))
                for box, group in ((left, "left"), (right, "right")):
                    raw = match.group(group)
                    if raw is not None:
                        candidates[box].add(cents(parse_amount(raw)))
                        sources[box].add("layout_line")
            normalized_line = " ".join(line.casefold().split())
            for box, context in STRUCTURAL_SINGLE_CONTEXT.items():
                if context not in normalized_line:
                    continue
                matches = list(
                    re.finditer(
                        rf"(?<!\d){box}[ \t]*(?P<value>{_AMOUNT_PATTERN})?[ \t]*$",
                        line,
                    )
                )
                if not matches:
                    continue
                match = matches[-1]
                recognized.add(box)
                raw = match.group("value")
                if raw is not None:
                    candidates[box].add(cents(parse_amount(raw)))
                    sources[box].add("layout_line")

    for fragments in positioned_pages:
        for label, label_x, label_y, font_size in fragments:
            if label not in boxes or font_size > Decimal("4"):
                continue
            recognized.add(label)
            if label_x == 0 and label_y == 0:
                continue
            next_label_x = min((
                x for raw, x, y, size in fragments
                if extended and raw.isdigit() and size <= 4
                and x > label_x and abs(y - label_y) <= 5
            ), default=Decimal("Infinity"))
            for raw, x, y, value_font_size in fragments:
                if x <= label_x + Decimal("5") or x >= next_label_x or abs(y - label_y) > Decimal("5"):
                    continue
                if value_font_size < Decimal("5") or re.fullmatch(_AMOUNT_PATTERN, raw) is None:
                    continue
                candidates[label].add(cents(parse_amount(raw)))
                sources[label].add("positioned_row")

    return _build_casilla_evidence(
        boxes=boxes,
        recognized=recognized,
        candidates=candidates,
        sources=sources,
        missing_status="structural_layout_not_found",
        incomplete_status="incomplete_structural_layout",
    )


def _settlement_casillas_from_fragments(
    positioned_pages: list[list[tuple[str, Decimal, Decimal, Decimal]]],
) -> tuple[tuple[tuple[str, Decimal], ...], str]:
    evidence = _settlement_casilla_evidence_from_fragments(positioned_pages)
    return evidence.casillas, evidence.status


def _settlement_casilla_evidence_from_fragments(
    positioned_pages: list[list[PositionedFragment]]
    | tuple[tuple[PositionedFragment, ...], ...],
) -> Modelo303CasillaEvidence:
    candidates_by_box: dict[str, set[Decimal]] = {box: set() for box in SETTLEMENT_BOXES}
    sources: dict[str, set[str]] = {box: set() for box in SETTLEMENT_BOXES}
    recognized: set[str] = set()
    for fragments in positioned_pages:
        for label, label_x, label_y, font_size in fragments:
            if label not in SETTLEMENT_BOXES or font_size > Decimal("4"):
                continue
            if label in {"72", "73"}:
                if label_x >= Decimal("150") or label_y <= Decimal("650"):
                    if label != "73" or label_x >= Decimal("180") or not Decimal("500") <= label_y <= Decimal("550"):
                        continue
            elif label_x <= Decimal("400") or not Decimal("350") <= label_y <= Decimal("580"):
                continue
            recognized.add(label)
            candidates: list[tuple[Decimal, Decimal, Decimal]] = []
            for value, x, y, value_font_size in fragments:
                if x <= label_x + Decimal("10") or abs(y - label_y) > Decimal("5"):
                    continue
                if value_font_size < Decimal("5") or not re.fullmatch(r"-?[0-9.]+,[0-9]{2}", value):
                    continue
                candidates.append((abs(y - label_y), x, parse_amount(value)))
            if candidates:
                candidates_by_box[label].add(
                    cents(min(candidates, key=lambda item: (item[0], item[1]))[2])
                )
                sources[label].add("positioned_row")

    return _build_casilla_evidence(
        boxes=SETTLEMENT_BOXES,
        recognized=recognized,
        candidates=candidates_by_box,
        sources=sources,
        missing_status="settlement_layout_not_found",
        incomplete_status="incomplete_settlement_layout",
    )


def _build_casilla_evidence(
    *,
    boxes: tuple[str, ...],
    recognized: set[str],
    candidates: dict[str, set[Decimal]],
    sources: dict[str, set[str]],
    missing_status: str,
    incomplete_status: str,
) -> Modelo303CasillaEvidence:
    if not recognized:
        return Modelo303CasillaEvidence((), (), (), missing_status)

    values: list[tuple[str, Decimal]] = []
    blank: list[str] = []
    value_sources: list[tuple[str, str]] = []
    for box in boxes:
        if box not in recognized:
            continue
        box_candidates = candidates[box]
        if len(box_candidates) > 1:
            rendered = ", ".join(f"{value:.2f}" for value in sorted(box_candidates))
            raise ValueError(f"Modelo 303 casilla {box} has ambiguous values: {rendered}")
        if box_candidates:
            value = next(iter(box_candidates))
            values.append((box, value))
            value_sources.append((box, "+".join(sorted(sources[box]))))
        else:
            values.append((box, Decimal("0.00")))
            blank.append(box)
            value_sources.append((box, "box_present_no_value_captured"))

    status = "casillas_extracted" if recognized == set(boxes) else incomplete_status
    return Modelo303CasillaEvidence(
        casillas=tuple(values),
        blank_casillas=tuple(blank),
        value_sources=tuple(value_sources),
        status=status,
    )


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
