"""Reusable synthetic fixtures shared by backend test suites."""

from __future__ import annotations
import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest
from autonomo_taxes import operational_cli
from autonomo_taxes.cli import main
from autonomo_taxes.fx_reference import (
    ECBRateObservation,
    ECBRateResult,
    FXRateUnavailableError,
)
from autonomo_taxes.ledger_db import LedgerDB, StaleRowVersionError, initialize
RATE_DATE = date(2026, 7, 1)
UNITS_PER_EUR = "1.2500"
ANNOTATED_REFERENCE = (
    "Synthetic official bulletin (rate not verified against the ECB reference: "
    "deviates 12.50% from 0.80000000 EUR per USD on 2026-07-01)"
)

RATE_DATE = date(2026, 7, 1)

UNITS_PER_EUR = "1.2500"

def synthetic_reference(
    units: str = UNITS_PER_EUR,
    currency: str = "USD",
    rate_date: date = RATE_DATE,
) -> ECBRateResult:
    units_per_eur = Decimal(units)
    raw = json.dumps(
        {"currency": currency, "date": rate_date.isoformat(), "value": units},
        sort_keys=True,
        separators=(",", ":"),
    )
    observation = ECBRateObservation(
        currency=currency,
        rate_date=rate_date,
        units_per_eur=units_per_eur,
        eur_per_unit=Decimal(1) / units_per_eur,
        source_url="https://example.invalid/ecb/EXR/D.USD.EUR.SP00.A",
        raw_observation=raw,
        raw_observation_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )
    return ECBRateResult(status="exact", observation=observation)
