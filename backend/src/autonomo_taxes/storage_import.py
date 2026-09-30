"""Import existing Google Drive files into the accounting intake flow.

The importer intentionally uses the ordinary CLI intake path for document
parsing and accounting lifecycle rules.  It only adds the immutable Drive
replica after the resulting catalogue file has been proven to have the same
bytes as the user-selected Drive object.
"""

from __future__ import annotations

from contextlib import suppress
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Mapping

from .ledger_db import LedgerDB
from .storage_adapters import GoogleDriveStorageAdapter, StorageAdapterError
from .storage_reconcile import StorageReconcileError, adapter_for_backend, reconcile_file_to_backend


class GoogleDriveImportError(RuntimeError):
    """A shared Drive URL cannot safely become an accounting document."""


MAX_GOOGLE_DRIVE_IMPORT_BYTES = 30 * 1024 * 1024


def ingest_google_drive_url(
    *,
    config: Any,
    fields: Mapping[str, str],
    drive_url: str,
    file_id: str,
) -> dict[str, Any]:
    """Create an intake draft backed by an existing verified Drive file.

    A temporary local file is needed only for the existing OCR/extraction CLI.
    The durable physical source remains the original Drive file and is promoted
    after its bytes match the content-addressed catalogue record.
    """
    adapter: GoogleDriveStorageAdapter | None = None
    backend: dict[str, Any] | None = None
    imported: Any | None = None
    last_error: Exception | None = None
    for candidate_adapter, candidate_backend in _google_readers(config.database):
        try:
            imported = candidate_adapter.import_url(
                drive_url,
                max_bytes=MAX_GOOGLE_DRIVE_IMPORT_BYTES,
            )
            adapter, backend = candidate_adapter, candidate_backend
            break
        except StorageAdapterError as exc:
            last_error = exc
    if adapter is None or backend is None or imported is None:
        raise GoogleDriveImportError(
            "Google Drive file is unavailable to the configured archive reader"
        ) from last_error
    if imported.file.locator != file_id:
        raise GoogleDriveImportError("Google Drive URL resolved to an unexpected file")

    display_name = str(imported.file.metadata.get("name") or f"drive-{file_id}")
    suffix = Path(display_name).suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".csv", ".txt"}:
        raise GoogleDriveImportError("Google Drive file type is not supported for intake")

    temp_root = Path(config.cache_root) / "drive-intake"
    temp_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="drive-",
            suffix=suffix,
            dir=temp_root,
            delete=False,
        ) as handle:
            handle.write(imported.content)
            temporary_path = Path(handle.name)
        temporary_path.chmod(0o600)

        payload = _run_cli_ingest(config, fields, temporary_path, file_id)
        document_id = str(payload.get("document_id") or "")
        if not document_id:
            raise GoogleDriveImportError("Intake did not return a document id")
        catalogue_file_id = _register_original_drive_replica(
            database=Path(config.database),
            document_id=document_id,
            backend=backend,
            adapter=adapter,
            file_id=file_id,
            drive_url=drive_url,
            imported=imported,
        )
        try:
            with LedgerDB.open(Path(config.database)) as mirror_db:
                mirror = reconcile_file_to_backend(
                    mirror_db,
                    backend_key="yandex_evidence",
                    file_id=catalogue_file_id,
                )
        except StorageReconcileError as exc:
            raise GoogleDriveImportError("Yandex evidence mirror is not configured") from exc
        if int(mirror["failed"]):
            raise GoogleDriveImportError("Google Drive file could not be mirrored to Yandex")
    finally:
        if temporary_path is not None:
            with suppress(FileNotFoundError):
                temporary_path.unlink()

    transaction = payload.get("transaction") or {}
    return {
        "status": "accepted_for_review",
        "period": fields["period"],
        "kind": fields["kind"],
        "file_name": display_name,
        "document_id": payload.get("document_id"),
        "transaction_id": transaction.get("transaction_id"),
        "document_lifecycle_status": payload.get("document_lifecycle_status"),
        "review_requirements": payload.get("review_requirements", []),
        "system_marker": payload.get("document_id"),
    }


