from __future__ import annotations

from dataclasses import replace
from datetime import date
import errno
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import types

import pytest

import autonomo_taxes.local_web as local_web
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.local_web import (
    LocalAccountingApp,
    LocalAccountingServer,
    LocalWebApiError,
    LocalWebConfig,
    LocalWebError,
    _cli_error_detail,
    _host_header_parts,
    _review_summary,
    _store_upload,
    normalize_google_drive_url,
)


DRIVE_URL = "https://" + "drive.google.com"
DOCS_URL = "https://" + "docs.google.com"


def _config(
    tmp_path: Path,
    *,
    trusted_proxy_mode: str | None = None,
    allowed_tailscale_logins: tuple[str, ...] = (),
    read_only_document_roots: tuple[Path, ...] = (),
    legacy_path_map_file: Path | None = None,
) -> LocalWebConfig:
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
        trusted_proxy_mode=trusted_proxy_mode,
        allowed_tailscale_logins=allowed_tailscale_logins,
        read_only_document_roots=read_only_document_roots,
        legacy_path_map_file=legacy_path_map_file,
    )


def _database(config: LocalWebConfig) -> None:
    with LedgerDB.initialize(config.database) as db:
        db.add_transaction(
            external_key="web-income",
            period_key="2026-Q3",
            transaction_date="2026-07-01",
            booking_date="2026-07-01",
            entry_type="income",
            description="Example income",
            amount_minor=125000,
            amount_eur_minor=125000,
            direction="credit",
            lifecycle_status="posted",
        )
        expense = db.add_transaction(
            external_key="web-expense",
            period_key="2026-Q3",
            transaction_date="2026-07-02",
            booking_date="2026-07-02",
            entry_type="expense",
            description="Example expense",
            amount_minor=12100,
            amount_eur_minor=12100,
            direction="debit",
            lifecycle_status="approved",
        )
        db.add_detailed_tax_treatment(
            transaction_id=expense["transaction_id"],
            treatment_type="expense",
            tax_code="G03",
            taxable_base_minor=10000,
            vat_minor=2100,
            deductible_irpf_minor=10000,
            deductible_vat_minor=2100,
            include_modelo130=True,
            include_modelo303=True,
        )


