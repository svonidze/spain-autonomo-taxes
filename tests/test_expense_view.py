from __future__ import annotations

from datetime import date
from http.client import HTTPConnection
import json
from pathlib import Path
import subprocess
import threading

import pytest

from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.local_web import LocalAccountingApp, LocalAccountingServer, LocalWebConfig, LocalWebError
from autonomo_taxes.tax_row_loader import load_tax_rows


PERIOD = "2026-Q3"
AS_OF = date(2026, 8, 20)


def add_expense(db, key, *, amount=120000, deduction=6000, status="approved", tax_code="historical_g03",
                tax_date="2026-09-30", document_date="2025-02-10", party=None, currency="EUR"):
    party = party or db.upsert_counterparty(external_key=f"party-{key}", display_name=f"Example supplier {key}")
    document = db.upsert_document(
        external_key=f"document-{key}", counterparty_id=party["counterparty_id"],
        document_type="gastos_book" if tax_code == "historical_g03" else "expense_invoice",
        document_number=f"TEST-INVOICE-{key}", issued_on=document_date, period_key=PERIOD,
        total_minor=amount, currency=currency, lifecycle_status="approved",
    )
    transaction = db.add_transaction(
        external_key=key, period_key=PERIOD, transaction_date=tax_date, booking_date=tax_date,
        entry_type="expense", description=f"Example expense {key}", amount_minor=amount,
        amount_eur_minor=amount if currency == "EUR" else None, currency=currency,
        direction="debit", lifecycle_status=status, document_id=document["document_id"],
        counterparty_id=party["counterparty_id"],
    )
    db.add_detailed_tax_treatment(
        transaction_id=transaction["transaction_id"], treatment_type="expense", tax_code=tax_code,
        deductible_irpf_minor=deduction, deductible_vat_minor=0, include_modelo130=True,
    )
    return transaction, document, party


def add_asset(db, key, *, document=None, transaction=None):
    return db.add_asset(
        asset_code=f"Example workstation {key}", cost_minor=120000, currency="EUR",
        depreciation_method="linear", source_hash=f"synthetic-{key}",
        document_id=document["document_id"] if document else None,
        acquisition_transaction_id=transaction["transaction_id"] if transaction else None,
    )


def demo_app(root: Path) -> LocalAccountingApp:
    """Entirely invented fixtures, also usable for isolated browser verification."""
    config = LocalWebConfig(
        project_root=root, database=root / "autonomo.sqlite", inbox_root=root / "Inbox",
        archive_root=root / "Evidence", cache_root=root / "cache",
        static_root=Path(__file__).resolve().parents[1] / "src/autonomo_taxes/web_ui",
    )
    with LedgerDB.initialize(config.database) as db:
        for index, (amount, deduction) in enumerate(((120000, 6000), (88000, 4400), (64000, 3200), (48000, 2400))):
            transaction, document, _ = add_expense(db, f"asset-{index}", amount=amount, deduction=deduction)
            add_asset(db, str(index), document=document)
        add_expense(db, "service", amount=18000, deduction=15000, status="posted", tax_code="domestic_input",
                    tax_date="2026-08-04", document_date="2026-08-04")
        transaction, document, _ = add_expense(db, "future-purchase", amount=6000, deduction=5000,
                                             tax_code="domestic_input", document_date="2026-09-30")
        # Owning an asset (and having a schedule) does not turn its purchase into depreciation.
        asset = add_asset(db, "new purchase", transaction=transaction)
        db.add_amortization_entry(asset_id=asset["asset_id"], period_key=PERIOD, amount_minor=250, source_hash="synthetic-schedule")
    return LocalAccountingApp(config, session_token="test-expense-session")


