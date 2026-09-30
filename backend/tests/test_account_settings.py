from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import stat

import pytest

from autonomo_taxes import ledger_db
from autonomo_taxes.account_settings import AccountSettingsError, read_settings, save_backups, save_profile
from autonomo_taxes.backup_settings import SETTINGS_FILENAME, read_policy
from autonomo_taxes.ledger_db import LedgerDB


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "autonomo.sqlite"
    with LedgerDB.initialize(path):
        pass
    return path


def profile_payload(profile=None, **changes):
    payload = {
        "taxpayer_profile_id": profile["taxpayer_profile_id"] if profile else None,
        "expected_row_version": profile["row_version"] if profile else 0,
        "tax_id": "TEST-TAX-ID-001", "full_name": "Synthetic Taxpayer", "residency_country": "ES",
    }
    payload.update(changes)
    return payload


def backup_payload(revision="missing", **changes):
    payload = {"expected_revision": revision, "daily_keep": 7, "monthly_keep": 3, "confirm_local_pruning": True}
    payload.update(changes)
    return payload


def test_profile_create_edit_is_normalized_audited_and_upsert_idempotent(database):
    profile = save_profile(database, profile_payload(tax_id=" test-tax-id-001 ", full_name="Synthetic Taxpayer".center(22), residency_country=" es "), actor="test-operator")
    edited = save_profile(database, profile_payload(profile, full_name="Changed Taxpayer"))
    assert edited["taxpayer_profile_id"] == profile["taxpayer_profile_id"]
    assert edited["row_version"] == 2
    with LedgerDB.open(database) as db:
        audit = db.connection.execute("SELECT * FROM taxpayer_profile_changes ORDER BY to_row_version").fetchall()
        assert len(audit) == 2
        assert audit[0]["actor"] == "test-operator"
        assert json.loads(audit[0]["old_values_json"]) is None
        assert json.loads(audit[1]["old_values_json"])["full_name"] == "Synthetic Taxpayer"
        same = db.upsert_taxpayer_profile(tax_id="test-tax-id-001", full_name=edited["full_name"], residency_country=" es ")
        assert same["row_version"] == 2
    assert read_settings(database, None)["profiles"][0] == edited
    assert read_settings(database, None)["backups"]["available"] is False


def test_stale_even_noop_and_racing_initial_save(database):
    payload = profile_payload()
    def create():
        try:
            return save_profile(database, payload)
        except AccountSettingsError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(), range(2)))
    assert sum(isinstance(item, dict) for item in results) == 1
    assert "profile_conflict" in results
    profile = next(item for item in results if isinstance(item, dict))
    with pytest.raises(AccountSettingsError, match="reload"):
        save_profile(database, profile_payload(profile, expected_row_version=0))


@pytest.mark.parametrize("change", [{"full_name": ""}, {"tax_id": 123}, {"full_name": None}, {"residency_country": "ZZZ"}, {"residency_country": "ЁЁ"}, {"expected_row_version": True}, {"extra": "no"}])
def test_invalid_profile_payload(database, change):
    with pytest.raises(AccountSettingsError) as error:
        save_profile(database, profile_payload(**change))
    assert error.value.status == 400


def test_profile_duplicate_identifier_and_foreign_keys(database):
    first = save_profile(database, profile_payload())
    with LedgerDB.open(database) as db:
        db.upsert_taxpayer_profile(tax_id="TEST-TAX-ID-002", full_name="Example Taxpayer")
        with db.transaction():
            db.connection.execute("INSERT INTO household_members (household_member_id,taxpayer_profile_id,full_name,relationship,source_hash,row_version,created_at,updated_at) VALUES ('member',?,'Synthetic Relative','child','fixture',1,'2030-01-01','2030-01-01')", (first["taxpayer_profile_id"],))
    with pytest.raises(AccountSettingsError) as error:
        save_profile(database, profile_payload(first, tax_id="TEST-TAX-ID-002"))
    assert error.value.status == 409
    assert error.value.code == "duplicate_tax_id"
    updated = save_profile(database, profile_payload(first, tax_id="TEST-TAX-ID-CORRECTED"))
    with LedgerDB.open(database) as db:
        assert db.connection.execute("SELECT taxpayer_profile_id FROM household_members").fetchone()[0] == updated["taxpayer_profile_id"]
        assert not db.connection.execute("PRAGMA foreign_key_check").fetchall()


@pytest.mark.parametrize("history", ["closed", "amended", "snapshot"])
def test_history_blocks_identifier_but_not_name(database, history):
    profile = save_profile(database, profile_payload())
    with LedgerDB.open(database) as db:
        period = db.ensure_period("2032-Q1")
        with db.transaction():
            if history == "snapshot":
                # A draft snapshot is enough: emitted history cannot follow an identity edit.
                db.create_filing_snapshot("2032-Q1", payload={"test": True})
            else:
                db.connection.execute("UPDATE periods SET status=?", (history,))
    assert read_settings(database, None)["profiles"][0]["identity_locked"]
    with pytest.raises(AccountSettingsError) as error:
        save_profile(database, profile_payload(profile, tax_id="TEST-TAX-ID-002"))
    assert error.value.code == "identity_locked"
    assert save_profile(database, profile_payload(profile, full_name="Changed Taxpayer"))["row_version"] == 2


