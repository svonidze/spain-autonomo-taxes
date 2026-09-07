from autonomo_test_support.paths import REPO_ROOT
"""Exercise callers and deployed databases across the FX verification boundary."""

from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from autonomo_taxes import ledger_db, expense_workflow as workflow
from autonomo_taxes.fx_reference import build_rate_url
from autonomo_taxes.ledger_db import open as open_db
from autonomo_taxes.review_packet import (
    ReviewPacketError,
    build_fx_suggestion,
    confirm_review_packet,
)
from autonomo_taxes.local_web import LocalWebConfig
from autonomo_test_support.local_web_guided import _Server, _fake_ecb_result
from autonomo_test_support.expense_workflow import setup_draft
from autonomo_test_support.review_confirm import (
    _invoice_fixture,
    _packet,
    _approve_decision,
    _ecb_observation,
    _ecb_fx_spec,
)


def _save_foreign_draft(db, fixture, draft, source):
    draft["payload"]["facts"]["currency"] = "USD"
    draft["payload"]["fx"] = {
        "rate_date": date.today().isoformat(),
        "rate": "1",
        "rate_source": source,
        "source_reference": "https://example.invalid/synthetic-settlement",
        "raw_observation": '{"operator_confirmed":true}',
        "supersedes_rate_id": None,
    }
    return workflow.save_draft(
        db,
        fixture["transaction_id"],
        payload=draft["payload"],
        expected_version=draft["draft_version"],
        source_snapshot_hash=draft["source_snapshot_hash"],
        actor="synthetic-user",
    )


@pytest.mark.parametrize("source", ["ecb", "actual_settlement"])
def test_foreign_expense_preview_post_and_retry(tmp_path, source):
    fixture, draft = setup_draft(tmp_path)
    with open_db(fixture["database"]) as db:
        saved = _save_foreign_draft(db, fixture, draft, source)
        calls = []

        def verify(currency, day):
            assert not db.connection.in_transaction
            calls.append((currency, day))
            return _ecb_observation("1", currency, day)

        before = "\n".join(db.connection.iterdump())
        proposed = workflow.preview(
            db,
            fixture["transaction_id"],
            expected_version=saved["draft_version"],
            ecb_verify=verify,
        )
        assert "\n".join(db.connection.iterdump()) == before
        assert proposed["currency"] == "USD"
        arguments = dict(
            expected_version=saved["draft_version"],
            preview_token=proposed["preview_token"],
            request_id=str(uuid4()),
            actor="synthetic-user",
            archive_root=tmp_path / "archive",
            ecb_verify=verify,
        )
        result = workflow.confirm_and_post(db, fixture["transaction_id"], **arguments)
        assert result["posted"]
        assert (
            workflow.confirm_and_post(db, fixture["transaction_id"], **arguments)
            == result
        )
        assert len(calls) == (2 if source == "ecb" else 0)
        transaction = dict(
            db.connection.execute(
                "SELECT * FROM transactions WHERE transaction_id=?",
                (fixture["transaction_id"],),
            ).fetchone()
        )
        assert transaction["amount_eur_minor"] == 12100
        evidence = db.fx_verification_for_transaction(
            fixture["transaction_id"],
            transaction["fx_rate_id"],
        )
        assert (evidence is not None) == (source == "ecb")
        if evidence:
            assert (
                evidence["raw_observation"]
                == _ecb_observation("1", "USD", date.today()).raw_observation
            )
        assert db.connection.execute(
            "SELECT COUNT(*) FROM fx_verifications"
        ).fetchone()[0] == (1 if source == "ecb" else 0)


