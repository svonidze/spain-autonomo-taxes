from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Any

from .money import cents, parse_amount
from .parsers import LedgerEntry


_AMOUNT = (
    r"((?:(?:[0-9]{1,3}(?:[.\u00a0\u202f ][0-9]{3})+)"
    r"|(?:[0-9]{1,3}(?:,[0-9]{3})+)|(?:[0-9]+))[,.][0-9]{2})"
)
_CURRENCY_BY_SYMBOL = {"€": "EUR", "$": "USD", "£": "GBP"}
_SUGGESTION_IS_GROSS = {
    "service_income",
    "reta",
    "ai_tools_eu_vat",
    "ai_tools_foreign",
    "dev_tools_foreign",
    "domains_foreign",
    "home_utility_review",
}


@dataclass(frozen=True)
class InvoiceAmounts:
    currency: str
    gross: Decimal | None
    taxable_base: Decimal | None
    vat: Decimal | None
    gross_source: str
    taxable_base_source: str
    vat_source: str
    errors: tuple[str, ...]

    @property
    def draft_ready(self) -> bool:
        return bool(self.currency and self.gross is not None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "gross": _money(self.gross),
            "taxable_base": _money(self.taxable_base),
            "vat": _money(self.vat),
            "gross_source": self.gross_source,
            "taxable_base_source": self.taxable_base_source,
            "vat_source": self.vat_source,
            "errors": list(self.errors),
            "draft_ready": self.draft_ready,
        }


def extract_invoice_amounts(
    text: str,
    suggestion: LedgerEntry | None,
    *,
    gross_override: Decimal | None = None,
    taxable_base_override: Decimal | None = None,
    vat_override: Decimal | None = None,
    currency_override: str | None = None,
) -> InvoiceAmounts:
    cleaned = text.replace("\x00", " ")
    currency = _currency(currency_override, suggestion, cleaned)

    gross, gross_source = _value(
        gross_override,
        cleaned,
        (
            (r"Precio total \(incl\. IVA\)[^\d]{0,24}" + _AMOUNT, "invoice_label:price_including_vat"),
            (
                r"(?:Amount due|Total pendiente|Invoice total|TOTAL IMPORTE FACTURA)"
                r"\s*:?\s*(?:EUR|USD|GBP|€|\$|£)?\s*" + _AMOUNT,
                "invoice_label:amount_due",
            ),
            (
                r"(?im)^\s*Total\s*:?\s*(?:EUR|USD|GBP|€|\$|£)?\s*" + _AMOUNT,
                "invoice_label:total",
            ),
        ),
        override_source="explicit:gross",
    )
    taxable_base, taxable_base_source = _value(
        taxable_base_override,
        cleaned,
        (
            (r"Base \(sin IVA\)\s*:?\s*" + _AMOUNT, "invoice_label:base_without_vat"),
            (
                r"Base imponible\s+IVA\s+Tasa de IVA\s*" + _AMOUNT,
                "invoice_label:taxable_base_table",
            ),
            (
                r"Base imponible\s*:?\s*(?:EUR|USD|GBP|€|\$|£)?\s*" + _AMOUNT,
                "invoice_label:taxable_base",
            ),
            (
                r"(?:Total excluding tax|Subtotal)\s*:?\s*"
                r"(?:EUR|USD|GBP|€|\$|£)?\s*" + _AMOUNT,
                "invoice_label:subtotal",
            ),
            (
                r"(?im)^\s*Total\s+" + _AMOUNT + r"\s*(?:EUR|€)\s+"
                + _AMOUNT
                + r"\s*(?:EUR|€)\s*$",
                "invoice_table:base_and_vat",
            ),
        ),
        override_source="explicit:taxable_base",
    )
    vat = cents(vat_override) if vat_override is not None else _vat_from_lines(cleaned)
    vat_source = "explicit:vat" if vat_override is not None else (
        "invoice_label:vat" if vat is not None else ""
    )

    if gross is None and taxable_base is not None and vat is not None:
        gross = cents(taxable_base + vat)
        gross_source = "derived:base_plus_vat"
    if gross is None and suggestion is not None:
        if suggestion.kind == "income" or suggestion.category in _SUGGESTION_IS_GROSS:
            gross = _optional_cents(suggestion.amount_original)
            if gross is not None:
                gross_source = "parser:gross_candidate"
    if taxable_base is None and gross is not None and vat is not None:
        taxable_base = cents(gross - vat)
        taxable_base_source = "derived:gross_minus_vat"
    if vat is None and gross is not None and taxable_base is not None:
        vat = cents(gross - taxable_base)
        vat_source = "derived:gross_minus_base"

    errors: list[str] = []
    if not currency:
        errors.append("Invoice currency is unknown")
    if gross is None:
        errors.append("Invoice gross total is unknown")
    if gross is not None and taxable_base is not None and taxable_base > gross:
        errors.append("Taxable base exceeds invoice gross total")
    if gross is not None and taxable_base is not None and vat is not None:
        if abs(gross - taxable_base - vat) > Decimal("0.01"):
            errors.append("Gross total does not equal taxable base plus VAT")

    return InvoiceAmounts(
        currency=currency,
        gross=gross,
        taxable_base=taxable_base,
        vat=vat,
        gross_source=gross_source,
        taxable_base_source=taxable_base_source,
        vat_source=vat_source,
        errors=tuple(errors),
    )


