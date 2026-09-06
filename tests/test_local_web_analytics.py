import json
import sqlite3
import subprocess
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.local_web import (
    LocalAccountingApp,
    LocalAccountingServer,
    LocalWebConfig,
    LocalWebError,
)

AS_OF = "2026-08-31"


def _config(tmp_path: Path) -> LocalWebConfig:
    static_root = (
        Path(__file__).resolve().parents[1]
        / "packages/ui/src/autonomo_taxes_ui/dist"
    )
    return LocalWebConfig(
        project_root=tmp_path,
        database=tmp_path / "autonomo.sqlite",
        inbox_root=tmp_path / "Inbox",
        archive_root=tmp_path / "Evidence",
        cache_root=tmp_path / "cache",
        static_root=static_root,
    )


def _write_dashboard_cache(
    config: LocalWebConfig,
    period_key: str,
    *,
    modelo130_values: dict[str, object] | None = None,
) -> None:
    payload = {
        "period": period_key,
        "as_of": AS_OF,
        "tax_arithmetic_preview": {
            "projected_reviewed": {
                "modelo130": {
                    "blocked": False,
                    "values": modelo130_values or {},
                    "warnings": [],
                },
                "modelo303": {"blocked": False, "values": {}, "warnings": []},
            }
        },
    }
    target = config.cache_root / period_key / "dashboard.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


def _database(config: LocalWebConfig) -> None:
    with LedgerDB.initialize(config.database) as db:
        for quarter, month in (("2026-Q1", "02"), ("2026-Q2", "05"), ("2026-Q3", "07")):
            db.add_transaction(
                external_key=f"income-{quarter}",
                period_key=quarter,
                transaction_date=f"2026-{month}-10",
                booking_date=f"2026-{month}-10",
                entry_type="income",
                description="Synthetic income",
                amount_minor=100000,
                amount_eur_minor=100000,
                direction="credit",
                lifecycle_status="posted",
            )
        db.add_obligation(
            period_key="2026-Q1",
            obligation_code="130",
            filing_status="filed",
            filed_at="2026-04-18T00:00:00+00:00",
        )
        db.create_filing_snapshot(
            "2026-Q1",
            payload={"form": "130", "values": {"19": "263.91"}},
            status="baseline",
            filed_on="2026-04-18",
        )
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            filed_at="2026-07-20T00:00:00+00:00",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            payload={"form": "130", "extraction_status": "no_values"},
            status="baseline",
            filed_on="2026-07-20",
        )
        db.add_obligation(
            period_key="2026-Q3",
            obligation_code="130",
            filing_status="due",
        )
    tamper = sqlite3.connect(config.database)
    try:
        tamper.execute(
            "UPDATE periods SET status = 'closed' WHERE period_key = '2026-Q1'"
        )
        tamper.execute(
            """
            UPDATE periods
            SET status = 'amended',
                amendment_period_id = (
                    SELECT period_id FROM periods WHERE period_key = '2026-Q3'
                ),
                amendment_reason = 'late invoice'
            WHERE period_key = '2026-Q2'
            """
        )
        tamper.commit()
    finally:
        tamper.close()
    _write_dashboard_cache(config, "2026-Q3", modelo130_values={"19": "120.00"})


