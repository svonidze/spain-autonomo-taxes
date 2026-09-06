"""Upgrade fixtures retain the source hashes used before clave-09 inference."""
import hashlib
import json

import pytest

from autonomo_taxes.history_migration import migrate_xolo_history
from autonomo_taxes.ledger_db import ClosedPeriodError, LedgerDB
from test_history_migration import SOURCE_BOOK_FIELDS, _load_csv, _write_csv, _write_fixture_csvs


def _source(tmp_path):
    source, _ = _write_fixture_csvs(tmp_path)
    row = _load_csv(source)[1]
    row.update(supplier="Synthetic Upgrade Supplier", document_number="SYNTH-UPGRADE-09",
               original_currency="EUR", original_amount="1000.00", gross_eur="1000.00",
               invoice_total_eur="1000.00", taxable_base_eur="1000.00", vat_rate_percent="21%",
               vat_eur="0.00", deductible_vat_eur="105.00", operation_key="09", reverse_charge="",
               counterparty_country_code="IE", counterparty_vat_id="IETEST-UPGRADE-09")
    _write_csv(source, SOURCE_BOOK_FIELDS, [row])
    return source, row


def _legacy_hash(row, kind):
    payload = {"kind": kind, "rule_version": "official-source-book-v6", "row": row}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _seed_pre_inference_state(db, source, row):
    migrate_xolo_history(db, source)
    # These are the exact hash contract and stored amounts of the old importer.
    db.connection.execute("UPDATE transactions SET source_hash = ?", (_legacy_hash(row, "transaction"),))
    db.connection.execute("UPDATE tax_treatments SET source_hash = ?, vat_minor = 0", (_legacy_hash(row, "tax_treatment"),))
    db.connection.commit()


def _financial_state(db):
    return {table: [dict(row) for row in db.connection.execute(f"SELECT * FROM {table}")]
            for table in ("transactions", "tax_treatments", "documents", "counterparties")}


def test_old_clave09_hash_refreshes_once_without_changing_deduction(tmp_path):
    source, row = _source(tmp_path)
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        _seed_pre_inference_state(db, source, row)
        migrate_xolo_history(db, source)
        treatment = db.connection.execute("SELECT * FROM tax_treatments").fetchone()
        assert treatment["vat_minor"] == 21000
        assert treatment["deductible_vat_minor"] == 10500
        assert treatment["source_hash"] != _legacy_hash(row, "tax_treatment")
        state = _financial_state(db)
        migrate_xolo_history(db, source)
        assert _financial_state(db) == state


def test_changed_clave09_rule_does_not_rewrite_closed_history(tmp_path):
    source, row = _source(tmp_path)
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        _seed_pre_inference_state(db, source, row)
        db.connection.execute("UPDATE periods SET status='closed' WHERE period_key='2026-Q2'")
        db.connection.commit()
        state = _financial_state(db)
        with pytest.raises(ClosedPeriodError, match="immutable"):
            migrate_xolo_history(db, source)
        assert _financial_state(db) == state
