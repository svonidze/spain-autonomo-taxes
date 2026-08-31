from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import replace
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from autonomo_taxes import operational_cli
from autonomo_taxes.books import write_accounting_books
from autonomo_taxes.cli import main
from autonomo_taxes.counterparty_names import CounterpartyMatchError
from autonomo_taxes.history_migration import _upsert_counterparty, migrate_xolo_history
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.non_invoice_expenses import _resolve_counterparty, NonInvoiceExpenseError
from autonomo_taxes.operational_cli import _upsert_intake_counterparty
from test_counterparty_names import get_party, rename
from test_history_migration import _write_fixture_csvs, _load_csv, _write_csv, SOURCE_BOOK_FIELDS
from test_non_invoice_expenses import _request


def test_alias_ambiguity_is_resolved_only_by_identity(tmp_path):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        parties = [
            db.upsert_counterparty(
                external_key=f"source-{index}", tax_id=f"TEST-TAX-ID-{index:03}",
                display_name="Synthetic Common,", country_code="GE",
            ) for index in (1, 2)
        ]
        for index, party in enumerate(parties):
            rename(db, party, f"Synthetic Corrected {index}")
        with pytest.raises(CounterpartyMatchError, match="ambiguous"):
            _upsert_intake_counterparty(db, None, preferred_name="Synthetic Common")
        with pytest.raises(CounterpartyMatchError, match="ambiguous"):
            _upsert_counterparty(db, {"supplier": "Synthetic Common,", "counterparty_country_code": "GE"})
        selected = _upsert_counterparty(db, {
            "supplier": "Synthetic Common,", "counterparty_country_code": "GE",
            "counterparty_tax_id": "TEST-TAX-ID-001",
        })
        assert selected == parties[0]["counterparty_id"]
        assert get_party(db, selected)["display_name"] == "Synthetic Corrected 0"
        assert db.connection.execute("SELECT COUNT(*) FROM counterparties").fetchone()[0] == 2


@pytest.mark.parametrize("explicit_id", [False, True])
def test_non_invoice_expense_accepts_old_name_but_not_unrelated_name(tmp_path, explicit_id):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        party = db.upsert_counterparty(
            display_name="Synthetic Supplier,", country_code="ES", tax_id="TEST-TAX-ID-001",
        )
        rename(db, party, "Synthetic Suplier")
        request = replace(
            _request(Path("synthetic.txt"), "a" * 64),
            counterparty_id=party["counterparty_id"] if explicit_id else None,
            counterparty_name="Synthetic Supplier,", counterparty_country="ES",
            counterparty_tax_id="TEST-TAX-ID-001",
        )
        assert _resolve_counterparty(db.connection, request, existing_transaction=None)["counterparty_id"] == party["counterparty_id"]
        with pytest.raises(NonInvoiceExpenseError, match="name conflicts"):
            _resolve_counterparty(db.connection, replace(request, counterparty_name="Unrelated"), existing_transaction=None)
        with pytest.raises(NonInvoiceExpenseError, match="country conflicts"):
            _resolve_counterparty(db.connection, replace(request, counterparty_country="PT"), existing_transaction=None)


def test_historical_identity_conflict_does_not_reassign_or_create(tmp_path):
    source, reconciliation = _write_fixture_csvs(tmp_path)
    rows = _load_csv(source)
    rows[1].update({"counterparty_country_code": "GE", "counterparty_tax_id": "TEST-TAX-ID-001"})
    _write_csv(source, SOURCE_BOOK_FIELDS, rows)
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        migrate_xolo_history(db, source, reconciliation)
        tables = ("counterparties", "documents", "document_sources", "transactions", "tax_treatments")
        before = {table: [dict(row) for row in db.connection.execute("SELECT * FROM " + table)] for table in tables}
        rows[1].update({"counterparty_country_code": "PT", "counterparty_tax_id": "TEST-TAX-ID-002"})
        _write_csv(source, SOURCE_BOOK_FIELDS, rows)
        with pytest.raises(CounterpartyMatchError, match="Historical counterparty conflict"):
            migrate_xolo_history(db, source, reconciliation)
        after = {table: [dict(row) for row in db.connection.execute("SELECT * FROM " + table)] for table in tables}
        assert before == after


def test_name_and_tax_sheet_edit_have_one_history_event_and_one_version(tmp_path):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        party = db.upsert_counterparty(display_name="Synthetic Supplier,", country_code="GE")
        updated = db.update_counterparty_review(
            party["counterparty_id"], display_name="Synthetic Supplier", tax_id="TEST-TAX-ID-001",
            country_code="GE", vat_id=None, roi_status="unknown", professional_supplier=None,
            retention_expected=None, email=None, phone=None, expected_row_version=party["row_version"],
        )
        assert updated["row_version"] == 2 and updated["name_is_manual"] == 1
        assert updated["tax_id"] == "TEST-TAX-ID-001"
        history = db.counterparty_name_history(party["counterparty_id"])
        assert len(history) == 1 and history[0]["change_source"] == "sheet"
        assert history[0]["to_row_version"] == 2


