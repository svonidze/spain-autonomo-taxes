from __future__ import annotations

from http.client import HTTPConnection
import json
import sqlite3
import threading
from uuid import uuid4

import pytest

from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.local_web import LocalAccountingApp, LocalAccountingServer, LocalWebApiError
from test_local_web import _config


@pytest.fixture
def name_server(tmp_path):
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        party = db.upsert_counterparty(display_name="Synthetic Supplier,", country_code="GE")
    app = LocalAccountingApp(config, session_token="synthetic-session")
    server = LocalAccountingServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def request(method, path, payload=None, **overrides):
        headers = {
            "Host": f"127.0.0.1:{port}", "Cookie": "autonomo_session=synthetic-session",
            "Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json",
        }
        headers.update(overrides)
        headers = {key: value for key, value in headers.items() if value is not None}
        conn = HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()
    try:
        yield app, party, request
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_name_http_round_trip_and_stale_retry(name_server):
    app, party, request = name_server
    party_id = party["counterparty_id"]
    path = f"/api/counterparties/{party_id}/rename"
    assert request("GET", f"/api/counterparties/{party_id}/name-history") == (
        200, {"counterparty_id": party_id, "changes": []},
    )
    payload = {"display_name": "Synthetic Supplier", "expected_row_version": 1}
    status, saved = request("POST", path, payload)
    assert status == 200
    assert saved == {
        "counterparty_id": party_id, "display_name": "Synthetic Supplier",
        "row_version": 2, "name_is_manual": True, "changed": True,
    }
    status, conflict = request("POST", path, payload)
    assert status == 409
    assert conflict["code"] == "stale_counterparty"
    assert conflict["current"]["display_name"] == "Synthetic Supplier"
    assert conflict["current"]["row_version"] == 2
    status, repeated = request("POST", path, {**payload, "expected_row_version": 2})
    assert status == 200 and repeated["changed"] is False
    status, history = request("GET", f"/api/counterparties/{party_id}/name-history")
    assert status == 200 and len(history["changes"]) == 1
    assert history["changes"][0]["actor"] is None
    status, parties = request("GET", "/api/counterparties")
    assert status == 200 and parties[0]["row_version"] == 2


@pytest.mark.parametrize(("headers", "status"), [
    ({"Origin": None}, 403),
    ({"Origin": "https://example.invalid"}, 400),
    ({"Content-Type": "text/plain"}, 415),
    ({"Cookie": None}, 400),
])
def test_name_write_security(name_server, headers, status):
    _, party, request = name_server
    assert request("POST", f"/api/counterparties/{party['counterparty_id']}/rename", {
        "display_name": "Synthetic Supplier", "expected_row_version": 1,
    }, **headers)[0] == status


@pytest.mark.parametrize("payload", [
    {"display_name": "", "expected_row_version": 1},
    {"display_name": "A\nB", "expected_row_version": 1},
    {"display_name": None, "expected_row_version": 1},
    {"display_name": "Synthetic", "expected_row_version": True},
    {"display_name": "Synthetic", "expected_row_version": 0},
    {"display_name": "Synthetic", "expected_row_version": "1"},
    {"display_name": "Synthetic", "expected_row_version": 1, "actor": "forged"},
    {"display_name": "Synthetic", "expected_row_version": 1, "tax_id": "forged"},
    {"display_name": "Synthetic"},
])
def test_name_payload_validation_does_not_write(name_server, payload):
    app, party, request = name_server
    assert request("POST", f"/api/counterparties/{party['counterparty_id']}/rename", payload)[0] == 400
    assert app.counterparties()[0]["display_name"] == party["display_name"]


def test_name_routes_and_author_provenance(name_server):
    app, party, request = name_server
    payload = {"display_name": "Synthetic Supplier", "expected_row_version": 1}
    assert request("POST", "/api/counterparties/not-a-uuid/rename", payload)[0] == 400
    assert request("POST", f"/api/counterparties/{uuid4()}/rename", payload)[0] == 404
    assert request("GET", f"/api/counterparties/{uuid4()}/name-history")[0] == 404
    assert request("POST", f"/api/counterparties/{party['counterparty_id']}/rename/extra", payload)[0] == 404
    assert request("GET", "/api/counterparties/%2f/name-history")[0] == 400
    app.rename_counterparty(party["counterparty_id"], payload, actor="synthetic@example.invalid")
    history = app.counterparty_name_history(party["counterparty_id"])["changes"]
    assert history[0]["actor"] == "synthetic@example.invalid"
    assert history[0]["change_source"] == "web"


def test_busy_is_retryable_without_writes(name_server, monkeypatch):
    app, party, _ = name_server
    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(LedgerDB, "rename_counterparty", busy)
    with pytest.raises(LocalWebApiError) as exc:
        app.rename_counterparty(party["counterparty_id"], {
            "display_name": "Synthetic Supplier", "expected_row_version": 1,
        })
    assert exc.value.status == 503
    assert exc.value.code == "counterparty_busy"
    assert app.counterparty_name_history(party["counterparty_id"])["changes"] == []


# Requires the separately built optional UI assets.
import pytest
pytestmark = pytest.mark.web
