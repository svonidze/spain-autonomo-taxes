"""Shared application operations for CLI and HTTP adapters."""

from __future__ import annotations
from datetime import date
from typing import Any, Mapping
from ..ledger_db import LedgerDB
from .common import (
    MAX_UPLOAD_BYTES,
    ServiceApiError,
    ServiceError,
    UPLOAD_KINDS,
    _quarter_key,
    _store_upload,
    _validate_period,
    _validated_intake_fields,
    normalize_google_drive_url,
)
from .intake_operations import ingest_fields


class IntakeService:
    def ingest_upload(
        self,
        *,
        fields: Mapping[str, str],
        filename: str,
        content: bytes,
        google_folder_id: str | None = None,
    ) -> dict[str, Any]:
        if self.config.inbox_root is None or self.config.archive_root is None:
            raise ServiceError(
                "Intake requires configured private inbox and evidence roots"
            )
        period = _validate_period(fields.get("period", ""))
        kind = fields.get("kind", "")
        if kind not in UPLOAD_KINDS:
            raise ServiceError("kind must be expense_invoice or income_invoice")
        if not content:
            raise ServiceError("Uploaded file is empty")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ServiceError("Uploaded file exceeds the 30 MB limit")
        issued_on = fields.get("issued_on", "").strip()
        if issued_on:
            parsed_date = date.fromisoformat(issued_on)
            if _quarter_key(parsed_date) != period:
                raise ServiceError(
                    f"Invoice date belongs to {_quarter_key(parsed_date)}, not {period}"
                )

        stored_path = _store_upload(
            self.config.inbox_root,
            period=period,
            kind=kind,
            filename=filename,
            content=content,
        )
        result = ingest_fields(self.config, fields, stored_path)
        if google_folder_id:
            document_id = str(result.get("document_id") or "")
            if not document_id:
                raise ServiceApiError(
                    503,
                    "storage_sync_failed",
                    "Document intake did not return an id for cloud archival",
                )
            self._sync_upload_to_selected_google_folder(
                document_id=document_id,
                google_folder_id=google_folder_id,
            )
        return {
            "status": "accepted_for_review",
            "period": period,
            "kind": kind,
            "file_name": stored_path.name,
            "document_id": result.get("document_id"),
            "transaction_id": (result.get("transaction") or {}).get("transaction_id"),
            "document_lifecycle_status": result.get("document_lifecycle_status"),
            "review_requirements": result.get("review_requirements", []),
            "system_marker": result.get("document_id"),
        }

    def _sync_upload_to_selected_google_folder(
        self,
        *,
        document_id: str,
        google_folder_id: str,
    ) -> None:
        """Create verified Google and Yandex copies for an explicitly chosen folder."""
        from ..storage_reconcile import reconcile_file_to_backend

        try:
            with LedgerDB.open(self.config.database) as db:
                file_row = db.connection.execute(
                    """
                    SELECT da.file_id
                    FROM document_attachments da
                    WHERE da.document_id = ? AND da.attachment_role = 'source'
                    ORDER BY da.created_at, da.document_attachment_id
                    LIMIT 1
                    """,
                    (document_id,),
                ).fetchone()
                writer = db.connection.execute(
                    """
                    SELECT backend_key
                    FROM storage_backends
                    WHERE enabled = 1
                      AND driver_key = 'google_drive'
                      AND access_mode = 'read_write'
                      AND json_extract(config_json, '$.credential_mode') = 'oauth'
                    ORDER BY read_priority, backend_key
                    LIMIT 1
                    """
                ).fetchone()
                if file_row is None or writer is None:
                    raise RuntimeError("Google Drive writer is not configured")
                file_id = str(file_row["file_id"])
                google = reconcile_file_to_backend(
                    db,
                    backend_key=str(writer["backend_key"]),
                    file_id=file_id,
                    google_folder_id=google_folder_id,
                    document_id=document_id,
                )
                yandex = reconcile_file_to_backend(
                    db,
                    backend_key="yandex_evidence",
                    file_id=file_id,
                    document_id=document_id,
                )
                if int(google.get("failed", 0)) or int(yandex.get("failed", 0)):
                    raise RuntimeError("Verified cloud replica could not be created")
        except Exception as exc:
            raise ServiceApiError(
                503,
                "storage_sync_failed",
                "Document was accepted locally but cloud archival is not verified",
            ) from exc

    def ingest_google_drive_url(
        self,
        *,
        fields: Mapping[str, str],
        drive_url: str,
    ) -> dict[str, Any]:
        """Ingest an existing Drive file without creating another Drive copy.

        The provider integration owns download, checksum verification, archival
        metadata, and replica registration.  Keeping the web boundary limited
        to a canonical file ID prevents arbitrary remote fetches from the
        private web service.
        """
        normalized_url, file_id = normalize_google_drive_url(drive_url)
        normalized_fields = _validated_intake_fields(fields)
        try:
            from ..storage_import import (
                ingest_google_drive_url as import_google_drive_url,
            )

            result = import_google_drive_url(
                config=self.config,
                fields=normalized_fields,
                drive_url=normalized_url,
                file_id=file_id,
            )
        except ImportError as exc:  # pragma: no cover - incomplete deployment guard
            raise ServiceError("Google Drive intake is not configured") from exc
        except ServiceError:
            raise
        except Exception as exc:
            # The provider may include remote URLs or credential context in its
            # exception.  Never reflect either back through the web API.
            raise ServiceError("Google Drive file could not be imported") from exc
        if not isinstance(result, Mapping):
            raise ServiceError("Google Drive intake returned an invalid result")
        return dict(result)
