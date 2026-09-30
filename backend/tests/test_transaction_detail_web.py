from __future__ import annotations
from autonomo_test_support.paths import REPO_ROOT

import hashlib
import json
import sqlite3
from datetime import date
from http.client import HTTPConnection
from pathlib import Path
import threading
from unittest.mock import ANY

import pytest

import autonomo_taxes.local_web as local_web
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.local_web import (
    LocalAccountingApp,
    LocalAccountingServer,
    LocalWebApiError,
    LocalWebConfig,
)


SESSION_TOKEN = "transaction-detail-test-session"
TRANSACTION_ID = "01111111-1111-4111-8111-111111111111"


def _config(tmp_path: Path) -> LocalWebConfig:
    return LocalWebConfig(
        project_root=tmp_path,
        database=tmp_path / "autonomo.sqlite",
        inbox_root=None,
        archive_root=None,
        cache_root=tmp_path / "cache",
        static_root=REPO_ROOT / "backend/src/autonomo_taxes" / "web_ui",
    )


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _posted_expense(config: LocalWebConfig) -> dict[str, str]:
    period = "2026-Q2"
    with LedgerDB.initialize(config.database) as db:
        counterparty = db.upsert_counterparty(
            external_key="detail-counterparty",
            display_name="Synthetic supplier",
            country_code="ES",
        )
        document = db.upsert_document(
            external_key="detail-document",
            counterparty_id=counterparty["counterparty_id"],
            document_type="expense_invoice",
            document_number="SYN-0004",
            issued_on="2026-06-19",
            period_key=period,
            currency="EUR",
            total_minor=0,
            lifecycle_status="posted",
            source_hash=_hash("detail-document"),
        )
        transaction = db.add_transaction(
            transaction_id=TRANSACTION_ID,
            external_key="detail-transaction",
            period_key=period,
            transaction_date="2026-06-19",
            booking_date="2026-06-20",
            entry_type="expense",
            description="Synthetic posted zero-value expense",
            amount_minor=0,
            currency="EUR",
            amount_original_minor=0,
            original_currency="EUR",
            amount_eur_minor=0,
            direction="debit",
            lifecycle_status="posted",
            document_id=document["document_id"],
            counterparty_id=counterparty["counterparty_id"],
            source_hash=_hash("detail-transaction"),
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="income_tax",
            jurisdiction="ES",
            tax_code="expense_general",
            deductible_irpf_minor=0,
            deductible_vat_minor=0,
            include_modelo130=True,
            notes="First preserved note\nwith a second line",
            source_hash=_hash("detail-treatment-income-tax"),
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="vat",
            jurisdiction="ES-IVA",
            tax_code="domestic_input",
            deductible_irpf_minor=0,
            deductible_vat_minor=0,
            include_modelo303=True,
            include_modelo347=True,
            notes="Second preserved note",
            source_hash=_hash("detail-treatment-vat"),
        )

    # The endpoint must remain available for facts stored in a closed period.
    with sqlite3.connect(config.database) as connection:
        connection.execute("UPDATE periods SET status = 'closed' WHERE period_key = ?", (period,))
    return {"transaction_id": transaction["transaction_id"], "document_id": document["document_id"]}


