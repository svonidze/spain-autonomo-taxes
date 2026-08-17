from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import mimetypes
from pathlib import Path
import sqlite3
from typing import Any

from .ledger_db import LATEST_SCHEMA_VERSION, LedgerDB
from .storage_adapters import sha256_file


class StorageMigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class StorageMigrationResult:
    migrated: int
    already_present: int
    unresolved: int
    schema_version: int
    pending: int

    def as_dict(self) -> dict[str, int]:
        return {
            "migrated": self.migrated,
            "already_present": self.already_present,
            "unresolved": self.unresolved,
            "schema_version": self.schema_version,
            "pending": self.pending,
        }


def raw_schema_version(database: Path) -> int:
    uri = f"file:{database.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def assert_storage_startup_ready(database: Path) -> None:
    """Fail before binding the web listener if a storage migration was bypassed."""
    db_path = database.resolve()
    version = raw_schema_version(db_path)
    if version != LATEST_SCHEMA_VERSION:
        raise StorageMigrationError(
            f"Database schema is {version}; run autonomo-tax storage migrate before starting the web service"
        )
    uri = f"file:{db_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        required = {"files", "document_attachments", "storage_backends", "file_replicas"}
        if not required.issubset(tables):
            raise StorageMigrationError(
                "Storage schema is incomplete; run autonomo-tax storage migrate before starting the web service"
            )
        pending = _pending_documents_read_only(connection)
    if pending:
        raise StorageMigrationError(
            f"Storage migration has {pending} unresolved legacy documents; run autonomo-tax storage migrate"
        )


def create_read_only_snapshot(database: Path, destination: Path) -> Path:
    """Use SQLite's online backup API without applying application migrations."""
    source_uri = f"file:{database.resolve().as_posix()}?mode=ro"
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(f".{destination.name}.tmp")
    with sqlite3.connect(source_uri, uri=True) as source, sqlite3.connect(temporary) as target:
        source.execute("PRAGMA query_only = ON")
        source.backup(target)
        target.commit()
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = target.execute("PRAGMA foreign_key_check").fetchall()
    if integrity != "ok" or foreign_keys:
        temporary.unlink(missing_ok=True)
        raise StorageMigrationError("Pre-migration SQLite snapshot verification failed")
    temporary.replace(destination)
    return destination


def migrate_legacy_storage(
    *,
    database: Path,
    local_root: Path,
    drive_mount_root: Path | None = None,
    batch_size: int = 25,
    require_complete: bool = False,
    snapshot_path: Path | None = None,
) -> StorageMigrationResult:
    """Backfill provider-neutral storage rows from legacy document source paths.

    This function never changes `documents` or its row versions. Repeated runs use
    the four storage tables as their durable progress marker.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    db_path = database.resolve()
    local = local_root.expanduser().resolve()
    mounted = drive_mount_root.expanduser().resolve() if drive_mount_root else None
    initial_version = raw_schema_version(db_path)
    if initial_version < 18:
        if snapshot_path is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            snapshot_path = db_path.with_name(f"{db_path.stem}.pre-storage-{stamp}{db_path.suffix}")
        create_read_only_snapshot(db_path, snapshot_path)

    with LedgerDB.migrate(db_path) as db:
        local_backend = db.upsert_storage_backend(
            backend_key="local_staging",
            display_name="Local private-root staging",
            driver_key="filesystem",
            provider_key="local",
            access_mode="read_write",
            config={"schema_version": 1, "root": str(local)},
            credential_ref=None,
            read_priority=20,
        )
        mount_backend = None
        if mounted is not None:
            mount_backend = db.upsert_storage_backend(
                backend_key="drive_mount_ro",
                display_name="Legacy Google Drive mount",
                driver_key="filesystem",
                provider_key="local",
                access_mode="read_only",
                config={"schema_version": 1, "root": str(mounted)},
                credential_ref=None,
                read_priority=40,
            )

        rows = [
            dict(row)
            for row in db.connection.execute(
                """
                SELECT d.document_id, d.source_path, d.mime_type
                FROM documents d
                WHERE d.source_path IS NOT NULL AND trim(d.source_path) != ''
                ORDER BY d.document_id
                """
            ).fetchall()
        ]
        migrated = 0
        already_present = 0
        unresolved_rows: list[str] = []
        for offset in range(0, len(rows), batch_size):
            for row in rows[offset : offset + batch_size]:
                document_id = str(row["document_id"])
                if _has_source_attachment(db, document_id):
                    already_present += 1
                    continue
                source = Path(str(row["source_path"])).expanduser()
                try:
                    source_path = source.resolve(strict=True)
                    backend, relative = _backend_for_path(
                        source_path,
                        local_root=local,
                        local_backend=local_backend,
                        mount_root=mounted,
                        mount_backend=mount_backend,
                    )
                except (FileNotFoundError, StorageMigrationError):
                    unresolved_rows.append(document_id)
                    continue
                digest = sha256_file(source_path)
                media_type = str(row["mime_type"] or mimetypes.guess_type(source_path.name)[0] or "application/octet-stream")
                file_row = db.upsert_file(
                    content_sha256=digest,
                    byte_size=source_path.stat().st_size,
                    media_type=media_type,
                )
                db.attach_file_to_document(
                    document_id=document_id,
                    file_id=str(file_row["file_id"]),
                    attachment_role="source",
                    display_name=source_path.name,
                )
                db.register_file_replica(
                    file_id=str(file_row["file_id"]),
                    storage_backend_id=str(backend["storage_backend_id"]),
                    provider_locator=relative,
                    provider_version=str(source_path.stat().st_mtime_ns),
                    is_primary=True,
                    last_verified_at=_utc_now(),
                    provider_metadata={"legacy_source": True},
                )
                migrated += 1

        pending = _pending_documents(db)
        if require_complete and pending:
            raise StorageMigrationError(
                f"Storage migration has {pending} unresolved legacy documents"
            )
        return StorageMigrationResult(
            migrated=migrated,
            already_present=already_present,
            unresolved=len(unresolved_rows),
            schema_version=raw_schema_version(db_path),
            pending=pending,
        )


def _has_source_attachment(db: LedgerDB, document_id: str) -> bool:
    row = db.connection.execute(
        """
        SELECT 1 FROM document_attachments
        WHERE document_id = ? AND attachment_role = 'source'
        """,
        (document_id,),
    ).fetchone()
    return row is not None


def _pending_documents(db: LedgerDB) -> int:
    return _pending_documents_read_only(db.connection)


def _pending_documents_read_only(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) FROM documents d
        WHERE d.source_path IS NOT NULL AND trim(d.source_path) != ''
          AND NOT EXISTS (
              SELECT 1 FROM document_attachments a
              WHERE a.document_id = d.document_id AND a.attachment_role = 'source'
          )
        """
    ).fetchone()
    return int(row[0])


def _backend_for_path(
    source: Path,
    *,
    local_root: Path,
    local_backend: dict[str, Any],
    mount_root: Path | None,
    mount_backend: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    for root, backend in ((local_root, local_backend), (mount_root, mount_backend)):
        if root is None or backend is None:
            continue
        try:
            return backend, source.relative_to(root).as_posix()
        except ValueError:
            continue
    raise StorageMigrationError("Legacy source is outside configured storage roots")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
