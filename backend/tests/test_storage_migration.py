from __future__ import annotations

from pathlib import Path

import pytest

from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.storage_migration import (
    StorageMigrationError,
    adopt_google_drive_originals,
    assert_storage_startup_ready,
    migrate_legacy_storage,
)
from autonomo_taxes.storage_adapters import GoogleDriveStorageAdapter, StorageObject


DRIVE_URL = "https://" + "drive.google.com"


class _VerifiedArchiveAdapter(GoogleDriveStorageAdapter):
    def __init__(self, objects: dict[str, tuple[bytes, str]]) -> None:
        super().__init__(root_folder_id="archive", token_file=Path("/unused"), credential_mode="service_account")
        self.objects = objects

    def verify(self, locator: str, expected_sha256: str) -> StorageObject:
        payload, name = self.objects[locator]
        assert sha256_file_from_bytes(payload) == expected_sha256
        return StorageObject(
            locator=locator,
            version="revision-1",
            size_bytes=len(payload),
            web_url=f"{DRIVE_URL}/open?id={locator}",
            metadata={"name": name, "parent_ids": ["archive"]},
        )


class _OutsideArchiveAdapter(_VerifiedArchiveAdapter):
    def is_within_root(self, locator: str, item: StorageObject | None = None) -> bool:
        return False


