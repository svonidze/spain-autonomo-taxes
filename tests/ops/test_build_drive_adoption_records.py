from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from autonomo_taxes.ledger_db import LedgerDB
from scripts.imports import build_drive_adoption_records as record_builder


def test_build_records_uses_mount_path_then_source_book_fallback(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "ledger.sqlite"
    first_digest = sha256(b"first").hexdigest()
    second_digest = sha256(b"second").hexdigest()
    with LedgerDB.initialize(database) as db:
        mount = db.upsert_storage_backend(
            backend_key="drive_mount_ro",
            display_name="Drive mount",
            driver_key="filesystem",
            provider_key="google",
            access_mode="read_only",
            config={"root": str(tmp_path / "mount")},
        )
        for digest, filename, mounted in (
            (first_digest, "invoice.pdf", True),
            (second_digest, "book.xlsx", False),
        ):
            document = db.upsert_document(
                document_type="expense_invoice",
                issued_on="2026-07-01",
                period_key="2026-Q3",
                source_hash=digest,
            )
            file_row = db.upsert_file(
                content_sha256=digest,
                byte_size=1,
                media_type="application/pdf",
            )
            db.attach_file_to_document(
                document_id=document["document_id"],
                file_id=file_row["file_id"],
                attachment_role="source",
            )
            if mounted:
                db.register_file_replica(
                    file_id=file_row["file_id"],
                    storage_backend_id=mount["storage_backend_id"],
                    provider_locator=f"archive/{filename}",
                    is_primary=True,
                    last_verified_at="2026-08-18T00:00:00Z",
                )
            db.connection.execute(
                "UPDATE documents SET source_path = ? WHERE document_id = ?",
                (str(tmp_path / "source" / filename), document["document_id"]),
            )
            db.connection.commit()

    calls: list[str] = []

    def fake_stat(**kwargs):
        path = kwargs["path"]
        calls.append(path)
        return {"ID": f"id-{len(calls)}", "Name": Path(path).name, "IsDir": False}

    monkeypatch.setattr(record_builder, "_rclone_stat", fake_stat)
    records = record_builder.build_records(
        database=database,
        rclone_binary="rclone",
        rclone_config=tmp_path / "rclone.conf",
        remote="gdrive-espana",
        mount_backend_key="drive_mount_ro",
        source_books_prefix="archive/source_books",
    )

    assert set(calls) == {"archive/source_books/book.xlsx", "archive/invoice.pdf"}
    by_digest = {record["content_sha256"]: record for record in records}
    assert by_digest[first_digest]["archive_path"] == "archive/invoice.pdf"
    assert by_digest[second_digest]["archive_path"] == "archive/source_books/book.xlsx"