def _snapshot_payload(
    form: str,
    *,
    values: dict[str, object] | None = None,
    filed_values: dict[str, object] | None = None,
    extraction_status: str | None = "casillas_extracted",
    value_extraction_schema: str | None = None,
    receipt_status: str | None = None,
    blank_casillas: list[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"form": form}
    if values is not None:
        payload["values"] = values
    if filed_values is not None:
        payload["filed_values"] = filed_values
    if extraction_status is not None:
        payload["extraction_status"] = extraction_status
    if value_extraction_schema is not None:
        payload["value_extraction_schema"] = value_extraction_schema
    if receipt_status is not None:
        payload["receipt_verification"] = {"status": receipt_status}
    if blank_casillas is not None:
        payload["blank_casillas"] = list(blank_casillas)
    return payload


def _write_dashboard_cache(
    config: LocalWebConfig,
    period_key: str,
    *,
    as_of: str,
    modelo130_values: dict[str, object] | None = None,
    modelo303_values: dict[str, object] | None = None,
) -> None:
    payload = {
        "period": period_key,
        "as_of": as_of,
        "tax_arithmetic_preview": {
            "projected_reviewed": {
                "modelo130": {
                    "blocked": False,
                    "values": modelo130_values or {},
                    "warnings": [],
                },
                "modelo303": {
                    "blocked": False,
                    "values": modelo303_values or {},
                    "warnings": [],
                },
            }
        },
    }
    target = config.cache_root / period_key / "dashboard.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload), encoding="utf-8")


def test_dashboard_and_transaction_views_use_sqlite_source_of_truth(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _database(config)
    app = LocalAccountingApp(config, session_token="test-token")

    bootstrap = app.bootstrap()
    dashboard = app.dashboard("2026-Q3")
    expenses = app.transactions("2026-Q3", entry_type="expense")

    assert bootstrap["default_period"] == "2026-Q3"
    assert dashboard["totals"]["actual"]["income_eur"] == "1250.00"
    assert dashboard["totals"]["actual"]["income_transaction_count"] == 1
    assert dashboard["totals"]["actual"]["expense_transaction_count"] == 0
    assert dashboard["totals"]["forecast"]["expense_gross_eur"] == "121.00"
    assert dashboard["totals"]["forecast"]["expense_transaction_count"] == 1
    assert dashboard["totals"]["forecast"]["deductible_irpf_eur"] == "100.00"
    assert dashboard["readyCount"] == 1
    assert dashboard["posting_summary"] == {
        "needs_review": 0,
        "ready": 1,
        "later": 0,
        "blocked": 0,
    }
    assert expenses[0]["deductible_vat_eur"] == "21.00"
    assert expenses[0]["lifecycle_status"] == "approved"


@pytest.mark.parametrize(
    ("source", "expected_id"),
    [
        (f"{DRIVE_URL}/file/d/abcDef_012-345678/view?usp=drive_link", "abcDef_012-345678"),
        (f"{DRIVE_URL}/open?id=abcDef_012-345678", "abcDef_012-345678"),
        (f"{DRIVE_URL}/uc?id=abcDef_012-345678&export=download", "abcDef_012-345678"),
        (f"{DOCS_URL}/spreadsheets/d/abcDef_012-345678/edit#gid=0", "abcDef_012-345678"),
    ],
)
def test_google_drive_url_is_canonicalized_without_retaining_query_data(
    source: str, expected_id: str
) -> None:
    canonical, file_id = normalize_google_drive_url(source)

    assert file_id == expected_id
    assert canonical == f"{DRIVE_URL}/open?id={expected_id}"


@pytest.mark.parametrize(
    "source",
    [
        "http://" + "drive.google.com/file/d/abcDef_012-345678/view",
        f"{DRIVE_URL}/file/d/short/view",
        "https://example.test/file/d/abcDef_012-345678/view",
        "https://" + "drive.google.com" + "@" + "evil.test/file/d/abcDef_012-345678/view",
        f"{DRIVE_URL}/open?id=abcDef_012-345678&id=another_012345",
    ],
)
def test_google_drive_url_rejects_non_file_or_untrusted_origins(source: str) -> None:
    with pytest.raises(LocalWebError, match="Google Drive URL is invalid"):
        normalize_google_drive_url(source)


def test_google_drive_intake_delegates_only_a_canonical_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _database(config)
    calls: list[dict[str, object]] = []

    def fake_import(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {"status": "accepted_for_review", "period": "2026-Q3", "system_marker": "doc-1"}

    module = types.ModuleType("autonomo_taxes.storage_import")
    module.ingest_google_drive_url = fake_import  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "autonomo_taxes.storage_import", module)

    result = LocalAccountingApp(config).ingest_google_drive_url(
        fields={"period": "2026-Q3", "kind": "expense_invoice"},
        drive_url=f"{DRIVE_URL}/file/d/abcDef_012-345678/view?resourcekey=resource_key_123",
    )

    assert result["system_marker"] == "doc-1"
    assert calls == [{
        "config": config,
        "fields": {"period": "2026-Q3", "kind": "expense_invoice"},
        "drive_url": f"{DRIVE_URL}/open?id=abcDef_012-345678&resourcekey=resource_key_123",
        "file_id": "abcDef_012-345678",
    }]


def test_google_picker_config_uses_only_the_enabled_oauth_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = replace(
        _config(
            tmp_path,
            trusted_proxy_mode="tailscale_serve",
            allowed_tailscale_logins=("agent-login",),
        ),
        google_picker_developer_key="restricted-browser-key",
        google_picker_app_id="123456789012",
    )
    _database(config)
    token_file = tmp_path / "oauth-token.json"
    token_file.write_text("{}", encoding="utf-8")
    with LedgerDB.open(config.database) as db:
        db.upsert_storage_backend(
            backend_key="google_user_oauth",
            display_name="Google user OAuth",
            driver_key="google_drive",
            provider_key="google",
            access_mode="read_write",
            config={"root_folder_id": "folder-123456789", "credential_mode": "oauth"},
            credential_ref=f"file:{token_file}",
        )
        db.upsert_storage_backend(
            backend_key="google_archive_reader",
            display_name="Google archive reader",
            driver_key="google_drive",
            provider_key="google",
            access_mode="read_only",
            config={"root_folder_id": "folder-987654321", "credential_mode": "service_account"},
            credential_ref="file:/missing-service-account.json",
        )

    calls: list[tuple[Path, str, str]] = []
    monkeypatch.setattr(
        local_web,
        "_google_picker_token",
        lambda path, key, app_id: calls.append((path, key, app_id)) or {
            "enabled": True,
            "developer_key": key,
            "app_id": app_id,
            "access" + "_token": "ephemeral-token",
        },
    )

    result = LocalAccountingApp(config).google_picker_config()

    assert result == {
        "enabled": True,
        "developer_key": "restricted-browser-key",
        "app_id": "123456789012",
        "access" + "_token": "ephemeral-token",
    }
    assert calls == [(token_file, "restricted-browser-key", "123456789012")]


def test_google_picker_is_disabled_without_tailscale_proxy(tmp_path: Path) -> None:
    config = replace(
        _config(tmp_path),
        google_picker_developer_key="restricted-browser-key",
        google_picker_app_id="123456789012",
    )
    _database(config)

    assert LocalAccountingApp(config).google_picker_config() == {"enabled": False}


@pytest.mark.parametrize(
    ("picker_lines", "message"),
    [
        (
            'google_picker_developer_key: "restricted-browser-key"\n',
            "must be configured together",
        ),
        (
            'google_picker_app_id: "123456789012"\n',
            "must be configured together",
        ),
        (
            'google_picker_developer_key: "restricted-browser-key"\n'
            'google_picker_app_id: "project-name"\n',
            "must be a Google Cloud project number",
        ),
    ],
)
def test_google_picker_key_and_app_id_are_validated_together(
    tmp_path: Path,
    picker_lines: str,
    message: str,
) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(picker_lines, encoding="utf-8")

    with pytest.raises(LocalWebError, match=message):
        local_web.load_config(tmp_path, config_path=config_file)


def test_dashboard_rejects_unknown_or_malformed_quarter(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _database(config)
    app = LocalAccountingApp(config)

    with pytest.raises(LocalWebError, match="YYYY-QN"):
        app.dashboard("2026")
    with pytest.raises(LocalWebError, match="Unknown quarter"):
        app.dashboard("2025-Q4")


def test_posting_summary_keeps_review_ready_later_and_blocked_exclusive() -> None:
    rows = [
        {"lifecycle_status": "needs_review"},
        {
            "lifecycle_status": "approved",
            "transaction_date": "2020-01-01",
            "tax_code": "G03",
            "currency": "EUR",
        },
        {
            "lifecycle_status": "approved",
            "transaction_date": "2099-01-01",
            "tax_code": "G03",
            "currency": "EUR",
        },
        {
            "lifecycle_status": "approved",
            "transaction_date": "2020-01-01",
            "tax_code": "unknown",
            "currency": "EUR",
        },
    ]

    for row, code in zip(rows, ("needs_review", "ready", "deferred", "blocked")):
        row["ui_context"] = {"state": code}

    assert _review_summary(rows) == {
        "needs_review": 1,
        "ready": 1,
        "later": 1,
        "blocked": 1,
    }


    # Persistence and live language switching are covered through real core and browser tests.


def test_dashboard_prefers_filed_snapshot_values_for_filed_forms_without_cache(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            determination="due",
        )
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="303",
            filing_status="filed",
            determination="due",
        )
    app = LocalAccountingApp(config, session_token="test-token")
    baseline = app.dashboard("2026-Q2")
    assert baseline["tax_preview"] == {}
    assert baseline["forecast_as_of"] is None
    with LedgerDB.open(config.database) as db:
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08T12:49:45",
            status="baseline",
            form_code="130",
            payload=_snapshot_payload("130", filed_values={"19": "2639.12", "07": "100.00"}),
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08T12:49:25",
            status="baseline",
            form_code="303",
            payload=_snapshot_payload(
                "303",
                filed_values={"result": "-407.36", "compensation_carryforward": "12.00"},
                value_extraction_schema="modelo303_v4",
            ),
        )
    dashboard = app.dashboard("2026-Q2")
    assert dashboard["tax_forms"]["modelo130"]["display_state"] == "filed"
    assert dashboard["tax_forms"]["modelo130"]["filed_on"] == "2026-07-08"
    assert dashboard["tax_forms"]["modelo130"]["values"]["19"] == "2639.12"
    assert dashboard["tax_forms"]["modelo303"]["values"]["result"] == "-407.36"
    assert dashboard["tax_forms"]["modelo303"]["headline_value"] == "-407.36"
    assert dashboard["tax_preview"] == baseline["tax_preview"]
    assert dashboard["forecast_as_of"] == baseline["forecast_as_of"]


def test_taxes_endpoint_marks_filed_without_values_explicitly(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="303",
            filing_status="filed",
            determination="due",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08T12:49:25",
            status="baseline",
            form_code="303",
            payload=_snapshot_payload("303", extraction_status="output_and_deductible"),
        )
    app = LocalAccountingApp(config)
    taxes = app.taxes("2026-Q2")
    assert taxes["tax_forms"]["modelo303"]["display_state"] == "filed_without_values"
    assert taxes["tax_forms"]["modelo303"]["values"] == {}
    assert taxes["tax_forms"]["modelo303"]["extraction_status"] == "output_and_deductible"


def test_taxes_summary_leads_with_current_quarter_cash_plan(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _write_dashboard_cache(
        config,
        "2026-Q3",
        as_of="2026-09-03",
        modelo130_values={"19": "3204.27"},
        modelo303_values={
            "71": "-1297.90",
            "72": "1297.90",
            "compensation_carryforward": "2681.11",
        },
    )
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q3",
            obligation_code="130",
            filing_status="due",
            determination="due",
        )
        db.add_obligation(
            period_key="2026-Q3",
            obligation_code="303",
            filing_status="due",
            determination="due",
        )

    summary = LocalAccountingApp(config).taxes(
        "2026-Q3", as_of=date(2026, 9, 4)
    )

    assert summary["period_state"]["phase"] == "current"
    assert summary["tax_summary"]["calculation_source"] == "preview"
    assert summary["tax_summary"]["total_payable_minor"] == 320427
    assert summary["tax_summary"]["settlement_status"] == "planned"
    assert summary["tax_summary"]["forms"]["130"]["payable_minor"] == 320427
    iva = summary["tax_summary"]["forms"]["303"]
    assert iva["preview_minor"] == -129790
    assert iva["payable_minor"] == 0
    assert iva["generated_credit_minor"] == 129790
    assert iva["carryforward_minor"] == 268111
    assert iva["disposition"] == "carryforward"


def test_taxes_summary_never_calls_a_filed_return_paid_without_evidence(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        obligation = db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            determination="due",
        )
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="303",
            filing_status="filed",
            determination="due",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08",
            status="baseline",
            form_code="130",
            payload=_snapshot_payload("130", filed_values={"19": "2639.12"}),
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08",
            status="baseline",
            form_code="303",
            payload=_snapshot_payload(
                "303",
                filed_values={"71": "-407.36", "72": "407.36"},
                value_extraction_schema="modelo303_v4",
            ),
        )

    app = LocalAccountingApp(config)
    before_payment = app.taxes("2026-Q2", as_of=date(2026, 9, 4))["tax_summary"]
    assert before_payment["total_payable_minor"] == 263912
    assert before_payment["total_confirmed_paid_minor"] == 0
    assert before_payment["outstanding_minor"] == 263912
    assert before_payment["settlement_status"] == "payment_unconfirmed"
    assert before_payment["forms"]["303"]["filed_minor"] == -40736

    with LedgerDB.open(config.database) as db:
        db.add_payment(
            paid_on="2026-07-20",
            amount_minor=263912,
            currency="EUR",
            source_hash="confirmed-q2-tax-payment",
            obligation_id=obligation["obligation_id"],
            match_status="manual",
            source_system="revolut",
            external_id="q2-modelo130-debit",
            source_row_json=json.dumps(
                {
                    "evidence_sha256": "a" * 64,
                    "evidence_locator": "2026-Q2/tax_payment_evidence/m130.pdf",
                    "source_system": "revolut",
                    "external_id": "q2-modelo130-debit",
                }
            ),
        )

    after_payment = app.taxes("2026-Q2", as_of=date(2026, 9, 4))["tax_summary"]
    assert after_payment["total_confirmed_paid_minor"] == 263912
    assert after_payment["outstanding_minor"] == 0
    assert after_payment["settlement_status"] == "paid"


