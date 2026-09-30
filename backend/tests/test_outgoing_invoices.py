from __future__ import annotations

import pytest

from autonomo_taxes.outgoing_invoices import (
    calculate_invoice_totals,
    normalize_invoice_lines,
)


def test_normalize_invoice_lines_rounds_each_line_in_minor_units() -> None:
    lines = normalize_invoice_lines(
        [
            {
                "description": "Consulting hours",
                "quantity": "1.5",
                "unit_amount_minor": 1001,
                "tax_code": "standard",
                "channel_tax_code": "S1",
                "tax_rate_basis_points": 2100,
            },
            {
                "description": "Additional consulting",
                "quantity": "1",
                "unit_amount_minor": 2000,
                "tax_code": "standard",
                "channel_tax_code": "S1",
                "tax_rate_basis_points": 2100,
            },
        ]
    )

    assert lines[0].subtotal_minor == 1502
    assert lines[0].tax_minor == 315
    assert lines[1].subtotal_minor == 2000
    totals = calculate_invoice_totals(lines, withholding_rate_basis_points=1500)
    assert totals.subtotal_minor == 3502
    assert totals.vat_minor == 735
    assert totals.withholding_minor == 525
    assert totals.total_minor == 3712


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (
            {
                "description": "Unknown",
                "quantity": "1",
                "unit_amount_minor": 100,
                "tax_code": "unknown",
                "channel_tax_code": "UNKNOWN",
            },
            "reviewed tax_code",
        ),
        (
            {
                "description": "Outside scope with tax",
                "quantity": "1",
                "unit_amount_minor": 100,
                "tax_code": "outside_scope",
                "channel_tax_code": "N2",
                "tax_rate_basis_points": 2100,
            },
            "requires zero tax rate",
        ),
        (
            {
                "description": "Bad amount",
                "quantity": "1",
                "unit_amount_minor": 1.5,
                "tax_code": "standard",
                "channel_tax_code": "S1",
            },
            "must be an integer",
        ),
    ],
)
def test_normalize_invoice_lines_fails_closed(line: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_invoice_lines([line])


def test_normalize_invoice_lines_rejects_mixed_tax_codes_early() -> None:
    with pytest.raises(ValueError, match="one tax_code"):
        normalize_invoice_lines(
            [
                {
                    "description": "Outside scope",
                    "quantity": "1",
                    "unit_amount_minor": 100,
                    "tax_code": "outside_scope",
                    "channel_tax_code": "N2",
                },
                {
                    "description": "Domestic",
                    "quantity": "1",
                    "unit_amount_minor": 100,
                    "tax_code": "domestic_output",
                    "channel_tax_code": "S1",
                    "tax_rate_basis_points": 2100,
                },
            ]
        )
