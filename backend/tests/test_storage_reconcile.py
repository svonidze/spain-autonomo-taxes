from __future__ import annotations

from pathlib import Path

from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.storage_reconcile import reconcile_backend, reconcile_file_to_backend
from autonomo_taxes.storage_adapters import GoogleDriveStorageAdapter, StorageObject
from autonomo_taxes.storage_service import register_local_source_replica


def test_reconcile_creates_verified_filesystem_replica(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    source_root = tmp_path / "source"
    source = source_root / "invoice.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"invoice")
    destination_root = tmp_path / "destination"
    with LedgerDB.initialize(database) as db:
        document = db.upsert_document(
            document_type="expense_invoice",
            issued_on="2026-08-17",
            source_hash="a" * 64,
        )
        register_local_source_replica(
            db,
            document_id=str(document["document_id"]),
            source_path=source,
            media_type="application/pdf",
            storage_root=source_root,
        )
        destination = db.upsert_storage_backend(
            backend_key="local_mirror",
            display_name="Local mirror",
            driver_key="filesystem",
            provider_key="local",
            access_mode="read_write",
            config={"schema_version": 1, "root": str(destination_root)},
            read_priority=80,
        )

        result = reconcile_backend(db, backend_key="local_mirror")
        replica = db.connection.execute(
            """
            SELECT * FROM file_replicas
            WHERE storage_backend_id = ?
            """,
            (destination["storage_backend_id"],),
        ).fetchone()

    assert result == {"backend_key": "local_mirror", "created": 1, "skipped": 0, "failed": 0}
    assert replica["replica_status"] == "available"
    assert (destination_root / replica["provider_locator"]).read_bytes() == b"invoice"


def test_reconcile_cli_returns_nonzero_for_missing_source_replica(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    with LedgerDB.initialize(database) as db:
        document = db.upsert_document(
            document_type="expense_invoice",
            issued_on="2026-08-17",
            source_hash="b" * 64,
        )
        file_row = db.upsert_file(
            content_sha256="c" * 64,
            byte_size=3,
            media_type="application/pdf",
        )
        db.attach_file_to_document(
            document_id=str(document["document_id"]),
            file_id=str(file_row["file_id"]),
            attachment_role="source",
        )
        db.upsert_storage_backend(
            backend_key="local_mirror",
            display_name="Local mirror",
            driver_key="filesystem",
            provider_key="local",
            access_mode="read_write",
            config={"schema_version": 1, "root": str(tmp_path / "destination")},
        )

    assert main(["storage", "reconcile", "--db", str(database), "--backend", "local_mirror"]) == 2


def test_google_reconcile_reuses_verified_original_on_another_google_backend(tmp_path: Path, monkeypatch) -> None:
    import autonomo_taxes.storage_reconcile as reconcile_module

    class Adapter:
        def verify(self, locator: str, expected_sha256: str) -> StorageObject:
            return StorageObject(locator=locator, version="v1", size_bytes=7)

        def put_file(self, *args, **kwargs) -> StorageObject:
            raise AssertionError("Google writer must not upload a duplicate")

    database = tmp_path / "ledger.sqlite"
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "invoice.pdf"
    source.write_bytes(b"invoice")
    with LedgerDB.initialize(database) as db:
        document = db.upsert_document(document_type="expense_invoice", issued_on="2026-08-17", source_hash="d" * 64)
        registered = register_local_source_replica(
            db, document_id=str(document["document_id"]), source_path=source,
            media_type="application/pdf", storage_root=source_root,
        )
        original = db.upsert_storage_backend(
            backend_key="google_archive_ro", display_name="Archive", driver_key="google_drive",
            provider_key="google", access_mode="read_only", config={"root_folder_id": "archive"}, credential_ref="file:/unused",
        )
        db.upsert_storage_backend(
            backend_key="google_writer_rw", display_name="Writer", driver_key="google_drive",
            provider_key="google", access_mode="read_write", config={"root_folder_id": "writer"}, credential_ref="file:/unused",
        )
        db.register_file_replica(
            file_id=str(registered["file"]["file_id"]), storage_backend_id=str(original["storage_backend_id"]),
            provider_locator="original-drive-id", is_primary=True, last_verified_at="2026-08-18T00:00:00Z",
        )
        monkeypatch.setattr(reconcile_module, "adapter_for_backend", lambda backend: Adapter())
        result = reconcile_backend(db, backend_key="google_writer_rw")

    assert result == {"backend_key": "google_writer_rw", "created": 0, "skipped": 1, "failed": 0}


def test_google_file_reconcile_uses_selected_root_period_type_and_attachment_name(
    tmp_path: Path, monkeypatch
) -> None:
    import autonomo_taxes.storage_reconcile as reconcile_module

    class Adapter(GoogleDriveStorageAdapter):
        def __init__(self) -> None:
            super().__init__(root_folder_id="default-root-id", token_file=Path("/unused"))
            self.kwargs: dict[str, object] | None = None

        def put_file(self, source: Path, **kwargs: object) -> StorageObject:
            self.kwargs = kwargs
            return StorageObject(
                locator="new-drive-file-id", version="v1", size_bytes=source.stat().st_size,
                metadata={"archive_relative_path": "unassigned/expense_invoice/Original invoice.pdf"},
            )

        def verify(self, locator: str, expected_sha256: str, **kwargs: object) -> StorageObject:
            return StorageObject(locator=locator, version="v1", size_bytes=7)

    database = tmp_path / "ledger.sqlite"
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "Original invoice.pdf"
    source.write_bytes(b"invoice")
    adapter = Adapter()
    with LedgerDB.initialize(database) as db:
        document = db.upsert_document(document_type="expense_invoice", issued_on="2026-08-17", source_hash="e" * 64)
        registered = register_local_source_replica(
            db, document_id=str(document["document_id"]), source_path=source,
            media_type="application/pdf", storage_root=source_root,
        )
        db.upsert_storage_backend(
            backend_key="google_writer_rw", display_name="Writer", driver_key="google_drive",
            provider_key="google", access_mode="read_write", config={"root_folder_id": "default-root-id"}, credential_ref="file:/unused",
        )
        monkeypatch.setattr(reconcile_module, "adapter_for_backend", lambda backend: adapter)
        result = reconcile_file_to_backend(
            db,
            backend_key="google_writer_rw",
            file_id=str(registered["file"]["file_id"]),
            google_folder_id="selected-root-id",
            document_id=str(document["document_id"]),
        )

    assert result["created"] == 1
    assert result["archive_path"] == "unassigned/expense_invoice/Original invoice.pdf"
    assert adapter.kwargs == {
        "logical_file_id": registered["file"]["file_id"],
        "content_sha256": registered["file"]["content_sha256"],
        "folder_id": "selected-root-id",
        "folder_path": ("unassigned", "expense_invoice"),
        "display_name": "Original invoice.pdf",
    }


def test_google_file_reconcile_uses_default_root_with_period_type_and_attachment_name(
    tmp_path: Path, monkeypatch
) -> None:
    import autonomo_taxes.storage_reconcile as reconcile_module

    class Adapter(GoogleDriveStorageAdapter):
        def __init__(self) -> None:
            super().__init__(root_folder_id="default-root-id", token_file=Path("/unused"))
            self.kwargs: dict[str, object] | None = None

        def put_file(self, source: Path, **kwargs: object) -> StorageObject:
            self.kwargs = kwargs
            return StorageObject(locator="new-drive-file-id", version="v1", size_bytes=source.stat().st_size, metadata={})

        def verify(self, locator: str, expected_sha256: str, **kwargs: object) -> StorageObject:
            return StorageObject(locator=locator, version="v1", size_bytes=7)

    database = tmp_path / "ledger.sqlite"
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "Original invoice.pdf"
    source.write_bytes(b"invoice")
    adapter = Adapter()
    with LedgerDB.initialize(database) as db:
        document = db.upsert_document(document_type="expense_invoice", issued_on="2026-08-17", source_hash="f" * 64)
        registered = register_local_source_replica(
            db, document_id=str(document["document_id"]), source_path=source,
            media_type="application/pdf", storage_root=source_root,
        )
        db.upsert_storage_backend(
            backend_key="google_writer_rw", display_name="Writer", driver_key="google_drive",
            provider_key="google", access_mode="read_write", config={"root_folder_id": "default-root-id"}, credential_ref="file:/unused",
        )
        monkeypatch.setattr(reconcile_module, "adapter_for_backend", lambda backend: adapter)
        result = reconcile_file_to_backend(
            db, backend_key="google_writer_rw", file_id=str(registered["file"]["file_id"])
        )

    assert result["created"] == 1
    assert adapter.kwargs is not None
    assert adapter.kwargs["folder_id"] is None
    assert adapter.kwargs["folder_path"] == ("unassigned", "expense_invoice")
    assert adapter.kwargs["display_name"] == "Original invoice.pdf"


def test_google_reconcile_blocks_upload_when_known_google_replica_is_unverified(
    tmp_path: Path, monkeypatch
) -> None:
    import autonomo_taxes.storage_reconcile as reconcile_module

    class Adapter(GoogleDriveStorageAdapter):
        def __init__(self) -> None:
            super().__init__(root_folder_id="writer-root-id", token_file=Path("/unused"))

        def put_file(self, *args: object, **kwargs: object) -> StorageObject:
            raise AssertionError("must not upload while a known Google replica is degraded")

    database = tmp_path / "ledger.sqlite"
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "invoice.pdf"
    source.write_bytes(b"invoice")
    with LedgerDB.initialize(database) as db:
        document = db.upsert_document(document_type="expense_invoice", issued_on="2026-08-17", source_hash="1" * 64)
        registered = register_local_source_replica(
            db, document_id=str(document["document_id"]), source_path=source,
            media_type="application/pdf", storage_root=source_root,
        )
        archive = db.upsert_storage_backend(
            backend_key="google_archive_ro", display_name="Archive", driver_key="google_drive",
            provider_key="google", access_mode="read_only", config={"root_folder_id": "archive-root-id"}, credential_ref="file:/unused",
        )
        db.upsert_storage_backend(
            backend_key="google_writer_rw", display_name="Writer", driver_key="google_drive",
            provider_key="google", access_mode="read_write", config={"root_folder_id": "writer-root-id"}, credential_ref="file:/unused",
        )
        db.register_file_replica(
            file_id=str(registered["file"]["file_id"]), storage_backend_id=str(archive["storage_backend_id"]),
            provider_locator="known-drive-file-id", replica_status="corrupt", is_primary=False,
        )
        monkeypatch.setattr(reconcile_module, "adapter_for_backend", lambda backend: Adapter())
        result = reconcile_file_to_backend(
            db, backend_key="google_writer_rw", file_id=str(registered["file"]["file_id"])
        )

    assert result["failed"] == 1
    assert result["created"] == 0


def test_google_reconcile_verifies_existing_replica_with_its_resource_key(tmp_path: Path, monkeypatch) -> None:
    import autonomo_taxes.storage_reconcile as reconcile_module

    class Adapter(GoogleDriveStorageAdapter):
        def __init__(self) -> None:
            super().__init__(root_folder_id="writer-root-id", token_file=Path("/unused"))
            self.resource_key: str | None = None

        def verify(self, locator: str, expected_sha256: str, *, resource_key: str | None = None) -> StorageObject:
            self.resource_key = resource_key
            return StorageObject(locator=locator, version="v1", size_bytes=7)

    database = tmp_path / "ledger.sqlite"
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "invoice.pdf"
    source.write_bytes(b"invoice")
    adapter = Adapter()
    with LedgerDB.initialize(database) as db:
        document = db.upsert_document(document_type="expense_invoice", issued_on="2026-08-17", source_hash="2" * 64)
        registered = register_local_source_replica(
            db, document_id=str(document["document_id"]), source_path=source,
            media_type="application/pdf", storage_root=source_root,
        )
        writer = db.upsert_storage_backend(
            backend_key="google_writer_rw", display_name="Writer", driver_key="google_drive",
            provider_key="google", access_mode="read_write", config={"root_folder_id": "writer-root-id"}, credential_ref="file:/unused",
        )
        db.register_file_replica(
            file_id=str(registered["file"]["file_id"]), storage_backend_id=str(writer["storage_backend_id"]),
            provider_locator="existing-drive-file-id", is_primary=False,
            provider_metadata={"resource_key": "resource-key-id"}, last_verified_at="2026-08-18T00:00:00Z",
        )
        monkeypatch.setattr(reconcile_module, "adapter_for_backend", lambda backend: adapter)
        result = reconcile_file_to_backend(
            db, backend_key="google_writer_rw", file_id=str(registered["file"]["file_id"])
        )

    assert result["skipped"] == 1
    assert adapter.resource_key == "resource-key-id"
