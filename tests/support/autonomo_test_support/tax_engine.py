"""Reusable synthetic fixtures shared by backend test suites."""

from datetime import date
from decimal import Decimal
import pytest
from autonomo_taxes.tax_engine import (
    CalculationBlocked,
    CalculationResult,
    TaxRow,
    calculate_modelo100_business_support,
    calculate_modelo130_rows,
    calculate_modelo303_rows,
    calculate_modelo347_rows,
    calculate_modelo349_rows,
    calculate_modelo390,
    calculate_retention_rows,
)

def row(identifier: str, **overrides) -> TaxRow:
    values = {
        "transaction_id": identifier,
        "tax_date": date(2026, 4, 15),
        "kind": "expense",
        "amount_eur": Decimal("121.00"),
        "taxable_base_eur": Decimal("100.00"),
        "vat_eur": Decimal("21.00"),
        "deductible_irpf_eur": Decimal("100.00"),
        "deductible_vat_eur": Decimal("21.00"),
    }
    values.update(overrides)
    return TaxRow(**values)