@pytest.mark.parametrize(
    ("paid_minor", "expected_status", "expected_outstanding", "expected_overpaid"),
    [
        (100000, "partially_paid", 163912, 0),
        (300000, "overpaid", 0, 36088),
    ],
)
def test_taxes_summary_exposes_partial_payment_and_overpayment(
    tmp_path: Path,
    paid_minor: int,
    expected_status: str,
    expected_outstanding: int,
    expected_overpaid: int,
) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        obligation = db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            determination="due",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08",
            status="baseline",
            form_code="130",
            payload=_snapshot_payload("130", filed_values={"19": "2639.12"}),
        )
        db.add_payment(
            paid_on="2026-07-20",
            amount_minor=paid_minor,
            currency="EUR",
            source_hash=f"q2-tax-payment-{paid_minor}",
            obligation_id=obligation["obligation_id"],
            match_status="exact",
            source_system="revolut",
            external_id=f"q2-tax-payment-{paid_minor}",
            source_row_json=json.dumps(
                {
                    "evidence_sha256": "b" * 64,
                    "evidence_locator": "2026-Q2/tax_payment_evidence/m130.json",
                    "source_system": "revolut",
                    "external_id": f"q2-tax-payment-{paid_minor}",
                }
            ),
        )

    summary = LocalAccountingApp(config).taxes(
        "2026-Q2", as_of=date(2026, 9, 4)
    )["tax_summary"]
    assert summary["settlement_status"] == expected_status
    assert summary["outstanding_minor"] == expected_outstanding
    assert summary["overpaid_minor"] == expected_overpaid


def test_taxes_summary_keeps_future_and_unknown_amounts_distinct_from_zero(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _write_dashboard_cache(
        config,
        "2026-Q4",
        as_of="2026-09-03",
        modelo130_values={"19": "0.00"},
    )
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q4",
            obligation_code="130",
            filing_status="due",
            determination="due",
        )

    future = LocalAccountingApp(config).taxes(
        "2026-Q4", as_of=date(2026, 9, 4)
    )
    assert future["period_state"]["phase"] == "future"
    assert future["tax_summary"]["total_payable_minor"] is None
    assert future["tax_summary"]["settlement_status"] == "not_started"