def _database_dump(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        return "\n".join(connection.iterdump())


class _Server:
    def __init__(self, config: LocalWebConfig) -> None:
        self.app = LocalAccountingApp(config, session_token=SESSION_TOKEN)
        self.server = LocalAccountingServer(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.connection = HTTPConnection("127.0.0.1", self.port, timeout=5)

    def close(self) -> None:
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()

    def get(self, path: str, *, cookie: bool = True) -> tuple[int, dict[str, str], bytes]:
        headers = {"Host": f"127.0.0.1:{self.port}"}
        if cookie:
            headers["Cookie"] = f"autonomo_session={SESSION_TOKEN}"
        self.connection.request("GET", path, headers=headers)
        response = self.connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()


def test_transaction_detail_returns_closed_posted_snapshot_without_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    fixture = _posted_expense(config)
    before = _database_dump(config.database)
    app = LocalAccountingApp(config, session_token=SESSION_TOKEN)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("transaction detail must not invoke workflow helpers")

    monkeypatch.setattr(local_web, "prepare_review_packet", forbidden)
    monkeypatch.setattr(local_web, "prepare_review_work_item", forbidden)
    monkeypatch.setattr(local_web, "build_posting_preview", forbidden)
    monkeypatch.setattr(local_web, "fetch_eur_rate", forbidden)

    detail = app.transaction_detail(fixture["transaction_id"])

    assert detail == {
        "workflow_follow_up": None,
        "transaction": {
            "transaction_id": TRANSACTION_ID,
            "entry_type": "expense",
            "transaction_date": "2026-06-19",
            "booking_date": "2026-06-20",
            "description": "Synthetic posted zero-value expense",
            "lifecycle_status": "posted",
            "row_version": 1,
            "amount_minor": 0,
            "currency": "EUR",
            "amount_original_minor": 0,
            "original_currency": "EUR",
            "amount_eur_minor": 0,
        },
        "period": {"period_key": "2026-Q2", "status": "closed"},
        "document": {
            "document_id": fixture["document_id"],
            "document_number": "SYN-0004",
            "document_type": "expense_invoice",
            "issued_on": "2026-06-19",
            "lifecycle_status": "posted",
        },
        "counterparty": {"display_name": "Synthetic supplier", "country_code": "ES"},
        "tax_treatments": [
            {
                "treatment_id": ANY,
                "treatment_type": "income_tax",
                "jurisdiction": "ES",
                "tax_code": "expense_general",
                "deductible_irpf_minor": 0,
                "deductible_vat_minor": 0,
                "include_modelo130": True,
                "include_modelo303": False,
                "include_modelo347": False,
                "vat_investment_good": None,
                "notes": "First preserved note\nwith a second line",
            },
            {
                "treatment_id": ANY,
                "treatment_type": "vat",
                "jurisdiction": "ES-IVA",
                "tax_code": "domestic_input",
                "deductible_irpf_minor": 0,
                "deductible_vat_minor": 0,
                "include_modelo130": False,
                "include_modelo303": True,
                "include_modelo347": True,
                "vat_investment_good": None,
                "notes": "Second preserved note",
            },
        ],
    }
    assert _database_dump(config.database) == before


def test_transaction_detail_allows_missing_document_and_treatments(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        transaction = db.add_transaction(
            period_key="2026-Q1",
            transaction_date="2026-01-10",
            booking_date="2026-01-10",
            entry_type="expense",
            description="Synthetic standalone expense",
            amount_minor=0,
            currency="EUR",
            direction="debit",
            lifecycle_status="included_in_snapshot",
            source_hash=_hash("standalone-transaction"),
        )

    detail = LocalAccountingApp(config).transaction_detail(transaction["transaction_id"])

    assert detail["document"] is None
    assert detail["counterparty"] is None
    assert detail["tax_treatments"] == []
    assert detail["transaction"]["amount_minor"] == 0
    assert detail["transaction"]["amount_eur_minor"] == 0


def test_transaction_detail_http_errors_auth_and_spa_deep_link(tmp_path: Path) -> None:
    config = _config(tmp_path)
    fixture = _posted_expense(config)
    server = _Server(config)
    try:
        status, headers, body = server.get(f"/expenses/{fixture['transaction_id']}", cookie=False)
        assert status == 200
        assert b"Aut\xc3\xb3nomo" in body
        assert "autonomo_session=" + SESSION_TOKEN in headers.get("Set-Cookie", "")

        status, headers, body = server.get(f"/api/transactions/{fixture['transaction_id']}")
        assert status == 200
        assert headers["Content-Type"].startswith("application/json")
        assert json.loads(body)["transaction"]["transaction_id"] == TRANSACTION_ID

        status, _headers, body = server.get("/api/transactions/not-a-uuid")
        assert status == 400
        assert json.loads(body)["code"] == "invalid_transaction_id"

        status, _headers, body = server.get("/api/transactions/02222222-2222-4222-8222-222222222222")
        assert status == 404
        assert json.loads(body)["code"] == "transaction_not_found"

        status, _headers, body = server.get(f"/api/transactions/{fixture['transaction_id']}", cookie=False)
        assert status == 400
        assert "session" in json.loads(body)["error"].lower()
    finally:
        server.close()


def test_transaction_detail_rejects_invalid_id_before_accessing_database(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _posted_expense(config)
    app = LocalAccountingApp(config)

    with pytest.raises(LocalWebApiError) as exc_info:
        app.transaction_detail("not-a-uuid")

    assert exc_info.value.status == 400
    assert exc_info.value.code == "invalid_transaction_id"
