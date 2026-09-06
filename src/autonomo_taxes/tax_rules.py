from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import re
from typing import Literal

from .money import cents


TaxFormCadence = Literal["quarterly", "annual"]

QUARTERLY_FORM_CODES = ("130", "303", "349", "111", "115", "216")
ANNUAL_FORM_CODES = ("390", "347", "190", "180", "296", "100", "714", "720", "721")
ALL_FORM_CODES = QUARTERLY_FORM_CODES + ANNUAL_FORM_CODES
DIFFICULT_EXPENSE_CAP_EUR = Decimal("2000.00")

_YEAR_PATTERN = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_QUARTER_PATTERNS = (
    re.compile(r"(?<!\d)([1-4])\s*T(?!\d)", re.I),
    re.compile(r"(?<!\d)T\s*([1-4])(?!\d)", re.I),
    re.compile(r"(?<!\d)Q\s*([1-4])(?!\d)", re.I),
)
_FORM_PATTERNS = {
    code: re.compile(rf"(?<!\d)(?:M(?:OD(?:ELO)?)?[\s._-]*)?{code}(?!\d)", re.I)
    for code in ALL_FORM_CODES
}


@dataclass(frozen=True)
class FormRule:
    code: str
    category: str
    cadence: TaxFormCadence
    source_citation: str
    introduced_year: int | None = None


@dataclass(frozen=True)
class RecognizedTaxForm:
    code: str
    category: str
    cadence: TaxFormCadence
    period: str
    source_citation: str


@dataclass(frozen=True)
class DifficultExpenseRule:
    year: int
    rate: Decimal
    annual_cap_eur: Decimal
    source_citation: str


FORM_RULES: dict[str, FormRule] = {
    "130": FormRule(
        code="130",
        category="modelo130_report",
        cadence="quarterly",
        source_citation="AEAT Modelo 130: IRPF. Empresarios y profesionales en Estimacion Directa. Pago fraccionado.",
    ),
    "303": FormRule(
        code="303",
        category="modelo303_report",
        cadence="quarterly",
        source_citation="AEAT Modelo 303: IVA. Autoliquidacion.",
    ),
    "349": FormRule(
        code="349",
        category="modelo349_report",
        cadence="quarterly",
        source_citation="AEAT Modelo 349: Declaracion recapitulativa de operaciones intracomunitarias.",
    ),
    "390": FormRule(
        code="390",
        category="modelo390_report",
        cadence="annual",
        source_citation="AEAT Modelo 390: Declaracion-resumen anual del IVA.",
    ),
    "347": FormRule(
        code="347",
        category="modelo347_report",
        cadence="annual",
        source_citation=(
            "AEAT Modelo 347: Declaracion anual de operaciones con terceras personas (arts. 31-35 RD 1065/2007). "
            "A recipient established outside Spain or the EU is not excluded by itself: DGT consulta vinculante "
            "V0516-19 (12-03-2019) requires listing services above 3,005.06 EUR supplied to an organisation established in Switzerland."
        ),
    ),
    "111": FormRule(
        code="111",
        category="modelo111_report",
        cadence="quarterly",
        source_citation="AEAT Modelo 111: Retenciones e ingresos a cuenta sobre rendimientos del trabajo y actividades economicas.",
    ),
    "190": FormRule(
        code="190",
        category="modelo190_report",
        cadence="annual",
        source_citation="AEAT Modelo 190: Resumen anual de retenciones e ingresos a cuenta.",
    ),
    "115": FormRule(
        code="115",
        category="modelo115_report",
        cadence="quarterly",
        source_citation="AEAT Modelo 115: Retenciones e ingresos a cuenta por arrendamiento o subarrendamiento de inmuebles urbanos.",
    ),
    "180": FormRule(
        code="180",
        category="modelo180_report",
        cadence="annual",
        source_citation="AEAT Modelo 180: Resumen anual de retenciones por arrendamiento de inmuebles urbanos.",
    ),
    "216": FormRule(
        code="216",
        category="modelo216_report",
        cadence="quarterly",
        source_citation=(
            "AEAT Modelo 216: IRNR. Retenciones e ingresos a cuenta sobre rentas obtenidas "
            "sin establecimiento permanente, incluidas declaraciones negativas por exencion de convenio."
        ),
    ),
    "296": FormRule(
        code="296",
        category="modelo296_report",
        cadence="annual",
        source_citation="AEAT Modelo 296: Resumen anual de retenciones e ingresos a cuenta del IRNR.",
    ),
    "100": FormRule(
        code="100",
        category="modelo100_report",
        cadence="annual",
        source_citation="AEAT Modelo 100: IRPF. Declaracion anual.",
    ),
    "714": FormRule(
        code="714",
        category="modelo714_report",
        cadence="annual",
        source_citation="AEAT Modelo 714: Impuesto sobre el Patrimonio.",
    ),
    "720": FormRule(
        code="720",
        category="modelo720_report",
        cadence="annual",
        source_citation="AEAT Modelo 720: Declaracion sobre bienes y derechos situados en el extranjero.",
    ),
    "721": FormRule(
        code="721",
        category="modelo721_report",
        cadence="annual",
        source_citation="AEAT Modelo 721: Declaracion informativa sobre monedas virtuales situadas en el extranjero.",
        introduced_year=2023,
    ),
}


