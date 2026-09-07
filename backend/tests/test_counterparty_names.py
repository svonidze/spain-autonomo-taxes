from __future__ import annotations
from autonomo_test_support.counterparty_names import rename, get_party

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






def test_rename_preserves_identity_source_and_only_appends_history(tmp_path):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        party = db.upsert_counterparty(external_key="synthetic-source", display_name="Synthetic Supplier,", country_code="GE")
        document = db.upsert_document(
            counterparty_id=party["counterparty_id"], document_type="expense_invoice",
            issued_on="2026-08-01", total_minor=12345,
        )
        before_documents = [dict(row) for row in db.connection.execute("SELECT * FROM documents")]
        updated = rename(db, party)
        assert {key: value for key, value in updated.items() if key not in {
            "display_name", "name_is_manual", "row_version", "updated_at",
        }} == {key: value for key, value in party.items() if key not in {
            "display_name", "name_is_manual", "row_version", "updated_at",
        }}
        assert updated["display_name"] == "Synthetic Supplier"
        assert updated["name_is_manual"] == 1
        assert updated["row_version"] == party["row_version"] + 1
        assert before_documents == [dict(row) for row in db.connection.execute("SELECT * FROM documents")]
        changes = db.counterparty_name_history(party["counterparty_id"])
        assert len(changes) == 1
        assert changes[0]["old_name"] == "Synthetic Supplier,"
        assert changes[0]["new_name"] == "Synthetic Supplier"
        assert changes[0]["actor"] is None
        assert changes[0]["from_row_version"] == party["row_version"]
        assert changes[0]["to_row_version"] == updated["row_version"]
        assert rename(db, updated) == updated
        with pytest.raises(StaleRowVersionError):
            rename(db, party)
        assert len(db.counterparty_name_history(party["counterparty_id"])) == 1
        assert document["counterparty_id"] == party["counterparty_id"]


@pytest.mark.parametrize("invalid", ["", "   ", "A\nB", "A\rB", "A\tB", "A\u2028B", "A\x00B", None, 123])
def test_invalid_name_does_not_write(tmp_path, invalid):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        party = db.upsert_counterparty(display_name="Synthetic Supplier,")
        with pytest.raises(ValueError):
            rename(db, party, invalid)
        assert get_party(db, party["counterparty_id"]) == party
        assert db.counterparty_name_history(party["counterparty_id"]) == []


def test_history_insert_failure_rolls_back_rename_even_in_outer_transaction(tmp_path):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        party = db.upsert_counterparty(display_name="Synthetic Supplier,")
        db.connection.execute("""CREATE TRIGGER reject_name_history
            BEFORE INSERT ON counterparty_name_changes BEGIN
            SELECT RAISE(ABORT, 'synthetic failure'); END""")
        with db.transaction():
            with pytest.raises(sqlite3.IntegrityError, match="synthetic failure"):
                rename(db, party)
            assert get_party(db, party["counterparty_id"]) == party
        assert get_party(db, party["counterparty_id"]) == party


@pytest.mark.parametrize("tax_id", ["", "TEST-TAX-ID-001"])
def test_reimports_and_intake_reuse_corrected_name_without_version_churn(tmp_path, tax_id):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        source = {"supplier": "Synthetic Supplier,", "counterparty_country_code": "GE",
                  "counterparty_tax_id": tax_id}
        party_id = _upsert_counterparty(db, source)
        original = get_party(db, party_id)
        updated = rename(db, original, "Synthetic Suplier")
        for _ in range(3):
            assert _upsert_counterparty(db, source) == party_id
            assert get_party(db, party_id) == updated
        for name in ("Synthetic Supplier,", "Synthetic Suplier", "Synthetic Supplier"):
            assert _upsert_intake_counterparty(db, None, preferred_name=name) == party_id
        assert db.connection.execute("SELECT COUNT(*) FROM counterparties").fetchone()[0] == 1
        assert get_party(db, party_id) == updated