def _google_readers(database: Path) -> list[tuple[GoogleDriveStorageAdapter, dict[str, Any]]]:
    readers: list[tuple[GoogleDriveStorageAdapter, dict[str, Any]]] = []
    with LedgerDB.open(database) as db:
        rows = db.connection.execute(
            """
            SELECT * FROM storage_backends
            WHERE enabled = 1 AND driver_key = 'google_drive'
            ORDER BY CASE access_mode WHEN 'read_only' THEN 0 ELSE 1 END,
                     read_priority, backend_key
            """
        ).fetchall()
        for row in rows:
            backend = dict(row)
            try:
                adapter = adapter_for_backend(backend)
            except Exception:
                continue
            if isinstance(adapter, GoogleDriveStorageAdapter):
                readers.append((adapter, backend))
    if not readers:
        raise GoogleDriveImportError("No configured Google Drive archive reader is available")
    return readers


def _run_cli_ingest(
    config: Any,
    fields: Mapping[str, str],
    temporary_path: Path,
    drive_file_id: str,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "autonomo_taxes.cli",
        "ingest",
        str(temporary_path),
        "--db",
        str(config.database),
        "--kind",
        fields["kind"],
        "--period",
        fields["period"],
        "--archive-root",
        str(config.archive_root),
        "--drive-file-id",
        drive_file_id,
    ]
    if fields.get("defer_counterparty") == "1":
        command.append("--defer-counterparty")
    for field, option in {
        "issued_on": "--issued-on",
        "document_number": "--document-number",
        "counterparty_name": "--counterparty-name",
        "gross": "--gross",
        "taxable_base": "--taxable-base",
        "vat": "--vat",
        "currency": "--currency",
    }.items():
        value = str(fields.get(field, "")).strip()
        if value:
            command.extend((option, value))
    environment = dict(__import__("os").environ)
    source_root = str(Path(config.project_root) / "src")
    environment["PYTHONPATH"] = __import__("os").pathsep.join(
        value for value in (source_root, environment.get("PYTHONPATH", "")) if value
    )
    run = subprocess.run(
        command,
        cwd=config.project_root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=180,
    )
    payload = _json_payload(run.stdout) or _json_payload(run.stderr)
    if run.returncode != 0 or payload is None:
        raise GoogleDriveImportError("Google Drive file could not be processed for intake")
    return payload


def _register_original_drive_replica(
    *,
    database: Path,
    document_id: str,
    backend: Mapping[str, Any],
    adapter: GoogleDriveStorageAdapter,
    file_id: str,
    drive_url: str,
    imported: Any,
) -> str:
    with LedgerDB.open(database) as db:
        attachment = db.connection.execute(
            """
            SELECT f.*
            FROM document_attachments da
            JOIN files f ON f.file_id = da.file_id
            WHERE da.document_id = ? AND da.attachment_role = 'source'
            """,
            (document_id,),
        ).fetchone()
        if attachment is None:
            raise GoogleDriveImportError("Intake did not register a source file")
        digest = str(attachment["content_sha256"])
        try:
            resource_key = imported.file.metadata.get("resource_key")
            verified = adapter.verify(
                file_id,
                digest,
                resource_key=str(resource_key) if resource_key else None,
            )
        except StorageAdapterError as exc:
            raise GoogleDriveImportError("Google Drive file changed during intake") from exc
        if int(verified.size_bytes) != int(attachment["byte_size"]):
            raise GoogleDriveImportError("Google Drive file size does not match intake")
        replica = db.register_file_replica(
            file_id=str(attachment["file_id"]),
            storage_backend_id=str(backend["storage_backend_id"]),
            provider_locator=file_id,
            provider_version=verified.version,
            is_primary=False,
            web_url=drive_url,
            provider_metadata={
                **dict(verified.metadata),
                **{
                    key: value
                    for key, value in dict(imported.file.metadata).items()
                    if key in {"resource_key", "submitted_source_url"} and value
                },
                "original_name": str(imported.file.metadata.get("name") or ""),
                "imported_from_google_drive_url": True,
            },
            last_verified_at=_utc_now(),
        )
        db.promote_file_replica(str(replica["file_replica_id"]))
        return str(attachment["file_id"])


def _json_payload(value: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