def test_expense_projection_preserves_accounting_and_old_totals(tmp_path):
    app = demo_app(tmp_path)
    with LedgerDB.open(app.config.database) as db:
        before = list(db.connection.iterdump())
        tax_before = load_tax_rows(db, 2026, include_approved_periods={PERIOD})
    result = app.expenses(PERIOD, today=AS_OF)
    assert result["period_counts"] == {"purchase": 2, "amortization": 4}
    assert result["summary"]["amortization"]["approved"]["amount_eur"] == "160.00"
    assert result["summary"]["amortization"]["posted"]["amount_eur"] == "0.00"
    assert result["summary"]["purchase"]["posted"]["amount_eur"] == "180.00"
    assert result["summary"]["purchase"]["future_approved"]["amount_eur"] == "60.00"
    for row in result["rows"]:
        assert row["view_as_of"] == AS_OF.isoformat()
        assert row["ui_context"]["domain"] == "transaction"
        if row["expense_kind"] == "amortization":
            assert row["document_issued_on"] == "2025-02-10"
            assert row["is_future_dated"] is True
            assert row["asset_match_count"] == 1
    dashboard = app.dashboard(PERIOD)
    assert dashboard["totals"]["forecast"]["expense_gross_eur"] == "3260.00"
    assert dashboard["totals"]["forecast"]["deductible_irpf_eur"] == "210.00"
    assert dashboard["totals"]["actual"]["expense_gross_eur"] == "180.00"
    assert dashboard["expense_summary"]["amortization"]["approved"]["amount_eur"] == "160.00"
    assert all("expense_kind" in row for row in dashboard["recent_transactions"])
    assert isinstance(app.transactions(PERIOD), list)
    with LedgerDB.open(app.config.database) as db:
        assert load_tax_rows(db, 2026, include_approved_periods={PERIOD}) == tax_before
        assert list(db.connection.iterdump()) == before


def test_asset_links_ambiguity_precedence_and_issue_counts(tmp_path):
    app = demo_app(tmp_path)
    with LedgerDB.open(app.config.database) as db:
        transaction, document, party = add_expense(db, "inferred")
        source = db.upsert_document(external_key="annual", document_type="xolo_annual_asset_evidence",
                                    issued_on=document["issued_on"], counterparty_id=party["counterparty_id"], lifecycle_status="approved")
        inferred = add_asset(db, "inferred", document=source)
        db.add_validation_issue(period_key=PERIOD, issue_code="example_warning", severity="warning",
                                message="Synthetic check", subject_table="transactions", subject_id=transaction["transaction_id"])
    row = app.expenses(PERIOD, query="workstation inferred")["rows"][0]
    assert row["asset_id"] == inferred["asset_id"]
    assert row["asset_match_method"] == "inferred"
    assert row["open_issue_count"] == 1
    with LedgerDB.open(app.config.database) as db:
        add_asset(db, "second inferred", document=source)
    assert app.expenses(PERIOD, query="workstation inferred")["rows"] == []
    row = app.expenses(PERIOD, query="TEST-INVOICE-inferred")["rows"][0]
    assert row["asset_id"] is None and row["asset_match_count"] == 2
    assert row["open_issue_count"] == 1
    with LedgerDB.open(app.config.database) as db:
        direct = add_asset(db, "direct", transaction=transaction)
    row = app.expenses(PERIOD, query="workstation direct")["rows"][0]
    assert row["asset_id"] == direct["asset_id"] and row["asset_match_count"] == 1
    with LedgerDB.open(app.config.database) as db:
        add_asset(db, "second direct", transaction=transaction)
    row = app.expenses(PERIOD, query="TEST-INVOICE-inferred")["rows"][0]
    assert row["asset_match_count"] == 2 and row["asset_description"] is None
    assert row["open_issue_count"] == 1


