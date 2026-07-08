from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from pathlib import Path

from .money import cents, parse_amount
from .pdf_text import extract_pdf_text


@dataclass(frozen=True)
class Modelo130Result:
    casilla_01: Decimal
    casilla_02: Decimal
    casilla_03: Decimal
    casilla_04: Decimal
    casilla_05: Decimal
    casilla_06: Decimal
    casilla_07: Decimal
    casilla_12: Decimal
    casilla_13: Decimal
    casilla_14: Decimal
    casilla_15: Decimal
    casilla_16: Decimal
    casilla_17: Decimal
    casilla_18: Decimal
    casilla_19: Decimal
    difficult_expenses: Decimal
    deductible_before_difficult: Decimal

    def as_dict(self) -> dict[str, Decimal]:
        return {
            "01": self.casilla_01,
            "02": self.casilla_02,
            "03": self.casilla_03,
            "04": self.casilla_04,
            "05": self.casilla_05,
            "06": self.casilla_06,
            "07": self.casilla_07,
            "12": self.casilla_12,
            "13": self.casilla_13,
            "14": self.casilla_14,
            "15": self.casilla_15,
            "16": self.casilla_16,
            "17": self.casilla_17,
            "18": self.casilla_18,
            "19": self.casilla_19,
        }


def difficult_expenses(income: Decimal, deductible_before_difficult: Decimal) -> Decimal:
    base = income - deductible_before_difficult
    if base <= 0:
        return Decimal("0.00")
    return min(cents(base * Decimal("0.05")), Decimal("2000.00"))


def calculate_modelo130(
    income_ytd: Decimal,
    deductible_before_difficult_ytd: Decimal,
    previous_positive_casilla_07: Decimal = Decimal("0.00"),
    retentions: Decimal = Decimal("0.00"),
    minoracion: Decimal = Decimal("0.00"),
    previous_negative_results: Decimal = Decimal("0.00"),
    vivienda_deduction: Decimal = Decimal("0.00"),
    complementary_previous_result: Decimal = Decimal("0.00"),
) -> Modelo130Result:
    income_ytd = cents(income_ytd)
    deductible_before_difficult_ytd = cents(deductible_before_difficult_ytd)
    hard_to_justify = difficult_expenses(income_ytd, deductible_before_difficult_ytd)
    casilla_02 = cents(deductible_before_difficult_ytd + hard_to_justify)
    casilla_03 = cents(income_ytd - casilla_02)
    casilla_04 = cents(max(casilla_03, Decimal("0.00")) * Decimal("0.20"))
    casilla_05 = cents(previous_positive_casilla_07)
    casilla_06 = cents(retentions)
    casilla_07 = cents(casilla_04 - casilla_05 - casilla_06)
    casilla_12 = max(casilla_07, Decimal("0.00"))
    casilla_13 = cents(minoracion)
    casilla_14 = cents(casilla_12 - casilla_13)
    casilla_15 = cents(min(previous_negative_results, max(casilla_14, Decimal("0.00"))))
    casilla_16 = cents(min(vivienda_deduction, max(casilla_14 - casilla_15, Decimal("0.00"))))
    casilla_17 = cents(casilla_14 - casilla_15 - casilla_16)
    casilla_18 = cents(complementary_previous_result)
    casilla_19 = cents(casilla_17 - casilla_18)
    return Modelo130Result(
        casilla_01=income_ytd,
        casilla_02=casilla_02,
        casilla_03=casilla_03,
        casilla_04=casilla_04,
        casilla_05=casilla_05,
        casilla_06=casilla_06,
        casilla_07=casilla_07,
        casilla_12=casilla_12,
        casilla_13=casilla_13,
        casilla_14=casilla_14,
        casilla_15=casilla_15,
        casilla_16=casilla_16,
        casilla_17=casilla_17,
        casilla_18=casilla_18,
        casilla_19=casilla_19,
        difficult_expenses=hard_to_justify,
        deductible_before_difficult=deductible_before_difficult_ytd,
    )


def extract_modelo130_values_from_text(text: str, year: int, quarter: int) -> dict[str, Decimal]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    period = f"{year} {quarter}T"
    try:
        start = next(i for i, line in enumerate(lines) if line == period)
    except StopIteration as exc:
        raise ValueError(f"Could not find Modelo 130 period {period}") from exc

    values: list[Decimal] = []
    for line in lines[start + 1 :]:
        if re.fullmatch(r"-?[0-9.]+,[0-9]{2}", line):
            values.append(parse_amount(line))
        if len(values) >= 12:
            break
    if len(values) < 5:
        raise ValueError(f"Could not extract enough numeric values for {period}")

    result = {
        "01": values[0],
        "02": values[1],
        "03": values[2],
        "04": values[3],
    }
    if quarter == 1:
        result.update({"05": Decimal("0.00"), "07": values[4], "17": values[4], "19": values[4]})
    else:
        if len(values) < 6:
            raise ValueError(f"Could not extract casilla 07 for {period}")
        result.update({"05": values[4], "07": values[5], "17": values[5], "19": values[5]})
    return result


def extract_modelo130_values(path: Path, year: int, quarter: int) -> dict[str, Decimal]:
    return extract_modelo130_values_from_text(extract_pdf_text(path), year, quarter)


def find_previous_reports(tax_report_dir: Path, year: int, quarter: int) -> list[Path]:
    reports: list[Path] = []
    for prev in range(1, quarter):
        patterns = [
            f"*130*{prev}T*{year}*.pdf",
            f"*130*{year}*{prev}T*.pdf",
        ]
        found: list[Path] = []
        for pattern in patterns:
            found.extend(tax_report_dir.glob(pattern))
        if found:
            reports.append(sorted(found)[0])
    return reports


def previous_positive_payments(tax_report_dir: Path, year: int, quarter: int) -> Decimal:
    total = Decimal("0.00")
    for report in find_previous_reports(tax_report_dir, year, quarter):
        match = re.search(r"([1-4])T", report.name, flags=re.IGNORECASE)
        if not match:
            continue
        report_quarter = int(match.group(1))
        values = extract_modelo130_values(report, year, report_quarter)
        total += max(values.get("07", Decimal("0.00")), Decimal("0.00"))
    return cents(total)