def test_analytics_reports_period_statuses_and_form_sources(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _database(config)
    app = LocalAccountingApp(config, session_token="test-token")

    analytics = app.analytics("2026-Q3", as_of=AS_OF)

    assert analytics["schema_version"] == 1
    assert analytics["scope"] == {
        "period": "2026-Q3",
        "year": 2026,
        "from": "2026-01-01",
        "through": "2026-09-30",
        "as_of": AS_OF,
        "currency": "EUR",
        "money_unit": "eur_minor",
    }
    assert analytics["policy"]["date_field"] == "transaction_date"

    statuses = {row["period_key"]: row["status"] for row in analytics["periods"]}
    assert statuses == {
        "2026-Q1": "closed",
        "2026-Q2": "amended",
        "2026-Q3": "open",
    }
    amended = next(
        row for row in analytics["periods"] if row["period_key"] == "2026-Q2"
    )
    assert amended["amendment_period_key"] == "2026-Q3"
    assert amended["amendment_reason"] == "late invoice"

    points = {
        point["period_key"]: point
        for point in analytics["datasets"]["quarterly_tax_due"]["points"]
    }
    assert points["2026-Q1"]["modelo130"] == {
        "result_minor": 26391,
        "payable_minor": 26391,
        "source": "filed",
    }
    assert points["2026-Q2"]["modelo130"]["source"] == "filed_without_values"
    assert points["2026-Q2"]["modelo130"]["result_minor"] is None
    assert points["2026-Q3"]["modelo130"] == {
        "result_minor": 12000,
        "payable_minor": 12000,
        "source": "preview",
    }

    reserve = analytics["datasets"]["reserve_bullet"]
    assert reserve["period_key"] == "2026-Q3"
    assert reserve["status"] == "not_checked"
    assert reserve["required_tax_minor"] == 12000
    assert reserve["available_minor"] is None


def test_analytics_marks_missing_quarters(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _database(config)
    app = LocalAccountingApp(config, session_token="test-token")

    analytics = app.analytics("2026-Q4", as_of=AS_OF)

    statuses = {row["period_key"]: row["status"] for row in analytics["periods"]}
    assert statuses["2026-Q4"] == "missing"
    q4 = {
        point["period_key"]: point
        for point in analytics["datasets"]["quarterly_tax_due"]["points"]
    }["2026-Q4"]
    assert q4["modelo130"]["source"] == "unavailable"
    assert q4["modelo130"]["result_minor"] is None


def test_analytics_rejects_invalid_arguments(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _database(config)
    app = LocalAccountingApp(config, session_token="test-token")

    with pytest.raises(LocalWebError):
        app.analytics("2026-13")
    with pytest.raises(LocalWebError):
        app.analytics("2026-Q3", as_of="yesterday")


def test_analytics_never_runs_cli_subprocesses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _database(config)
    app = LocalAccountingApp(config, session_token="test-token")

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("analytics GET must not launch subprocesses")

    monkeypatch.setattr(subprocess, "run", _forbidden)
    analytics = app.analytics("2026-Q3", as_of=AS_OF)
    assert analytics["schema_version"] == 1


def test_analytics_http_requires_session_and_sets_headers(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _database(config)
    app = LocalAccountingApp(config, session_token="test-token")
    server = LocalAccountingServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)

        connection.request("GET", "/")
        response = connection.getresponse()
        response.read()
        assert response.status == 200
        cookie = response.getheader("Set-Cookie")
        assert cookie is not None
        session_cookie = cookie.split(";", 1)[0]

        connection.request(
            "GET",
            f"/api/analytics?period=2026-Q3&as_of={AS_OF}",
            headers={"Cookie": session_cookie},
        )
        ok = connection.getresponse()
        body = ok.read()
        assert ok.status == 200
        assert ok.getheader("Cache-Control") == "no-store"
        assert "default-src 'self'" in (ok.getheader("Content-Security-Policy") or "")
        payload = json.loads(body)
        assert payload["schema_version"] == 1
        assert payload["scope"]["period"] == "2026-Q3"

        connection.request(
            "GET", f"/api/analytics?period=2026-Q3&as_of={AS_OF}"
        )
        unauthorized = connection.getresponse()
        unauthorized_body = unauthorized.read()
        assert unauthorized.status == 400
        assert b"schema_version" not in unauthorized_body

        connection.request(
            "GET",
            "/api/analytics?period=not-a-period",
            headers={"Cookie": session_cookie},
        )
        invalid = connection.getresponse()
        invalid.read()
        assert invalid.status == 400

        connection.request(
            "GET",
            "/api/analytics?period=2026-Q3&as_of=yesterday",
            headers={"Cookie": session_cookie},
        )
        invalid_as_of = connection.getresponse()
        invalid_as_of.read()
        assert invalid_as_of.status == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# Requires the separately built optional UI assets.
import pytest
pytestmark = pytest.mark.web
