import sqlite3
from datetime import date
from pathlib import Path

import pytest

from autonomo_taxes import status_context
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.local_web import (
    LocalAccountingApp,
    LocalWebApiError,
    LocalWebConfig,
)
from autonomo_taxes.posting import build_posting_preview


@pytest.fixture
def context_app(tmp_path, monkeypatch):
    config = LocalWebConfig(
        project_root=tmp_path,
        database=tmp_path / "ledger.sqlite",
        inbox_root=tmp_path / "inbox",
        archive_root=tmp_path / "archive",
        cache_root=tmp_path / "cache",
        static_root=Path(__file__).resolve().parents[1] / "src/autonomo_taxes/web_ui",
    )
    monkeypatch.setattr(
        status_context,
        "build_posting_preview",
        lambda db, today=None, **kwargs: build_posting_preview(
            db, today=date(2032, 4, 1), **kwargs
        ),
    )
    with LedgerDB.initialize(config.database) as db:
        cp = db.upsert_counterparty(
            display_name="Synthetic Equipment Supplier",
            external_key="synthetic-supplier",
        )
        doc = db.upsert_document(
            document_type="expense_invoice",
            document_number="SYN-DEVICE-001",
            issued_on="2032-04-20",
            period_key="2032-Q2",
            total_minor=121000,
            counterparty_id=cp["counterparty_id"],
            lifecycle_status="approved",
            source_hash="synthetic-document",
        )
        tx = db.add_transaction(
            external_key="synthetic-acquisition",
            period_key="2032-Q2",
            transaction_date="2032-04-20",
            booking_date="2032-04-20",
            entry_type="expense",
            description="Synthetic equipment",
            amount_minor=121000,
            document_id=doc["document_id"],
            counterparty_id=cp["counterparty_id"],
            lifecycle_status="approved",
        )
        for kind in ("vat", "irpf"):
            db.add_detailed_tax_treatment(
                transaction_id=tx["transaction_id"],
                treatment_type=kind,
                tax_code="domestic_input",
                taxable_base_minor=100000,
                vat_minor=21000,
                deductible_irpf_minor=0,
                deductible_vat_minor=21000,
            )
        db.add_validation_issue(
            period_key="2032-Q2",
            issue_code="synthetic_advisory",
            severity="warning",
            blocking=False,
            message="Advisory only",
            subject_table="transactions",
            subject_id=tx["transaction_id"],
        )
        asset = db.add_asset(
            asset_code="SYN-DEVICE",
            cost_minor=121000,
            currency="EUR",
            depreciation_method="linear",
            source_hash="synthetic-asset",
            document_id=doc["document_id"],
            acquisition_transaction_id=tx["transaction_id"],
            placed_in_service_on="2032-04-20",
        )
        db.add_amortization_entry(
            asset_id=asset["asset_id"],
            period_key="2032-Q2",
            amount_minor=10000,
            source_hash="synthetic-quarter",
            include_in_books=False,
        )
        db.add_amortization_entry(
            asset_id=asset["asset_id"],
            period_key="2032",
            amount_minor=10000,
            entry_kind="annual_evidence",
            tax_year=2032,
            source_hash="synthetic-annual",
        )
        db.add_amortization_entry(
            asset_id=asset["asset_id"],
            period_key="2032-Q1",
            amount_minor=-500,
            entry_kind="adjustment",
            source_hash="synthetic-adjustment",
            include_in_books=True,
        )
    return LocalAccountingApp(config), config, tx, asset


def dump(config):
    with sqlite3.connect(config.database) as c:
        return "\n".join(c.iterdump())


def test_asset_period_separates_excluded_annual_and_adjustment(context_app):
    app, config, _, _ = context_app
    before = dump(config)
    rows = app.assets("2032-Q2")
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert row["scheduled_minor"] == 10000 and row["schedule_rows"] == 1
    summary = row["ui_context"]["amortization"]
    assert summary["book_minor"] == 0 and summary["excluded_minor"] == 10000
    assert summary["annual_evidence"][0]["amount_minor"] == 10000
    summary = app.assets("2032-Q1")[0]["ui_context"]["amortization"]
    assert summary["book_minor"] == -500 and summary["adjustment_minor"] == -500
    assert summary["excluded_minor"] == 0
    assert app.assets()[0]["period"] == "2032-Q2"
    with pytest.raises(LocalWebApiError) as e:
        app.assets("2032-Q4")
    assert e.value.status == 404
    assert dump(config) == before


def test_issue_counts_and_posting_readiness_do_not_multiply(context_app):
    app, _, _, _ = context_app
    row = app.transactions("2032-Q2")[0]
    assert row["open_issue_count"] == 1
    assert row["blocking_issue_count"] == 0
    assert row["ui_context"]["posting"]["preview_bucket"] == "deferred"
    assert row["deductible_vat_minor"] == 21000


def test_future_date_does_not_hide_blocker_and_gets_do_not_write(context_app):
    app, config, tx, _ = context_app
    with LedgerDB.open(config.database) as db:
        db.add_validation_issue(
            period_key="2032-Q2",
            issue_code="unrecognized_synthetic_reason",
            severity="warning",
            blocking=True,
            message="<script>untrusted text</script>",
            subject_table="transactions",
            subject_id=tx["transaction_id"],
        )
    before = dump(config)
    row = app.transactions("2032-Q2")[0]
    assert row["open_issue_count"] == 2 and row["blocking_issue_count"] == 1
    ctx = row["ui_context"]
    assert ctx["state"] == "blocked"
    assert ctx["posting"]["preview_bucket"] == "blocked"
    assert ctx["posting"]["posting_deferred_until"] == "2032-04-20"
    assert {r["code"] for r in ctx["posting"]["blockers"]} == {
        "future_dated",
        "unrecognized_synthetic_reason",
    }
    assert not ctx["posting"]["ready_to_post"]
    app.documents("2032-Q2")
    app.issues("2032-Q2")
    app.assets()
    app.counterparties()
    app.taxes("2032-Q2")
    assert dump(config) == before


