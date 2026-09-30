from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.storage_adapters import StorageAdapterError
from autonomo_taxes.storage_service import resolve_verified_replica
import autonomo_taxes.storage_reconcile as storage_reconcile


class _Reader:
    def __init__(self, objects: dict[str, bytes], calls: list[str]) -> None:
        self.objects = objects
        self.calls = calls

    def read_bytes(self, locator: str) -> bytes:
        self.calls.append(locator)
        try:
            return self.objects[locator]
        except KeyError as exc:
            raise StorageAdapterError("object missing") from exc


def _source_catalogue(tmp_path: Path) -> tuple[LedgerDB, str, str, dict[str, str]]:
    database = tmp_path / "ledger.sqlite"
    db = LedgerDB.initialize(database)
    document = db.upsert_document(
        document_type="expense_invoice",
        issued_on="2026-08-17",
        source_hash="a" * 64,
    )
    content = b"original invoice bytes"
    file_row = db.upsert_file(
        content_sha256=sha256(content).hexdigest(),
        byte_size=len(content),
        media_type="application/pdf",
    )
    db.attach_file_to_document(
        document_id=str(document["document_id"]),
        file_id=str(file_row["file_id"]),
        attachment_role="source",
        display_name="invoice.pdf",
    )
    primary = db.upsert_storage_backend(
        backend_key="primary",
        display_name="Primary",
        driver_key="google_drive",
        provider_key="google",
        access_mode="read_only",
        config={"root_folder_id": "root", "credential_mode": "service_account"},
        credential_ref="file:/private/reader.json",
        read_priority=10,
    )
    mirror = db.upsert_storage_backend(
        backend_key="mirror",
        display_name="Mirror",
        driver_key="s3",
        provider_key="yandex_s3",
        access_mode="read_only",
        config={"rclone_remote": "mirror-crypt:", "crypt_config_fingerprint": "fingerprint"},
        credential_ref="file:/private/rclone.conf",
        read_priority=20,
    )
    return db, str(document["document_id"]), content.decode(), {
        "primary": str(primary["storage_backend_id"]),
        "mirror": str(mirror["storage_backend_id"]),
        "file": str(file_row["file_id"]),
    }


def test_remote_reader_fails_over_and_caches_verified_mirror(tmp_path: Path, monkeypatch) -> None:
    db, document_id, expected, ids = _source_catalogue(tmp_path)
    db.register_file_replica(
        file_id=ids["file"], storage_backend_id=ids["primary"], provider_locator="primary-file",
        is_primary=True, last_verified_at="2026-08-17T00:00:00Z",
    )
    mirror = db.register_file_replica(
        file_id=ids["file"], storage_backend_id=ids["mirror"], provider_locator="mirror-file",
        last_verified_at="2026-08-17T00:00:00Z",
    )
    calls: list[str] = []
    readers = {
        "primary": _Reader({"primary-file": b"tampered"}, calls),
        "mirror": _Reader({"mirror-file": expected.encode()}, calls),
    }
    monkeypatch.setattr(
        storage_reconcile,
        "adapter_for_backend",
        lambda backend: readers[str(backend["backend_key"])],
    )

    resolved = resolve_verified_replica(
        db.connection, document_id=document_id, cache_root=tmp_path / "private-cache"
    )

    assert resolved is not None
    assert resolved.replica_id == mirror["file_replica_id"]
    assert resolved.media_type == "application/pdf"
    assert resolved.path.read_bytes() == expected.encode()
    assert resolved.path.parent.name == "sha256"
    assert resolved.path.stat().st_mode & 0o777 == 0o600
    assert calls == ["primary-file", "mirror-file"]
    db.close()


def test_remote_reader_skips_retired_replicas(tmp_path: Path, monkeypatch) -> None:
    db, document_id, expected, ids = _source_catalogue(tmp_path)
    db.register_file_replica(
        file_id=ids["file"], storage_backend_id=ids["primary"], provider_locator="retired-file",
        replica_status="retired", last_verified_at=None,
    )
    mirror = db.register_file_replica(
        file_id=ids["file"], storage_backend_id=ids["mirror"], provider_locator="mirror-file",
        is_primary=True, last_verified_at="2026-08-17T00:00:00Z",
    )
    calls: list[str] = []
    monkeypatch.setattr(
        storage_reconcile,
        "adapter_for_backend",
        lambda _backend: _Reader({"mirror-file": expected.encode()}, calls),
    )
    cache_root = tmp_path / "private-cache"

    first = resolve_verified_replica(db.connection, document_id=document_id, cache_root=cache_root)

    assert first is not None
    assert first.replica_id == mirror["file_replica_id"]
    assert calls == ["mirror-file"]
    db.close()


def test_remote_reader_does_not_attribute_cached_bytes_to_trashed_primary(tmp_path: Path, monkeypatch) -> None:
    db, document_id, expected, ids = _source_catalogue(tmp_path)
    primary = db.register_file_replica(
        file_id=ids["file"], storage_backend_id=ids["primary"], provider_locator="primary-file",
        is_primary=True, last_verified_at="2026-08-17T00:00:00Z",
    )
    mirror = db.register_file_replica(
        file_id=ids["file"], storage_backend_id=ids["mirror"], provider_locator="mirror-file",
        last_verified_at="2026-08-17T00:00:00Z",
    )
    calls: list[str] = []
    primary_objects = {"primary-file": expected.encode()}
    readers = {
        "primary": _Reader(primary_objects, calls),
        "mirror": _Reader({"mirror-file": expected.encode()}, calls),
    }
    monkeypatch.setattr(
        storage_reconcile,
        "adapter_for_backend",
        lambda backend: readers[str(backend["backend_key"])],
    )
    cache_root = tmp_path / "private-cache"

    first = resolve_verified_replica(db.connection, document_id=document_id, cache_root=cache_root)
    primary_objects.clear()  # Simulate Drive trashing the object after the cache is populated.
    second = resolve_verified_replica(db.connection, document_id=document_id, cache_root=cache_root)

    assert first is not None and first.replica_id == primary["file_replica_id"]
    assert second is not None and second.replica_id == mirror["file_replica_id"]
    assert second.path == first.path
    assert calls == ["primary-file", "primary-file", "mirror-file"]
    db.close()
