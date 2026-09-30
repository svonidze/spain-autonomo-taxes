"""Provider-neutral storage helpers used by intake and the local web server.

The database remains the catalogue of replicas.  Local filesystem reads avoid
an unnecessary copy; provider reads are SHA-256 verified and cached under the
private root before the web handler can serve them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from .ledger_db import LedgerDB
from .storage_adapters import (
    GoogleDriveStorageAdapter,
    StorageAdapterError,
    StorageObjectCorruptError,
    sha256_file,
    verify_file,
)


@dataclass(frozen=True)
class ResolvedFileReplica:
    path: Path
    media_type: str
    replica_id: str


def resolve_verified_replica(
    connection: sqlite3.Connection,
    *,
    document_id: str,
    cache_root: Path,
) -> ResolvedFileReplica | None:
    """Cache and return a verified source replica for ``document_id``.

    The catalogue determines order: an available primary replica is tried
    first, then enabled mirrors by backend ``read_priority``.  Every request
    reads and SHA-256 checks the selected provider object before bytes are
    atomically made visible beneath ``cache_root``.  The cache is therefore a
    verified delivery copy, never evidence that a particular remote replica is
    still healthy.  Missing,
    corrupt, retired, disabled, or inaccessible replicas are skipped so a
    later mirror can satisfy the request.  ``None`` means no usable replica
    was found (including an unmigrated legacy database).

    ``cache_root`` is private application state.  Cached objects are addressed
    only by digest and created mode 0600; no provider-controlled path or name
    is used as a filesystem path.
    """
    try:
        rows = connection.execute(
            """
            SELECT fr.file_replica_id, fr.provider_locator, fr.provider_metadata_json, sb.*, f.content_sha256,
                   f.media_type
            FROM document_attachments da
            JOIN files f ON f.file_id = da.file_id
            JOIN file_replicas fr ON fr.file_id = f.file_id
            JOIN storage_backends sb ON sb.storage_backend_id = fr.storage_backend_id
            WHERE da.document_id = ?
              AND da.attachment_role = 'source'
              AND fr.replica_status = 'available'
              AND sb.enabled = 1
            ORDER BY fr.is_primary DESC, sb.read_priority ASC, fr.updated_at DESC
            """,
            (document_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        # Schema-17 databases still use documents.source_path until the
        # explicit startup migration has completed.
        return None

    for row in rows:
        digest = str(row["content_sha256"])
        try:
            # Local import avoids a module cycle: storage_reconcile uses
            # filesystem_replica_path from this module.
            from .storage_reconcile import adapter_for_backend

            adapter = adapter_for_backend(dict(row))
            metadata = _metadata_object(row["provider_metadata_json"])
            if isinstance(adapter, GoogleDriveStorageAdapter):
                payload = adapter.read_bytes(
                    str(row["provider_locator"]),
                    resource_key=str(metadata.get("resource_key") or "") or None,
                )
            else:
                payload = adapter.read_bytes(str(row["provider_locator"]))
            if hashlib.sha256(payload).hexdigest() != digest:
                raise StorageObjectCorruptError("Provider returned bytes with unexpected SHA-256")
            cached = _atomically_cache(cache_root, digest, payload)
        except (StorageAdapterError, StorageObjectCorruptError, OSError, ValueError):
            continue
        return ResolvedFileReplica(
            path=cached,
            media_type=str(row["media_type"]),
            replica_id=str(row["file_replica_id"]),
        )
    return None


def _cached_file(cache_root: Path, digest: str) -> Path | None:
    target = _cache_target(cache_root, digest)
    if not target.is_file():
        return None
    try:
        verify_file(target, digest)
    except (StorageAdapterError, OSError):
        return None
    return target


def _atomically_cache(cache_root: Path, digest: str, payload: bytes) -> Path:
    target = _cache_target(cache_root, digest)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.chmod(0o700)
    existing = _cached_file(cache_root, digest)
    if existing is not None:
        return existing
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{digest}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        verify_file(temporary, digest)
        temporary.replace(target)
        target.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return target


def _cache_target(cache_root: Path, digest: str) -> Path:
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("Expected lowercase SHA-256 digest")
    root = cache_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root / "sha256" / digest


def _metadata_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def register_local_source_replica(
    db: LedgerDB,
    *,
    document_id: str,
    source_path: Path,
    media_type: str | None,
    storage_root: Path,
) -> dict[str, Any]:
    """Register an archived intake file as the current verified source replica."""
    source = source_path.expanduser().resolve(strict=True)
    root = storage_root.expanduser().resolve(strict=True)
    try:
        locator = str(source.relative_to(root))
    except ValueError as exc:
        raise ValueError("Source file must be inside its configured storage root") from exc
    digest = sha256_file(source)
    resolved_media_type = media_type or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    backend = _local_backend(db, root)
    file_row = db.upsert_file(
        content_sha256=digest,
        byte_size=source.stat().st_size,
        media_type=resolved_media_type,
    )
    attachment = db.attach_file_to_document(
        document_id=document_id,
        file_id=str(file_row["file_id"]),
        attachment_role="source",
        display_name=source.name,
    )
    replica = db.register_file_replica(
        file_id=str(file_row["file_id"]),
        storage_backend_id=str(backend["storage_backend_id"]),
        provider_locator=locator,
        provider_version=str(source.stat().st_mtime_ns),
        is_primary=True,
        last_verified_at=_utc_now(),
        provider_metadata={"intake_archive": True},
    )
    return {"file": file_row, "attachment": attachment, "replica": replica}


def resolve_verified_filesystem_replica(
    connection: sqlite3.Connection,
    *,
    document_id: str,
) -> ResolvedFileReplica | None:
    """Return the highest-priority intact local replica, or ``None``.

    A stale catalogue entry is never trusted: its bytes are verified against
    the immutable file digest at read time before it can be served.
    """
    try:
        rows = connection.execute(
            """
            SELECT fr.file_replica_id, fr.provider_locator, sb.config_json,
                   f.content_sha256, f.media_type
            FROM document_attachments da
            JOIN files f ON f.file_id = da.file_id
            JOIN file_replicas fr ON fr.file_id = f.file_id
            JOIN storage_backends sb ON sb.storage_backend_id = fr.storage_backend_id
            WHERE da.document_id = ?
              AND da.attachment_role = 'source'
              AND fr.replica_status = 'available'
              AND fr.last_verified_at IS NOT NULL
              AND sb.enabled = 1
              AND sb.driver_key = 'filesystem'
            ORDER BY fr.is_primary DESC, sb.read_priority ASC, fr.updated_at DESC
            """,
            (document_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        # Schema-17 databases still use documents.source_path until the
        # explicit startup migration has completed.
        return None
    for row in rows:
        path = filesystem_replica_path(str(row["config_json"]), str(row["provider_locator"]))
        if path is None:
            continue
        try:
            verify_file(path, str(row["content_sha256"]))
        except (StorageObjectCorruptError, OSError):
            continue
        return ResolvedFileReplica(
            path=path,
            media_type=str(row["media_type"]),
            replica_id=str(row["file_replica_id"]),
        )
    return None


def _local_backend(db: LedgerDB, root: Path) -> dict[str, Any]:
    requested_config = {"schema_version": 1, "root": str(root)}
    existing = db.connection.execute(
        "SELECT * FROM storage_backends WHERE backend_key = 'local_staging'"
    ).fetchone()
    backend_key = "local_staging"
    if existing is not None:
        try:
            existing_root = str(json.loads(existing["config_json"]).get("root") or "")
        except (TypeError, json.JSONDecodeError):
            existing_root = ""
        if existing_root and Path(existing_root).expanduser().resolve() != root:
            backend_key = "local_staging_" + hashlib.sha256(str(root).encode()).hexdigest()[:12]
    return db.upsert_storage_backend(
        backend_key=backend_key,
        display_name="Local private-root staging",
        driver_key="filesystem",
        provider_key="local",
        access_mode="read_write",
        config=requested_config,
        credential_ref=None,
        read_priority=20,
    )


def filesystem_replica_path(config_json: str, locator: str) -> Path | None:
    try:
        config = json.loads(config_json)
        root = Path(str(config["root"])).expanduser().resolve()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    relative = Path(locator)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
