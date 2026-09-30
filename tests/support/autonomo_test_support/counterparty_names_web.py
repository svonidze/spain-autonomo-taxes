"""Reusable synthetic fixtures shared by backend test suites."""

from __future__ import annotations
from http.client import HTTPConnection
import json
import sqlite3
import threading
from uuid import uuid4
import pytest
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.local_web import LocalAccountingApp, LocalAccountingServer, LocalWebApiError
from autonomo_test_support.local_web import _config

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
