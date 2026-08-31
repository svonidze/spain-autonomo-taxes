from uuid import uuid4

import pytest

from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.local_web import LocalAccountingApp, LocalWebApiError, LocalWebError
from test_local_web import _config
from test_counterparty_names_web import name_server


def test_counterparty_read_views_are_id_scoped_paginated_and_do_not_write(tmp_path):
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        party = db.upsert_counterparty(display_name="Synthetic Shared Name", country_code="GE")
        other = db.upsert_counterparty(display_name="Synthetic Shared Name", country_code="US")
        for index in range(61):
            period = "2026-Q1" if index < 30 else "2026-Q2"
            day = "2026-01-10" if index < 30 else "2026-04-10"
            db.add_transaction(
                external_key=f"contact-op-{index}", period_key=period,
                transaction_date=day, booking_date=day, entry_type="income" if index % 2 else "expense",
                description=f"Synthetic operation {index}", amount_minor=index * 100,
                counterparty_id=party["counterparty_id"],
                lifecycle_status=("received", "approved", "posted", "void")[index % 4],
            )
        db.add_transaction(
            external_key="other-party", period_key="2026-Q2", transaction_date="2026-04-10",
            booking_date="2026-04-10", entry_type="expense", description="Unrelated", amount_minor=1234,
            counterparty_id=other["counterparty_id"],
        )
    before = config.database.read_bytes()
    app = LocalAccountingApp(config)
    app._run_cli = lambda *args, **kwargs: pytest.fail("Read view must not invoke CLI")
    detail = app.counterparty_detail(party["counterparty_id"])
    assert detail["counterparty"]["display_name"] == party["display_name"]
    assert detail["counterparty"]["row_version"] == 1
    assert detail["periods"] == ["2026-Q2", "2026-Q1"]
    first = app.counterparty_transactions(party["counterparty_id"])
    second = app.counterparty_transactions(party["counterparty_id"], offset=first["next_offset"])
    assert len(first["rows"]) == 50 and first["has_more"] is True
    assert len(second["rows"]) == 11 and second["has_more"] is False
    assert first["matching_count"] == 61
    rows = first["rows"] + second["rows"]
    assert len({row["transaction_id"] for row in rows}) == 61
    assert {row["counterparty_id"] for row in rows} == {party["counterparty_id"]}
    assert [row["transaction_date"] for row in rows] == sorted(
        [row["transaction_date"] for row in rows], reverse=True,
    )
    assert {row["lifecycle_status"] for row in rows} == {"received", "approved", "posted", "void"}
    assert all("ui_context" in row for row in rows)
    scoped = app.counterparty_transactions(party["counterparty_id"], period_key="2026-Q1")
    assert len(scoped["rows"]) == 30 and scoped["matching_count"] == 30
    assert config.database.read_bytes() == before


def test_empty_counterparty_keeps_missing_facts_and_empty_records(tmp_path):
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        party = db.upsert_counterparty(display_name="Synthetic Empty")
    app = LocalAccountingApp(config)
    detail = app.counterparty_detail(party["counterparty_id"])
    assert detail["counterparty"]["tax_id"] is None
    assert detail["counterparty"]["email"] is None
    assert detail["periods"] == []
    page = app.counterparty_transactions(party["counterparty_id"])
    assert page["rows"] == [] and page["matching_count"] == 0
    assert page["has_more"] is False


def test_operations_keep_source_document_availability_and_zero_amount(tmp_path):
    config = _config(tmp_path)
    config.archive_root.mkdir(parents=True)
    original = config.archive_root / "synthetic-source.txt"
    original.write_text("Synthetic source document", encoding="utf-8")
    with LedgerDB.initialize(config.database) as db:
        party = db.upsert_counterparty(display_name="Synthetic Source Owner")
        document = db.upsert_document(
            document_type="income_invoice", issued_on="2026-07-01",
            counterparty_id=party["counterparty_id"], document_number="SYNTHETIC-001",
        )
        with db.connection:
            db.connection.execute("UPDATE documents SET source_path=?, mime_type='text/plain' WHERE document_id=?",
                                  (str(original), document["document_id"]))
        db.add_transaction(
            period_key="2026-Q3", transaction_date="2026-07-01", booking_date="2026-07-01",
            entry_type="income", description="Synthetic zero", amount_minor=0,
            counterparty_id=party["counterparty_id"], document_id=document["document_id"],
        )
    result = LocalAccountingApp(config).counterparty_transactions(party["counterparty_id"])
    row = result["rows"][0]
    assert row["amount_eur"] == "0.00"
    assert row["document_id"] == document["document_id"]
    assert row["document_number"] == "SYNTHETIC-001"
    assert row["ui_context"]["documents"][0]["available"] is True
    assert original.read_text(encoding="utf-8") == "Synthetic source document"


@pytest.mark.parametrize("arguments", [
    {"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": 101}, {"limit": True},
    {"period_key": "2026-Q5"},
])
def test_counterparty_page_rejects_invalid_parameters(tmp_path, arguments):
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        party = db.upsert_counterparty(display_name="Synthetic")
    with pytest.raises(LocalWebError):
        LocalAccountingApp(config).counterparty_transactions(party["counterparty_id"], **arguments)


def test_counterparty_routes_keep_existing_history_route_and_protect_reads(name_server):
    app, party, request = name_server
    party_id = party["counterparty_id"]
    status, detail = request("GET", f"/api/counterparties/{party_id}")
    assert status == 200 and detail["counterparty"]["counterparty_id"] == party_id
    assert request("GET", f"/api/counterparties/{party_id}/transactions")[0] == 200
    assert request("GET", f"/api/counterparties/{party_id}/name-history")[0] == 200
    assert request("GET", f"/api/counterparties/{party_id}", Cookie=None)[0] in {400, 403}
    assert request("GET", f"/api/counterparties/{uuid4()}")[0] == 404
    assert request("GET", f"/api/counterparties/{uuid4()}/transactions")[0] == 404
    assert request("GET", "/api/counterparties/not-a-uuid")[0] == 400
    assert request("GET", f"/api/counterparties/{party_id}/transactions?limit=abc")[0] == 400
    assert request("GET", f"/api/counterparties/{party_id}/unrecognized")[0] == 404
    from autonomo_taxes.local_web import _is_spa_route
    assert _is_spa_route(f"/contacts/{party_id}")
    assert not _is_spa_route("/contacts/not-a-uuid")
