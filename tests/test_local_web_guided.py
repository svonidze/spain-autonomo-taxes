from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from http.client import HTTPConnection
from pathlib import Path
import threading

import pytest

import autonomo_taxes.local_web as local_web
from autonomo_taxes.fx_reference import (
    ECBRateObservation,
    ECBRateResult,
    FXRateUnavailableError,
    clear_cache,
    fetch_eur_rate,
)
from autonomo_taxes.ledger_db import LedgerDB, open as open_ledger_db
from autonomo_taxes.local_web import (
    LocalAccountingApp,
    LocalAccountingServer,
    LocalWebConfig,
)
from autonomo_taxes.review_packet import prepare_review_work_item

SESSION_TOKEN = "guided-web-token"


def _config(tmp_path: Path) -> LocalWebConfig:
    return LocalWebConfig(
        project_root=tmp_path,
        database=tmp_path / "autonomo.sqlite",
        inbox_root=tmp_path / "Inbox",
        archive_root=tmp_path / "Evidence",
        cache_root=tmp_path / "cache",
        static_root=(
            Path(__file__).resolve().parents[1]
            / "packages/ui/src/autonomo_taxes_ui/dist"
        ),
    )


def _usd_invoice_fixture(
    config: LocalWebConfig,
    *,
    transaction_date: date | None = None,
) -> dict[str, object]:
    database = config.database
    source = config.project_root / "private" / "supplier-invoice.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"immutable invoice fixture")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    tax_date = (transaction_date or date.today()).isoformat()
    period = f"{date.fromisoformat(tax_date).year}-Q{((date.fromisoformat(tax_date).month - 1) // 3) + 1}"
    with LedgerDB.initialize(database) as db:
        counterparty = db.upsert_counterparty(
            external_key="web-confirm-counterparty",
            display_name="Supplier Example SL",
            country_code="ES",
        )
        document = db.upsert_document(
            external_key=f"sha256:{digest}",
            counterparty_id=counterparty["counterparty_id"],
            document_type="expense_invoice",
            document_number="INV-WEB-1",
            issued_on=tax_date,
            period_key=period,
            currency="USD",
            total_minor=12100,
            lifecycle_status="needs_review",
            source_hash=digest,
        )
        document = db.set_document_storage(
            document["document_id"],
            source_path=str(source),
            mime_type="application/pdf",
            expected_row_version=document["row_version"],
        )
        transaction = db.add_transaction(
            external_key="web-confirm-transaction",
            period_key=period,
            transaction_date=tax_date,
            booking_date=tax_date,
            entry_type="expense",
            description="Confirmed invoice fixture",
            amount_minor=12100,
            currency="USD",
            amount_original_minor=12100,
            original_currency="USD",
            amount_eur_minor=None,
            direction="debit",
            lifecycle_status="needs_review",
            document_id=document["document_id"],
            counterparty_id=counterparty["counterparty_id"],
            source_hash=hashlib.sha256(b"web-confirm-transaction").hexdigest(),
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="invoice_review",
            tax_code="unknown",
            jurisdiction="ES",
            taxable_base_minor=10000,
            vat_minor=2100,
            notes="Extraction candidate only",
            source_hash=hashlib.sha256(b"web-confirm-treatment").hexdigest(),
        )
        db.add_validation_issue(
            period_key=period,
            issue_code="transaction_tax_review",
            severity="warning",
            message="Review IRPF and IVA treatment",
            subject_table="transactions",
            subject_id=transaction["transaction_id"],
            blocking=True,
            source_hash=digest,
        )
    return {
        "review_id": f"transaction:{transaction['transaction_id']}",
        "transaction_id": transaction["transaction_id"],
        "document_id": document["document_id"],
        "period": period,
    }


