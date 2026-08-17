from __future__ import annotations

from pathlib import Path

import pytest

from autonomo_taxes.storage_adapters import (
    FilesystemStorageAdapter,
    RcloneStorageAdapter,
    StorageAdapterError,
    StorageObjectCorruptError,
    sha256_file,
    verify_file,
)


def test_filesystem_adapter_content_addresses_and_verifies_uploaded_bytes(tmp_path: Path) -> None:
    source = tmp_path / "invoice.pdf"
    source.write_bytes(b"invoice content")
    digest = sha256_file(source)
    adapter = FilesystemStorageAdapter(tmp_path / "replicas")

    replica = adapter.put_file(
        source,
        logical_file_id="file-1",
        content_sha256=digest,
    )
    assert replica.locator == f"sha256/{digest}"
    assert adapter.read_bytes(replica.locator) == b"invoice content"
    assert adapter.stat(replica.locator).size_bytes == len(b"invoice content")

    duplicate = adapter.put_file(
        source,
        logical_file_id="file-1",
        content_sha256=digest,
    )
    assert duplicate.locator == replica.locator


def test_filesystem_adapter_rejects_escape_and_corruption(tmp_path: Path) -> None:
    adapter = FilesystemStorageAdapter(tmp_path / "replicas")
    with pytest.raises(StorageAdapterError, match="escapes"):
        adapter.read_bytes("../outside")

    path = tmp_path / "content.bin"
    path.write_bytes(b"actual")
    with pytest.raises(StorageObjectCorruptError, match="mismatch"):
        verify_file(path, "0" * 64)


def test_rclone_adapter_requires_a_crypt_remote() -> None:
    with pytest.raises(StorageAdapterError, match="crypt remote"):
        RcloneStorageAdapter("plain-s3:")
