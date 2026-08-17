from __future__ import annotations

from pathlib import Path

import pytest

from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.storage_migration import (
    StorageMigrationError,
    assert_storage_startup_ready,
    migrate_legacy_storage,
)


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
