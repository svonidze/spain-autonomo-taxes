from __future__ import annotations

from datetime import date
from decimal import Decimal

from autonomo_taxes.intake_bundle import (
    extract_invoice_amounts,
    extract_invoice_number,
    review_requirements,
)
from autonomo_taxes.parsers import LedgerEntry


def _suggestion(*, category: str, amount: str, currency: str = "EUR") -> LedgerEntry:
    value = Decimal(amount)
    return LedgerEntry(
        kind="expense",
        date=date(2026, 6, 1),
        document="invoice.pdf",
        counterparty="Supplier",
        description="invoice.pdf",
        amount_original=value,
        currency=currency,
        amount_eur=value if currency == "EUR" else None,
        deductible_eur=None,
        category=category,
        confidence="high",
        review_required=category == "asset_review",
    )


def test_xolo_invoice_keeps_gross_base_and_vat_separate() -> None:
    text = """
    FACTURA
    Factura №: SYNTH-DOCUMENT-011
    Fecha de emisión: 01/06/2026
    Base (sin IVA): 59,00 EUR
    VAT 21% 12,39 EUR
    Total: 71,39 EUR
    """

    amounts = extract_invoice_amounts(
        text,
        _suggestion(category="accounting_service_domestic", amount="59.00"),
    )

    assert extract_invoice_number(text) == "SYNTH-DOCUMENT-011"
    assert amounts.gross == Decimal("71.39")
    assert amounts.taxable_base == Decimal("59.00")
    assert amounts.vat == Decimal("12.39")
    assert amounts.errors == ()


def test_large_apple_invoice_is_an_asset_review_candidate() -> None:
    text = """
    Número de factura: SYNTH-DOCUMENT-031
    Fecha de factura: 08.04.2026
    Base imponible IVA Tasa de IVA
    1.647,93 346,07 21,00 %
    Precio total (incl. IVA) EUR 1.994,00
    """
    suggestion = _suggestion(category="asset_review", amount="1647.93")

    amounts = extract_invoice_amounts(text, suggestion)
    requirements = review_requirements("expense_invoice", suggestion, amounts)

    assert extract_invoice_number(text) == "SYNTH-DOCUMENT-031"
    assert amounts.gross == Decimal("1994.00")
    assert amounts.taxable_base == Decimal("1647.93")
    assert amounts.vat == Decimal("346.07")
    assert "decide_expense_or_amortizable_asset" in requirements


def test_explicit_amounts_are_validated_but_never_silently_balanced() -> None:
    amounts = extract_invoice_amounts(
        "Unreadable scan",
        None,
        gross_override=Decimal("100.00"),
        taxable_base_override=Decimal("90.00"),
        vat_override=Decimal("15.00"),
        currency_override="EUR",
    )

    assert amounts.draft_ready is True
    assert amounts.gross == Decimal("100.00")
    assert amounts.errors == ("Gross total does not equal taxable base plus VAT",)


def test_foreign_invoice_requires_fx_review() -> None:
    suggestion = _suggestion(category="domains_foreign", amount="75.98", currency="USD")
    amounts = extract_invoice_amounts("Total $75.98", suggestion)

    assert amounts.gross == Decimal("75.98")
    assert "attach_official_or_settlement_fx" in review_requirements(
        "expense_invoice", suggestion, amounts
    )


def test_zero_vat_subtotal_can_be_gross_for_supported_foreign_service() -> None:
    suggestion = _suggestion(category="dev_tools_foreign", amount="24.20", currency="USD")

    amounts = extract_invoice_amounts("Total excluding tax $24.20", suggestion)

    assert amounts.gross == Decimal("24.20")
    assert amounts.taxable_base == Decimal("24.20")
    assert amounts.vat == Decimal("0.00")
    assert amounts.errors == ()


def test_extracted_vat_mismatch_is_preserved_for_review() -> None:
    text = """
    Base (sin IVA): 90,00 EUR
    VAT 21% 15,00 EUR
    Total: 100,00 EUR
    """

    amounts = extract_invoice_amounts(
        text,
        _suggestion(category="accounting_service_domestic", amount="90.00"),
    )

    assert amounts.vat == Decimal("15.00")
    assert amounts.errors == ("Gross total does not equal taxable base plus VAT",)