def test_sheet_failure_rolls_back_batch_without_erasing_concurrent_rename(tmp_path, monkeypatch, capsys):
    path = tmp_path / "ledger.sqlite"
    with LedgerDB.initialize(path) as db:
        parties = [db.upsert_counterparty(display_name=f"Synthetic Supplier {i}") for i in range(3)]
    export = tmp_path / "export"
    assert main(["sheet", "export", "--db", str(path), "--out-dir", str(export)]) == 0
    capsys.readouterr()
    remote = export / "counterparties.csv"
    with remote.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        rows = list(reader)
    for row in rows:
        if row["uuid"] != parties[2]["counterparty_id"]:
            row["display_name"] += " corrected"
            row["row_version"] = "2"
    with remote.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    original_apply = operational_cli._apply_reviewed_sheet_row
    attempts = []
    started = threading.Event()
    def concurrent_rename():
        with LedgerDB.open(path) as other:
            started.set()
            return rename(other, parties[2], "Synthetic Concurrent")
    with ThreadPoolExecutor(max_workers=1) as pool:
        def apply_then_fail(db, *args):
            assert db.connection.in_transaction
            if attempts:
                raise RuntimeError("synthetic late-row failure")
            result = original_apply(db, *args)
            assert db.connection.in_transaction
            attempts.append(pool.submit(concurrent_rename))
            assert started.wait(timeout=5)
            return result
        monkeypatch.setattr(operational_cli, "_apply_reviewed_sheet_row", apply_then_fail)
        with pytest.raises(RuntimeError, match="late-row"):
            operational_cli._cmd_sheet_apply(SimpleNamespace(db=path, remote_csv=remote, tab="counterparties"))
        changed = attempts[0].result(timeout=5)
    with LedgerDB.open(path) as db:
        for party in parties[:2]:
            assert get_party(db, party["counterparty_id"]) == party
            assert db.counterparty_name_history(party["counterparty_id"]) == []
        assert get_party(db, parties[2]["counterparty_id"]) == changed
        assert len(db.counterparty_name_history(parties[2]["counterparty_id"])) == 1


def test_shared_sheet_mutations_do_not_commit_an_outer_transaction(tmp_path):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        document = db.upsert_document(document_type="expense_invoice", issued_on="2026-08-01", period_key="2026-Q3")
        transaction = db.add_transaction(
            period_key="2026-Q3", transaction_date="2026-08-01", booking_date="2026-08-01",
            entry_type="expense", description="Synthetic", amount_minor=1000,
        )
        treatment = db.add_detailed_tax_treatment(transaction_id=transaction["transaction_id"], treatment_type="expense", tax_code="historical_g03")
        issues = [db.add_validation_issue(period_key="2026-Q3", issue_code=f"synthetic-{i}", severity="warning", message="Synthetic") for i in range(2)]
        asset = db.add_asset(asset_code="Synthetic", cost_minor=1000, currency="EUR", depreciation_method="linear", source_hash="synthetic-asset")
        tables = ("documents", "transactions", "tax_treatments", "validation_issues", "assets")
        before = {table: [dict(row) for row in db.connection.execute("SELECT * FROM " + table)] for table in tables}
        with pytest.raises(RuntimeError, match="rollback"):
            with db.transaction():
                db.transition_document(document["document_id"], lifecycle_status="extracted", expected_row_version=1)
                assert db.connection.in_transaction
                db.transition_transaction(transaction["transaction_id"], lifecycle_status="extracted", expected_row_version=1)
                assert db.connection.in_transaction
                db.add_detailed_tax_treatment(transaction_id=transaction["transaction_id"], treatment_type="expense", tax_code="historical_g03", notes="changed", expected_row_version=treatment["row_version"])
                assert db.connection.in_transaction
                db.resolve_issue(issues[0]["validation_issue_id"], reason="Synthetic", expected_row_version=1)
                db.waive_issue(issues[1]["validation_issue_id"], reason="Synthetic", expected_row_version=1)
                assert db.connection.in_transaction
                db.update_asset_decision(asset["asset_id"], advisor_decision="confirmed", advisor_decision_on="2026-08-01", expected_row_version=1)
                assert db.connection.in_transaction
                raise RuntimeError("rollback")
        assert before == {table: [dict(row) for row in db.connection.execute("SELECT * FROM " + table)] for table in tables}


def test_new_books_use_corrected_name_without_rewriting_saved_books(tmp_path):
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        party = db.upsert_counterparty(display_name="Synthetic Supplier,")
        transaction = db.add_transaction(
            period_key="2026-Q3", transaction_date="2026-08-01", booking_date="2026-08-01",
            entry_type="expense", description="Synthetic", amount_minor=1000,
            counterparty_id=party["counterparty_id"], lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(transaction_id=transaction["transaction_id"], treatment_type="expense", tax_code="historical_g03", deductible_irpf_minor=1000)
        old = write_accounting_books(db, tmp_path / "before", period_key="2026-Q3")
        original_bytes = old.files["expense"].read_bytes()
        rename(db, party)
        new = write_accounting_books(db, tmp_path / "after", period_key="2026-Q3")
        assert old.files["expense"].read_bytes() == original_bytes
        with old.files["expense"].open(encoding="utf-8-sig") as handle:
            before = next(csv.DictReader(handle))
        with new.files["expense"].open(encoding="utf-8-sig") as handle:
            after = next(csv.DictReader(handle))
        assert before.pop("counterparty_name") == "Synthetic Supplier,"
        assert after.pop("counterparty_name") == "Synthetic Supplier"
        assert before == after