@pytest.mark.parametrize("existing", [False, True])
def test_audit_failure_rolls_back_profile_mutation(database, existing):
    profile = save_profile(database, profile_payload()) if existing else None
    with LedgerDB.open(database) as db:
        db.connection.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON taxpayer_profile_changes BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        db.connection.commit()
    with pytest.raises(AccountSettingsError):
        save_profile(database, profile_payload(profile, full_name="Reviewed Taxpayer"))
    profiles = read_settings(database, None)["profiles"]
    assert profiles == ([profile] if existing else [])


def test_migration_from_22_is_explicit(tmp_path, monkeypatch):
    database = tmp_path / "migration.sqlite"
    monkeypatch.setattr(ledger_db, "LATEST_SCHEMA_VERSION", 22)
    with LedgerDB.initialize(database):
        pass
    monkeypatch.setattr(ledger_db, "LATEST_SCHEMA_VERSION", 23)
    with pytest.raises(ledger_db.SchemaVersionError):
        LedgerDB.open(database)
    with LedgerDB.migrate(database) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 23
        assert db.connection.execute("SELECT COUNT(*) FROM taxpayer_profile_changes").fetchone()[0] == 0


def test_backup_save_cas_permissions_reset_and_no_archive_pruning(database, tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    sentinel = backups / "private-root-fixture.tar.gz"
    sentinel.write_bytes(b"untouched")
    initial = read_settings(database, tmp_path)["backups"]
    assert initial["daily_keep"] is None and initial["revision"] == "missing"
    result = save_backups(database, tmp_path, backup_payload())
    assert result["daily_keep"] == 7 and result["monthly_keep"] == 3
    assert stat.S_IMODE((tmp_path / SETTINGS_FILENAME).stat().st_mode) == 0o600
    assert sentinel.read_bytes() == b"untouched"
    with pytest.raises(AccountSettingsError) as error:
        save_backups(database, tmp_path, backup_payload())
    assert error.value.code == "backup_settings_conflict"
    reset = save_backups(database, tmp_path, backup_payload(result["revision"], daily_keep=None, monthly_keep=None))
    assert reset["daily_keep"] is None
    assert read_policy(tmp_path)[0]["monthly_keep"] is None


@pytest.mark.parametrize("change", [{"daily_keep": 6}, {"monthly_keep": 2}, {"daily_keep": 366}, {"monthly_keep": 121}, {"daily_keep": True}, {"monthly_keep": 3.1}, {"confirm_local_pruning": False}, {"confirm_local_pruning": 1}])
def test_invalid_or_unconfirmed_policy_never_saved(database, tmp_path, change):
    with pytest.raises(AccountSettingsError):
        save_backups(database, tmp_path, backup_payload(**change))
    assert not (tmp_path / SETTINGS_FILENAME).exists()


@pytest.mark.parametrize("content", [b"{", b'{"format":1,"daily_keep":7,"monthly_keep":3,"secret":"redacted"}', b"[1]", b"x" * 17000])
def test_corrupt_policy_is_partial_unavailability_not_overwrite(database, tmp_path, content):
    path = tmp_path / SETTINGS_FILENAME
    path.write_bytes(content)
    path.chmod(0o600)
    assert read_settings(database, tmp_path)["backups"]["error_code"] == "backup_settings_invalid"
    assert save_profile(database, profile_payload())["full_name"] == "Synthetic Taxpayer"
    with pytest.raises(AccountSettingsError):
        save_backups(database, tmp_path, backup_payload())
    assert path.read_bytes() == content


def test_symlink_policy_rejected_without_target_change(database, tmp_path):
    target = tmp_path / "outside.json"
    target.write_text("sensitive fixture")
    (tmp_path / SETTINGS_FILENAME).symlink_to(target)
    assert read_settings(database, tmp_path)["backups"]["available"] is False
    with pytest.raises(AccountSettingsError):
        save_backups(database, tmp_path, backup_payload())
    assert target.read_text() == "sensitive fixture"


def test_last_marker_is_allowlisted_and_future_or_symlink_is_unknown(database, tmp_path):
    directory = tmp_path / "backups"
    directory.mkdir()
    marker = directory / "last-backup-daily.json"
    payload = {"class": "daily", "recorded_at": "2020-01-01T03:17:00Z", "offsite": "yes", "keep": "7", "settings_format": "1", "archive": "private-name", "secret": "private-value"}
    marker.write_text(json.dumps(payload))
    success = read_settings(database, tmp_path)["backups"]["last_success"]["daily"]
    assert success == {"recorded_at": "2020-01-01T03:17:00Z", "offsite": True, "keep": 7, "settings_format": 1}
    payload["recorded_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker.write_text(json.dumps(payload))
    assert read_settings(database, tmp_path)["backups"]["last_success"]["daily"] is None
    marker.unlink()
    marker.symlink_to(tmp_path / "elsewhere")
    assert read_settings(database, tmp_path)["backups"]["last_success"]["daily"] is None


def test_format_two_upload_and_recovery_statuses_are_allowlisted(database, tmp_path):
    directory = tmp_path / "backups"
    directory.mkdir()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (directory / "last-backup-daily.json").write_text(json.dumps({
        "format": 2, "class": "daily", "recorded_at": now, "offsite": "yes",
        "offsite_status": "acknowledged", "keep": 7, "settings_format": 1,
    }))
    (directory / "last-backup-verification-attempt-monthly.json").write_text(json.dumps({
        "format": 1, "class": "monthly", "recorded_at": now, "status": "failed",
        "failure_code": "remote_download_failed", "private": "must not escape",
    }))
    (directory / "last-backup-verified-monthly.json").write_text(json.dumps({
        "format": 1, "class": "monthly", "recorded_at": now, "status": "success",
        "sqlite_schema": 23, "private": "must not escape",
    }))

    backups = read_settings(database, tmp_path)["backups"]

    assert backups["last_success"]["daily"]["offsite"] is True
    assert backups["last_success"]["daily"]["offsite_status"] == "acknowledged"
    assert backups["recovery_verification"]["monthly"] == {
        "last_attempt": {"recorded_at": now, "status": "failed", "failure_code": "remote_download_failed"},
        "last_success": {"recorded_at": now, "status": "success", "sqlite_schema": 23},
    }


def test_atomic_replace_failure_cleans_temporary(database, tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("private fixture path must not leak")
    monkeypatch.setattr("autonomo_taxes.account_settings.os.replace", fail)
    with pytest.raises(AccountSettingsError) as error:
        save_backups(database, tmp_path, backup_payload())
    assert "fixture path" not in str(error.value)
    assert not list(tmp_path.glob(".account-backup-settings-*"))


def test_concurrent_policy_writers_have_one_winner(database, tmp_path):
    def write(count):
        try:
            return save_backups(database, tmp_path, backup_payload(daily_keep=count))
        except AccountSettingsError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [7, 8]))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert "backup_settings_conflict" in results


@pytest.mark.parametrize("changes,expected", [
    ({"keep": "unknown", "settings_format": "0"}, {"keep": None, "settings_format": None}),
    ({"keep": "0", "settings_format": True}, {"keep": None, "settings_format": None}),
    ({"keep": "003", "settings_format": 1}, {"keep": 3, "settings_format": 1}),
])
def test_marker_applied_counts_are_observed_not_invented(database, tmp_path, changes, expected):
    directory = tmp_path / "backups"
    directory.mkdir()
    marker = {"class": "daily", "recorded_at": "2020-01-01T03:17:00Z", "offsite": "no", **changes}
    (directory / "last-backup-daily.json").write_text(json.dumps(marker))
    success = read_settings(database, tmp_path)["backups"]["last_success"]["daily"]
    assert {key: success[key] for key in expected} == expected


@pytest.mark.parametrize("operation", ["read", "profile", "backup"])
def test_database_errors_are_sanitized(database, tmp_path, monkeypatch, operation):
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("sensitive database path")
    monkeypatch.setattr(LedgerDB, "open", fail)
    with pytest.raises(AccountSettingsError) as error:
        if operation == "read":
            read_settings(database, tmp_path)
        elif operation == "profile":
            save_profile(database, profile_payload())
        else:
            save_backups(database, tmp_path, backup_payload())
    assert error.value.status == 503
    assert "sensitive" not in str(error.value)


def test_settings_audit_respects_outer_transaction(database):
    with LedgerDB.open(database) as db:
        with pytest.raises(RuntimeError):
            with db.transaction():
                db.edit_taxpayer_profile(**profile_payload())
                raise RuntimeError("rollback caller")
        assert db.connection.execute("SELECT COUNT(*) FROM taxpayer_profile").fetchone()[0] == 0
        assert db.connection.execute("SELECT COUNT(*) FROM taxpayer_profile_changes").fetchone()[0] == 0


@pytest.mark.parametrize("field,value", [
    ("full_name", "Synthetic\nTaxpayer"),
    ("full_name", "Synthetic\tTaxpayer"),
    ("full_name", "Synthetic\u2028Taxpayer"),
    ("tax_id", "TEST-TAX-ID-001\n"),
    ("tax_id", "TEST\x00TAX-ID-001"),
    ("tax_id", "TEST/TAX-ID-001"),
    ("tax_id", "TEST\\TAX-ID-001"),
])
def test_taxpayer_controls_and_identifier_path_separators_are_rejected(database, field, value):
    with pytest.raises(AccountSettingsError) as error:
        save_profile(database, profile_payload(**{field: value}))
    assert error.value.code == "invalid_profile"
    assert read_settings(database, None)["profiles"] == []


def test_cleanup_failure_preserves_safe_primary_error(database, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("private fixture path must not leak")
    monkeypatch.setattr("autonomo_taxes.account_settings.os.replace", fail)
    monkeypatch.setattr(Path, "unlink", fail)
    with pytest.raises(AccountSettingsError) as error:
        save_backups(database, tmp_path, backup_payload())
    assert error.value.code == "backup_settings_unavailable"
    assert "private fixture" not in str(error.value)