def test_taxes_summary_distinguishes_modelo303_refund_from_carryforward(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2025-Q4",
            obligation_code="303",
            filing_status="filed",
            determination="due",
        )
        db.create_filing_snapshot(
            "2025-Q4",
            filed_on="2026-01-15",
            status="baseline",
            form_code="303",
            payload=_snapshot_payload(
                "303",
                filed_values={"71": "-500.00", "73": "500.00"},
                value_extraction_schema="modelo303_v4",
            ),
        )

    summary = LocalAccountingApp(config).taxes(
        "2025-Q4", as_of=date(2026, 2, 1)
    )["tax_summary"]
    iva = summary["forms"]["303"]
    assert iva["payable_minor"] == 0
    assert iva["refund_requested_minor"] == 50000
    assert iva["disposition"] == "refund"


def test_filed_form_selection_treats_forms_independently(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _write_dashboard_cache(
        config,
        "2026-Q2",
        as_of="2026-07-16",
        modelo303_values={"result": "99.10"},
    )
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            determination="due",
        )
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="303",
            filing_status="due",
            determination="due",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08",
            status="baseline",
            form_code="130",
            payload=_snapshot_payload("130", filed_values={"19": "2639.12"}),
        )
    app = LocalAccountingApp(config)
    dashboard = app.dashboard("2026-Q2")
    assert dashboard["tax_forms"]["modelo130"]["display_state"] == "filed"
    assert dashboard["tax_forms"]["modelo130"]["headline_value"] == "2639.12"
    assert dashboard["tax_forms"]["modelo303"]["display_state"] == "preview"
    assert dashboard["tax_forms"]["modelo303"]["headline_value"] == "99.10"


def test_latest_snapshot_wins_without_inventing_amendment_ui(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            determination="due",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08T11:00:00",
            status="baseline",
            form_code="130",
            payload=_snapshot_payload("130", filed_values={"19": "100.00"}),
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-09T11:00:00",
            status="baseline",
            form_code="130",
            payload=_snapshot_payload("130", filed_values={"19": "200.00"}),
        )
    app = LocalAccountingApp(config)
    dashboard = app.dashboard("2026-Q2")
    assert dashboard["tax_forms"]["modelo130"]["headline_value"] == "200.00"
    assert "amendment" not in dashboard["tax_forms"]["modelo130"]


def test_snapshot_only_state_is_used_when_snapshot_exists_but_obligation_is_not_filed(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _write_dashboard_cache(
        config,
        "2026-Q2",
        as_of="2026-07-16",
        modelo130_values={"19": "10.00"},
    )
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="unknown",
            determination="unknown",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08",
            status="baseline",
            form_code="130",
            payload=_snapshot_payload("130", filed_values={"19": "2639.12"}),
        )
    app = LocalAccountingApp(config)
    dashboard = app.dashboard("2026-Q2")
    assert dashboard["tax_forms"]["modelo130"]["display_state"] == "snapshot_only"
    assert dashboard["tax_forms"]["modelo130"]["headline_value"] == "2639.12"


@pytest.mark.parametrize("stored_zero", [0, "0.00"])
def test_zero_filed_values_renderable_after_backend_coercion(
    tmp_path: Path,
    stored_zero: object,
) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            determination="due",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08",
            status="baseline",
            form_code="130",
            payload=_snapshot_payload("130", filed_values={"19": stored_zero}),
        )
    app = LocalAccountingApp(config)
    dashboard = app.dashboard("2026-Q2")
    assert dashboard["tax_forms"]["modelo130"]["headline_value"] == "0.00"
    assert dashboard["tax_forms"]["modelo130"]["values"]["19"] == "0.00"


def test_invalid_snapshot_json_is_skipped(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            determination="due",
        )
        period = db.ensure_period("2026-Q2")
        db.connection.execute(
            """
            INSERT INTO filing_snapshots (
                filing_snapshot_id, period_id, snapshot_hash, filed_on, status, payload_json,
                source_hash, row_version, created_at, updated_at, form_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                "broken-snapshot",
                period["period_id"],
                "broken-hash",
                "2026-07-09T12:00:00",
                "baseline",
                "{bad-json",
                "broken-hash",
                "2026-07-09T12:00:00",
                "2026-07-09T12:00:00",
                "130",
            ),
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08T12:00:00",
            status="baseline",
            form_code="130",
            payload=_snapshot_payload("130", filed_values={"19": "2639.12"}),
        )
    app = LocalAccountingApp(config)
    dashboard = app.dashboard("2026-Q2")
    assert dashboard["tax_forms"]["modelo130"]["headline_value"] == "2639.12"


def test_non_mapping_snapshot_payloads_and_receipt_shape_are_skipped_or_tolerated(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="303",
            filing_status="filed",
            determination="due",
        )
        period = db.ensure_period("2026-Q2")
        for snapshot_id, payload_json in (
            ("null-snapshot", "null"),
            ("list-snapshot", "[]"),
            ("string-snapshot", '"303"'),
        ):
            db.connection.execute(
                """
                INSERT INTO filing_snapshots (
                    filing_snapshot_id, period_id, snapshot_hash, filed_on, status, payload_json,
                    source_hash, row_version, created_at, updated_at, form_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    period["period_id"],
                    f"{snapshot_id}-hash",
                    "2026-07-08T10:00:00",
                    "baseline",
                    payload_json,
                    f"{snapshot_id}-hash",
                    "2026-07-08T10:00:00",
                    "2026-07-08T10:00:00",
                    "303",
                ),
            )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08T12:49:25",
            status="baseline",
            form_code="303",
            payload=_snapshot_payload(
                "303",
                values={"result": "10.00"},
                extraction_status="casillas_extracted",
                receipt_status=None,
            )
            | {"receipt_verification": "matched"},
        )
    app = LocalAccountingApp(config)
    dashboard = app.dashboard("2026-Q2")
    assert dashboard["tax_forms"]["modelo303"]["display_state"] == "filed"
    assert dashboard["tax_forms"]["modelo303"]["headline_value"] == "10.00"


def test_modelo303_headline_falls_back_to_casilla_keys(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="303",
            filing_status="filed",
            determination="due",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08",
            status="baseline",
            form_code="303",
            payload=_snapshot_payload(
                "303",
                filed_values={"71": "33.33"},
                value_extraction_schema="modelo303_v4",
            ),
        )
    app = LocalAccountingApp(config)
    dashboard = app.dashboard("2026-Q2")
    assert dashboard["tax_forms"]["modelo303"]["headline_value"] == "33.33"