@pytest.mark.parametrize("legacy_reference", ["same", "weekend", "missing"])
def test_v23_upgrade_preserves_legacy_rate_and_records_verified_evidence(
    tmp_path,
    monkeypatch,
    legacy_reference,
):
    rate_day, operation_day = date(2026, 8, 28), date(2026, 8, 30)
    server_url = build_rate_url("USD", rate_day - timedelta(days=7), rate_day)
    old_url = {
        "same": server_url,
        "weekend": build_rate_url(
            "USD", operation_day - timedelta(days=7), operation_day
        ),
        "missing": None,
    }[legacy_reference]
    raw = '{"legacy_client_claim":"preserve this historical evidence"}'
    with monkeypatch.context() as legacy:
        legacy.setattr(ledger_db, "LATEST_SCHEMA_VERSION", 23)
        fixture = _invoice_fixture(
            tmp_path, currency="USD", transaction_date=operation_day.isoformat()
        )
        with open_db(fixture["database"]) as db:
            rate = db.add_fx_rate(
                rate_date=rate_day.isoformat(),
                base_currency="USD",
                quote_currency="EUR",
                rate="0.9216",
                rate_source="ecb",
                source_reference=old_url,
                source_hash=hashlib.sha256(b"synthetic-old-rate").hexdigest(),
                provenance={"raw_observation": raw},
            )
            original = db.fx_provenance_for_rate(rate["fx_rate_id"])
    with open_db(fixture["database"], apply_migrations=True) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 24
        assert (
            db.connection.execute("SELECT COUNT(*) FROM fx_verifications").fetchone()[0]
            == 0
        )
        assert db.fx_provenance_for_rate(rate["fx_rate_id"]) == original

    packet = _packet(fixture)
    _approve_decision(packet)
    spec = _ecb_fx_spec()
    spec.update(
        rate_date=rate_day.isoformat(),
        source_reference="https://example.invalid/forged",
        raw_observation="forged",
    )
    observation = replace(
        _ecb_observation("0.9216", "USD", rate_day), source_url=server_url
    )
    with open_db(fixture["database"]) as db:
        result = confirm_review_packet(
            db, packet, spec, ecb_verify=lambda *_: observation
        )
        assert result["outcome"] == "approve"
        assert dict(db.connection.execute("SELECT * FROM fx_rates").fetchone()) == rate
        assert db.fx_provenance_for_rate(rate["fx_rate_id"]) == original
        evidence = db.fx_verification_for_transaction(
            fixture["transaction_id"], rate["fx_rate_id"]
        )
        assert evidence["source_url"] == server_url
        assert evidence["raw_observation"] == observation.raw_observation
        assert (
            evidence["raw_observation_hash"]
            == hashlib.sha256(observation.raw_observation.encode()).hexdigest()
        )
        assert (
            db.fx_verification_for_transaction(
                "another-transaction", rate["fx_rate_id"]
            )
            is None
        )
        state = _packet(fixture)["state"]
        suggestion = build_fx_suggestion(
            state, provenance=original, verification=evidence
        )
        assert suggestion["source_reference"] == server_url
        assert suggestion["raw_observation"] == observation.raw_observation
        assert suggestion["provenance"] == original
        assert suggestion["verification"] == evidence
        for statement in (
            "UPDATE fx_verifications SET source_url = source_url",
            "DELETE FROM fx_verifications",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.connection.execute(statement)
            db.connection.rollback()


def test_verified_numeric_conflict_is_rejected_without_rewriting_evidence(tmp_path):
    fixture = _invoice_fixture(tmp_path, currency="USD")
    with open_db(fixture["database"]) as db:
        db.add_fx_rate(
            rate_date=date.today().isoformat(),
            base_currency="USD",
            quote_currency="EUR",
            rate="0.95",
            rate_source="ecb",
            source_reference="legacy source",
            source_hash="synthetic",
        )
    packet = _packet(fixture)
    _approve_decision(packet)
    with open_db(fixture["database"]) as db:
        before = "\n".join(db.connection.iterdump())
        with pytest.raises(ReviewPacketError, match="conflicts with the stored rate"):
            confirm_review_packet(
                db,
                packet,
                _ecb_fx_spec(),
                ecb_verify=lambda *_: _ecb_observation("0.9216", "USD", date.today()),
            )
        assert "\n".join(db.connection.iterdump()) == before


def test_failed_decision_rolls_back_verification_event(tmp_path):
    fixture = _invoice_fixture(tmp_path, currency="USD")
    packet = _packet(fixture)
    _approve_decision(packet)
    packet["decision"]["reason"] = ""
    with open_db(fixture["database"]) as db:
        before = "\n".join(db.connection.iterdump())
        with pytest.raises(ReviewPacketError):
            confirm_review_packet(
                db,
                packet,
                _ecb_fx_spec(),
                ecb_verify=lambda *_: _ecb_observation("0.9216", "USD", date.today()),
            )
        assert "\n".join(db.connection.iterdump()) == before


@pytest.mark.parametrize("source", ["ecb", "actual_settlement"])
def test_foreign_expense_http_preview_and_confirm(tmp_path, monkeypatch, source):
    fixture, draft = setup_draft(tmp_path)
    with open_db(fixture["database"]) as db:
        saved = _save_foreign_draft(db, fixture, draft, source)
    root = REPO_ROOT
    config = LocalWebConfig(
        project_root=root,
        database=fixture["database"],
        inbox_root=tmp_path / "inbox",
        archive_root=tmp_path / "archive",
        cache_root=tmp_path / "cache",
        static_root=root / "backend/src/autonomo_taxes/web_ui",
        read_only_document_roots=(tmp_path,),
    )
    server = _Server(config, monkeypatch, ecb=lambda *_: _fake_ecb_result(units="1"))
    monkeypatch.setattr(
        server.server.app, "expense_follow_up", lambda *_: {"follow_up_pending": False}
    )
    endpoint = "/api/expense-workflows/" + fixture["transaction_id"]
    origin = f"http://127.0.0.1:{server.port}"
    try:
        status, _, body = server.request(
            "POST",
            endpoint + "/preview",
            origin=origin,
            content_type="application/json",
            body=json.dumps({"expected_version": saved["draft_version"]}).encode(),
        )
        assert status == 200, body
        proposed = json.loads(body)
        request = json.dumps(
            {
                "expected_version": saved["draft_version"],
                "preview_token": proposed["preview_token"],
                "request_id": str(uuid4()),
            }
        ).encode()
        status, _, body = server.request(
            "POST",
            endpoint + "/confirm",
            body=request,
            origin=origin,
            content_type="application/json",
        )
        assert status == 200, body
        assert json.loads(body)["posted"]
        status, _, body = server.request(
            "POST",
            endpoint + "/confirm",
            body=request,
            origin=origin,
            content_type="application/json",
        )
        assert status == 200, body
        assert json.loads(body)["posted"]
        suggestion = server.server.app.expense_draft(fixture["transaction_id"])[
            "fx_suggestion"
        ]
        assert (suggestion["verification"] is not None) == (source == "ecb")
        if source == "ecb":
            assert (
                suggestion["raw_observation"]
                == _fake_ecb_result(units="1").observation.raw_observation
            )
    finally:
        server.close()


def test_verification_migration_is_explicit_and_atomic(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite"
    with monkeypatch.context() as legacy:
        legacy.setattr(ledger_db, "LATEST_SCHEMA_VERSION", 23)
        with ledger_db.initialize(path):
            pass
    with pytest.raises(ledger_db.SchemaVersionError):
        open_db(path)
    original = ledger_db._MIGRATIONS[24]

    def fail_after_create(connection):
        original(connection)
        raise RuntimeError("synthetic migration failure")

    with monkeypatch.context() as broken:
        broken.setitem(ledger_db._MIGRATIONS, 24, fail_after_create)
        with pytest.raises(RuntimeError, match="synthetic migration failure"):
            open_db(path, apply_migrations=True)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 23
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='fx_verifications'"
            ).fetchone()
            is None
        )
    with open_db(path, apply_migrations=True) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 24
        assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_changed_draft_during_ecb_lookup_is_rejected(tmp_path):
    fixture, draft = setup_draft(tmp_path)
    with open_db(fixture["database"]) as db:
        saved = _save_foreign_draft(db, fixture, draft, "ecb")

        def verify(currency, day):
            with open_db(fixture["database"]) as other:
                payload = dict(saved["payload"])
                payload["change_reason"] = "Concurrent synthetic edit"
                workflow.save_draft(
                    other,
                    fixture["transaction_id"],
                    payload=payload,
                    expected_version=saved["draft_version"],
                    source_snapshot_hash=saved["source_snapshot_hash"],
                    actor="another-user",
                )
            return _ecb_observation("1", currency, day)

        with pytest.raises(workflow.ExpenseWorkflowError, match="changed"):
            workflow.preview(
                db,
                fixture["transaction_id"],
                expected_version=saved["draft_version"],
                ecb_verify=verify,
            )
        assert db.connection.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0] == 0
        assert (
            db.connection.execute("SELECT COUNT(*) FROM fx_verifications").fetchone()[0]
            == 0
        )
