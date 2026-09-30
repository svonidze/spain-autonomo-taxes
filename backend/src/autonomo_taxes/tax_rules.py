from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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
# EU Member States by ISO 3166-1 alpha-2 code (Greece is GR here, EL in VAT numbers).
EU_COUNTRY_CODES = frozenset({
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU",
    "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK",
})

INVOICE_ISSUE_DEADLINE_CODE = "invoice_issue_deadline_missed"
INVOICE_ISSUE_DEADLINE_SOURCE = (
    "RD 1619/2012 (Reglamento de facturacion), art. 11.1: when the recipient is a business "
    "or professional acting as such, the invoice must be issued before the 16th day of the "
    "month following the accrual (devengo). The service date or service period end is used "
    "as the accrual date; advance payments (art. 75.Dos LIVA) and continuous supplies "
    "(art. 75.Uno.7º LIVA) can accrue on another date. Corrective invoices follow art. 15.3 "
    "(up to four years) instead. https://www.boe.es/buscar/act.php?id=BOE-A-2012-14696"
)
INCOME_BEFORE_ACTIVITY_START_CODE = "income_before_activity_start"

# Professional IRPF withholding on outgoing invoices (basis points).
PROFESSIONAL_WITHHOLDING_RATES = frozenset({0, 700, 1500})
REDUCED_PROFESSIONAL_WITHHOLDING_RATE = 700
REDUCED_PROFESSIONAL_WITHHOLDING_EXTRA_YEARS = 2
PROFESSIONAL_WITHHOLDING_SOURCE = (
    "LIRPF (Ley 35/2006) art. 101.5.a and RIRPF (RD 439/2007) art. 95.1: professional "
    "income is withheld at 15%, or 7% in the year the professional activity starts and "
    "the two following years when there was no professional activity in the previous "
    "year and the professional gave the payer a written notice, which the payer keeps. "
    "Professional activities are IAE sections 2 and 3 (RIRPF art. 95.2). The obligation "
    "arises on payment (RIRPF art. 78); the invoice issue year is used as a proxy. Only "
    "payers obliged to withhold (RIRPF art. 76) apply it: private individuals and "
    "foreign clients do not, while a non-resident operating through a Spanish permanent "
    "establishment does (RIRPF art. 76.1.c) and must be recorded with country ES. The "
    "0/7/15% presets are not exhaustive: the permanent 7% for the activities in RIRPF "
    "art. 95.1 a-d and the 60% Ceuta/Melilla reduction are not modelled. "
    "https://www.boe.es/buscar/act.php?id=BOE-A-2007-6820"
)
WITHHOLDING_RATE_NOT_ALLOWED_CODE = "withholding_rate_not_allowed"
WITHHOLDING_FOREIGN_COUNTERPARTY_CODE = "withholding_foreign_counterparty"
WITHHOLDING_REDUCED_RATE_OUTSIDE_WINDOW_CODE = "withholding_reduced_rate_outside_window"
WITHHOLDING_REDUCED_RATE_NOTICE_UNCONFIRMED_CODE = "withholding_reduced_rate_notice_unconfirmed"

