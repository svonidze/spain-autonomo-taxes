"""Remaining accounting CLI capabilities use the same core service contracts."""

import json
from pathlib import Path
import pytest
from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import initialize, open as open_db
from autonomo_taxes.backup_settings import SETTINGS_FILENAME
from test_account_settings import profile_payload, backup_payload
from test_toolkit_cli import invoke
from test_review_packet import _invoice_fixture


def test_cli_profile_edit_is_versioned_and_identity_locked(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture = _invoice_fixture(tmp_path, entry_type="income")
    code, profile = invoke(
        capsys, fixture, ["profile", "edit"], payload=profile_payload()
    )
    assert code == 0, profile
    code, current = invoke(capsys, fixture, ["profile", "inspect"])
    assert code == 0 and current["profiles"][0]["identity_locked"] is False
    code, updated = invoke(
        capsys,
        fixture,
        ["profile", "edit"],
        payload=profile_payload(profile, full_name="Example Taxpayer"),
    )
    assert (
        code == 0
        and updated["taxpayer_profile_id"] == profile["taxpayer_profile_id"]
        and updated["row_version"] > profile["row_version"]
    )
    code, error = invoke(
        capsys, fixture, ["profile", "edit"], payload=profile_payload(profile)
    )
    assert code == 1 and error["code"] == "profile_conflict"
    with open_db(fixture["database"]) as db:
        db.connection.execute(
            "UPDATE periods SET status='closed' WHERE period_key=?",
            (fixture["period"],),
        )
        db.connection.commit()
    code, current = invoke(capsys, fixture, ["profile", "inspect"])
    assert code == 0 and current["profiles"][0]["identity_locked"]
    code, error = invoke(
        capsys,
        fixture,
        ["profile", "edit"],
        payload=profile_payload(updated, tax_id="OTHER-SYNTHETIC-ID"),
    )
    assert code == 1 and error["code"] == "identity_locked"
    with open_db(fixture["database"], read_only=True) as db:
        assert (
            db.connection.execute("SELECT COUNT(*) FROM taxpayer_profile").fetchone()[0]
            == 1
        )
        assert (
            db.connection.execute(
                "SELECT COUNT(*) FROM taxpayer_profile_changes"
            ).fetchone()[0]
            == 2
        )


def test_cli_backup_policy_requires_confirmation_and_opaque_revision(
    tmp_path, capsys, monkeypatch
):
    root = tmp_path / "private"
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(root))
    fixture = _invoice_fixture(tmp_path, entry_type="income")
    code, initial = invoke(capsys, fixture, ["backup", "settings", "show"])
    assert code == 0 and initial["revision"] == "missing"
    code, error = invoke(
        capsys,
        fixture,
        ["backup", "settings", "set"],
        payload=backup_payload(confirm_local_pruning=False),
    )
    assert code == 1 and not (root / SETTINGS_FILENAME).exists()
    code, saved = invoke(
        capsys,
        fixture,
        ["backup", "settings", "set"],
        payload=backup_payload(daily_keep=14, monthly_keep=None),
    )
    assert code == 0 and saved["daily_keep"] == 14 and saved["monthly_keep"] is None
    assert isinstance(saved["revision"], str) and saved["revision"] != "missing"
    before = (root / SETTINGS_FILENAME).read_bytes()
    code, error = invoke(
        capsys, fixture, ["backup", "settings", "set"], payload=backup_payload()
    )
    assert code == 1 and error["code"] == "backup_settings_conflict"
    assert (root / SETTINGS_FILENAME).read_bytes() == before
    code, read = invoke(capsys, fixture, ["backup", "settings", "show"])
    assert (
        code == 0
        and read["revision"] == saved["revision"]
        and str(root) not in json.dumps(read)
    )


def test_cli_counterparty_rename_records_actor_and_rejects_stale_version(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture = _invoice_fixture(tmp_path, entry_type="income")
    code, party = invoke(
        capsys, fixture, ["counterparties", "show", fixture["counterparty_id"]]
    )
    assert code == 0
    party = party["counterparty"]
    request = {
        "display_name": "Synthetic Customer",
        "expected_row_version": party["row_version"],
    }
    code, result = invoke(
        capsys,
        fixture,
        ["counterparties", "rename", fixture["counterparty_id"]],
        payload=request,
    )
    assert code == 0 and result["display_name"] == "Synthetic Customer"
    code, error = invoke(
        capsys,
        fixture,
        ["counterparties", "rename", fixture["counterparty_id"]],
        payload=request,
    )
    assert code == 1 and error["code"] == "stale_counterparty"
    assert error["current"]["row_version"] == result["row_version"]
    code, history = invoke(
        capsys, fixture, ["counterparties", "name-history", fixture["counterparty_id"]]
    )
    assert (
        code == 0
        and len(history["changes"]) == 1
        and history["changes"][0]["actor"].startswith("cli:")
        and history["changes"][0]["change_source"] == "cli"
    )


def test_cli_financial_reads_do_not_modify_ledger_or_create_cache(
    tmp_path, capsys, monkeypatch
):
    root = tmp_path / "private"
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(root))
    fixture = _invoice_fixture(
        tmp_path, entry_type="income", transaction_date="2026-07-01"
    )
    with open_db(fixture["database"], read_only=True) as db:
        before = "\n".join(db.connection.iterdump())
    cache = root / "private-cache"
    for name in ["summary", "taxes", "analytics"]:
        code, result = invoke(
            capsys,
            fixture,
            ["period", name, "--period", fixture["period"], "--cache-root", str(cache)],
        )
        assert code == 0, (name, result)
    code, assets = invoke(
        capsys, fixture, ["assets", "inspect", "--period", fixture["period"]]
    )
    assert code == 0 and isinstance(assets, list)
    assert not cache.exists()
    with open_db(fixture["database"], read_only=True) as db:
        assert "\n".join(db.connection.iterdump()) == before
    code, error = invoke(
        capsys, fixture, ["period", "analytics", "--period", "invalid"]
    )
    assert code == 1 and error["code"] == "invalid_input"
