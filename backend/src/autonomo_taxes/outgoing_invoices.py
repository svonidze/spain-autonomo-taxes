from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any, Iterable, Mapping

from .counterparty_names import is_oss_non_union_identifier
from .tax_rules import (
    EU_COUNTRY_CODES,
    EU_REVERSE_CHARGE_TAX_CODES,
    EXEMPT_INCOME_PROVISIONS,
    INVOICE_ISSUE_DEADLINE_CODE,
    INVOICE_ISSUE_DEADLINE_SOURCE,
    INVOICE_MENTION_NOT_SUBJECT_TEXT,
    INVOICE_MENTION_REVERSE_CHARGE_TEXT,
    INVOICE_MENTION_SOURCES,
    OUTSIDE_SPAIN_SERVICE_TAX_CODES,
    invoice_issue_deadline_warning,
)


@dataclass(frozen=True)
class OutgoingInvoiceLine:
    line_number: int
    description: str
    quantity: str
    unit_amount_minor: int
    tax_code: str
    channel_tax_code: str
    tax_rate_basis_points: int
    subtotal_minor: int
    tax_minor: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutgoingInvoiceTotals:
    subtotal_minor: int
    vat_minor: int
    withholding_minor: int
    total_minor: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def normalize_invoice_lines(lines: Iterable[Mapping[str, Any]]) -> tuple[OutgoingInvoiceLine, ...]:
    normalized: list[OutgoingInvoiceLine] = []
    for line_number, source in enumerate(lines, start=1):
        description = str(source.get("description") or "").strip()
        if not description:
            raise ValueError(f"Invoice line {line_number} requires description")

        quantity = _positive_decimal(source.get("quantity", "1"), f"line {line_number} quantity")
        unit_amount_minor = _non_negative_int(
            source.get("unit_amount_minor"),
            f"line {line_number} unit_amount_minor",
        )
        if unit_amount_minor == 0:
            raise ValueError(f"Invoice line {line_number} unit_amount_minor must be positive")

        tax_code = str(source.get("tax_code") or "").strip()
        if not tax_code or tax_code == "unknown":
            raise ValueError(f"Invoice line {line_number} requires a reviewed tax_code")
        channel_tax_code = str(source.get("channel_tax_code") or "").strip().upper()
        if not channel_tax_code or channel_tax_code == "UNKNOWN":
            raise ValueError(
                f"Invoice line {line_number} requires a reviewed channel_tax_code"
            )
        tax_rate_basis_points = _basis_points(
            source.get("tax_rate_basis_points", 0),
            f"line {line_number} tax_rate_basis_points",
        )
        if tax_code in {"outside_scope", "not_subject_place_of_supply"} and tax_rate_basis_points:
            raise ValueError(
                f"Invoice line {line_number} outside-scope tax_code requires zero tax rate"
            )
        if tax_code in {"outside_scope", "not_subject_place_of_supply"} and channel_tax_code != "N2":
            raise ValueError(
                f"Invoice line {line_number} place-of-supply treatment requires channel_tax_code N2"
            )

        subtotal_minor = _round_minor(quantity * Decimal(unit_amount_minor))
        tax_minor = _round_minor(
            Decimal(subtotal_minor) * Decimal(tax_rate_basis_points) / Decimal(10_000)
        )
        normalized.append(
            OutgoingInvoiceLine(
                line_number=line_number,
                description=description,
                quantity=_decimal_text(quantity),
                unit_amount_minor=unit_amount_minor,
                tax_code=tax_code,
                channel_tax_code=channel_tax_code,
                tax_rate_basis_points=tax_rate_basis_points,
                subtotal_minor=subtotal_minor,
                tax_minor=tax_minor,
            )
        )

    if not normalized:
        raise ValueError("An outgoing invoice requires at least one line")
    tax_codes = {line.tax_code for line in normalized}
    if len(tax_codes) != 1:
        raise ValueError(
            "An outgoing invoice currently requires one tax_code across all lines"
        )
    return tuple(normalized)


def calculate_invoice_totals(
    lines: Iterable[OutgoingInvoiceLine],
    *,
    withholding_rate_basis_points: int = 0,
) -> OutgoingInvoiceTotals:
    rate = _basis_points(withholding_rate_basis_points, "withholding_rate_basis_points")
    materialized = tuple(lines)
    if not materialized:
        raise ValueError("An outgoing invoice requires at least one line")
    subtotal_minor = sum(line.subtotal_minor for line in materialized)
    vat_minor = sum(line.tax_minor for line in materialized)
    withholding_minor = _round_minor(Decimal(subtotal_minor) * Decimal(rate) / Decimal(10_000))
    total_minor = subtotal_minor + vat_minor - withholding_minor
    if total_minor <= 0:
        raise ValueError("Outgoing invoice total must be positive")
    return OutgoingInvoiceTotals(
        subtotal_minor=subtotal_minor,
        vat_minor=vat_minor,
        withholding_minor=withholding_minor,
        total_minor=total_minor,
    )


