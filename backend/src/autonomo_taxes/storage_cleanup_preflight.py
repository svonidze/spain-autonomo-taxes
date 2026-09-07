"""Read-only safety gate before retiring duplicate Drive objects.

The preflight combines the storage catalogue, a signed-off operator proof, and
live SHA-256 verification through the configured providers.  It never changes
the catalogue, so it is safe to run immediately before a Drive Trash operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from .ledger_db import LedgerDB
from .storage_adapters import GoogleDriveStorageAdapter, StorageAdapterError


PROOF_FORMAT = "autonomo-backup-restore-proof/v1"


@dataclass(frozen=True)
class CleanupPreflightResult:
    """A non-secret, machine-readable cleanup decision."""

    passed: bool
    source_files: int
    archive_verified: int
    yandex_verified: int
    managed_active: int
    proof_present: bool
    proof_valid: bool
    proof_fresh: bool
    failure_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "source_files": self.source_files,
            "archive_verified": self.archive_verified,
            "yandex_verified": self.yandex_verified,
            "managed_active": self.managed_active,
            "backup_restore_proof": {
                "present": self.proof_present,
                "valid": self.proof_valid,
                "fresh": self.proof_fresh,
            },
            "failure_codes": list(self.failure_codes),
        }


def check_cleanup_preflight(
    db: LedgerDB,
    *,
    archive_backend_key: str,
    yandex_backend_key: str,
    managed_backend_key: str,
    backup_restore_proof: Path,
    max_proof_age_hours: int = 36,
    now: datetime | None = None,
    adapter_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> CleanupPreflightResult:
    """Return whether it is safe to retire a named managed Drive backend.

    A pass requires every *source* file to have exactly one available, verified
    copy in the named archive backend and exactly one in the Yandex backend,
    while the managed backend has no non-retired copies.  The proof is an
    operator-created JSON document; its contract is intentionally small and
    contains no credentials or provider locators.
    """
    if max_proof_age_hours <= 0:
        raise ValueError("max_proof_age_hours must be positive")

    failures: list[str] = []
    source_files = _source_file_count(db)
    archive = _backend(db, archive_backend_key, "archive_backend_not_found", failures)
    yandex = _backend(db, yandex_backend_key, "yandex_backend_not_found", failures)
    managed = _backend(db, managed_backend_key, "managed_backend_not_found", failures)
    _validate_backend_roles(
        archive=archive,
        yandex=yandex,
        managed=managed,
        failures=failures,
    )

    archive_verified = _live_verified_source_count(db, archive, adapter_factory) if archive else 0
    yandex_verified = _live_verified_source_count(db, yandex, adapter_factory) if yandex else 0
    managed_active = _active_source_count(db, str(managed["storage_backend_id"])) if managed else 0
    if archive is not None and archive_verified != source_files:
        failures.append("archive_coverage_incomplete")
    if yandex is not None and yandex_verified != source_files:
        failures.append("yandex_coverage_incomplete")
    if managed is not None and managed_active:
        failures.append("managed_backend_still_active")

    proof_present, proof_valid, proof_fresh = _proof_status(
        backup_restore_proof,
        max_age=timedelta(hours=max_proof_age_hours),
        now=now or datetime.now(timezone.utc),
    )
    if not proof_present:
        failures.append("backup_restore_proof_missing")
    elif not proof_valid:
        failures.append("backup_restore_proof_invalid")
    elif not proof_fresh:
        failures.append("backup_restore_proof_stale")

    return CleanupPreflightResult(
        passed=not failures,
        source_files=source_files,
        archive_verified=archive_verified,
        yandex_verified=yandex_verified,
        managed_active=managed_active,
        proof_present=proof_present,
        proof_valid=proof_valid,
        proof_fresh=proof_fresh,
        failure_codes=tuple(failures),
    )


def _backend(
    db: LedgerDB, backend_key: str, failure_code: str, failures: list[str]
) -> dict[str, Any] | None:
    row = db.connection.execute(
        "SELECT * FROM storage_backends WHERE backend_key = ? AND enabled = 1",
        (backend_key,),
    ).fetchone()
    if row is None:
        failures.append(failure_code)
        return None
    return dict(row)


def _validate_backend_roles(
    *,
    archive: Mapping[str, Any] | None,
    yandex: Mapping[str, Any] | None,
    managed: Mapping[str, Any] | None,
    failures: list[str],
) -> None:
    resolved = [item for item in (archive, yandex, managed) if item is not None]
    if len({str(item["storage_backend_id"]) for item in resolved}) != len(resolved):
        failures.append("cleanup_backends_not_distinct")
    if archive is not None and (
        archive.get("driver_key") != "google_drive"
        or archive.get("access_mode") != "read_only"
    ):
        failures.append("archive_backend_role_invalid")
    if yandex is not None and (
        yandex.get("driver_key") != "s3"
        or yandex.get("provider_key") != "yandex_cloud"
    ):
        failures.append("yandex_backend_role_invalid")
    if managed is not None and managed.get("driver_key") != "google_drive":
        failures.append("managed_backend_role_invalid")


def _source_file_count(db: LedgerDB) -> int:
    row = db.connection.execute(
        "SELECT COUNT(DISTINCT file_id) FROM document_attachments WHERE attachment_role = 'source'"
    ).fetchone()
    return int(row[0])


def _live_verified_source_count(
    db: LedgerDB,
    backend: Mapping[str, Any],
    adapter_factory: Callable[[dict[str, Any]], Any] | None,
) -> int:
    """Return source replicas whose bytes verify *now*, never mutating state."""
    rows = db.connection.execute(
        """
        SELECT DISTINCT f.content_sha256, fr.provider_locator, fr.provider_metadata_json
        FROM document_attachments da
        JOIN files f ON f.file_id = da.file_id
        JOIN file_replicas fr ON fr.file_id = f.file_id
        WHERE da.attachment_role = 'source'
          AND fr.storage_backend_id = ?
          AND fr.replica_status = 'available'
        """,
        (str(backend["storage_backend_id"]),),
    ).fetchall()
    try:
        adapter = adapter_factory(dict(backend)) if adapter_factory else _adapter_for_backend(dict(backend))
    except Exception:
        # Configuration/credential failures are intentionally collapsed into a
        # count mismatch so the preflight cannot expose secrets or provider details.
        return 0
    verified = 0
    for row in rows:
        try:
            if isinstance(adapter, GoogleDriveStorageAdapter):
                metadata = _metadata_object(row["provider_metadata_json"])
                adapter.verify(
                    str(row["provider_locator"]),
                    str(row["content_sha256"]),
                    resource_key=str(metadata.get("resource_key") or "") or None,
                )
            else:
                adapter.verify(str(row["provider_locator"]), str(row["content_sha256"]))
        except (StorageAdapterError, OSError, ValueError, KeyError, TypeError):
            continue
        except Exception:
            # Provider SDKs have heterogeneous exception types.  This gate is
            # fail-closed and output must remain non-secret.
            continue
        verified += 1
    return verified


def _adapter_for_backend(backend: dict[str, Any]) -> Any:
    # Delayed import avoids a reconcile/import cycle and keeps this module's
    # contract explicit: adapters may read/verify, never register or mutate.
    from .storage_reconcile import adapter_for_backend

    return adapter_for_backend(backend)


def _active_source_count(db: LedgerDB, backend_id: str) -> int:
    row = db.connection.execute(
        """
        SELECT COUNT(DISTINCT da.file_id)
        FROM document_attachments da
        JOIN file_replicas fr ON fr.file_id = da.file_id
        WHERE da.attachment_role = 'source'
          AND fr.storage_backend_id = ?
          AND fr.replica_status != 'retired'
        """,
        (backend_id,),
    ).fetchone()
    return int(row[0])


def _proof_status(path: Path, *, max_age: timedelta, now: datetime) -> tuple[bool, bool, bool]:
    """Validate a non-secret backup/restore proof without exposing its contents."""
    if path.is_symlink() or not path.is_file():
        return False, False, False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return True, False, False
    if not isinstance(raw, dict) or raw.get("format") != PROOF_FORMAT:
        return True, False, False
    digest = raw.get("backup_archive_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
        return True, False, False
    restore = raw.get("restore")
    if not isinstance(restore, dict):
        return True, False, False
    if restore.get("sqlite_integrity_check") != "ok" or restore.get("foreign_key_check") != "ok":
        return True, False, False
    try:
        backup_at = _parse_utc(raw["backup_completed_at"])
        restore_at = _parse_utc(raw["restore_completed_at"])
    except (KeyError, TypeError, ValueError):
        return True, False, False
    if restore_at < backup_at or backup_at > now + timedelta(minutes=5):
        return True, False, False
    return True, True, now - restore_at <= max_age


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _metadata_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}