# Invoice mentions the operator must add in the external invoicing channel.
# Box 59 of Modelo 303 (eu_service_income) is the EU B2B case; box 120
# (outside_scope) holds services located outside Spain for non-EU customers.
EU_REVERSE_CHARGE_TAX_CODES = frozenset({"eu_service_income"})
OUTSIDE_SPAIN_SERVICE_TAX_CODES = frozenset({"outside_scope", "not_subject_place_of_supply"})
EXEMPT_INCOME_PROVISIONS = {"export": "art. 21 LIVA", "eu_goods_income": "art. 25 LIVA"}
INVOICE_MENTION_REVERSE_CHARGE_TEXT = "Inversión del sujeto pasivo"
INVOICE_MENTION_NOT_SUBJECT_TEXT = "Operación no sujeta a IVA en España (art. 69 LIVA)"
_LIVA_PLACE_OF_SUPPLY_SOURCE = (
    "Ley 37/1992 (LIVA) art. 69 locates services outside Spain: to a business where the "
    "recipient is established (art. 69.Uno.1º) and certain services to private customers "
    "outside the EU (art. 69.Dos). Citing the non-subject provision is common practice; "
    "RD 1619/2012 does not fix this wording. "
    "https://www.boe.es/buscar/act.php?id=BOE-A-1992-28740"
)
INVOICE_MENTION_SOURCES = {
    "reverse_charge": (
        "RD 1619/2012 art. 6.1.m: when the recipient is liable for the tax, the invoice "
        "must state «inversión del sujeto pasivo». "
        "https://www.boe.es/buscar/act.php?id=BOE-A-2012-14696"
    ),
    "not_subject_place_of_supply": _LIVA_PLACE_OF_SUPPLY_SOURCE,
    "place_of_supply_review": _LIVA_PLACE_OF_SUPPLY_SOURCE,
    "exempt_provision": (
        "RD 1619/2012 art. 6.1.j: an exempt operation needs a reference to the exempting "
        "provision of Directive 2006/112/CE or LIVA, or an indication that it is exempt. "
        "https://www.boe.es/buscar/act.php?id=BOE-A-2012-14696"
    ),
    "vat_amount_in_eur": (
        "RD 1619/2012 art. 12.1: amounts may use any currency, but the Spanish VAT charged "
        "must be stated in EUR at the exchange rate of art. 79.Once LIVA. "
        "https://www.boe.es/buscar/act.php?id=BOE-A-2012-14696"
    ),
}
DIRECT_ESTIMATION_IRPF_METHODS = frozenset(
    {"estimacion_directa", "estimacion_directa_normal", "estimacion_directa_simplificada"}
)
NEW_ACTIVITY_REDUCTION_RATE = Decimal("0.20")
NEW_ACTIVITY_REDUCTION_BASE_CAP_EUR = Decimal("100000.00")
NEW_ACTIVITY_REDUCTION_SOURCE = (
    "LIRPF art. 32.3 (Ley 35/2006): taxpayers in estimacion directa who start an economic activity "
    "may reduce by 20% the positive net activity income of the first tax period with positive net "
    "income and of the following period, on at most 100,000 EUR per year. No activity may have been "
    "exercised in the year before the start date (ceased activities without positive net income do "
    "not count); excluded when more than 50% of the period's income comes from a payer of employment "
    "income in the year before the start. AEAT Manual practico IRPF 2024: "
    "https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos/"
    "irpf-2024/c07-rendimientos-actividades-economicas-estimacion-directa/"
    "fase-3-determinacion-rendimiento-neto-total/reduccion-rendimiento-neto-inicio-actividad-economica.html"
)
# Libertad de amortizacion for immaterial-value tangible fixed assets: Ley 27/2014
# (LIS) art. 12.3, applicable to IRPF estimacion directa. Immediate write-off is
# only allowed for new items with unit value <= 300 EUR, up to a cumulative
# IMMEDIATE_WRITE_OFF_ANNUAL_CAP_EUR per tax period. Above the unit limit,
# amortize per the simplified table (IT equipment: max 26%/year, max 10 years).
# See AEAT manual:
# https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos/irpf-2024/c07-rendimientos-actividades-economicas-estimacion-directa/fase-1-determinacion-rendimiento-neto/amortizaciones-dotaciones-ejercicio-fiscalmente-deducibles/supuestos-libertad-amortizacion.html
# The expense parsers compare this limit against the whole invoice base
# (conservative for multi-item invoices, which can include several sub-300 EUR
# units) and against the base without recoverable IVA (prorrata/exempt-activity
# IVA treatment is not modelled).
IMMEDIATE_WRITE_OFF_UNIT_LIMIT_EUR = Decimal("300.00")
IMMEDIATE_WRITE_OFF_ANNUAL_CAP_EUR = Decimal("25000.00")
# Date every FORM_RULES.source_url below was last confirmed to resolve (HTTP 200).
SOURCE_CHECKED_ON = "2026-09-24"

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
    source_url: str
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
        source_citation=(
            "AEAT Modelo 130: IRPF. Empresarios y profesionales en Estimacion Directa. Pago fraccionado. "
            "LIRPF art. 99.1.c) y 99.7 (obligacion de pago fraccionado); RIRPF arts. 109-112 "
            "(obligados, importe, declaracion e ingreso)."
        ),
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G601.shtml",
    ),
    "303": FormRule(
        code="303",
        category="modelo303_report",
        cadence="quarterly",
        source_citation=(
            "AEAT Modelo 303: IVA. Autoliquidacion. "
            "LIVA art. 164.Uno.6 (presentar las declaraciones-liquidaciones e ingresar el impuesto); "
            "RIVA art. 71 (liquidacion del impuesto, normas generales)."
        ),
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G414.shtml",
    ),
    # V2594-21 could not be checked directly: petete.tributos.hacienda.gob.es fails
    # certificate verification (incomplete FNMT chain). Verified instead from a
    # third-party copy via a Wayback Machine snapshot of a mirror; checked 2026-09-24.
    "349": FormRule(
        code="349",
        category="modelo349_report",
        cadence="quarterly",
        source_citation=(
            "AEAT Modelo 349: Declaracion recapitulativa de operaciones intracomunitarias. "
            "LIVA art. 164.Uno.5 (declaracion recapitulativa de operaciones intracomunitarias); "
            "RIVA arts. 78-81 (declaracion recapitulativa: obligados, contenido, lugar, forma y plazos). "
            "DGT consulta vinculante V2594-21 (25-10-2021): quien cobra de una empresa establecida en Irlanda "
            "por servicios prestados a traves de una plataforma de internet (p. ej. YouTube/Google) debe "
            "inscribirse en el ROI, recibe factura sin IVA espanol con mencion 'inversion del sujeto pasivo' "
            "y declara la operacion en el modelo 349."
        ),
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GI28.shtml",
    ),
    "390": FormRule(
        code="390",
        category="modelo390_report",
        cadence="annual",
        source_citation="AEAT Modelo 390: Declaracion-resumen anual del IVA.",
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G412.shtml",
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
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GI27.shtml",
    ),
    "111": FormRule(
        code="111",
        category="modelo111_report",
        cadence="quarterly",
        source_citation="AEAT Modelo 111: Retenciones e ingresos a cuenta sobre rendimientos del trabajo y actividades economicas.",
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GH01.shtml",
    ),
    "190": FormRule(
        code="190",
        category="modelo190_report",
        cadence="annual",
        source_citation="AEAT Modelo 190: Resumen anual de retenciones e ingresos a cuenta.",
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GI10.shtml",
    ),
    "115": FormRule(
        code="115",
        category="modelo115_report",
        cadence="quarterly",
        source_citation="AEAT Modelo 115: Retenciones e ingresos a cuenta por arrendamiento o subarrendamiento de inmuebles urbanos.",
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GH02.shtml",
    ),
    "180": FormRule(
        code="180",
        category="modelo180_report",
        cadence="annual",
        source_citation="AEAT Modelo 180: Resumen anual de retenciones por arrendamiento de inmuebles urbanos.",
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GI00.shtml",
    ),
    "216": FormRule(
        code="216",
        category="modelo216_report",
        cadence="quarterly",
        source_citation=(
            "AEAT Modelo 216: IRNR. Retenciones e ingresos a cuenta sobre rentas obtenidas "
            "sin establecimiento permanente, incluidas declaraciones negativas por exencion de convenio."
        ),
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GF05.shtml",
    ),
    "296": FormRule(
        code="296",
        category="modelo296_report",
        cadence="annual",
        source_citation="AEAT Modelo 296: Resumen anual de retenciones e ingresos a cuenta del IRNR.",
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GI22.shtml",
    ),
    "100": FormRule(
        code="100",
        category="modelo100_report",
        cadence="annual",
        source_citation="AEAT Modelo 100: IRPF. Declaracion anual.",
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G229.shtml",
    ),
    "714": FormRule(
        code="714",
        category="modelo714_report",
        cadence="annual",
        source_citation="AEAT Modelo 714: Impuesto sobre el Patrimonio.",
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/G611.shtml",
    ),
    "720": FormRule(
        code="720",
        category="modelo720_report",
        cadence="annual",
        source_citation=(
            "AEAT Modelo 720: Declaracion sobre bienes y derechos situados en el extranjero. "
            "LGT (Ley 58/2003) disposicion adicional decimoctava, letras a) a c) "
            "(informacion sobre cuentas, valores y bienes inmuebles en el extranjero)."
        ),
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GI34.shtml",
    ),
    "721": FormRule(
        code="721",
        category="modelo721_report",
        cadence="annual",
        source_citation=(
            "AEAT Modelo 721: Declaracion informativa sobre monedas virtuales situadas en el extranjero. "
            "LGT (Ley 58/2003) disposicion adicional decimoctava, letra d) "
            "(informacion sobre monedas virtuales en el extranjero)."
        ),
        source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GI55.shtml",
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


def invoice_issue_deadline(accrued_on: date) -> date:
    """Last issue date allowed for a business recipient: the 15th of the next month."""
    if accrued_on.month == 12:
        return date(accrued_on.year + 1, 1, 15)
    return date(accrued_on.year, accrued_on.month + 1, 15)


def invoice_issue_deadline_warning(*, accrued_on: date, issued_on: date) -> str | None:
    deadline = invoice_issue_deadline(accrued_on)
    if issued_on <= deadline:
        return None
    return (
        f"Issue date {issued_on.isoformat()} is after {deadline.isoformat()}, the last day "
        f"allowed for a business recipient when the service accrues on {accrued_on.isoformat()}. "
        "The ledger cannot tell a private individual from a business, so the recipient is "
        "treated as a business or professional."
    )


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