def difficult_expense_rule_for_year(year: int) -> DifficultExpenseRule:
    return DifficultExpenseRule(
        year=year,
        rate=Decimal("0.07") if year == 2023 else Decimal("0.05"),
        annual_cap_eur=DIFFICULT_EXPENSE_CAP_EUR,
        source_citation=(
            "AEAT IRPF estimacion directa simplificada: gastos de dificil justificacion; "
            "7% para 2023 y 5% en el resto, con limite anual de 2.000 EUR."
        ),
    )


def difficult_expense_rate_for_year(year: int) -> Decimal:
    return difficult_expense_rule_for_year(year).rate


def calculate_difficult_expenses(
    year: int,
    income: Decimal,
    deductible_before_difficult: Decimal,
) -> Decimal:
    base = income - deductible_before_difficult
    if base <= 0:
        return Decimal("0.00")
    rule = difficult_expense_rule_for_year(year)
    return min(cents(base * rule.rate), rule.annual_cap_eur)


def recognize_tax_form_filename(name: str) -> RecognizedTaxForm | None:
    base_name = Path(name).name
    rule = _rule_for_name(base_name)
    if rule is None:
        return None
    if rule.cadence == "quarterly":
        quarter = _extract_quarter(base_name)
        if quarter is None:
            return None
        year = _extract_year(base_name, quarter=quarter)
        if year is None:
            return None
        period = f"{year}-Q{quarter}"
    else:
        year = _extract_year(base_name)
        if year is None:
            return None
        period = str(year)
    return RecognizedTaxForm(
        code=rule.code,
        category=rule.category,
        cadence=rule.cadence,
        period=period,
        source_citation=rule.source_citation,
    )


def normalize_form_code(value: str | int) -> str | None:
    text = str(value).strip()
    if not text:
        return None
    digits_only = re.sub(r"\D", "", text)
    if digits_only in FORM_RULES:
        return digits_only
    for code, pattern in _FORM_PATTERNS.items():
        if pattern.search(text):
            return code
    return None


def filed_form_codes_from_values(values: list[str] | tuple[str, ...] | set[str]) -> set[str]:
    filed: set[str] = set()
    for value in values:
        normalized = normalize_form_code(value)
        if normalized is not None:
            filed.add(normalized)
            continue
        recognized = recognize_tax_form_filename(value)
        if recognized is not None:
            filed.add(recognized.code)
    return filed


def _rule_for_name(name: str) -> FormRule | None:
    for code in ALL_FORM_CODES:
        if _FORM_PATTERNS[code].search(name):
            return FORM_RULES[code]
    return None


def _extract_year(name: str, *, quarter: int | None = None) -> int | None:
    year_matches = list(_YEAR_PATTERN.finditer(name))
    if not year_matches:
        return None
    date_matches = {
        match.start(): match
        for match in re.finditer(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)", name)
    }
    explicit_years = [match for match in year_matches if match.start() not in date_matches]
    if explicit_years:
        quarter_match = next(
            (pattern.search(name) for pattern in _QUARTER_PATTERNS if pattern.search(name)),
            None,
        )
        if quarter_match is not None:
            explicit_years.sort(key=lambda match: abs(match.start() - quarter_match.end()))
        return int(explicit_years[0].group(1))
    if quarter is not None and len(date_matches) == 1:
        date_match = next(iter(date_matches.values()))
        submission_year = int(date_match.group(1))
        submission_month = int(date_match.group(2))
        if quarter == 4 and submission_month <= 3:
            return submission_year - 1
        return submission_year
    return int(year_matches[0].group(1))


def _extract_quarter(name: str) -> int | None:
    for pattern in _QUARTER_PATTERNS:
        match = pattern.search(name)
        if match:
            return int(match.group(1))
    return None