def sha256_file_from_bytes(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def _legacy_document(db: LedgerDB, source: Path) -> dict[str, object]:
    document = db.upsert_document(
        document_type="expense_invoice",
        issued_on="2026-08-17",
        source_hash="a" * 64,
    )
    return db.set_document_storage(
        str(document["document_id"]),
        source_path=str(source),
        mime_type="application/pdf",
        expected_row_version=int(document["row_version"]),
    )


def test_storage_migration_backfills_and_is_idempotent_without_document_mutation(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private"
    evidence = private_root / "evidence" / "2026-Q3" / "expense_invoice" / "invoice.pdf"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"invoice bytes")
    database = private_root / "autonomo.sqlite"
    with LedgerDB.initialize(database) as db:
        document = _legacy_document(db, evidence)
        original_version = int(document["row_version"])
        document_id = str(document["document_id"])

    first = migrate_legacy_storage(
        database=database,
        local_root=private_root,
        require_complete=True,
    )
    assert first.migrated == 1
    assert first.pending == 0
    with LedgerDB.open(database) as db:
        attachment = db.connection.execute(
            "SELECT * FROM document_attachments WHERE document_id = ?", (document_id,)
        ).fetchone()
        replica = db.connection.execute(
            "SELECT * FROM file_replicas WHERE is_primary = 1"
        ).fetchone()
        version = db.connection.execute(
            "SELECT row_version FROM documents WHERE document_id = ?", (document_id,)
        ).fetchone()[0]
    assert attachment is not None
    assert replica is not None
    assert replica["provider_locator"] == "evidence/2026-Q3/expense_invoice/invoice.pdf"
    assert version == original_version

    second = migrate_legacy_storage(
        database=database,
        local_root=private_root,
        require_complete=True,
    )
    assert second.migrated == 0
    assert second.already_present == 1
    assert second.pending == 0


def test_storage_migration_requires_complete_readable_legacy_sources(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    private_root.mkdir()
    database = private_root / "autonomo.sqlite"
    missing = private_root / "evidence" / "missing.pdf"
    with LedgerDB.initialize(database) as db:
        _legacy_document(db, missing)

    with pytest.raises(StorageMigrationError, match="unresolved"):
        migrate_legacy_storage(
            database=database,
            local_root=private_root,
            require_complete=True,
        )


def test_storage_startup_readiness_requires_backfill(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    private_root.mkdir()
    database = private_root / "autonomo.sqlite"
    evidence = private_root / "evidence" / "invoice.pdf"
    evidence.parent.mkdir()
    evidence.write_bytes(b"invoice")
    with LedgerDB.initialize(database) as db:
        _legacy_document(db, evidence)

    with pytest.raises(StorageMigrationError, match="unresolved"):
        assert_storage_startup_ready(database)

    migrate_legacy_storage(database=database, local_root=private_root, require_complete=True)
    assert_storage_startup_ready(database)


def test_adopt_google_drive_originals_verifies_then_promotes_and_retires_managed_copy(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    content = b"original archive invoice"
    digest = sha256_file_from_bytes(content)
    with LedgerDB.initialize(database) as db:
        document = db.upsert_document(
            document_type="expense_invoice", issued_on="2026-08-17", source_hash="a" * 64
        )
        file_row = db.upsert_file(content_sha256=digest, byte_size=len(content), media_type="application/pdf")
        db.attach_file_to_document(document_id=str(document["document_id"]), file_id=str(file_row["file_id"]), attachment_role="source")
        managed = db.upsert_storage_backend(
            backend_key="google_managed_rw", display_name="Managed", driver_key="google_drive",
            provider_key="google", access_mode="read_write", config={"root_folder_id": "managed"}, credential_ref="file:/unused",
        )
        archive = db.upsert_storage_backend(
            backend_key="google_archive_ro", display_name="Archive", driver_key="google_drive",
            provider_key="google", access_mode="read_only",
            config={"root_folder_id": "archive", "credential_mode": "service_account"}, credential_ref="file:/unused",
        )
        db.register_file_replica(
            file_id=str(file_row["file_id"]), storage_backend_id=str(managed["storage_backend_id"]),
            provider_locator=f"sha256-{digest}", is_primary=True, last_verified_at="2026-08-18T00:00:00Z",
        )
        adapter = _VerifiedArchiveAdapter({"original-drive-id": (content, "factura.pdf")})
        no_retirement = adopt_google_drive_originals(
            db,
            backend_key="google_archive_ro",
            records=[{"content_sha256": digest, "drive_file_id": "original-drive-id"}],
            dry_run=True,
            adapter=adapter,
        )
        assert no_retirement.as_dict()["retired_managed"] == 0
        dry_run = adopt_google_drive_originals(
            db,
            backend_key="google_archive_ro",
            records=[{"content_sha256": digest, "drive_file_id": "original-drive-id", "archive_path": "2026/Q3/factura.pdf"}],
            dry_run=True,
            adapter=adapter,
            retire_backend_keys=("google_managed_rw",),
        )
        assert dry_run.as_dict()["retired_managed"] == 1
        result = adopt_google_drive_originals(
            db,
            backend_key="google_archive_ro",
            records=[{"content_sha256": digest, "drive_file_id": "original-drive-id", "archive_path": "2026/Q3/factura.pdf"}],
            adapter=adapter,
            retire_backend_keys=("google_managed_rw",),
        )
        rows = db.connection.execute("SELECT * FROM file_replicas WHERE file_id = ? ORDER BY provider_locator", (file_row["file_id"],)).fetchall()

    assert result.as_dict()["adopted"] == 1
    by_locator = {str(row["provider_locator"]): row for row in rows}
    assert by_locator["original-drive-id"]["is_primary"] == 1
    assert by_locator["original-drive-id"]["replica_status"] == "available"
    assert by_locator[f"sha256-{digest}"]["replica_status"] == "retired"
    assert "2026/Q3/factura.pdf" in str(by_locator["original-drive-id"]["provider_metadata_json"])


def test_adopt_google_drive_originals_makes_no_writes_when_one_record_is_bad(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    content = b"original"
    digest = sha256_file_from_bytes(content)
    with LedgerDB.initialize(database) as db:
        file_row = db.upsert_file(content_sha256=digest, byte_size=len(content), media_type="application/pdf")
        db.upsert_storage_backend(
            backend_key="google_archive_ro", display_name="Archive", driver_key="google_drive",
            provider_key="google", access_mode="read_only",
            config={"root_folder_id": "archive", "credential_mode": "service_account"}, credential_ref="file:/unused",
        )
        adapter = _VerifiedArchiveAdapter({"original-drive-id": (content, "factura.pdf")})
        with pytest.raises(StorageMigrationError, match="not in the catalogue"):
            adopt_google_drive_originals(
                db, backend_key="google_archive_ro", records=[
                    {"content_sha256": digest, "drive_file_id": "original-drive-id"},
                    {"content_sha256": "f" * 64, "drive_file_id": "other-drive-id"},
                ], adapter=adapter
            )
        assert db.connection.execute("SELECT COUNT(*) FROM file_replicas WHERE file_id = ?", (file_row["file_id"],)).fetchone()[0] == 0


def test_adopt_google_drive_originals_rejects_an_original_outside_archive_root(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    content = b"original"
    digest = sha256_file_from_bytes(content)
    with LedgerDB.initialize(database) as db:
        db.upsert_file(content_sha256=digest, byte_size=len(content), media_type="application/pdf")
        db.upsert_storage_backend(
            backend_key="google_archive_ro", display_name="Archive", driver_key="google_drive",
            provider_key="google", access_mode="read_only",
            config={"root_folder_id": "archive", "credential_mode": "service_account"}, credential_ref="file:/unused",
        )
        with pytest.raises(StorageMigrationError, match="archive verification failed"):
            adopt_google_drive_originals(
                db, backend_key="google_archive_ro",
                records=[{"content_sha256": digest, "drive_file_id": "original-drive-id"}],
                adapter=_OutsideArchiveAdapter({"original-drive-id": (content, "factura.pdf")}),
            )