def review_requirements(
    document_kind: str,
    suggestion: LedgerEntry | None,
    amounts: InvoiceAmounts,
) -> tuple[str, ...]:
    requirements: list[str] = []
    if document_kind == "income_invoice":
        requirements.extend(("confirm_income_recognition", "confirm_iva_treatment"))
    elif document_kind == "expense_invoice":
        requirements.extend(("confirm_business_purpose", "confirm_irpf_deductible_amount"))
        requirements.append("confirm_iva_treatment")
        if suggestion is not None and suggestion.category == "asset_review":
            requirements.append("decide_expense_or_amortizable_asset")
        if suggestion is not None and suggestion.category == "home_utility_review":
            requirements.append("confirm_business_use_percentage")
    if amounts.currency and amounts.currency != "EUR":
        requirements.append("attach_official_or_settlement_fx")
    requirements.extend(f"resolve_amount_error:{error}" for error in amounts.errors)
    return tuple(dict.fromkeys(requirements))


def extract_invoice_number(text: str) -> str | None:
    text = text.replace("\x00", "-")
    patterns = (
        r"Invoice No\s*:\s*([A-Z0-9][A-Z0-9._/-]+)",
        r"Factura\s*(?:N[º°o]|№)\s*:\s*([A-Z0-9][A-Z0-9._/-]+)",
        r"N[º°o]\s*de factura\s*:\s*([A-Z0-9][A-Z0-9._/-]+)",
        r"(?:Invoice number|Número de factura|Número del documento)\s*:?\s*([A-Z0-9][A-Z0-9._/-]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _value(
    override: Decimal | None,
    text: str,
    patterns: tuple[tuple[str, str], ...],
    *,
    override_source: str,
) -> tuple[Decimal | None, str]:
    if override is not None:
        return cents(override), override_source
    for pattern, source in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
        if not matches:
            continue
        raw = matches[-1]
        if isinstance(raw, tuple):
            raw = raw[0]
        return cents(parse_amount(str(raw))), source
    return None, ""


def _vat_from_lines(text: str) -> Decimal | None:
    candidates: list[Decimal] = []
    for line in text.splitlines():
        if not re.search(r"\b(?:VAT|IVA)\b", line, re.IGNORECASE):
            continue
        if re.search(
            r"Base imponible\s+IVA\s+Tasa|Precio total\s+\(incl\. IVA\)",
            line,
            re.IGNORECASE,
        ):
            continue
        values = re.findall(
            r"(?:EUR|USD|GBP|€|\$|£)\s*" + _AMOUNT + r"|" + _AMOUNT + r"\s*(?:EUR|USD|GBP|€|\$|£)",
            line,
            re.IGNORECASE,
        )
        for value in values:
            raw = next((part for part in value if part), "") if isinstance(value, tuple) else value
            if raw:
                candidates.append(cents(parse_amount(raw)))
    return candidates[-1] if candidates else None


def _currency(explicit: str | None, suggestion: LedgerEntry | None, text: str) -> str:
    if explicit:
        return explicit.strip().upper()
    if suggestion is not None and suggestion.currency:
        return suggestion.currency.strip().upper()
    codes = re.findall(r"\b(EUR|USD|GBP)\b", text, re.IGNORECASE)
    if codes:
        return codes[-1].upper()
    symbols = [symbol for symbol in _CURRENCY_BY_SYMBOL if symbol in text]
    return _CURRENCY_BY_SYMBOL[symbols[-1]] if symbols else ""


def _optional_cents(value: Decimal | None) -> Decimal | None:
    return cents(value) if value is not None else None


def _money(value: Decimal | None) -> str | None:
    return None if value is None else f"{cents(value):.2f}"
