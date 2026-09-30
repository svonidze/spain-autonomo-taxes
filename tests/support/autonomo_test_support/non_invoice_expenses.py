"""Reusable synthetic fixtures shared by backend test suites."""

from __future__ import annotations
from dataclasses import replace
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import pytest
from autonomo_taxes.aeat_books import build_aeat_book_projection
from autonomo_taxes.cli import main
from autonomo_taxes.intake import archive_evidence
from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.non_invoice_expenses import (
    NonInvoiceExpenseError,
    NonInvoiceExpenseInput,
    record_non_invoice_expense,
)

def _request(
    archived: Path,
    digest: str,
    *,
    gross: str = "370.59",
    deductible: str = "370.59",
) -> NonInvoiceExpenseInput:
    return NonInvoiceExpenseInput(
        kind="social-security",
        evidence_sha256=digest,
        evidence_mime_type="text/plain",
        archived_path=archived,
        transaction_date=date(2026, 7, 1),
        gross_eur=Decimal(gross),
        deductible_eur=Decimal(deductible),
        reference="RETA-2026-07",
        business_purpose="Mandatory contribution for the registered activity",
        description="Autonomo social security contribution",
        counterparty_name="Synthetic Party 011",
    )
