"""Reusable synthetic fixtures shared by backend test suites."""

from __future__ import annotations
from decimal import Decimal
from pathlib import Path
import tempfile
import pytest
from autonomo_taxes.aeat_books import (
    AeatBookProjectionError,
    build_aeat_book_projection,
    write_aeat_book_projection,
)
from autonomo_taxes.aeat_workbook import build_aeat_workbook_payload
from autonomo_taxes.ledger_db import initialize

def _profile_and_activity(db):
    profile = db.upsert_taxpayer_profile(
        tax_id="X0000000A",
        full_name="Example Taxpayer",
        source_hash="modelo-036-hash",
    )
    return db.upsert_business_activity(
        taxpayer_profile_id=profile["taxpayer_profile_id"],
        activity_key="software-development",
        aeat_activity_code="A",
        aeat_activity_type="05",
        iae_section="2",
        iae_group_epigraph="763",
        description="Programmers and computer analysts",
        starts_on="2023-05-31",
        source_reference="Modelo 036",
        source_hash="activity-source-hash",
    )