def test_unlinked_legacy_asset_is_not_declared_unreviewed(context_app):
    app, config, _, _ = context_app
    with LedgerDB.open(config.database) as db:
        legacy = db.add_asset(
            asset_code="SYN-LEGACY",
            cost_minor=50000,
            currency="EUR",
            depreciation_method="linear",
            source_hash="synthetic-legacy",
        )
        db.add_amortization_entry(
            asset_id=legacy["asset_id"],
            period_key="2032",
            amount_minor=7000,
            entry_kind="annual_evidence",
            tax_year=2032,
            source_hash="synthetic-legacy-evidence",
        )
    row = next(a for a in app.assets() if a["asset_code"] == "SYN-LEGACY")
    assert row["ui_context"]["state"] == "recorded_information"
    assert row["scheduled_minor"] == 0 and row["schedule_rows"] == 0
    assert (
        row["ui_context"]["amortization"]["annual_evidence"][0]["amount_minor"] == 7000
    )


def test_complementary_fields_merge_but_conflicting_amounts_stay_unknown(context_app):
    app, config, _tx, _ = context_app
    with LedgerDB.open(config.database) as db, db.connection:
        db.connection.execute(
            "UPDATE tax_treatments SET deductible_vat_minor=NULL WHERE treatment_type='irpf'"
        )
    # Matches tax_row_loader's canonical handling of complementary NULL fields.
    assert app.transactions("2032-Q2")[0]["deductible_vat_minor"] == 21000
    with LedgerDB.open(config.database) as db, db.connection:
        db.connection.execute(
            "UPDATE tax_treatments SET deductible_vat_minor=19000 WHERE treatment_type='irpf'"
        )
    assert app.transactions("2032-Q2")[0]["deductible_vat_minor"] is None


def test_issues_keep_their_identity_and_legacy_resolution(
    context_app, tmp_path, monkeypatch
):
    app, config, tx, _ = context_app
    source = tmp_path / "resolved-original.txt"
    source.write_text("Synthetic source")
    monkeypatch.setattr(app, "resolve_document_path", lambda value: source)
    with LedgerDB.open(config.database) as db:
        doc = db.connection.execute(
            "SELECT * FROM documents WHERE document_id=?", (tx["document_id"],)
        ).fetchone()
        db.set_document_storage(
            doc["document_id"],
            source_path="/nonexistent/synthetic-source.txt",
            expected_row_version=doc["row_version"],
        )
        db.add_validation_issue(
            period_key="2032-Q2",
            issue_code="synthetic_second_issue",
            severity="warning",
            blocking=True,
            message="A separate concern",
            subject_table="transactions",
            subject_id=tx["transaction_id"],
        )
    issues = app.issues("2032-Q2")
    assert {row["ui_context"]["reasons"][0]["code"] for row in issues} == {
        "synthetic_advisory",
        "synthetic_second_issue",
    }
    assert all(
        row["ui_context"]["subject_id"] == row["validation_issue_id"] for row in issues
    )
    source_info = app.assets()[0]["ui_context"]["documents"][0]
    assert source_info["available"] and source_info["availability"] == "local"
    assert "source_path" not in source_info


def test_dashboard_reuses_one_posting_preview(context_app, monkeypatch):
    app, _, _, _ = context_app
    original = status_context.build_posting_preview
    calls = []

    def tracked(*args, **kwargs):
        calls.append(kwargs["period_key"])
        return original(*args, **kwargs)

    monkeypatch.setattr(status_context, "build_posting_preview", tracked)
    app.dashboard("2032-Q2")
    assert calls == ["2032-Q2"]


def test_no_link_to_unsupported_review_form(context_app):
    app, _, _, _ = context_app
    # Source-book entries have no invoice_review record and cannot use the wizard.
    assert app.transactions("2032-Q2")[0]["ui_context"]["actions"] == []


def test_get_array_contract_and_controlled_unknown_period(context_app):
    import http.cookiejar
    import json
    import threading
    import urllib.error
    import urllib.request

    from autonomo_taxes.local_web import LocalAccountingServer

    app, config, _, _ = context_app
    before = dump(config)
    server = LocalAccountingServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with client.open(base + "/") as response:
            assert response.status == 200
        for path in (
            "/api/assets",
            "/api/assets?period=2032-Q2",
            "/api/issues?period=2032-Q2",
            "/api/counterparties",
        ):
            with client.open(base + path) as response:
                data = json.load(response)
                assert isinstance(data, list)
                assert all("ui_context" in row for row in data)
        with pytest.raises(urllib.error.HTTPError) as error:
            client.open(base + "/api/assets?period=2032-Q4")
        assert error.value.code == 404
        for resource in server.app.ui_assets.files:
            if resource.endswith(".js"):
                with client.open(base + "/" + resource) as response:
                    assert "javascript" in response.headers["Content-Type"]
                    assert response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert dump(config) == before