def test_snapshot_value_with_one_bad_numeric_field_is_partially_preserved(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            determination="due",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08",
            status="baseline",
            form_code="130",
            payload=_snapshot_payload("130", filed_values={"19": "2639.12", "07": "n/a"}),
        )
    app = LocalAccountingApp(config)
    dashboard = app.dashboard("2026-Q2")
    assert dashboard["tax_forms"]["modelo130"]["values"] == {"19": "2639.12"}


def test_newer_filing_without_values_does_not_fall_back_to_older_filing(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="303",
            filing_status="filed",
            determination="due",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08T12:49:25",
            status="baseline",
            form_code="303",
            payload=_snapshot_payload(
                "303",
                filed_values={"result": "-407.36"},
                value_extraction_schema="modelo303_v4",
            ),
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-10-20T09:00:00",
            status="baseline",
            form_code="303",
            payload=_snapshot_payload("303", extraction_status="values_unavailable"),
        )
    app = LocalAccountingApp(config)
    dashboard = app.dashboard("2026-Q2")
    assert dashboard["tax_forms"]["modelo303"]["display_state"] == "filed_without_values"
    assert dashboard["tax_forms"]["modelo303"]["values"] == {}
    assert dashboard["tax_forms"]["modelo303"]["filed_on"] == "2026-10-20"


def test_cached_preview_remains_visible_but_snapshot_wins_for_filed_card(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _write_dashboard_cache(
        config,
        "2026-Q2",
        as_of="2026-07-16",
        modelo130_values={"19": "111.11"},
        modelo303_values={"result": "222.22"},
    )
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            determination="due",
        )
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="303",
            filing_status="filed",
            determination="due",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08",
            status="baseline",
            form_code="130",
            payload=_snapshot_payload("130", filed_values={"19": "2639.12"}),
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08",
            status="baseline",
            form_code="303",
            payload=_snapshot_payload(
                "303",
                filed_values={"result": "-407.36"},
                value_extraction_schema="modelo303_v4",
            ),
        )
    app = LocalAccountingApp(config)
    dashboard = app.dashboard("2026-Q2")
    assert dashboard["tax_forms"]["modelo130"]["headline_value"] == "2639.12"
    assert dashboard["tax_forms"]["modelo303"]["headline_value"] == "-407.36"
    assert dashboard["tax_preview"] == {
        "modelo130": {"blocked": False, "values": {"19": "111.11"}, "warnings": []},
        "modelo303": {"blocked": False, "values": {"result": "222.22"}, "warnings": []},
    }
    assert dashboard["forecast_as_of"] == "2026-07-16"


def test_nonfinite_values_and_invalid_blank_casillas_are_ignored(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="303",
            filing_status="filed",
            determination="due",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08T12:49:25",
            status="baseline",
            form_code="303",
            payload=_snapshot_payload(
                "303",
                filed_values={"result": "NaN", "71": "Infinity", "69": "5.00", "72": "12.00"},
                value_extraction_schema="modelo303_v4",
            )
            | {"blank_casillas": "30,31"},
        )
    app = LocalAccountingApp(config)
    dashboard = app.dashboard("2026-Q2")
    assert dashboard["tax_forms"]["modelo303"]["values"] == {"69": "5.00", "72": "12.00"}
    assert dashboard["tax_forms"]["modelo303"]["headline_value"] == "5.00"


def test_modelo303_phase_b_provenance_follows_the_value_row(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database) as db:
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="303",
            filing_status="filed",
            determination="due",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08T12:49:25",
            status="baseline",
            snapshot_hash="v4-row",
            form_code="303",
            payload=_snapshot_payload(
                "303",
                filed_values={"result": "-407.36"},
                extraction_status="casillas_extracted",
                value_extraction_schema="modelo303_v4",
            ),
        )
        db.create_filing_snapshot(
            "2026-Q2",
            filed_on="2026-07-08T18:00:00",
            status="baseline",
            snapshot_hash="legacy-row",
            form_code="303",
            payload=_snapshot_payload(
                "303",
                filed_values={"result": "-999.99"},
                extraction_status="values_unavailable",
            ),
        )
    app = LocalAccountingApp(config)
    dashboard = app.dashboard("2026-Q2")
    assert dashboard["tax_forms"]["modelo303"]["values"]["result"] == "-407.36"
    assert dashboard["tax_forms"]["modelo303"]["snapshot_hash"] == "v4-row"
    assert dashboard["tax_forms"]["modelo303"]["extraction_status"] == "casillas_extracted"
    assert dashboard["tax_forms"]["modelo303"]["filed_on"] == "2026-07-08"
    assert dashboard["tax_forms"]["modelo303"]["snapshot_status"] == "baseline"


def test_upload_is_content_verified_and_conflicts_get_distinct_name(
    tmp_path: Path,
) -> None:
    inbox = tmp_path / "Inbox"
    first = _store_upload(
        inbox,
        period="2026-Q3",
        kind="expense_invoice",
        filename="../../invoice.pdf",
        content=b"first",
    )
    same = _store_upload(
        inbox,
        period="2026-Q3",
        kind="expense_invoice",
        filename="invoice.pdf",
        content=b"first",
    )
    different = _store_upload(
        inbox,
        period="2026-Q3",
        kind="expense_invoice",
        filename="invoice.pdf",
        content=b"second",
    )

    assert first == same
    assert first.read_bytes() == b"first"
    assert different != first
    assert different.parent == inbox / "2026-Q3" / "expense_invoice"
    assert different.read_bytes() == b"second"


def test_server_reuse_policy_tracks_exclusive_socket_support() -> None:
    assert LocalAccountingServer.allow_reuse_address == (
        not hasattr(socket, "SO_EXCLUSIVEADDRUSE")
    )


