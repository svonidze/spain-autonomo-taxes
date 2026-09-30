from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autonomo_taxes.storage_adapters import (
    FilesystemStorageAdapter,
    GoogleDriveStorageAdapter,
    RcloneStorageAdapter,
    StorageAdapterError,
    StorageObjectCorruptError,
    StorageObject,
    sha256_file,
    verify_file,
    parse_google_drive_file_id,
    sanitize_google_drive_url,
)


DRIVE_URL = "https://" + "drive.google.com"
DOCS_URL = "https://" + "docs.google.com"


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


def test_rclone_verify_uses_only_rclone_locator_methods(monkeypatch) -> None:
    adapter = object.__new__(RcloneStorageAdapter)
    payload = b"verified mirror"
    digest = sha256_file_from_bytes(payload)
    monkeypatch.setattr(adapter, "read_bytes", lambda locator: payload)
    monkeypatch.setattr(
        adapter,
        "stat",
        lambda locator: StorageObject(locator=locator, version="v1", size_bytes=len(payload)),
    )

    verified = adapter.verify("sha256/object", digest)

    assert verified.locator == "sha256/object"


@pytest.mark.parametrize(
    "url,file_id",
    [
        (f"{DRIVE_URL}/file/d/abcDEF_123-456/view?usp=drive_link", "abcDEF_123-456"),
        (f"{DRIVE_URL}/open?id=abcDEF_123-456", "abcDEF_123-456"),
        (f"{DRIVE_URL}/uc?id=abcDEF_123-456&export=download", "abcDEF_123-456"),
        (f"{DOCS_URL}/document/d/abcDEF_123-456/edit", "abcDEF_123-456"),
    ],
)
def test_parse_google_drive_file_id(url: str, file_id: str) -> None:
    assert parse_google_drive_file_id(url) == file_id


def test_parse_google_drive_file_id_rejects_folder_and_other_hosts() -> None:
    with pytest.raises(StorageAdapterError):
        parse_google_drive_file_id(f"{DRIVE_URL}/drive/folders/abcDEF_123-456")
    with pytest.raises(StorageAdapterError):
        parse_google_drive_file_id("https://example.com/file/d/abcDEF_123-456")


def test_sanitize_google_drive_url_preserves_only_file_identity_and_resource_key() -> None:
    assert sanitize_google_drive_url(
        f"{DRIVE_URL}/file/d/abcDEF_123-456/view?usp=drive_link&resourcekey=key_123-456"
    ) == f"{DRIVE_URL}/open?id=abcDEF_123-456&resourcekey=key_123-456"


def test_google_service_account_is_read_only(tmp_path: Path) -> None:
    adapter = GoogleDriveStorageAdapter(
        root_folder_id="folder-id",
        token_file=tmp_path / "service-account.json",
        credential_mode="service_account",
    )
    source = tmp_path / "invoice.pdf"
    source.write_bytes(b"invoice")
    with pytest.raises(StorageAdapterError, match="read-only"):
        adapter.put_file(source, logical_file_id="logical", content_sha256=sha256_file(source))


def test_google_upload_parent_reuses_then_creates_period_type_folders(tmp_path: Path) -> None:
    class Request:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def execute(self) -> dict[str, object]:
            return self.payload

    class Files:
        def __init__(self) -> None:
            self.queries: list[str] = []
            self.created: list[dict[str, object]] = []
            self.responses = [{"files": [{"id": "period-folder-id"}]}, {"files": []}, {"id": "type-folder-id"}]

        def list(self, **kwargs: object) -> Request:
            self.queries.append(str(kwargs["q"]))
            return Request(self.responses.pop(0))

        def create(self, **kwargs: object) -> Request:
            self.created.append(dict(kwargs["body"]))
            return Request(self.responses.pop(0))

    class Service:
        def __init__(self, files: Files) -> None:
            self.value = files

        def files(self) -> Files:
            return self.value

    adapter = GoogleDriveStorageAdapter(root_folder_id="default-root-id", token_file=tmp_path / "token.json")
    files = Files()
    adapter._service = Service(files)

    assert adapter._upload_parent(folder_id="selected-root-id", folder_path=("2026-Q3", "expense_invoice")) == "type-folder-id"
    assert "'selected-root-id' in parents" in files.queries[0]
    assert "'period-folder-id' in parents" in files.queries[1]
    assert files.created == [{
        "name": "expense_invoice", "parents": ["period-folder-id"],
        "mimeType": "application/vnd.google-apps.folder",
    }]


def test_google_upload_accepts_selected_folder_and_attachment_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class Request:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def execute(self) -> dict[str, object]:
            return self.payload

    class Files:
        def __init__(self) -> None:
            self.body: dict[str, object] | None = None

        def create(self, **kwargs: object) -> Request:
            self.body = dict(kwargs["body"])
            return Request({"id": "uploaded-file-id"})

    adapter = GoogleDriveStorageAdapter(root_folder_id="default-root", token_file=tmp_path / "token.json")
    files = Files()
    adapter._service = SimpleNamespace(files=lambda: files)
    monkeypatch.setattr(adapter, "_upload_parent", lambda **_kwargs: "period-type-folder")
    monkeypatch.setattr(adapter, "_find_existing", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        adapter,
        "stat",
        lambda locator: StorageObject(locator=locator, version="v1", size_bytes=7, metadata={}),
    )
    source = tmp_path / "temporary-name.pdf"
    source.write_bytes(b"content")

    adapter.put_file(
        source,
        logical_file_id="catalogue-file-id",
        content_sha256=sha256_file(source),
        folder_id="selected-folder-id",
        folder_path=("2026-Q3", "expense_invoice"),
        display_name="human-invoice.pdf",
    )

    assert files.body is not None
    assert files.body["name"] == "human-invoice.pdf"
    assert files.body["parents"] == ["period-type-folder"]


def sha256_file_from_bytes(value: bytes) -> str:
    from hashlib import sha256

    return sha256(value).hexdigest()
