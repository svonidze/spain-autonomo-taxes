"""OSS classification refreshes old hashes without rewriting closed periods."""
import hashlib
import json
from decimal import Decimal

import pytest

from autonomo_taxes.history_migration import migrate_xolo_history
from autonomo_taxes.ledger_db import ClosedPeriodError, LedgerDB
from autonomo_taxes.tax_engine import CalculationBlocked, calculate_modelo303_rows
from autonomo_taxes.tax_row_loader import load_tax_rows
from autonomo_test_support.history_migration import SOURCE_BOOK_FIELDS, _load_csv, _write_csv, _write_fixture_csvs


def _source(tmp_path):
    source, _ = _write_fixture_csvs(tmp_path)
    row = _load_csv(source)[1]
    row.update(supplier="Synthetic OSS Upgrade Supplier", document_number="SYNTH-OSS-UPGRADE",
               original_currency="EUR", original_amount="1000.00", gross_eur="1000.00",
               invoice_total_eur="1000.00", taxable_base_eur="1000.00", vat_rate_percent="21%",
               vat_eur="210.00", deductible_vat_eur="210.00", operation_key="", reverse_charge="S",
               counterparty_country_code="IE", counterparty_vat_id="EU123456789")
    _write_csv(source, SOURCE_BOOK_FIELDS, [row])
    return source, row


def _legacy_hash(row, kind):
    payload = {"kind": kind, "rule_version": "official-source-book-v6", "row": row}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _seed_old_classification(db, source, row):
    migrate_xolo_history(db, source)
    db.connection.execute("UPDATE transactions SET source_hash=?", (_legacy_hash(row, "transaction"),))
    db.connection.execute(
        "UPDATE tax_treatments SET source_hash=?, tax_code='eu_service_expense', aeat_operation_key='09', aeat_reverse_charge=0",
        (_legacy_hash(row, "tax_treatment"),),
    )
    db.connection.commit()


def _financial_state(db):
    return {table: [dict(row) for row in db.connection.execute(f"SELECT * FROM {table}")]
            for table in ("transactions", "tax_treatments", "documents", "counterparties")}


def test_old_oss_classification_refreshes_once_and_uses_non_eu_casillas(tmp_path):
    source, row = _source(tmp_path)
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        _seed_old_classification(db, source, row)
        migrate_xolo_history(db, source)
        treatment = db.connection.execute("SELECT * FROM tax_treatments").fetchone()
        assert treatment["tax_code"] == "non_eu_service_expense"
        assert treatment["aeat_operation_key"] == "01"
        assert treatment["aeat_reverse_charge"] == 1
        assert treatment["source_hash"] != _legacy_hash(row, "tax_treatment")
        report = calculate_modelo303_rows(load_tax_rows(db, 2026, mode="verify_history"), year=2026, quarter=2)
        assert report.values["10"] == Decimal("0")
        assert report.values["12"] == Decimal("1000")
        assert report.values["13"] == Decimal("210")
        state = _financial_state(db)
        migrate_xolo_history(db, source)
        assert _financial_state(db) == state


def test_oss_refresh_leaves_closed_financial_records_unchanged(tmp_path):
    source, row = _source(tmp_path)
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        _seed_old_classification(db, source, row)
        db.connection.execute("UPDATE periods SET status='closed' WHERE period_key='2026-Q2'")
        db.connection.commit()
        state = _financial_state(db)
        with pytest.raises(ClosedPeriodError, match="immutable"):
            migrate_xolo_history(db, source)
        assert _financial_state(db) == state
        with pytest.raises(CalculationBlocked, match="OSS non-Union"):
            calculate_modelo303_rows(load_tax_rows(db, 2026, mode="verify_history"), year=2026, quarter=2)


def test_source_key09_with_oss_id_cannot_produce_a_misclassified_303(tmp_path):
    source, row = _source(tmp_path)
    row["operation_key"] = "09"
    _write_csv(source, SOURCE_BOOK_FIELDS, [row])
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        migrate_xolo_history(db, source)
        with pytest.raises(CalculationBlocked, match="OSS non-Union"):
            calculate_modelo303_rows(load_tax_rows(db, 2026, mode="verify_history"), year=2026, quarter=2)