@pytest.mark.skipif(
    not hasattr(socket, "SO_EXCLUSIVEADDRUSE"),
    reason="Windows exclusive-address semantics",
)
def test_existing_reusable_listener_blocks_local_accounting_server(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _database(config)
    app = LocalAccountingApp(config)
    existing = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    existing.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    existing.bind(("127.0.0.1", 0))
    existing.listen()
    try:
        with pytest.raises(OSError) as raised:
            LocalAccountingServer(existing.getsockname(), app)
        assert raised.value.errno == errno.EADDRINUSE
    finally:
        existing.close()


@pytest.mark.skipif(
    not hasattr(socket, "SO_EXCLUSIVEADDRUSE"),
    reason="Windows exclusive-address semantics",
)
def test_local_accounting_server_blocks_reusable_second_listener(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _database(config)
    app = LocalAccountingApp(config)
    server = LocalAccountingServer(("127.0.0.1", 0), app)
    second = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    second.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        with pytest.raises(OSError) as raised:
            second.bind(server.server_address)
        assert raised.value.errno == errno.EACCES
    finally:
        second.close()
        server.server_close()


@pytest.mark.skipif(
    not hasattr(socket, "SO_EXCLUSIVEADDRUSE"),
    reason="Windows exclusive-address semantics",
)
def test_exclusive_server_can_rebind_after_serving_a_request(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _database(config)
    app = LocalAccountingApp(config)
    server = LocalAccountingServer(("127.0.0.1", 0), app)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        response.read()
        assert response.status == 200
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    rebound = LocalAccountingServer(("127.0.0.1", port), app)
    rebound.server_close()


@pytest.mark.parametrize("bind_errno", [errno.EADDRINUSE, errno.EACCES])
def test_main_reports_address_conflicts_without_a_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bind_errno: int,
) -> None:
    config = _config(tmp_path)
    _database(config)

    def fail_to_bind(*_args: object, **_kwargs: object) -> None:
        raise OSError(bind_errno, "occupied")

    monkeypatch.setattr(local_web, "LocalAccountingServer", fail_to_bind)
    with pytest.raises(SystemExit) as raised:
        local_web.main(
            [
                "--project-root",
                str(tmp_path),
                "--db",
                str(config.database),
                "--host",
                "127.0.0.1",
                "--port",
                "43210",
            ]
        )

    message = str(raised.value)
    assert "http://127.0.0.1:43210" in message
    assert "already in use or reserved" in message
    assert "Get-NetTCPConnection -LocalPort 43210 -State Listen" in message
    assert "--port <port>" in message


def test_main_keeps_non_address_bind_failures_neutral(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    _database(config)

    def fail_to_bind(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EINVAL, "unsupported address")

    monkeypatch.setattr(local_web, "LocalAccountingServer", fail_to_bind)
    with pytest.raises(SystemExit) as raised:
        local_web.main(
            [
                "--project-root",
                str(tmp_path),
                "--db",
                str(config.database),
                "--host",
                "::1",
                "--port",
                "43210",
            ]
        )

    message = str(raised.value)
    assert "http://[::1]:43210" in message
    assert "unsupported address" in message
    assert "already in use or reserved" not in message
    assert "--port <port>" not in message


def test_http_interface_sets_local_session_and_protects_api(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _database(config)
    app = LocalAccountingApp(config, session_token="test-token")
    server = LocalAccountingServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        body = response.read()
        cookie = response.getheader("Set-Cookie")
        assert response.status == 200
        assert b"Aut\xc3\xb3nomo" in body
        assert "autonomo_session=test-token" in cookie
        assert "__Host-autonomo_session" not in cookie
        assert "Secure" not in cookie
        assert response.getheader("Referrer-Policy") == "no-referrer"

        connection.request(
            "GET",
            "/api/bootstrap",
            headers={
                "Host": f"127.0.0.1:{port}",
                "Cookie": "autonomo_session=test-token",
            },
        )
        api_response = connection.getresponse()
        api_body = api_response.read()
        assert api_response.status == 200
        assert b'"default_period":"2026-Q3"' in api_body

        connection.request(
            "GET",
            "/api/bootstrap",
            headers={"Host": f"127.0.0.1:{port}"},
        )
        denied = connection.getresponse()
        denied.read()
        assert denied.status == 400

        connection.request(
            "GET",
            "/api/not-a-route",
            headers={
                "Host": f"127.0.0.1:{port}",
                "Cookie": "autonomo_session=test-token",
            },
        )
        missing_get = connection.getresponse()
        missing_get_body = json.loads(missing_get.read())
        assert missing_get.status == 404
        assert missing_get.getheader("Content-Type").startswith("application/json")
        assert missing_get_body == {"error": "Unknown API endpoint"}

        connection.request(
            "POST",
            "/api/not-a-route",
            body=b"",
            headers={
                "Host": f"127.0.0.1:{port}",
                "Cookie": "autonomo_session=test-token",
                "Origin": f"http://127.0.0.1:{port}",
                "Content-Length": "0",
            },
        )
        missing_post = connection.getresponse()
        missing_post_body = json.loads(missing_post.read())
        assert missing_post.status == 404
        assert missing_post.getheader("Content-Type").startswith("application/json")
        assert missing_post_body == {"error": "Unknown API endpoint"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_picker_security_headers_send_only_the_tailnet_origin(tmp_path: Path) -> None:
    config = replace(
        _config(
            tmp_path,
            trusted_proxy_mode="tailscale_serve",
            allowed_tailscale_logins=("agent-login",),
        ),
        google_picker_developer_key="restricted-browser-key",
        google_picker_app_id="344327133225",
    )
    _database(config)
    app = LocalAccountingApp(
        config,
        session_token="test-token",
        principal_session_secret="secret-key",
    )
    server = LocalAccountingServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request(
            "GET",
            "/",
            headers={
                "Host": "ubuntu-16gb-nbg1-2.tail6c29f3.ts.net",
                "Tailscale-User-Login": "agent-login",
                "X-Forwarded-Proto": "https",
            },
        )
        response = connection.getresponse()
        response.read()

        assert response.status == 200
        assert response.getheader("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "https://apis.google.com" in response.getheader("Content-Security-Policy")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_tailscale_proxy_requires_identity_before_setting_cookie(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        trusted_proxy_mode="tailscale_serve",
        allowed_tailscale_logins=("agent-login",),
    )
    _database(config)
    app = LocalAccountingApp(
        config,
        session_token="test-token",
        principal_session_secret="secret-key",
    )
    server = LocalAccountingServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {
            "Host": "ubuntu-16gb-nbg1-2.tail6c29f3.ts.net",
            "Tailscale-User-Login": "agent-login",
            "X-Forwarded-Proto": "https",
        }
        connection.request("GET", "/", headers=headers)
        response = connection.getresponse()
        body = response.read()
        cookie = response.getheader("Set-Cookie")
        assert response.status == 200
        assert b"Aut\xc3\xb3nomo" in body
        assert "__Host-autonomo_session=" in cookie
        assert "Secure" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie
        assert "autonomo_session=test-token" not in cookie

        session_cookie = cookie.split(";", 1)[0]
        connection.request(
            "GET",
            "/api/bootstrap",
            headers={
                **headers,
                "Cookie": session_cookie,
            },
        )
        api_response = connection.getresponse()
        api_body = api_response.read()
        assert api_response.status == 200
        assert b'"default_period":"2026-Q3"' in api_body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_tailscale_proxy_rejects_missing_or_wrong_identity_without_cookie(
    tmp_path: Path,
) -> None:
    config = _config(
        tmp_path,
        trusted_proxy_mode="tailscale_serve",
        allowed_tailscale_logins=("agent-login",),
    )
    _database(config)
    app = LocalAccountingApp(
        config,
        session_token="test-token",
        principal_session_secret="secret-key",
    )
    server = LocalAccountingServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request(
            "GET",
            "/",
            headers={
                "Host": "ubuntu-16gb-nbg1-2.tail6c29f3.ts.net",
                "X-Forwarded-Proto": "https",
            },
        )
        missing_identity = connection.getresponse()
        missing_body = json.loads(missing_identity.read())
        assert missing_identity.status == 403
        assert missing_identity.getheader("Set-Cookie") is None
        assert missing_body["code"] == "tailscale_identity_required"

        connection.request(
            "GET",
            "/",
            headers={
                "Host": "ubuntu-16gb-nbg1-2.tail6c29f3.ts.net",
                "Tailscale-User-Login": "intruder-login",
                "X-Forwarded-Proto": "https",
            },
        )
        wrong_identity = connection.getresponse()
        wrong_body = json.loads(wrong_identity.read())
        assert wrong_identity.status == 403
        assert wrong_identity.getheader("Set-Cookie") is None
        assert wrong_body["code"] == "tailscale_identity_forbidden"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_tailscale_proxy_binds_session_to_principal_and_https_origin(
    tmp_path: Path,
) -> None:
    config = _config(
        tmp_path,
        trusted_proxy_mode="tailscale_serve",
        allowed_tailscale_logins=("agent-login", "second-login"),
    )
    _database(config)
    app = LocalAccountingApp(
        config,
        session_token="test-token",
        principal_session_secret="secret-key",
    )
    server = LocalAccountingServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        connection = HTTPConnection("127.0.0.1", port, timeout=5)
        base_headers = {
            "Host": "ubuntu-16gb-nbg1-2.tail6c29f3.ts.net",
            "Tailscale-User-Login": "agent-login",
            "X-Forwarded-Proto": "https",
        }
        connection.request("GET", "/", headers=base_headers)
        landing = connection.getresponse()
        cookie = landing.getheader("Set-Cookie")
        landing.read()
        assert cookie is not None
        session_cookie = cookie.split(";", 1)[0]

        connection.request(
            "POST",
            "/api/not-a-route",
            body=b"",
            headers={
                **base_headers,
                "Cookie": session_cookie,
                "Origin": "https://ubuntu-16gb-nbg1-2.tail6c29f3.ts.net",
                "Content-Length": "0",
            },
        )
        same_origin = connection.getresponse()
        same_origin_body = json.loads(same_origin.read())
        assert same_origin.status == 404
        assert same_origin_body == {"error": "Unknown API endpoint"}

        connection.request(
            "GET",
            "/api/bootstrap",
            headers={
                "Host": "ubuntu-16gb-nbg1-2.tail6c29f3.ts.net",
                "Tailscale-User-Login": "second-login",
                "X-Forwarded-Proto": "https",
                "Cookie": session_cookie,
            },
        )
        rebound = connection.getresponse()
        rebound_body = json.loads(rebound.read())
        assert rebound.status == 403
        assert rebound_body == {
            "code": "session_forbidden",
            "error": "Session is missing or expired",
        }

        connection.request(
            "POST",
            "/api/not-a-route",
            body=b"",
            headers={
                **base_headers,
                "Cookie": session_cookie,
                "Origin": "http://ubuntu-16gb-nbg1-2.tail6c29f3.ts.net",
                "Content-Length": "0",
            },
        )
        bad_origin = connection.getresponse()
        bad_origin_body = json.loads(bad_origin.read())
        assert bad_origin.status == 400
        assert bad_origin_body["error"] == "Cross-origin writes are not allowed"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_document_file_returns_503_when_read_only_root_is_unavailable(
    tmp_path: Path,
) -> None:
    readonly_root = tmp_path / "gdrive"
    config = _config(tmp_path, read_only_document_roots=(readonly_root,))
    _database(config)
    document_path = readonly_root / "2026" / "receipt.pdf"
    document_path.parent.mkdir(parents=True, exist_ok=True)
    document_path.write_bytes(b"pdf")
    with LedgerDB.open(config.database) as db:
        document = db.upsert_document(
            external_key="gdrive-doc",
            document_type="expense_invoice",
            issued_on="2026-08-01",
            period_key="2026-Q3",
            currency="EUR",
            total_minor=12100,
            lifecycle_status="approved",
            source_hash="sha256:readonly-document",
        )
        document_id = str(document["document_id"])
        db.set_document_storage(
            document_id,
            source_path=str(document_path),
            mime_type="application/pdf",
            expected_row_version=int(document["row_version"]),
        )
    shutil.rmtree(readonly_root)

    app = LocalAccountingApp(config)

    with pytest.raises(LocalWebApiError) as raised:
        app.document_file(document_id)

    assert raised.value.status == 503
    assert raised.value.code == "document_root_unavailable"


def test_document_file_prefers_verified_local_replica_over_legacy_path(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _database(config)
    replica_path = config.archive_root / "2026-Q3" / "invoice.pdf"
    replica_path.parent.mkdir(parents=True)
    content = b"verified invoice bytes"
    replica_path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    with LedgerDB.open(config.database) as db:
        document = db.upsert_document(
            external_key="replica-doc",
            document_type="expense_invoice",
            issued_on="2026-08-01",
            period_key="2026-Q3",
            lifecycle_status="approved",
            source_hash=digest,
        )
        document = db.set_document_storage(
            str(document["document_id"]),
            source_path=str(tmp_path / "legacy-missing.pdf"),
            mime_type="application/pdf",
            expected_row_version=int(document["row_version"]),
        )
        backend = db.upsert_storage_backend(
            backend_key="local_staging",
            display_name="Local staging",
            driver_key="filesystem",
            provider_key="local",
            access_mode="read_write",
            config={"schema_version": 1, "root": str(config.archive_root)},
            read_priority=20,
        )
        file_row = db.upsert_file(
            content_sha256=digest,
            byte_size=len(content),
            media_type="application/pdf",
        )
        db.attach_file_to_document(
            document_id=str(document["document_id"]),
            file_id=str(file_row["file_id"]),
            attachment_role="source",
            display_name=replica_path.name,
        )
        db.register_file_replica(
            file_id=str(file_row["file_id"]),
            storage_backend_id=str(backend["storage_backend_id"]),
            provider_locator="2026-Q3/invoice.pdf",
            is_primary=True,
            last_verified_at="2026-08-17T12:00:00Z",
        )

    path, mime_type = LocalAccountingApp(config).document_file(str(document["document_id"]))

    assert path == replica_path.resolve()
    assert mime_type == "application/pdf"


def test_document_file_falls_back_when_catalogued_replica_is_corrupt(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _database(config)
    legacy_path = config.archive_root / "legacy.pdf"
    legacy_content = b"intact legacy invoice"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(legacy_content)
    corrupt_path = config.archive_root / "replicas" / "invoice.pdf"
    corrupt_path.parent.mkdir()
    corrupt_path.write_bytes(b"corrupt")
    digest = hashlib.sha256(legacy_content).hexdigest()
    with LedgerDB.open(config.database) as db:
        document = db.upsert_document(
            external_key="corrupt-replica-doc",
            document_type="expense_invoice",
            issued_on="2026-08-01",
            period_key="2026-Q3",
            lifecycle_status="approved",
            source_hash=digest,
        )
        document = db.set_document_storage(
            str(document["document_id"]),
            source_path=str(legacy_path),
            mime_type="application/pdf",
            expected_row_version=int(document["row_version"]),
        )
        backend = db.upsert_storage_backend(
            backend_key="local_staging",
            display_name="Local staging",
            driver_key="filesystem",
            provider_key="local",
            access_mode="read_write",
            config={"schema_version": 1, "root": str(config.archive_root)},
        )
        file_row = db.upsert_file(
            content_sha256=digest,
            byte_size=len(legacy_content),
            media_type="application/pdf",
        )
        db.attach_file_to_document(
            document_id=str(document["document_id"]),
            file_id=str(file_row["file_id"]),
            attachment_role="source",
        )
        db.register_file_replica(
            file_id=str(file_row["file_id"]),
            storage_backend_id=str(backend["storage_backend_id"]),
            provider_locator="replicas/invoice.pdf",
            is_primary=True,
            last_verified_at="2026-08-17T12:00:00Z",
        )

    path, _ = LocalAccountingApp(config).document_file(str(document["document_id"]))

    assert path == legacy_path.resolve()


@pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir(),
    reason="requires procfs to inspect file descriptors",
)
def test_bootstrap_does_not_leak_file_descriptors(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _database(config)
    app = LocalAccountingApp(config)

    def fd_count() -> int:
        return len(list(Path("/proc/self/fd").iterdir()))

    baseline = fd_count()
    for _ in range(2000):
        payload = app.bootstrap()
        assert payload["default_period"] == "2026-Q3"
    assert fd_count() <= baseline + 3


def test_review_apply_fx_uses_shared_operation_and_refreshes_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    _database(config)
    app = LocalAccountingApp(config)
    review_id = "transaction:00000000-0000-0000-0000-0000000SYNTH-DOCUMENT-023"
    calls: list[tuple[list[str], str | None]] = []
    work_items = [
        {"review_id": review_id, "packet": {"snapshot_hash": "before"}},
        {"review_id": review_id, "packet": {"snapshot_hash": "after"}},
    ]

    monkeypatch.setattr(app, "review_work_item", lambda _review_id: work_items.pop(0))
    monkeypatch.setattr(
        invoice_services,
        "apply_fx",
        lambda database, payload: calls.append((database, payload)) or ({}, 0),
    )
    payload = {
        "review_id": review_id,
        "expected_row_version": 3,
        "rate_date": "2026-08-01",
        "rate": "0.91",
        "rate_source": "banco_de_espana",
        "source_reference": "Official daily rates",
    }

    refreshed = app.review_apply_fx(payload)

    assert refreshed["packet"]["snapshot_hash"] == "after"
    database, submitted = calls[0]
    assert database == config.database
    assert submitted == payload


def test_review_apply_fx_maps_stale_operation_failure_to_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    _database(config)
    app = LocalAccountingApp(config)
    monkeypatch.setattr(app, "review_work_item", lambda _review_id: {"supported": True})

    def fail(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise LocalWebError("Expected row_version 1, found 2")

    monkeypatch.setattr(invoice_services, "apply_fx", fail)
    with pytest.raises(LocalWebApiError) as raised:
        app.review_apply_fx(
            {
                "review_id": "transaction:00000000-0000-0000-0000-0000000SYNTH-DOCUMENT-023",
                "expected_row_version": 1,
                "rate_date": "2026-08-01",
                "rate": "0.91",
                "rate_source": "banco_de_espana",
                "source_reference": "Official daily rates",
            }
        )
    assert raised.value.status == 409
    assert raised.value.code == "stale_snapshot"


def test_cli_error_detail_extracts_safe_final_exception_message() -> None:
    stderr = "Traceback (most recent call last):\nValueError: invalid review decision\n"
    assert _cli_error_detail(stderr, "") == "invalid review decision"


def test_host_header_parts_requires_a_valid_same_origin_target() -> None:
    assert _host_header_parts("127.0.0.1:8876", default_port=80) == (
        "127.0.0.1",
        8876,
    )
    assert _host_header_parts("[::1]:8876", default_port=80) == ("::1", 8876)
    assert _host_header_parts("localhost", default_port=8876) == (
        "localhost",
        8876,
    )
    assert _host_header_parts(
        "ubuntu-16gb-nbg1-2.tail6c29f3.ts.net",
        default_port=443,
    ) == ("ubuntu-16gb-nbg1-2.tail6c29f3.ts.net", 443)
    with pytest.raises(LocalWebApiError, match="Host header is invalid"):
        _host_header_parts("localhost:not-a-port", default_port=8876)


# Requires the separately built optional UI assets.
import pytest
pytestmark = pytest.mark.web

from autonomo_taxes.services import invoices as invoice_services
