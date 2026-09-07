"""Reusable synthetic fixtures shared by backend test suites."""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import sqlite3
import threading
import pytest
from autonomo_taxes import ledger_db as ledger_module
from autonomo_taxes.history_migration import (
    _prune_unreferenced_migration_counterparty, _prune_superseded_source_book_records,
    _upsert_counterparty,
)
from autonomo_taxes.counterparty_names import (
    CounterpartyMatchError, identity_conflicts, is_oss_non_union_identifier, usable_tax_id,
)
from autonomo_taxes.ledger_db import LedgerDB, SchemaVersionError, StaleRowVersionError
from autonomo_taxes.operational_cli import _upsert_intake_counterparty

def rename(db, party, name="Synthetic Supplier", **kwargs):
    return db.rename_counterparty(
        party["counterparty_id"], display_name=name,
        expected_row_version=party["row_version"], change_source="web", actor=None,
        **kwargs,
    )

def get_party(db, party_id):
    return dict(db.connection.execute(
        "SELECT * FROM counterparties WHERE counterparty_id = ?", (party_id,)
    ).fetchone())