def test_two_renames_have_one_winner(tmp_path):
    path = tmp_path / "ledger.sqlite"
    with LedgerDB.initialize(path) as db:
        party = db.upsert_counterparty(display_name="Synthetic Supplier,")
    barrier = threading.Barrier(2)

    def attempt(name):
        with LedgerDB.open(path) as db:
            barrier.wait(timeout=5)
            try:
                rename(db, party, name)
                return "saved"
            except StaleRowVersionError:
                return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ["Synthetic A", "Synthetic B"])) == ["saved", "stale"]
    with LedgerDB.open(path) as db:
        assert get_party(db, party["counterparty_id"])["row_version"] == 2
        assert len(db.counterparty_name_history(party["counterparty_id"])) == 1


def test_import_read_before_rename_cannot_overwrite_it(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite"
    with LedgerDB.initialize(path) as db:
        party = db.upsert_counterparty(external_key="intake-name:syntheticsupplier", display_name="Synthetic Supplier,")
    read_done, resume_import = threading.Event(), threading.Event()
    original_find = LedgerDB._find_counterparty

    def delayed_find(db, *args):
        result = original_find(db, *args)
        if threading.current_thread().name.startswith("import-probe"):
            read_done.set()
            assert resume_import.wait(timeout=5)
        return result

    monkeypatch.setattr(LedgerDB, "_find_counterparty", delayed_find)

    def reimport():
        with LedgerDB.open(path) as db:
            return db.upsert_counterparty(external_key=party["external_key"], display_name=party["display_name"])

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="import-probe") as pool:
        pending = pool.submit(reimport)
        assert read_done.wait(timeout=5)
        try:
            with LedgerDB.open(path) as db:
                changed = rename(db, party)
        finally:
            resume_import.set()
        assert pending.result(timeout=5)["display_name"] == changed["display_name"]
    with LedgerDB.open(path) as db:
        assert get_party(db, party["counterparty_id"]) == changed
        assert len(db.counterparty_name_history(party["counterparty_id"])) == 1


def test_manual_counterparty_is_not_pruned(tmp_path):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        party = db.upsert_counterparty(display_name="Synthetic Supplier,")
        changed = rename(db, party)
        _prune_unreferenced_migration_counterparty(db, party["counterparty_id"])
        assert get_party(db, party["counterparty_id"]) == changed


