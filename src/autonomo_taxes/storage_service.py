"""Provider-neutral storage helpers used by intake and the local web server.

The database remains the catalogue of replicas.  This module deliberately only
resolves filesystem replicas: remote providers require their own credentials
and must not be implicitly contacted by a web request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import sqlite3
from typing import Any

from .ledger_db import LedgerDB
from .storage_adapters import StorageObjectCorruptError, sha256_file, verify_file


@dataclass(frozen=True)
class ResolvedFileReplica:
    path: Path
    media_type: str
    replica_id: str


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
