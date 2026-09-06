from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.storage_adapters import ImportedGoogleDriveFile, StorageObject
import autonomo_taxes.storage_import as storage_import


DRIVE_URL = "https://" + "drive.google.com"


class _DriveReader:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def import_url(self, url: str, *, max_bytes: int | None = None) -> ImportedGoogleDriveFile:
        if max_bytes is not None and len(self.content) > max_bytes:
            raise storage_import.StorageAdapterError("Google Drive file exceeds the configured intake size limit")
        return ImportedGoogleDriveFile(
            file=StorageObject(
                locator="drive-original-123",
                version="v1",
                size_bytes=len(self.content),
                web_url=f"{DRIVE_URL}/open?id=drive-original-123",
                metadata={
                    "name": "original.pdf",
                    "resource_key": "resource_key_123",
                    "submitted_source_url": f"{DRIVE_URL}/open?id=drive-original-123&resourcekey=resource_key_123",
                },
            ),
            content=self.content,
        )

    def verify(
        self,
        locator: str,
        expected_sha256: str,
        *,
        resource_key: str | None = None,
    ) -> StorageObject:
        assert locator == "drive-original-123"
        assert expected_sha256 == sha256(self.content).hexdigest()
        return self.import_url("").file


def test_google_url_import_promotes_original_drive_replica_without_upload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = tmp_path / "ledger.sqlite"
    content = b"verified Drive source"
    digest = sha256(content).hexdigest()
    with LedgerDB.initialize(database) as db:
        document = db.upsert_document(
            external_key=f"sha256:{digest}",
            document_type="expense_invoice",
            issued_on="2026-07-01",
            period_key="2026-Q3",
            source_hash=digest,
        )
        file_row = db.upsert_file(
            content_sha256=digest,
            byte_size=len(content),
            media_type="application/pdf",
        )
        db.attach_file_to_document(
            document_id=document["document_id"],
            file_id=file_row["file_id"],
            attachment_role="source",
            display_name="original.pdf",
        )
        backend = db.upsert_storage_backend(
            backend_key="google_archive_ro",
            display_name="Google archive",
            driver_key="google_drive",
            provider_key="google",
            access_mode="read_only",
            config={"root_folder_id": "root", "credential_mode": "service_account"},
            credential_ref="file:/private/reader.json",
        )

    reader = _DriveReader(content)
    monkeypatch.setattr(
        storage_import,
        "_google_readers",
        lambda _database: [(reader, backend)],
    )
    monkeypatch.setattr(
        storage_import,
        "_ingest_original",
        lambda *_args, **_kwargs: {
            "document_id": document["document_id"],
            "document_lifecycle_status": "needs_review",
            "review_requirements": [],
            "transaction": {"transaction_id": "transaction-1"},
        },
    )
    monkeypatch.setattr(
        storage_import,
        "reconcile_file_to_backend",
        lambda *_args, **_kwargs: {"created": 1, "skipped": 0, "failed": 0},
    )
    config = SimpleNamespace(
        database=database,
        cache_root=tmp_path / "cache",
        project_root=tmp_path,
        archive_root=tmp_path / "evidence",
    )

    result = storage_import.ingest_google_drive_url(
        config=config,
        fields={"period": "2026-Q3", "kind": "expense_invoice"},
        drive_url=f"{DRIVE_URL}/open?id=drive-original-123",
        file_id="drive-original-123",
    )

    assert result["document_id"] == document["document_id"]
    with LedgerDB.open(database) as db:
        replica = db.connection.execute(
            """
            SELECT fr.provider_locator, fr.is_primary, fr.web_url, fr.replica_status,
                   fr.provider_metadata_json
            FROM file_replicas fr
            WHERE fr.storage_backend_id = ?
            """,
            (backend["storage_backend_id"],),
        ).fetchone()
    assert tuple(replica)[:4] == (
        "drive-original-123",
        1,
        f"{DRIVE_URL}/open?id=drive-original-123",
        "available",
    )
    assert '"resource_key":"resource_key_123"' in replica["provider_metadata_json"]
    assert list((tmp_path / "cache" / "drive-intake").glob("drive-*.pdf")) == []


def test_google_url_import_rejects_files_over_upload_limit(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "ledger.sqlite"
    with LedgerDB.initialize(database) as db:
        backend = db.upsert_storage_backend(
            backend_key="google_archive_ro",
            display_name="Google archive",
            driver_key="google_drive",
            provider_key="google",
            access_mode="read_only",
            config={"root_folder_id": "root", "credential_mode": "service_account"},
            credential_ref="file:/private/reader.json",
        )
    oversized = _DriveReader(b"x" * (storage_import.MAX_GOOGLE_DRIVE_IMPORT_BYTES + 1))
    monkeypatch.setattr(storage_import, "_google_readers", lambda _database: [(oversized, backend)])
    config = SimpleNamespace(
        database=database,
        cache_root=tmp_path / "cache",
        project_root=tmp_path,
        archive_root=tmp_path / "evidence",
    )
    with pytest.raises(storage_import.GoogleDriveImportError, match="unavailable"):
        storage_import.ingest_google_drive_url(
            config=config,
            fields={"period": "2026-Q3", "kind": "expense_invoice"},
            drive_url=f"{DRIVE_URL}/open?id=drive-original-123",
            file_id="drive-original-123",
        )
