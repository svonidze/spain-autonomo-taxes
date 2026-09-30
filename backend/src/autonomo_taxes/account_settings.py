"""Allowlisted account edits; operator configuration and secrets stay outside the API."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from .backup_settings import (
    BackupSettingsError, SETTINGS_FILENAME, read_policy, read_private_bytes, validate_policy,
)
from .ledger_db import LedgerDB, SchemaVersionError, StaleRowVersionError, TaxpayerIdentityLockedError

PROFILE_FIELDS = (
    "taxpayer_profile_id", "full_name", "tax_id", "residency_country",
    "tax_year_start_month", "row_version",
)
ACTIVITY_FIELDS = (
    "business_activity_id", "activity_key", "description", "aeat_activity_code",
    "aeat_activity_type", "iae_section", "iae_group_epigraph", "starts_on", "ends_on",
    "irpf_method", "iva_regime",
)


class AccountSettingsError(ValueError):
    def __init__(self, status: int, code: str, message: str = "Account settings could not be saved.") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _profile(db: LedgerDB, row: dict) -> dict:
    profile = {key: row[key] for key in PROFILE_FIELDS}
    profile["activities"] = [
        {key: activity[key] for key in ACTIVITY_FIELDS}
        for activity in db.list_business_activities(taxpayer_profile_id=row["taxpayer_profile_id"])
    ]
    profile["identity_locked"] = db.taxpayer_identity_locked()
    return profile


def _root(private_root: Path | None) -> Path:
    if private_root is None:
        raise AccountSettingsError(409, "backup_root_unavailable", "Backup settings require an explicit private root.")
    root = Path(private_root)
    try:
        available = root.is_absolute() and root.is_dir()
    except OSError:
        available = False
    if not available:
        raise AccountSettingsError(409, "backup_root_unavailable", "The configured backup root is unavailable.")
    return root


def _last_success(root: Path, backup_class: str) -> dict | None:
    path = root / "backups" / f"last-backup-{backup_class}.json"
    try:
        if path.parent.is_symlink():
            return None
        marker = json.loads(read_private_bytes(path))
        if not isinstance(marker, dict) or marker.get("class") != backup_class or marker.get("offsite") not in ("yes", "no"):
            return None
        recorded = datetime.strptime(marker["recorded_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if recorded > datetime.now(timezone.utc):
            return None
        count = marker.get("keep")
        keep = int(count) if isinstance(count, str) and count.isascii() and count.isdigit() and len(count) < 10 and int(count) > 0 else None
        settings_format = marker.get("settings_format")
        result = {
            "recorded_at": recorded.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "offsite": marker["offsite"] == "yes",
            "keep": keep,
            "settings_format": 1 if settings_format == "1" or type(settings_format) is int and settings_format == 1 else None,
        }
        if marker.get("format") == 2 and marker.get("offsite_status") in {"disabled", "pending", "acknowledged", "failed"}:
            result["offsite_status"] = marker["offsite_status"]
        return result
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return None


def _verification_marker(root: Path, name: str) -> dict | None:
    path = root / "backups" / name
    try:
        if path.parent.is_symlink():
            return None
        marker = json.loads(read_private_bytes(path))
        if not isinstance(marker, dict) or marker.get("class") != "monthly":
            return None
        recorded = datetime.strptime(marker["recorded_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if recorded > datetime.now(timezone.utc) or marker.get("status") not in {"running", "success", "failed"}:
            return None
        result = {"recorded_at": recorded.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": marker["status"]}
        if marker.get("status") == "failed" and marker.get("failure_code") in {
            "invalid_monthly_marker", "monthly_marker_not_current", "monthly_upload_not_acknowledged",
            "unsafe_backup_name", "insufficient_scratch_space", "remote_download_failed",
            "download_hash_mismatch", "archive_validation_failed", "sqlite_validation_failed",
            "sqlite_schema_mismatch", "internal_error",
        }:
            result["failure_code"] = marker["failure_code"]
        if marker.get("status") == "success" and type(marker.get("sqlite_schema")) is int:
            result["sqlite_schema"] = marker["sqlite_schema"]
        return result
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return None


def _backups(private_root: Path | None) -> dict:
    try:
        root = _root(private_root)
        policy, revision = read_policy(root)
    except AccountSettingsError as exc:
        return {"available": False, "error_code": exc.code}
    except (BackupSettingsError, OSError):
        return {"available": False, "error_code": "backup_settings_invalid"}
    return {
        "available": True, "revision": revision,
        "daily_keep": policy["daily_keep"], "monthly_keep": policy["monthly_keep"],
        "last_success": {kind: _last_success(root, kind) for kind in ("daily", "monthly")},
        "recovery_verification": {"monthly": {
            "last_attempt": _verification_marker(root, "last-backup-verification-attempt-monthly.json"),
            "last_success": _verification_marker(root, "last-backup-verified-monthly.json"),
        }},
    }


def read_settings(database: Path, private_root: Path | None) -> dict:
    try:
        with LedgerDB.open(database, read_only=True) as db:
            profiles = [_profile(db, dict(row)) for row in db.connection.execute(
                "SELECT * FROM taxpayer_profile ORDER BY created_at, taxpayer_profile_id"
            )]
    except SchemaVersionError:
        raise AccountSettingsError(409, "schema_upgrade_required", "An operator must upgrade the database before viewing settings.") from None
    except (sqlite3.Error, OSError):
        raise AccountSettingsError(503, "settings_unavailable", "Account settings are temporarily unavailable.") from None
    return {"profiles": profiles, "backups": _backups(private_root)}


def save_profile(database: Path, payload: dict, actor: str | None = None) -> dict:
    fields = {"taxpayer_profile_id", "expected_row_version", "full_name", "tax_id", "residency_country"}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise AccountSettingsError(400, "invalid_profile", "Check the profile fields.")
    profile_id = payload["taxpayer_profile_id"]
    if profile_id is not None and (not isinstance(profile_id, str) or not profile_id.strip() or len(profile_id) > 128):
        raise AccountSettingsError(400, "invalid_profile", "Check the profile identifier.")
    try:
        with LedgerDB.open(database) as db:
            row = db.edit_taxpayer_profile(**payload, actor=actor)
            return _profile(db, row)
    except TaxpayerIdentityLockedError:
        raise AccountSettingsError(409, "identity_locked", "Tax identity is locked by accounting history.") from None
    except StaleRowVersionError:
        raise AccountSettingsError(409, "profile_conflict", "The profile changed; reload before saving.") from None
    except sqlite3.IntegrityError as exc:
        if (
            getattr(exc, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE"
            and str(exc) == "UNIQUE constraint failed: taxpayer_profile.tax_id"
        ):
            raise AccountSettingsError(409, "duplicate_tax_id", "Another profile already uses this tax identifier.") from None
        raise AccountSettingsError(409, "profile_conflict", "The profile could not be saved; check for an existing tax identifier.") from None
    except SchemaVersionError:
        raise AccountSettingsError(409, "schema_upgrade_required", "An operator must upgrade the database before editing settings.") from None
    except ValueError:
        raise AccountSettingsError(400, "invalid_profile", "Check the profile fields.") from None
    except (sqlite3.Error, OSError):
        raise AccountSettingsError(503, "settings_unavailable", "Account settings are temporarily unavailable.") from None


def save_backups(database: Path, private_root: Path | None, payload: dict) -> dict:
    fields = {"expected_revision", "daily_keep", "monthly_keep", "confirm_local_pruning"}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise AccountSettingsError(400, "invalid_backup_settings", "Check the backup settings.")
    if payload["confirm_local_pruning"] is not True:
        raise AccountSettingsError(400, "pruning_confirmation_required", "Confirm permanent deletion of older local copies on a subsequent backup.")
    if not isinstance(payload["expected_revision"], str) or len(payload["expected_revision"]) > 64:
        raise AccountSettingsError(400, "invalid_backup_settings", "Check the backup revision.")
    root = _root(private_root)
    try:
        policy = validate_policy({"format": 1, "daily_keep": payload["daily_keep"], "monthly_keep": payload["monthly_keep"]})
    except BackupSettingsError:
        raise AccountSettingsError(400, "invalid_backup_settings", "Check the backup retention limits.") from None
    temporary: str | None = None
    try:
        with LedgerDB.open(database) as db, db.transaction():
            existing, revision = read_policy(root)
            if payload["expected_revision"] != revision:
                raise AccountSettingsError(409, "backup_settings_conflict", "Backup settings changed; reload before saving.")
            if existing != policy or revision == "missing":
                descriptor, temporary = tempfile.mkstemp(dir=root, prefix=".account-backup-settings-")
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    if hasattr(os, "fchmod"):
                        os.fchmod(stream.fileno(), 0o600)
                    else:
                        os.chmod(temporary, 0o600)
                    json.dump(policy, stream, sort_keys=True)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, root / SETTINGS_FILENAME)
                temporary = None
        return _backups(root)
    except BackupSettingsError:
        raise AccountSettingsError(409, "backup_settings_invalid", "An operator must repair the existing backup settings.") from None
    except SchemaVersionError:
        raise AccountSettingsError(409, "schema_upgrade_required", "An operator must upgrade the database before editing settings.") from None
    except (OSError, sqlite3.Error):
        raise AccountSettingsError(503, "backup_settings_unavailable", "Backup settings could not be saved.") from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
