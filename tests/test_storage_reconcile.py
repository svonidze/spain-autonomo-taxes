from __future__ import annotations

from pathlib import Path

from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.storage_reconcile import reconcile_backend
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
