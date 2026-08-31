from __future__ import annotations

from contextlib import contextmanager
from http.client import HTTPConnection
import json
from pathlib import Path
import threading

from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.local_web import LocalAccountingApp, LocalAccountingServer, LocalWebConfig, load_config


@contextmanager
def server_for(tmp_path):
    database = tmp_path / "autonomo.sqlite"
    with LedgerDB.initialize(database):
        pass
    config = LocalWebConfig(
        project_root=tmp_path, database=database, inbox_root=None, archive_root=None,
        cache_root=tmp_path / "cache",
        static_root=Path(__file__).parents[1] / "src/autonomo_taxes/web_ui",
        private_root=tmp_path,
    )
    server = LocalAccountingServer(("127.0.0.1", 0), LocalAccountingApp(config))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        client.request("GET", "/settings")
        response = client.getresponse()
        assert response.status == 200
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        response.read()
        headers = {"Cookie": cookie, "Origin": f"http://127.0.0.1:{server.server_port}", "Content-Type": "application/json"}
        yield client, headers, database
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def request(client, method, path, headers=None, payload=None):
    client.request(method, path, body=None if payload is None else json.dumps(payload), headers=headers or {})
    response = client.getresponse()
    raw = response.read()
    return response.status, response.getheader("Cache-Control"), json.loads(raw) if raw else None


def profile_payload(**changes):
    return {"taxpayer_profile_id": None, "expected_row_version": 0, "full_name": "Example Taxpayer", "tax_id": "TEST-TAX-ID-001", "residency_country": "ES", **changes}


def test_settings_cookie_origin_persistence_and_empty_periods(tmp_path):
    with server_for(tmp_path) as (client, headers, database):
        assert request(client, "GET", "/api/settings")[0] in (400, 403)
        status, cache, data = request(client, "GET", "/api/settings", headers)
        assert status == 200 and cache == "no-store"
        assert data["profiles"] == [] and data["backups"]["available"]
        assert request(client, "POST", "/api/settings/profile", {k: v for k, v in headers.items() if k != "Origin"}, profile_payload())[0] == 403
        assert request(client, "POST", "/api/settings/profile", {**headers, "Origin": "http://127.0.0.1:1"}, profile_payload())[0] == 403
        assert request(client, "POST", "/api/settings/profile", {**headers, "Content-Type": "text/plain"}, profile_payload())[0] in (400, 415)
        status, _, profile = request(client, "POST", "/api/settings/profile", headers, profile_payload())
        assert status == 200 and profile["row_version"] == 1
        assert request(client, "POST", "/api/settings/profile", headers, profile_payload())[0] == 409
        assert request(client, "GET", "/api/bootstrap", headers)[2]["profile_name"] == "Example Taxpayer"
        assert request(client, "POST", "/api/settings/profile", headers, profile_payload(full_name="a" * 17000))[0] == 400
        with LedgerDB.open(database, read_only=True) as db:
            assert db.connection.execute("SELECT COUNT(*) FROM taxpayer_profile").fetchone()[0] == 1


def test_backup_http_confirmation_and_cas(tmp_path):
    with server_for(tmp_path) as (client, headers, _):
        settings = {"expected_revision": "missing", "daily_keep": 14, "monthly_keep": None, "confirm_local_pruning": False}
        assert request(client, "POST", "/api/settings/backups", headers, settings)[0] == 400
        status, _, result = request(client, "POST", "/api/settings/backups", headers, {**settings, "confirm_local_pruning": True})
        assert status == 200 and result["daily_keep"] == 14 and result["monthly_keep"] is None
        assert request(client, "POST", "/api/settings/backups", headers, {**settings, "confirm_local_pruning": True})[0] == 409
        assert request(client, "GET", "/api/settings", headers)[2]["backups"]["daily_keep"] == 14


def test_sops_config_does_not_redirect_backup_settings(tmp_path, monkeypatch):
    root = tmp_path / "private"
    root.mkdir()
    config = tmp_path / "secret-generation" / "config.yaml"
    config.parent.mkdir()
    config.write_text("{}")
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(root))
    assert load_config(tmp_path, config_path=config).private_root == root
    monkeypatch.delenv("AUTONOMO_PRIVATE_ROOT")
    assert load_config(tmp_path, config_path=config).private_root is None
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", "relative")
    assert load_config(tmp_path, config_path=config).private_root is None


def test_settings_failure_does_not_disclose_database_path(tmp_path):
    with server_for(tmp_path) as (client, headers, database):
        with LedgerDB.open(database) as db:
            db.connection.execute("PRAGMA user_version = 21")
        status, _, data = request(client, "GET", "/api/settings", headers)
        assert status == 409 and data["code"] == "schema_upgrade_required"
        assert str(tmp_path) not in json.dumps(data)


def test_account_settings_ui_behavior():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["node", str(root / "tests/test_account_settings_ui.js")], cwd=root, check=True)