def _fake_ecb_result(*, status: str = "exact", units: str = "1.2500") -> ECBRateResult:
    day = date.today()
    units_per_eur = Decimal(units)
    raw = json.dumps(
        {"currency": "USD", "date": day.isoformat(), "value": units},
        sort_keys=True,
        separators=(",", ":"),
    )
    observation = ECBRateObservation(
        currency="USD",
        rate_date=day,
        units_per_eur=units_per_eur,
        eur_per_unit=Decimal(1) / units_per_eur,
        source_url="https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
        raw_observation=raw,
        raw_observation_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )
    return ECBRateResult(status=status, observation=observation)


class _Server:
    def __init__(self, config: LocalWebConfig, monkeypatch: pytest.MonkeyPatch, ecb) -> None:
        self.config = config
        app = LocalAccountingApp(config, session_token=SESSION_TOKEN)
        self.server = LocalAccountingServer(("127.0.0.1", 0), app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        if ecb is not None:
            monkeypatch.setattr(local_web, "fetch_eur_rate", ecb)

    def close(self) -> None:
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        origin: str | None = None,
        cookie: bool = True,
        content_type: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        headers = {"Host": f"127.0.0.1:{self.port}"}
        if cookie:
            headers["Cookie"] = f"autonomo_session={SESSION_TOKEN}"
        if origin is not None:
            headers["Origin"] = origin
        if content_type is not None:
            headers["Content-Type"] = content_type
        self.connection.request(method, path, body=body, headers=headers)
        response = self.connection.getresponse()
        payload = response.read()
        return response.status, dict(response.getheaders()), payload


def _approve_packet(packet: dict[str, object]) -> None:
    decision = packet["decision"]
    assert isinstance(decision, dict)
    decision.update(
        {
            "outcome": "approve",
            "reason": "Valid business software expense",
            "business_purpose": "Software used exclusively for the professional activity",
            "document_valid": True,
            "asset_decision": "current_expense",
            "asset_id": None,
            "counterparty_changes": {},
            "tax_treatment": {
                "tax_code": "domestic_input",
                "aeat_invoice_type": "F1",
                "aeat_operation_key": "01",
                "aeat_operation_qualification": "S1",
                "aeat_exemption_code": None,
                "aeat_reverse_charge": False,
                "vat_investment_good": False,
                "aeat_expense_concept": "G03",
                "rate_basis_points": 2100,
                "deductible_ratio": 1.0,
                "taxable_base_minor": 10000,
                "vat_minor": 2100,
                "deductible_irpf_minor": 9000,
                "deductible_vat_minor": 2100,
                "withholding_minor": 0,
                "include_modelo130": True,
                "include_modelo303": True,
                "include_modelo347": False,
                "rule_version_id": None,
                "notes": "Reviewed domestic current expense; 100% business use",
            },
        }
    )
    for resolution in decision["issue_resolutions"]:
        resolution["action"] = "resolve"
        resolution["reason"] = "Reviewed against the immutable source invoice"


def test_spa_deep_links_serve_the_app_shell_and_session_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    fixture = _usd_invoice_fixture(config)
    server = _Server(config, monkeypatch, ecb=None)
    try:
        for path in (
            "/",
            "/dashboard",
            "/income",
            "/expenses",
            "/review",
            "/review/%s" % fixture["review_id"].split(":", 1)[1],
            "/assets",
            "/taxes",
            "/contacts",
        ):
            status, headers, body = server.request("GET", path, cookie=False)
            assert status == 200, path
            assert b"Aut\xc3\xb3nomo" in body
            assert "autonomo_session=" + SESSION_TOKEN in headers.get("Set-Cookie", "")
            assert headers.get("Content-Type", "").startswith("text/html")
    finally:
        server.close()


def test_review_deep_link_packet_keeps_its_period_when_bootstrap_defaults_to_q3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    fixture = _usd_invoice_fixture(config, transaction_date=date(2026, 5, 15))
    with LedgerDB.open(config.database) as db:
        db.ensure_period("2026-Q3")
    monkeypatch.setattr(local_web, "_current_or_latest_period", lambda periods: "2026-Q3")

    def offline_fx(currency: str, as_of: date) -> ECBRateResult:
        raise FXRateUnavailableError("Synthetic fixture: no external FX lookup")

    server = _Server(config, monkeypatch, ecb=offline_fx)
    try:
        status, _headers, body = server.request("GET", "/api/bootstrap")
        assert status == 200
        bootstrap = json.loads(body)
        assert bootstrap["default_period"] == "2026-Q3"
        assert {row["period_key"] for row in bootstrap["periods"]} >= {
            "2026-Q2", "2026-Q3"
        }
        for path in (
            f"/review/{fixture['transaction_id']}",
            "/review?period=2026-Q2",
        ):
            status, headers, _body = server.request("GET", path, cookie=False)
            assert status == 200
            assert headers["Content-Type"].startswith("text/html")

        status, _headers, body = server.request(
            "GET", f"/api/review/work-item?review_id={fixture['review_id']}"
        )
        assert status == 200
        packet = json.loads(body)["packet"]
        assert packet["review_id"] == fixture["review_id"]
        assert packet["state"]["transaction"]["transaction_id"] == fixture["transaction_id"]
        assert packet["state"]["period"]["period_key"] == "2026-Q2"
    finally:
        server.close()


def test_unknown_routes_and_api_keep_404_without_html_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _usd_invoice_fixture(config)
    server = _Server(config, monkeypatch, ecb=None)
    try:
        status, _headers, _body = server.request("GET", "/nope")
        assert status == 404

        status, _headers, _body = server.request(
            "GET", "/review/not-a-uuid"
        )
        assert status == 404

        status, headers, body = server.request("GET", "/api/unknown")
        assert status == 404
        assert headers.get("Content-Type", "").startswith("application/json")
        assert b"<html" not in body.lower()

        status, _headers, _body = server.request(
            "POST", "/api/review/unknown",
            body=b"{}",
            content_type="application/json",
            origin=f"http://127.0.0.1:{server.port}",
        )
        assert status == 404
    finally:
        server.close()


def test_work_item_exposes_fx_suggestion_statuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    fixture = _usd_invoice_fixture(config)
    review_id = fixture["review_id"]

    def _work_item(monkeypatch, ecb) -> dict[str, object]:
        server = _Server(config, monkeypatch, ecb=ecb)
        try:
            status, _headers, body = server.request(
                "GET", f"/api/review/work-item?review_id={review_id}"
            )
            assert status == 200
            return json.loads(body)
        finally:
            server.close()

    exact = _work_item(
        monkeypatch, lambda currency, as_of: _fake_ecb_result(status="exact")
    )
    suggestion = exact["fx_suggestion"]
    assert suggestion is not None
    assert suggestion["status"] == "exact"
    assert suggestion["rate_source"] == "ecb"
    assert suggestion["raw_observation_hash"]
    assert exact["guidance"]["decision_profiles"]

    prior = _work_item(
        monkeypatch, lambda currency, as_of: _fake_ecb_result(status="prior")
    )
    assert prior["fx_suggestion"]["status"] == "prior"
    assert prior["fx_suggestion"]["note"] == "prior_business_day"

    def broken_lookup(currency: str, as_of: date) -> ECBRateResult:
        raise FXRateUnavailableError("ECB reference service is unreachable")

    unavailable = _work_item(monkeypatch, broken_lookup)
    assert unavailable["fx_suggestion"]["status"] == "unavailable"
    assert (
        unavailable["fx_suggestion"]["note"] == "ECB reference service is unreachable"
    )

    with LedgerDB.initialize(config.database) as db:
        db.review_transaction_fx_rate(
            fixture["transaction_id"],
            expected_row_version=1,
            rate_date=date.today().isoformat(),
            rate="0.8000",
            rate_source="actual_settlement",
            source_reference="Bank settlement advice 9",
        )
    existing = _work_item(
        monkeypatch, lambda currency, as_of: _fake_ecb_result(status="exact")
    )
    assert existing["fx_suggestion"]["status"] == "existing"
    assert existing["fx_suggestion"]["rate_source"] == "actual_settlement"
    assert existing["fx_suggestion"]["provenance"]["provenance_kind"] == (
        "documented_settlement"
    )


def test_work_item_uses_the_recorded_ecb_csv_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    requested_date = date(2026, 8, 17)
    fixture = _usd_invoice_fixture(config, transaction_date=requested_date)
    csv_payload = (
        Path(__file__).parent / "fixtures" / "ecb_exr_usd_sample.csv"
    ).read_bytes()
    clear_cache()

    def ecb_lookup(currency: str, as_of: date) -> ECBRateResult:
        assert currency == "USD"
        assert as_of == requested_date
        return fetch_eur_rate(
            currency,
            as_of,
            http_get=lambda _url: (200, csv_payload),
        )

    server = _Server(config, monkeypatch, ecb=ecb_lookup)
    try:
        status, _headers, body = server.request(
            "GET", f"/api/review/work-item?review_id={fixture['review_id']}"
        )
        assert status == 200
        work_item = json.loads(body)
        assert work_item["fx_suggestion"]["status"] == "exact"
        assert work_item["fx_suggestion"]["rate_date"] == "2026-08-17"
        assert work_item["fx_suggestion"]["eur_per_unit"] == str(
            Decimal(1) / Decimal("1.1593")
        )
    finally:
        server.close()
        clear_cache()


def test_confirm_endpoint_applies_verified_fx_and_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    fixture = _usd_invoice_fixture(config)
    review_id = fixture["review_id"]
    ecb = _fake_ecb_result(status="exact", units="1.2500")
    server = _Server(config, monkeypatch, ecb=lambda currency, as_of: ecb)
    try:
        status, _headers, body = server.request(
            "GET", f"/api/review/work-item?review_id={review_id}"
        )
        assert status == 200
        work_item = json.loads(body)
        packet = work_item["packet"]
        _approve_packet(packet)
        suggestion = work_item["fx_suggestion"]
        fx = {
            "rate_date": suggestion["rate_date"],
            "rate": suggestion["eur_per_unit"],
            "rate_source": "ecb",
            "source_reference": suggestion["source_reference"],
            "raw_observation": suggestion["raw_observation"],
            "raw_observation_hash": suggestion["raw_observation_hash"],
            "supersedes_rate_id": None,
        }
        payload = json.dumps({"packet": packet, "fx": fx}).encode("utf-8")
        status, _headers, body = server.request(
            "POST",
            "/api/review/confirm",
            body=payload,
            content_type="application/json",
            origin=f"http://127.0.0.1:{server.port}",
        )
        assert status == 200, body
        result = json.loads(body)
        assert result["fx_applied"] is True
        assert result["outcome"] == "approve"
        assert result["transaction"]["lifecycle_status"] == "approved"
    finally:
        server.close()
    with open_ledger_db(config.database, read_only=True) as db:
        transaction = db.connection.execute(
            "SELECT amount_eur_minor FROM transactions WHERE transaction_id = ?",
            (fixture["transaction_id"],),
        ).fetchone()
        assert transaction["amount_eur_minor"] == 9680


def test_confirm_endpoint_rejects_stale_packet_with_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    fixture = _usd_invoice_fixture(config)
    review_id = fixture["review_id"]
    server = _Server(
        config, monkeypatch, ecb=lambda currency, as_of: _fake_ecb_result()
    )
    try:
        status, _headers, body = server.request(
            "GET", f"/api/review/work-item?review_id={review_id}"
        )
        work_item = json.loads(body)
        packet = work_item["packet"]
        _approve_packet(packet)
        suggestion = work_item["fx_suggestion"]
        fx = {
            "rate_date": suggestion["rate_date"],
            "rate": suggestion["eur_per_unit"],
            "rate_source": "ecb",
            "source_reference": suggestion["source_reference"],
            "raw_observation": suggestion["raw_observation"],
            "raw_observation_hash": suggestion["raw_observation_hash"],
            "supersedes_rate_id": None,
        }
        with LedgerDB.initialize(config.database) as db:
            db.connection.execute(
                "UPDATE transactions SET description = 'Edited by someone else', "
                "row_version = row_version + 1 WHERE transaction_id = ?",
                (fixture["transaction_id"],),
            )
            db.connection.commit()
        payload = json.dumps({"packet": packet, "fx": fx}).encode("utf-8")
        status, _headers, body = server.request(
            "POST",
            "/api/review/confirm",
            body=payload,
            content_type="application/json",
            origin=f"http://127.0.0.1:{server.port}",
        )
        assert status == 409
        error = json.loads(body)
        assert error["code"] == "stale_snapshot"
    finally:
        server.close()


def test_confirm_endpoint_requires_same_origin_and_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    fixture = _usd_invoice_fixture(config)
    review_id = fixture["review_id"]
    server = _Server(config, monkeypatch, ecb=None)
    try:
        status, _headers, body = server.request(
            "GET", f"/api/review/work-item?review_id={review_id}"
        )
        work_item = json.loads(body)
        packet = work_item["packet"]
        _approve_packet(packet)

        cross_origin = json.dumps({"packet": packet, "fx": None}).encode("utf-8")
        status, _headers, body = server.request(
            "POST",
            "/api/review/confirm",
            body=cross_origin,
            content_type="application/json",
            origin="http://evil.example",
        )
        assert status == 400
        assert "Cross-origin" in json.loads(body)["error"]

        missing_origin = json.dumps({"packet": packet, "fx": None}).encode("utf-8")
        status, _headers, body = server.request(
            "POST",
            "/api/review/confirm",
            body=missing_origin,
            content_type="application/json",
        )
        assert status == 403
        assert json.loads(body)["code"] == "origin_required"

        missing_fx = json.dumps({"packet": packet}).encode("utf-8")
        status, _headers, body = server.request(
            "POST",
            "/api/review/confirm",
            body=missing_fx,
            content_type="application/json",
            origin=f"http://127.0.0.1:{server.port}",
        )
        assert status == 400
    finally:
        server.close()


def test_reject_via_confirm_endpoint_needs_no_fx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    fixture = _usd_invoice_fixture(config)
    review_id = fixture["review_id"]
    server = _Server(config, monkeypatch, ecb=None)
    try:
        status, _headers, body = server.request(
            "GET", f"/api/review/work-item?review_id={review_id}"
        )
        work_item = json.loads(body)
        packet = work_item["packet"]
        decision = packet["decision"]
        decision.update(
            {
                "outcome": "reject",
                "reason": "Document does not describe the contracted service",
                "business_purpose": None,
                "document_valid": False,
                "asset_decision": None,
                "asset_id": None,
                "counterparty_changes": {},
                "tax_treatment": None,
            }
        )
        for resolution in decision["issue_resolutions"]:
            resolution["action"] = "resolve"
            resolution["reason"] = "Closed by the rejection decision"
        payload = json.dumps({"packet": packet, "fx": None}).encode("utf-8")
        status, _headers, body = server.request(
            "POST",
            "/api/review/confirm",
            body=payload,
            content_type="application/json",
            origin=f"http://127.0.0.1:{server.port}",
        )
        assert status == 200, body
        result = json.loads(body)
        assert result["fx_applied"] is False
        assert result["transaction"]["lifecycle_status"] == "rejected"
    finally:
        server.close()


# Requires the separately built optional UI assets.
import pytest
pytestmark = pytest.mark.web
