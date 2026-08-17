from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from autonomo_taxes import ledger_db as ledger_db_module
from autonomo_taxes.ledger_db import LedgerDB, SchemaVersionError


def _schema_17_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        for version in range(1, 18):
            ledger_db_module._MIGRATIONS[version](connection)
            connection.execute(f"PRAGMA user_version = {version}")
        connection.commit()
    finally:
        connection.close()


def _document(db: LedgerDB) -> dict[str, object]:
    return db.upsert_document(
        document_type="expense_invoice",
        issued_on="2026-08-17",
        source_hash="a" * 64,
    )


def test_schema_18_requires_an_explicit_writable_migration(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite3"
    _schema_17_database(path)

    with pytest.raises(SchemaVersionError, match="explicit migration"):
        LedgerDB.open(path)

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
    finally:
        connection.close()

    with LedgerDB.open(path, apply_migrations=True) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 18
        assert set(db.table_counts()).issuperset(
            {"files", "document_attachments", "storage_backends", "file_replicas"}
        )


def test_file_content_can_be_attached_without_mutating_document_row_version(tmp_path: Path) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        document = _document(db)
        initial_version = document["row_version"]
        content = db.upsert_file(
            content_sha256="B" * 64,
            byte_size=42,
            media_type="application/pdf",
        )
        backend = db.upsert_storage_backend(
            backend_key="google_primary_rw",
            display_name="Google Drive",
            driver_key="google_drive",
            provider_key="google",
            access_mode="read_write",
            config={"root_folder_id": "folder-1", "schema_version": 1},
            credential_ref="google-production-oauth",
        )

        attachment = db.attach_file_to_document(
            document_id=str(document["document_id"]),
            file_id=str(content["file_id"]),
            attachment_role="source",
            display_name="invoice.pdf",
        )
        replica = db.register_file_replica(
            file_id=str(content["file_id"]),
            storage_backend_id=str(backend["storage_backend_id"]),
            provider_locator="drive-file-id",
            provider_version="revision-1",
            is_primary=True,
            last_verified_at="2026-08-17T12:00:00Z",
        )

        assert attachment["document_id"] == document["document_id"]
        assert replica["is_primary"] == 1
        assert replica["replica_status"] == "available"
        assert db._fetch_one(
            "SELECT row_version FROM documents WHERE document_id = ?",
            (document["document_id"],),
        )["row_version"] == initial_version


def test_storage_constraints_enforce_one_source_and_one_primary(tmp_path: Path) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        document = _document(db)
        first = db.upsert_file(
            content_sha256="1" * 64, byte_size=1, media_type="application/pdf"
        )
        second = db.upsert_file(
            content_sha256="2" * 64, byte_size=2, media_type="application/pdf"
        )
        drive = db.upsert_storage_backend(
            backend_key="google_primary_rw",
            display_name="Google Drive",
            driver_key="google_drive",
            provider_key="google",
            access_mode="read_write",
        )
        yandex = db.upsert_storage_backend(
            backend_key="yandex_evidence",
            display_name="Yandex evidence",
            driver_key="s3",
            provider_key="yandex_cloud",
            access_mode="read_write",
        )
        db.attach_file_to_document(
            document_id=str(document["document_id"]),
            file_id=str(first["file_id"]),
            attachment_role="source",
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.attach_file_to_document(
                document_id=str(document["document_id"]),
                file_id=str(second["file_id"]),
                attachment_role="source",
            )

        db.register_file_replica(
            file_id=str(first["file_id"]),
            storage_backend_id=str(drive["storage_backend_id"]),
            provider_locator="drive-id",
            is_primary=True,
            last_verified_at="2026-08-17T12:00:00Z",
        )
        promoted = db.register_file_replica(
            file_id=str(first["file_id"]),
            storage_backend_id=str(yandex["storage_backend_id"]),
            provider_locator="sha256/" + "1" * 64,
            is_primary=True,
            last_verified_at="2026-08-17T12:00:01Z",
        )
        assert promoted["is_primary"] == 1
        assert db._fetch_one(
            "SELECT is_primary FROM file_replicas WHERE storage_backend_id = ?",
            (drive["storage_backend_id"],),
        )["is_primary"] == 0


def test_storage_metadata_rejects_embedded_credentials_and_unverified_primary(tmp_path: Path) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        with pytest.raises(ValueError, match="credential_ref"):
            db.upsert_storage_backend(
                backend_key="bad", display_name="Bad", driver_key="s3",
                provider_key="yandex_cloud", access_mode="read_write",
                config={"secret_key": "not-allowed"},
            )
        content = db.upsert_file(
            content_sha256="3" * 64, byte_size=3, media_type="application/pdf"
        )
        backend = db.upsert_storage_backend(
            backend_key="local_staging", display_name="Local", driver_key="filesystem",
            provider_key="local", access_mode="read_write",
        )
        with pytest.raises(ValueError, match="last_verified_at"):
            db.register_file_replica(
                file_id=str(content["file_id"]),
                storage_backend_id=str(backend["storage_backend_id"]),
                provider_locator="incoming/invoice.pdf",
                is_primary=True,
            )


def test_file_content_identity_deduplicates_bytes_despite_mime_discovery_difference(tmp_path: Path) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        first = db.upsert_file(
            content_sha256="4" * 64,
            byte_size=12,
            media_type="application/pdf",
        )
        second = db.upsert_file(
            content_sha256="4" * 64,
            byte_size=12,
            media_type="application/octet-stream",
        )
    assert second["file_id"] == first["file_id"]
    assert second["media_type"] == "application/pdf"