def canonical_lines_json(lines: Iterable[OutgoingInvoiceLine]) -> str:
    return json.dumps(
        [line.as_dict() for line in lines],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def parse_template_lines(value: str) -> tuple[OutgoingInvoiceLine, ...]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("Invoice template default_lines_json is invalid") from exc
    if not isinstance(payload, list):
        raise ValueError("Invoice template default_lines_json must contain a list")
    return normalize_invoice_lines(payload)


def invoice_line_hash(draft_key: str, line: OutgoingInvoiceLine) -> str:
    payload = json.dumps(
        {"draft_key": draft_key, **line.as_dict()},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_currency(value: str) -> str:
    currency = str(value or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("Invoice currency must be a three-letter alphabetic code")
    return currency


def validate_withholding_rate(value: Any) -> int:
    return _basis_points(value, "withholding_rate_basis_points")


def outgoing_invoice_warnings(draft: Mapping[str, Any]) -> list[dict[str, str]]:
    """Non-blocking checks derived from a stored draft; historical rows stay readable."""
    warnings: list[dict[str, str]] = []
    deadline_message = invoice_issue_deadline_warning(
        accrued_on=date.fromisoformat(str(draft["service_on"])),
        issued_on=date.fromisoformat(str(draft["planned_issue_on"])),
    )
    if deadline_message is not None:
        warnings.append(
            {
                "code": INVOICE_ISSUE_DEADLINE_CODE,
                "message": deadline_message,
                "source": INVOICE_ISSUE_DEADLINE_SOURCE,
            }
        )
    return warnings


def outgoing_invoice_mentions(draft: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Mentions to add in the external invoicing channel; totals are unchanged."""
    lines = draft.get("lines") or []
    tax_code = str(lines[0]["tax_code"]) if lines else ""
    country = str(draft.get("country_code") or "").strip().upper()
    vat_id = str(draft.get("counterparty_vat_id") or "").strip()
    mentions: list[dict[str, Any]] = []
    not_subject = _mention(
        "not_subject_place_of_supply",
        "recommended",
        "practice",
        INVOICE_MENTION_NOT_SUBJECT_TEXT,
        "The service is located outside Spain; citing the non-subject provision is practice.",
    )
    if tax_code in EU_REVERSE_CHARGE_TAX_CODES:
        eu_business = (
            country in EU_COUNTRY_CODES - {"ES"}
            and bool(vat_id)
            and not is_oss_non_union_identifier(vat_id)
            and draft.get("counterparty_roi_status") != "not_registered"
        )
        if eu_business:
            mentions.append(
                _mention(
                    "reverse_charge",
                    "required",
                    "legal_requirement",
                    INVOICE_MENTION_REVERSE_CHARGE_TEXT,
                    "The EU business customer is liable for the VAT.",
                )
            )
            mentions.append(not_subject)
        else:
            mentions.append(_location_review(tax_code, country))
    elif tax_code in OUTSIDE_SPAIN_SERVICE_TAX_CODES:
        if len(country) == 2 and country.isalpha() and country not in EU_COUNTRY_CODES | {"ZZ"}:
            mentions.append(not_subject)
        else:
            mentions.append(_location_review(tax_code, country))
    if tax_code in EXEMPT_INCOME_PROVISIONS:
        article = EXEMPT_INCOME_PROVISIONS[tax_code]
        mentions.append(
            _mention(
                "exempt_provision",
                "required",
                "legal_requirement",
                f"Operación exenta de IVA ({article})",
                f"The operation is exempt under {article}.",
            )
        )
    currency = str(draft.get("currency") or "").upper()
    if int(draft.get("vat_minor") or 0) > 0 and currency != "EUR":
        mentions.append(
            _mention(
                "vat_amount_in_eur",
                "required",
                "legal_requirement",
                None,
                f"Spanish VAT is charged in {currency}; also state the VAT amount in EUR.",
            )
        )
    return mentions


def _location_review(tax_code: str, country: str) -> dict[str, Any]:
    if country in EU_COUNTRY_CODES - {"ES"}:
        return _mention(
            "reverse_charge",
            "unknown_review",
            "legal_requirement",
            None,
            f"tax_code {tax_code} with EU counterparty country {country}: the recorded VAT "
            "id and ROI status do not confirm an EU business customer liable for the VAT "
            "(reverse charge mention required); review before issuing.",
        )
    return _mention(
        "place_of_supply_review",
        "unknown_review",
        "legal_requirement",
        None,
        f"tax_code {tax_code} with counterparty country {country or 'unknown'}: the place "
        "of supply cannot be determined, so the required mentions are unknown; review "
        "before issuing.",
    )


def _mention(
    code: str, status: str, basis: str, text: str | None, message: str
) -> dict[str, Any]:
    return {
        "code": code,
        "status": status,
        "basis": basis,
        "text": text,
        "message": message,
        "source": INVOICE_MENTION_SOURCES[code],
    }


def _positive_decimal(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a decimal number") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{label} must be positive")
    if abs(result.as_tuple().exponent) > 6:
        raise ValueError(f"{label} supports at most six decimal places")
    return result


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must not be negative")
    return value


def _basis_points(value: Any, label: str) -> int:
    result = _non_negative_int(value, label)
    if result > 10_000:
        raise ValueError(f"{label} must be between 0 and 10000")
    return result


def _round_minor(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _decimal_text(value: Decimal) -> str:
    normalized = format(value.normalize(), "f")
    return normalized if "." in normalized else f"{normalized}.0"
