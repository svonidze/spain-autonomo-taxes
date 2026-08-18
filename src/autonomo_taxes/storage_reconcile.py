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
    """Create and verify missing durable replicas for all source attachments.

    Reconcile never promotes a replica merely because it belongs to a backend
    with a particular name.  Promotion is a separately verified operational
    action, so a failed/partial copy cannot silently become the read source.
    """
    if limit < 0:
        raise ValueError("limit must not be negative")
    target = _backend(db, backend_key)
    if target["access_mode"] != "read_write":
        raise StorageReconcileError(f"Storage backend is read-only: {backend_key}")
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
        outcome = reconcile_file_to_backend(
            db, backend_key=backend_key, file_id=str(raw["file_id"])
        )
        created += int(outcome["created"])
        skipped += int(outcome["skipped"])
        failed += int(outcome["failed"])
    return {
        "backend_key": backend_key,
        "created": created,
        "skipped": skipped,
        "failed": failed,
    }


def reconcile_file_to_backend(
    db: LedgerDB,
    *,
    backend_key: str,
    file_id: str,
    google_folder_id: str | None = None,
    document_id: str | None = None,
) -> dict[str, Any]:
    """Verify or create one replica, for synchronous intake mirror gates.

    The stable result has ``created``, ``skipped`` and ``failed`` counters plus
    ``provider_locator`` and ``archive_path`` (both nullable).  A supplied
    ``google_folder_id`` only affects new Google uploads; verified existing
    Google originals always win provider-wide deduplication.
    """
    target = _backend(db, backend_key)
    if target["access_mode"] != "read_write":
        raise StorageReconcileError(f"Storage backend is read-only: {backend_key}")
    raw = db.connection.execute(
        "SELECT file_id, content_sha256 FROM files WHERE file_id = ?", (file_id,)
    ).fetchone()
    if raw is None:
        raise StorageReconcileError(f"Unknown catalogue file: {file_id}")
    digest = str(raw["content_sha256"])
    adapter = adapter_for_backend(target)
    existing = db.connection.execute(
        "SELECT * FROM file_replicas WHERE file_id = ? AND storage_backend_id = ?",
        (file_id, target["storage_backend_id"]),
    ).fetchone()
    if existing is not None and existing["replica_status"] == "available":
        try:
            _verify_replica(
                adapter,
                str(existing["provider_locator"]),
                digest,
                _json_object(existing["provider_metadata_json"]),
            )
            return _outcome(
                backend_key, file_id, skipped=1, provider_locator=str(existing["provider_locator"])
            )
        except (StorageAdapterError, StorageReconcileError, OSError):
            db.register_file_replica(
                file_id=file_id,
                storage_backend_id=str(target["storage_backend_id"]),
                provider_locator=str(existing["provider_locator"]),
                provider_version=existing["provider_version"],
                replica_status="corrupt",
                is_primary=False,
                provider_metadata=_json_object(existing["provider_metadata_json"]),
            )
    if target["driver_key"] == "google_drive":
        google_state = _google_replica_state(db, file_id=file_id, digest=digest)
        if google_state == "verified":
            return _outcome(backend_key, file_id, skipped=1)
        if google_state == "degraded":
            return _outcome(backend_key, file_id, failed=1)
    try:
        source = _verified_filesystem_source(db, file_id, digest)
        archive_path: str | None = None
        if isinstance(adapter, GoogleDriveStorageAdapter):
            attachment = _source_attachment_context(db, file_id=file_id, document_id=document_id)
            folder_path = (attachment["period_key"], attachment["document_type"])
            remote = adapter.put_file(
                source,
                logical_file_id=file_id,
                content_sha256=digest,
                folder_id=google_folder_id,
                folder_path=folder_path,
                display_name=attachment["display_name"],
            )
            archive_path = str(remote.metadata.get("archive_relative_path") or "") or None
        else:
            remote = adapter.put_file(source, logical_file_id=file_id, content_sha256=digest)
        _verify(adapter, remote.locator, digest)
        db.register_file_replica(
            file_id=file_id,
            storage_backend_id=str(target["storage_backend_id"]),
            provider_locator=remote.locator,
            provider_version=remote.version,
            is_primary=False,
            web_url=remote.web_url,
            provider_metadata=dict(remote.metadata),
            last_verified_at=_utc_now(),
        )
        return _outcome(
            backend_key, file_id, created=1, provider_locator=remote.locator, archive_path=archive_path
        )
    except (StorageAdapterError, StorageReconcileError, OSError):
        return _outcome(backend_key, file_id, failed=1)


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
            raise StorageReconcileError("Google Drive backend requires credential_ref=file:<credential>")
        credential_mode = str(config.get("credential_mode") or "oauth")
        if credential_mode not in {"oauth", "service_account"}:
            raise StorageReconcileError("Google Drive config.credential_mode must be oauth or service_account")
        return GoogleDriveStorageAdapter(
            root_folder_id=root_folder_id,
            token_file=Path(credential_ref.removeprefix("file:")),
            supports_all_drives=bool(config.get("shared_drive_id")),
            credential_mode=credential_mode,
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


def _google_replica_state(db: LedgerDB, *, file_id: str, digest: str) -> str:
    """Classify known Google copies before any writer can create a duplicate."""
    rows = db.connection.execute(
        """
        SELECT fr.provider_locator, fr.provider_metadata_json, fr.replica_status, sb.*
        FROM file_replicas fr
        JOIN storage_backends sb ON sb.storage_backend_id = fr.storage_backend_id
        WHERE fr.file_id = ? AND fr.replica_status != 'retired'
          AND sb.enabled = 1 AND sb.driver_key = 'google_drive'
        ORDER BY fr.is_primary DESC, sb.read_priority ASC, fr.updated_at DESC
        """,
        (file_id,),
    ).fetchall()
    if not rows:
        return "absent"
    degraded = False
    for row in rows:
        if row["replica_status"] != "available":
            degraded = True
            continue
        try:
            adapter = adapter_for_backend(dict(row))
            _verify_replica(
                adapter,
                str(row["provider_locator"]),
                digest,
                _json_object(row["provider_metadata_json"]),
            )
        except (StorageAdapterError, StorageReconcileError, OSError):
            degraded = True
    return "degraded" if degraded else "verified"


def _verify_replica(
    adapter: StorageAdapter,
    locator: str,
    digest: str,
    metadata: dict[str, Any],
) -> None:
    if isinstance(adapter, GoogleDriveStorageAdapter):
        resource_key = metadata.get("resource_key")
        adapter.verify(locator, digest, resource_key=str(resource_key) if resource_key else None)
        return
    _verify(adapter, locator, digest)


def _source_attachment_context(
    db: LedgerDB,
    *,
    file_id: str,
    document_id: str | None,
) -> dict[str, str]:
    where = "da.file_id = ? AND da.attachment_role = 'source'"
    parameters: list[str] = [file_id]
    if document_id is not None:
        where += " AND da.document_id = ?"
        parameters.append(document_id)
    row = db.connection.execute(
        f"""
        SELECT da.display_name, d.document_type, p.period_key
        FROM document_attachments da
        JOIN documents d ON d.document_id = da.document_id
        LEFT JOIN periods p ON p.period_id = d.period_id
        WHERE {where}
        ORDER BY da.created_at ASC, da.document_attachment_id ASC
        LIMIT 1
        """,
        parameters,
    ).fetchone()
    if row is None:
        raise StorageReconcileError("Google archive upload requires a source attachment")
    display_name = str(row["display_name"] or "").strip()
    document_type = str(row["document_type"] or "").strip()
    if not display_name or not document_type:
        raise StorageReconcileError("Source attachment lacks an archive display name or document type")
    return {
        "display_name": display_name,
        "document_type": document_type,
        "period_key": str(row["period_key"] or "unassigned"),
    }


def _outcome(
    backend_key: str,
    file_id: str,
    *,
    created: int = 0,
    skipped: int = 0,
    failed: int = 0,
    provider_locator: str | None = None,
    archive_path: str | None = None,
) -> dict[str, Any]:
    return {
        "backend_key": backend_key,
        "file_id": file_id,
        "created": created,
        "skipped": skipped,
        "failed": failed,
        "provider_locator": provider_locator,
        "archive_path": archive_path,
    }


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
