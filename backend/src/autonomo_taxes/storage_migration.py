from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import mimetypes
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping

from .ledger_db import LATEST_SCHEMA_VERSION, LedgerDB
from .storage_adapters import GoogleDriveStorageAdapter, StorageAdapterError, sha256_file


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


@dataclass(frozen=True)
class DriveArchiveAdoptionResult:
    """Outcome of attaching catalogue files to pre-existing Drive originals."""

    backend_key: str
    adopted: int
    already_adopted: int
    retired_managed: int
    dry_run: bool

    def as_dict(self) -> dict[str, int | str | bool]:
        return {
            "backend_key": self.backend_key,
            "adopted": self.adopted,
            "already_adopted": self.already_adopted,
            "retired_managed": self.retired_managed,
            "dry_run": self.dry_run,
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


def adopt_google_drive_originals(
    db: LedgerDB,
    *,
    backend_key: str,
    records: Iterable[Mapping[str, Any]],
    dry_run: bool = False,
    adapter: GoogleDriveStorageAdapter | None = None,
    retire_backend_keys: Iterable[str] = (),
) -> DriveArchiveAdoptionResult:
    """Atomically adopt verified pre-existing Google Drive files.

    Each record must include ``content_sha256`` (or ``digest``) and
    ``drive_file_id``.  Optional ``archive_path``, ``web_url`` and
    ``display_name`` become non-secret replica metadata.  The function first
    verifies every remote object; only then does it write the database, so a
    bad mapping cannot partially promote the archive.

    The returned shape is deliberately CLI-friendly: callers can parse a JSON
    list of records, run ``dry_run=True``, then apply the exact same list.
    Existing replicas are retired only from explicitly named legacy managed
    backends: a Drive file ID alone does not distinguish a managed duplicate
    from an intentionally retained Google copy.
    """
    from .storage_reconcile import adapter_for_backend

    backend_row = db.connection.execute(
        "SELECT * FROM storage_backends WHERE backend_key = ? AND enabled = 1",
        (backend_key,),
    ).fetchone()
    if backend_row is None:
        raise StorageMigrationError(f"Unknown or disabled storage backend: {backend_key}")
    backend = dict(backend_row)
    if str(backend["driver_key"]) != "google_drive":
        raise StorageMigrationError("Drive archive adoption requires a google_drive backend")
    resolved_adapter = adapter or adapter_for_backend(backend)
    if not isinstance(resolved_adapter, GoogleDriveStorageAdapter):
        raise StorageMigrationError("Drive archive adoption requires a Google Drive adapter")

    prepared: list[tuple[dict[str, Any], dict[str, Any], Any]] = []
    seen_digests: set[str] = set()
    for record in records:
        digest = str(record.get("content_sha256") or record.get("digest") or "").lower()
        locator = str(
            record.get("drive_file_id")
            or record.get("google_file_id")
            or record.get("provider_locator")
            or ""
        )
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise StorageMigrationError("Each Drive adoption record requires a SHA-256 content_sha256")
        if not locator:
            raise StorageMigrationError("Each Drive adoption record requires drive_file_id")
        if digest in seen_digests:
            raise StorageMigrationError(f"Drive adoption mapping duplicates content SHA-256: {digest}")
        seen_digests.add(digest)
        file_row = db.connection.execute(
            "SELECT * FROM files WHERE content_sha256 = ?", (digest,)
        ).fetchone()
        if file_row is None:
            raise StorageMigrationError(f"Drive adoption digest is not in the catalogue: {digest}")
        try:
            remote = resolved_adapter.verify(locator, digest)
            if not resolved_adapter.is_within_root(locator, remote):
                raise StorageAdapterError("Google Drive object is outside the configured archive root")
        except StorageAdapterError as exc:
            raise StorageMigrationError(f"Drive archive verification failed for {locator}") from exc
        if remote.size_bytes != int(file_row["byte_size"]):
            raise StorageMigrationError(f"Drive archive size does not match catalogue for {locator}")
        prepared.append((dict(record), dict(file_row), remote))

    existing_rows = {
        str(row["file_id"]): row
        for row in db.connection.execute(
            "SELECT * FROM file_replicas WHERE storage_backend_id = ?",
            (backend["storage_backend_id"],),
        ).fetchall()
    }
    already_adopted = sum(
        1
        for _, file_row, remote in prepared
        if (existing := existing_rows.get(str(file_row["file_id"]))) is not None
        and str(existing["provider_locator"]) == remote.locator
        and existing["replica_status"] == "available"
    )
    adopted = len(prepared) - already_adopted
    file_ids = [str(file_row["file_id"]) for _, file_row, _ in prepared]
    retirement_backends = {
        str(value).strip() for value in retire_backend_keys if str(value).strip()
    }
    retirement_backends.discard(backend_key)
    retired_candidates: list[Any] = []
    if file_ids and retirement_backends:
        placeholders = ",".join("?" for _ in file_ids)
        backend_placeholders = ",".join("?" for _ in retirement_backends)
        retired_candidates = db.connection.execute(
            f"""
            SELECT fr.* FROM file_replicas fr
            JOIN storage_backends sb ON sb.storage_backend_id = fr.storage_backend_id
            WHERE fr.file_id IN ({placeholders})
              AND sb.backend_key IN ({backend_placeholders})
              AND fr.replica_status != 'retired'
            """,
            (*file_ids, *sorted(retirement_backends)),
        ).fetchall()
    if dry_run:
        return DriveArchiveAdoptionResult(
            backend_key=backend_key,
            adopted=adopted,
            already_adopted=already_adopted,
            retired_managed=len(retired_candidates),
            dry_run=True,
        )

    timestamp = _utc_now()
    # All remote verification completed above.  Keep the subsequent catalogue
    # writes in one SQLite transaction so primary pointers cannot be split.
    if db.connection.in_transaction:
        raise StorageMigrationError("Drive archive adoption requires its own write transaction")
    with db.connection:
        # Start the transaction before calling LedgerDB write helpers.  Those
        # helpers intentionally join an active transaction, but would commit
        # independently if this BEGIN were deferred until the first write.
        db.connection.execute("BEGIN IMMEDIATE")
        for record, file_row, remote in prepared:
            metadata = dict(remote.metadata)
            metadata.update(
                {
                    "archive_path": str(record.get("archive_path") or ""),
                    "original_name": str(record.get("display_name") or metadata.get("name") or ""),
                    "content_sha256": str(file_row["content_sha256"]),
                    "adopted_existing_drive_archive": True,
                }
            )
            db.register_file_replica(
                file_id=str(file_row["file_id"]),
                storage_backend_id=str(backend["storage_backend_id"]),
                provider_locator=remote.locator,
                provider_version=remote.version,
                replica_status="available",
                is_primary=True,
                web_url=str(
                    record.get("web_url")
                    or remote.web_url
                    or ("https://" + "drive.google.com/open?id=" + remote.locator)
                ),
                provider_metadata=metadata,
                last_verified_at=timestamp,
            )
        for row in retired_candidates:
            try:
                metadata = dict(json.loads(str(row["provider_metadata_json"])))
            except (TypeError, ValueError):
                metadata = {}
            metadata["retired_reason"] = "duplicate_of_existing_drive_archive"
            metadata["retired_at"] = timestamp
            db.connection.execute(
                """
                UPDATE file_replicas
                SET replica_status = 'retired', is_primary = 0,
                    provider_metadata_json = ?, row_version = row_version + 1, updated_at = ?
                WHERE file_replica_id = ?
                """,
                (json.dumps(metadata, sort_keys=True, separators=(",", ":")), timestamp, row["file_replica_id"]),
            )
    return DriveArchiveAdoptionResult(
        backend_key=backend_key,
        adopted=adopted,
        already_adopted=already_adopted,
        retired_managed=len(retired_candidates),
        dry_run=False,
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