def test_migration_preserves_v19_records(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite"
    with monkeypatch.context() as old_schema:
        old_schema.setattr(ledger_module, "LATEST_SCHEMA_VERSION", 19)
        with LedgerDB.initialize(path) as db:
            original = db.upsert_counterparty(display_name="Synthetic Supplier,")
    with pytest.raises(SchemaVersionError):
        LedgerDB.open(path, read_only=True)
    with LedgerDB.open(path, apply_migrations=True) as db:
        migrated = get_party(db, original["counterparty_id"])
        assert migrated.pop("name_is_manual") == 0
        assert migrated == original
        assert db.counterparty_name_history(original["counterparty_id"]) == []


def test_vat_import_keeps_source_hash_and_version_after_rename(tmp_path):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        source = {"supplier": "Synthetic VAT Supplier,", "counterparty_country_code": "GE",
                  "counterparty_vat_id": "GETEST-TAX-ID-001"}
        party_id = _upsert_counterparty(db, source)
        changed = rename(db, get_party(db, party_id), "Synthetic VAT Supplier")
        for _ in range(3):
            assert _upsert_counterparty(db, source) == party_id
            assert get_party(db, party_id) == changed


def test_matching_vat_does_not_hide_conflicting_tax_id(tmp_path):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        source = {"supplier": "Synthetic VAT Supplier", "counterparty_country_code": "ES",
                  "counterparty_tax_id": "TEST-TAX-ID-001", "counterparty_vat_id": "ESTEST-TAX-ID-001"}
        party_id = _upsert_counterparty(db, source)
        before = get_party(db, party_id)
        with pytest.raises(CounterpartyMatchError, match="conflict"):
            _upsert_counterparty(db, {**source, "counterparty_tax_id": "TEST-TAX-ID-002"},
                                 previous_counterparty_id=party_id)
        assert get_party(db, party_id) == before
        assert not identity_conflicts(before, country_code="ES", tax_id="ESTEST-TAX-ID-001")


@pytest.mark.parametrize("value, expected", [
    ("EU123456789", True),
    ("eu 123 456 789", True),
    ("EU12345678", False),
    ("EU1234567890", False),
    ("IETEST-TAX-ID-004", False),
    ("", False),
    (None, False),
])
def test_oss_non_union_identifier_is_eu_prefix_with_nine_digits(value, expected):
    assert is_oss_non_union_identifier(value) is expected


def test_oss_non_union_identifier_stays_usable_for_matching_but_placeholder_digits_do_not():
    assert usable_tax_id("EU123456789") is True
    assert usable_tax_id("EU000000000") is False


def test_alias_cannot_override_primary_vat_identity(tmp_path):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        party = db.upsert_counterparty(
            external_key="synthetic-primary", display_name="Synthetic VAT Supplier,", country_code="GE",
        )
        db.upsert_counterparty_identity(
            counterparty_id=party["counterparty_id"], identity_kind="vat_id", country_code="GE",
            identifier="GETEST-TAX-ID-001", source_reference="Synthetic registry", source_hash="synthetic-identity",
        )
        changed = rename(db, get_party(db, party["counterparty_id"]), "Synthetic VAT Supplier")
        with pytest.raises(CounterpartyMatchError, match="conflict"):
            _upsert_counterparty(db, {
                "supplier": "Synthetic VAT Supplier,", "counterparty_country_code": "GE",
                "counterparty_vat_id": "GETEST-TAX-ID-002",
            })
        assert get_party(db, party["counterparty_id"]) == changed
        assert db.connection.execute("SELECT COUNT(*) FROM counterparties").fetchone()[0] == 1


def test_superseded_source_cleanup_retains_manual_name_history(tmp_path):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        party = db.upsert_counterparty(display_name="Synthetic Supplier,")
        changed = rename(db, party)
        old = db.upsert_document(
            external_key="old", counterparty_id=party["counterparty_id"],
            document_type="gastos_book", issued_on="2026-08-01",
        )
        new = db.upsert_document(external_key="new", document_type="gastos_book", issued_on="2026-08-01")
        for index, document in enumerate((old, new)):
            db.add_document_source(
                document_id=document["document_id"], source_book_line_id="synthetic-duplicate-line",
                source_hash=f"synthetic-source-{index}",
            )
        db.add_transaction(
            period_key="2026-Q3", transaction_date="2026-08-01", booking_date="2026-08-01",
            entry_type="expense", description="Synthetic", amount_minor=1000, document_id=new["document_id"],
        )
        counts = _prune_superseded_source_book_records(db)
        assert counts["pruned_documents"] == 1
        assert counts["pruned_counterparties"] == 0
        assert get_party(db, party["counterparty_id"]) == changed
        assert len(db.counterparty_name_history(party["counterparty_id"])) == 1


def test_profile_update_and_rename_cannot_both_accept_the_same_version(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite"
    with LedgerDB.initialize(path) as db:
        party = db.upsert_counterparty(display_name="Synthetic Supplier,")
    read_done, release = threading.Event(), threading.Event()
    original_fetch = LedgerDB._fetch_one

    def delayed_fetch(db, sql, params=()):
        value = original_fetch(db, sql, params)
        if threading.current_thread().name.startswith("profile-race") and not read_done.is_set():
            read_done.set()
            assert release.wait(timeout=5)
        return value
    monkeypatch.setattr(LedgerDB, "_fetch_one", delayed_fetch)

    def profile():
        with LedgerDB.open(path) as db:
            try:
                db.set_counterparty_tax_profile(party["counterparty_id"], roi_status="registered", expected_row_version=1)
                return "saved"
            except StaleRowVersionError:
                return "stale"

    def edit():
        with LedgerDB.open(path) as db:
            try:
                rename(db, party)
                return "saved"
            except StaleRowVersionError:
                return "stale"

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="profile-race") as pool:
        profile_result = pool.submit(profile)
        assert read_done.wait(timeout=5)
        edit_result = pool.submit(edit)
        try:
            edit_result.result(timeout=0.15)
        except TimeoutError:
            pass
        finally:
            release.set()
        assert sorted([profile_result.result(timeout=5), edit_result.result(timeout=5)]) == ["saved", "stale"]
    with LedgerDB.open(path) as db:
        current = get_party(db, party["counterparty_id"])
        assert current["row_version"] == 2
        next_change = rename(db, current, "Synthetic Next")
        assert next_change["row_version"] == 3
