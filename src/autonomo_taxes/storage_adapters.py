from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping, Protocol


class StorageAdapterError(RuntimeError):
    """A configured storage backend could not complete an operation."""


class StorageObjectMissingError(StorageAdapterError):
    pass


class StorageObjectCorruptError(StorageAdapterError):
    pass


@dataclass(frozen=True)
class StorageObject:
    """Provider-neutral information about one physical file replica."""

    locator: str
    version: str | None
    size_bytes: int
    web_url: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class StorageAdapter(Protocol):
    """The narrow provider contract used by replica registration and reads."""

    def stat(self, locator: str) -> StorageObject: ...

    def read_bytes(self, locator: str) -> bytes: ...

    def put_file(
        self,
        source: Path,
        *,
        logical_file_id: str,
        content_sha256: str,
    ) -> StorageObject: ...


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, expected_sha256: str) -> int:
    if not path.is_file():
        raise StorageObjectMissingError(f"Storage object does not exist: {path}")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise StorageObjectCorruptError(
            f"Storage object SHA-256 mismatch: expected {expected_sha256}, got {actual}"
        )
    return path.stat().st_size


class FilesystemStorageAdapter:
    """A rooted local filesystem backend using provider-relative locators."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def stat(self, locator: str) -> StorageObject:
        path = self._path(locator)
        if not path.is_file():
            raise StorageObjectMissingError(f"Storage object does not exist: {locator}")
        return StorageObject(locator=locator, version=str(path.stat().st_mtime_ns), size_bytes=path.stat().st_size)

    def read_bytes(self, locator: str) -> bytes:
        path = self._path(locator)
        if not path.is_file():
            raise StorageObjectMissingError(f"Storage object does not exist: {locator}")
        return path.read_bytes()

    def put_file(
        self,
        source: Path,
        *,
        logical_file_id: str,
        content_sha256: str,
    ) -> StorageObject:
        source_file = source.expanduser().resolve(strict=True)
        if sha256_file(source_file) != content_sha256:
            raise StorageObjectCorruptError("Source file does not match declared SHA-256")
        locator = f"sha256/{content_sha256}"
        destination = self._path(locator)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.exists():
            verify_file(destination, content_sha256)
        else:
            temporary = destination.with_name(f".{destination.name}.tmp")
            shutil.copy2(source_file, temporary)
            verify_file(temporary, content_sha256)
            temporary.replace(destination)
        return StorageObject(
            locator=locator,
            version=str(destination.stat().st_mtime_ns),
            size_bytes=destination.stat().st_size,
            metadata={"logical_file_id": logical_file_id},
        )

    def _path(self, locator: str) -> Path:
        candidate = (self.root / locator).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise StorageAdapterError("Storage locator escapes configured root") from exc
        return candidate


class RcloneStorageAdapter:
    """An rclone remote, normally a crypt remote rooted at one raw S3 prefix."""

    def __init__(
        self,
        remote: str,
        *,
        rclone_binary: str = "rclone",
        config_path: Path | None = None,
        require_crypt: bool = True,
    ) -> None:
        if not remote.endswith(":"):
            remote = f"{remote}:"
        if require_crypt and "crypt" not in remote.casefold():
            raise StorageAdapterError("Rclone remote must be a configured crypt remote")
        if require_crypt and config_path is None:
            raise StorageAdapterError("Crypt rclone backends require an explicit private config path")
        self.remote = remote
        self.rclone_binary = rclone_binary
        self.config_path = config_path.expanduser().resolve() if config_path else None
        if require_crypt:
            self._assert_crypt_remote()

    def stat(self, locator: str) -> StorageObject:
        result = self._run("lsjson", "--stat", self._remote_path(locator))
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise StorageAdapterError("rclone returned invalid lsjson output") from exc
        if not isinstance(payload, Mapping) or payload.get("IsDir"):
            raise StorageObjectMissingError(f"Storage object does not exist: {locator}")
        return StorageObject(
            locator=locator,
            version=str(payload.get("ModTime") or "") or None,
            size_bytes=int(payload.get("Size") or 0),
            metadata={"rclone_id": str(payload.get("ID") or "")},
        )

    def read_bytes(self, locator: str) -> bytes:
        result = self._run("cat", self._remote_path(locator), text=False)
        return result.stdout

    def put_file(
        self,
        source: Path,
        *,
        logical_file_id: str,
        content_sha256: str,
    ) -> StorageObject:
        source_file = source.expanduser().resolve(strict=True)
        if sha256_file(source_file) != content_sha256:
            raise StorageObjectCorruptError("Source file does not match declared SHA-256")
        locator = f"sha256/{content_sha256}"
        try:
            current = self.stat(locator)
        except StorageObjectMissingError:
            self._run("copyto", str(source_file), self._remote_path(locator))
            current = self.stat(locator)
        if current.size_bytes <= 0 and source_file.stat().st_size > 0:
            raise StorageObjectCorruptError("rclone uploaded an unexpected empty object")
        return current

    def verify(self, locator: str, expected_sha256: str) -> StorageObject:
        payload = self.read_bytes(locator)
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected_sha256:
            raise StorageObjectCorruptError(
                f"Storage object SHA-256 mismatch: expected {expected_sha256}, got {actual}"
            )
        return self.stat(locator)

    def _remote_path(self, locator: str) -> str:
        if locator.startswith("/") or ".." in Path(locator).parts:
            raise StorageAdapterError("Storage locator must be relative")
        return f"{self.remote}{locator}"

    def _assert_crypt_remote(self) -> None:
        name = self.remote[:-1]
        result = self._run("config", "show", name)
        if "type = crypt" not in str(result.stdout):
            raise StorageAdapterError(f"Rclone remote is not configured as crypt: {name}")

    def _run(self, *args: str, text: bool = True) -> subprocess.CompletedProcess[Any]:
        try:
            command = [self.rclone_binary]
            if self.config_path is not None:
                command.extend(("--config", str(self.config_path)))
            command.extend(args)
            return subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=text,
            )
        except FileNotFoundError as exc:
            raise StorageAdapterError("rclone executable is not installed") from exc
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr if isinstance(exc.stderr, str) else "rclone command failed"
            raise StorageAdapterError(str(detail).strip() or "rclone command failed") from exc


class GoogleDriveStorageAdapter:
    """Google Drive backend keyed by fileId and authenticated user OAuth credentials."""

    def __init__(
        self,
        *,
        root_folder_id: str,
        token_file: Path,
        client_secret_file: Path | None = None,
        supports_all_drives: bool = False,
    ) -> None:
        self.root_folder_id = root_folder_id
        self.token_file = token_file
        self.client_secret_file = client_secret_file
        self.supports_all_drives = supports_all_drives
        self._service: Any | None = None

    def stat(self, locator: str) -> StorageObject:
        try:
            payload = self._files().get(
                fileId=locator,
                fields="id,size,mimeType,md5Checksum,sha256Checksum,headRevisionId,webViewLink,trashed,resourceKey",
                supportsAllDrives=self.supports_all_drives,
            ).execute()
        except Exception as exc:
            raise StorageAdapterError(f"Google Drive stat failed for {locator}") from exc
        if payload.get("trashed"):
            raise StorageObjectMissingError(f"Google Drive object is trashed: {locator}")
        return StorageObject(
            locator=str(payload["id"]),
            version=str(payload.get("headRevisionId") or "") or None,
            size_bytes=int(payload.get("size") or 0),
            web_url=str(payload.get("webViewLink") or "") or None,
            metadata={
                "mime_type": str(payload.get("mimeType") or ""),
                "md5_checksum": str(payload.get("md5Checksum") or ""),
                "sha256_checksum": str(payload.get("sha256Checksum") or ""),
                "resource_key": str(payload.get("resourceKey") or ""),
            },
        )

    def read_bytes(self, locator: str) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload
        import io

        try:
            stream = io.BytesIO()
            request = self._files().get_media(fileId=locator, supportsAllDrives=self.supports_all_drives)
            downloader = MediaIoBaseDownload(stream, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            return stream.getvalue()
        except Exception as exc:
            raise StorageAdapterError(f"Google Drive read failed for {locator}") from exc

    def put_file(
        self,
        source: Path,
        *,
        logical_file_id: str,
        content_sha256: str,
    ) -> StorageObject:
        from googleapiclient.http import MediaFileUpload

        source_file = source.expanduser().resolve(strict=True)
        if sha256_file(source_file) != content_sha256:
            raise StorageObjectCorruptError("Source file does not match declared SHA-256")
        existing = self._find_existing(logical_file_id, content_sha256)
        if existing is not None:
            return self.stat(existing)
        try:
            response = self._files().create(
                body={
                    "name": f"sha256-{content_sha256}",
                    "parents": [self.root_folder_id],
                    "appProperties": {
                        "autonomo_file_id": logical_file_id,
                        "autonomo_content_sha256": content_sha256,
                    },
                },
                media_body=MediaFileUpload(str(source_file), resumable=True),
                fields="id",
                supportsAllDrives=self.supports_all_drives,
            ).execute()
        except Exception as exc:
            raise StorageAdapterError("Google Drive upload failed") from exc
        return self.stat(str(response["id"]))

    def verify(self, locator: str, expected_sha256: str) -> StorageObject:
        payload = self.read_bytes(locator)
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected_sha256:
            raise StorageObjectCorruptError(
                f"Google Drive object SHA-256 mismatch: expected {expected_sha256}, got {actual}"
            )
        return self.stat(locator)

    def _find_existing(self, logical_file_id: str, content_sha256: str) -> str | None:
        query = (
            "trashed = false and "
            f"appProperties has {{ key='autonomo_file_id' and value='{logical_file_id}' }} and "
            f"appProperties has {{ key='autonomo_content_sha256' and value='{content_sha256}' }}"
        )
        try:
            response = self._files().list(
                q=query,
                spaces="drive",
                fields="files(id)",
                pageSize=2,
                supportsAllDrives=self.supports_all_drives,
                includeItemsFromAllDrives=self.supports_all_drives,
            ).execute()
        except Exception as exc:
            raise StorageAdapterError("Google Drive lookup failed") from exc
        files = response.get("files") or []
        if len(files) > 1:
            raise StorageAdapterError("Multiple Google Drive objects match one logical file")
        return str(files[0]["id"]) if files else None

    def _files(self) -> Any:
        if self._service is None:
            try:
                from google.oauth2.credentials import Credentials
                from googleapiclient.discovery import build
            except ImportError as exc:
                raise StorageAdapterError(
                    "Google Drive support requires google-api-python-client and google-auth-oauthlib"
                ) from exc
            credentials = Credentials.from_authorized_user_file(
                str(self.token_file),
                scopes=["https://www.googleapis.com/auth/drive.file"],
            )
            if credentials.expired and credentials.refresh_token:
                from google.auth.transport.requests import Request

                credentials.refresh(Request())
                self.token_file.write_text(credentials.to_json(), encoding="utf-8")
                self.token_file.chmod(0o600)
            if not credentials.valid:
                raise StorageAdapterError("Google OAuth credential is expired or invalid")
            self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        return self._service.files()
