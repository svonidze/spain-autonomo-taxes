from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .ledger_db import LedgerDB
from .storage_adapters import (
    FilesystemStorageAdapter,
    GoogleDriveStorageAdapter,
    RcloneStorageAdapter,
    StorageAdapter,
    StorageAdapterError,
)
from .storage_service import filesystem_replica_path


class StorageReconcileError(RuntimeError):
    pass


def reconcile_backend(
    db: LedgerDB,
    *,
    backend_key: str,
    limit: int = 0,
) -> dict[str, int | str]:
    """Create and verify missing durable replicas for all source attachments."""
    if limit < 0:
        raise ValueError("limit must not be negative")
    target = _backend(db, backend_key)
    if target["access_mode"] != "read_write":
        raise StorageReconcileError(f"Storage backend is read-only: {backend_key}")
    adapter = adapter_for_backend(target)
    rows = db.connection.execute(
        """
        SELECT f.file_id, f.content_sha256, f.byte_size
        FROM document_attachments da
        JOIN files f ON f.file_id = da.file_id
        WHERE da.attachment_role = 'source'
        GROUP BY f.file_id
        ORDER BY f.file_id
        """
    ).fetchall()
    created = 0
    skipped = 0
    failed = 0
    for raw in rows:
        if limit and created + skipped + failed >= limit:
            break
        file_id = str(raw["file_id"])
        digest = str(raw["content_sha256"])
        existing = db.connection.execute(
            """
            SELECT * FROM file_replicas
            WHERE file_id = ? AND storage_backend_id = ?
            """,
            (file_id, target["storage_backend_id"]),
        ).fetchone()
        if existing is not None and existing["replica_status"] == "available":
            try:
                adapter.verify(str(existing["provider_locator"]), digest)  # type: ignore[attr-defined]
                skipped += 1
                continue
            except StorageAdapterError:
                db.register_file_replica(
                    file_id=file_id,
                    storage_backend_id=str(target["storage_backend_id"]),
                    provider_locator=str(existing["provider_locator"]),
                    provider_version=existing["provider_version"],
                    replica_status="corrupt",
                    is_primary=False,
                    provider_metadata=_json_object(existing["provider_metadata_json"]),
                )
        try:
            source = _verified_filesystem_source(db, file_id, digest)
            remote = adapter.put_file(source, logical_file_id=file_id, content_sha256=digest)
            _verify(adapter, remote.locator, digest)
            db.register_file_replica(
                file_id=file_id,
                storage_backend_id=str(target["storage_backend_id"]),
                provider_locator=remote.locator,
                provider_version=remote.version,
                is_primary=backend_key == "google_primary_rw",
                web_url=remote.web_url,
                provider_metadata=dict(remote.metadata),
                last_verified_at=_utc_now(),
            )
            created += 1
        except (StorageAdapterError, StorageReconcileError, OSError):
            failed += 1
    return {
        "backend_key": backend_key,
        "created": created,
        "skipped": skipped,
        "failed": failed,
    }


def adapter_for_backend(backend: dict[str, Any]) -> StorageAdapter:
    config = _json_object(backend["config_json"])
    driver = str(backend["driver_key"])
    if driver == "filesystem":
        root = config.get("root")
        if not isinstance(root, str) or not root:
            raise StorageReconcileError("Filesystem backend requires config.root")
        return FilesystemStorageAdapter(Path(root))
    if driver == "s3":
        remote = config.get("rclone_remote")
        if not isinstance(remote, str) or not remote:
            raise StorageReconcileError("S3 backend requires config.rclone_remote")
        if not config.get("crypt_config_fingerprint"):
            raise StorageReconcileError("S3 backend requires config.crypt_config_fingerprint")
        credential_ref = str(backend.get("credential_ref") or "")
        if not credential_ref.startswith("file:"):
            raise StorageReconcileError("S3 backend requires credential_ref=file:<rclone-config>")
        config_path = Path(credential_ref.removeprefix("file:"))
        if not config_path.is_file():
            raise StorageReconcileError("Configured rclone credential file does not exist")
        return RcloneStorageAdapter(remote, config_path=config_path, require_crypt=True)
    if driver == "google_drive":
        root_folder_id = config.get("root_folder_id")
        credential_ref = str(backend.get("credential_ref") or "")
        if not isinstance(root_folder_id, str) or not root_folder_id:
            raise StorageReconcileError("Google Drive backend requires config.root_folder_id")
        if not credential_ref.startswith("file:"):
            raise StorageReconcileError("Google Drive backend requires credential_ref=file:<oauth-token>")
        return GoogleDriveStorageAdapter(
            root_folder_id=root_folder_id,
            token_file=Path(credential_ref.removeprefix("file:")),
            supports_all_drives=bool(config.get("shared_drive_id")),
        )
    raise StorageReconcileError(f"Unsupported storage driver: {driver}")


def _backend(db: LedgerDB, backend_key: str) -> dict[str, Any]:
    row = db.connection.execute(
        "SELECT * FROM storage_backends WHERE backend_key = ? AND enabled = 1",
        (backend_key,),
    ).fetchone()
    if row is None:
        raise StorageReconcileError(f"Unknown or disabled storage backend: {backend_key}")
    return dict(row)


def _verified_filesystem_source(db: LedgerDB, file_id: str, digest: str) -> Path:
    rows = db.connection.execute(
        """
        SELECT fr.provider_locator, sb.config_json
        FROM file_replicas fr
        JOIN storage_backends sb ON sb.storage_backend_id = fr.storage_backend_id
        WHERE fr.file_id = ? AND fr.replica_status = 'available'
          AND sb.enabled = 1 AND sb.driver_key = 'filesystem'
        ORDER BY fr.is_primary DESC, sb.read_priority ASC
        """,
        (file_id,),
    ).fetchall()
    for row in rows:
        path = filesystem_replica_path(str(row["config_json"]), str(row["provider_locator"]))
        if path is None:
            continue
        try:
            from .storage_adapters import verify_file

            verify_file(path, digest)
            return path
        except StorageAdapterError:
            continue
    raise StorageReconcileError(f"No verified filesystem source for file {file_id}")


def _verify(adapter: StorageAdapter, locator: str, digest: str) -> None:
    verify = getattr(adapter, "verify", None)
    if verify is not None:
        verify(locator, digest)
        return
    import hashlib

    if hashlib.sha256(adapter.read_bytes(locator)).hexdigest() != digest:
        raise StorageReconcileError("Provider returned bytes with unexpected SHA-256")


def _json_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
