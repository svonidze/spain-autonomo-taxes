from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from autonomo_taxes import ledger_db as ledger_db_module
from autonomo_taxes.ledger_db import LedgerDB, LedgerDbError, SchemaVersionError


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
        assert (
            db.connection.execute("PRAGMA user_version").fetchone()[0]
            == ledger_db_module.LATEST_SCHEMA_VERSION
        )
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


def test_verified_replica_can_be_promoted_and_duplicate_retired_without_document_mutation(
    tmp_path: Path,
) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        document = _document(db)
        initial_document_version = document["row_version"]
        content = db.upsert_file(
            content_sha256="5" * 64, byte_size=5, media_type="application/pdf"
        )
        managed_backend = db.upsert_storage_backend(
            backend_key="legacy_drive_archive",
            display_name="Legacy Drive archive",
            driver_key="google_drive",
            provider_key="google",
            access_mode="read_only",
        )
        original_backend = db.upsert_storage_backend(
            backend_key="existing_drive_archive",
            display_name="Existing Drive archive",
            driver_key="google_drive",
            provider_key="google",
            access_mode="read_only",
        )
        db.attach_file_to_document(
            document_id=str(document["document_id"]),
            file_id=str(content["file_id"]),
            attachment_role="source",
        )
        managed = db.register_file_replica(
            file_id=str(content["file_id"]),
            storage_backend_id=str(managed_backend["storage_backend_id"]),
            provider_locator="managed-sha256-object",
            is_primary=True,
            last_verified_at="2026-08-17T12:00:00Z",
        )
        original = db.register_file_replica(
            file_id=str(content["file_id"]),
            storage_backend_id=str(original_backend["storage_backend_id"]),
            provider_locator="existing-drive-file-id",
            web_url="https://" + "drive.google.com/open?id=existing-drive-file-id",
            provider_metadata={"archive_relative_path": "2026/expense/invoice.pdf"},
            last_verified_at="2026-08-17T12:01:00Z",
        )

        promoted = db.promote_file_replica(str(original["file_replica_id"]))
        retired = db.retire_file_replica(
            str(managed["file_replica_id"]),
            retirement_reason="duplicate_of_existing_drive_archive",
        )

        assert promoted["is_primary"] == 1
        assert retired["replica_status"] == "retired"
        assert retired["is_primary"] == 0
        assert json.loads(retired["provider_metadata_json"])["retirement_reason"] == (
            "duplicate_of_existing_drive_archive"
        )
        assert db._fetch_one(
            "SELECT row_version FROM documents WHERE document_id = ?",
            (document["document_id"],),
        )["row_version"] == initial_document_version


def test_only_verified_non_primary_replicas_can_be_promoted_or_retired(tmp_path: Path) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        content = db.upsert_file(
            content_sha256="6" * 64, byte_size=6, media_type="application/pdf"
        )
        backend = db.upsert_storage_backend(
            backend_key="provider_independent_backend",
            display_name="Any provider",
            driver_key="filesystem",
            provider_key="local",
            access_mode="read_write",
        )
        unavailable = db.register_file_replica(
            file_id=str(content["file_id"]),
            storage_backend_id=str(backend["storage_backend_id"]),
            provider_locator="unverified-object",
            replica_status="missing",
        )
        with pytest.raises(LedgerDbError, match="available, verified"):
            db.promote_file_replica(str(unavailable["file_replica_id"]))

        primary = db.register_file_replica(
            file_id=str(content["file_id"]),
            storage_backend_id=str(backend["storage_backend_id"]),
            provider_locator="verified-object",
            is_primary=True,
            last_verified_at="2026-08-17T12:00:00Z",
        )
        with pytest.raises(LedgerDbError, match="Promote another verified"):
            db.retire_file_replica(
                str(primary["file_replica_id"]), retirement_reason="obsolete"
            )


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