@pytest.mark.parametrize("today,future", [(date(2026, 9, 29), True), (date(2026, 9, 30), False), (date(2026, 10, 1), False)])
def test_live_server_date_and_no_automatic_posting(tmp_path, today, future):
    app = demo_app(tmp_path)
    cache = app.config.cache_root / PERIOD / "dashboard.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps({"period": PERIOD, "as_of": "2020-01-01"}))
    result = app.expenses(PERIOD, query="asset-0", today=today)
    assert result["as_of"] == today.isoformat()
    row = result["rows"][0]
    assert row["is_future_dated"] is future
    assert any(item["source_code"] == "future_dated" for item in row["ui_context"]["posting"]["blockers"]) is future
    assert row["lifecycle_status"] == "approved"
    assert result["summary"]["amortization"]["future_approved"]["count"] == (4 if future else 0)


def test_search_and_pagination_do_not_change_whole_quarter_totals(tmp_path):
    app = demo_app(tmp_path)
    with LedgerDB.open(app.config.database) as db:
        for index in range(260):
            add_expense(db, f"page-{index}", amount=100, deduction=100, tax_code="domestic_input")
    first = app.expenses(PERIOD, today=AS_OF)
    second = app.expenses(PERIOD, today=AS_OF, offset=first["next_offset"])
    assert first["has_more"] and len(first["rows"]) == 250
    assert second["has_more"] is False and len(second["rows"]) == 16
    assert len({row["transaction_id"] for row in first["rows"] + second["rows"]}) == 266
    assert first["summary"] == second["summary"]
    assert first["view_revision"] == second["view_revision"]
    searched = app.expenses(PERIOD, query="WORKSTATION 0", limit=1, today=AS_OF)
    assert searched["matching_counts"] == {"purchase": 0, "amortization": 1}
    assert searched["summary"] == first["summary"]
    assert searched["view_revision"] == first["view_revision"]
    with LedgerDB.open(app.config.database) as db:
        add_expense(db, "changed-snapshot", amount=100, deduction=100, tax_code="domestic_input")
    assert app.expenses(PERIOD, today=AS_OF)["view_revision"] != first["view_revision"]
    assert app.expenses(PERIOD, query="' OR 1=1 --")["matching_count"] == 0
    for options in ({"offset": -1}, {"limit": 0}, {"limit": 501}):
        with pytest.raises(LocalWebError):
            app.expenses(PERIOD, **options)


def test_zero_negative_missing_and_terminal_amounts(tmp_path):
    app = demo_app(tmp_path)
    with LedgerDB.open(app.config.database) as db:
        for key, deduction in (("zero", 0), ("negative", -300), ("missing", None)):
            add_expense(db, key, deduction=deduction)
        for status in ("duplicate", "rejected", "void", "needs_review"):
            add_expense(db, status, deduction=500000, status=status)
    result = app.expenses(PERIOD, today=AS_OF)
    bucket = result["summary"]["amortization"]["approved"]
    assert bucket == {"count": 7, "missing_amount_count": 1, "amount_eur": None}
    amounts = {row["document_number"]: row["deductible_irpf_eur"] for row in result["rows"]}
    assert amounts["TEST-INVOICE-zero"] == "0.00"
    assert amounts["TEST-INVOICE-negative"] == "-3.00"
    assert amounts["TEST-INVOICE-missing"] is None
    assert result["summary"]["amortization"]["reviewed_total"]["count"] == 7


def test_expense_http_endpoint_is_session_protected_and_validates_paging(tmp_path):
    app = demo_app(tmp_path)
    server = LocalAccountingServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", f"/api/expenses?period={PERIOD}")
        response = connection.getresponse()
        denied = json.loads(response.read())
        assert response.status == 400  # Missing-cookie guard remains unchanged.
        assert denied["code"] == "session_forbidden"
        assert "rows" not in denied
        for suffix, expected in (("&limit=1", 200), ("&offset=-1", 400), ("&limit=invalid", 400)):
            connection.request("GET", f"/api/expenses?period={PERIOD}{suffix}", headers={"Cookie": "autonomo_session=test-expense-session"})
            response = connection.getresponse()
            payload = json.loads(response.read())
            assert response.status == expected
            if expected == 200:
                assert len(payload["rows"]) == 1 and payload["has_more"]
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
