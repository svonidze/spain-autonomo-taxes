from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import re
from typing import Any, Literal, Mapping, Protocol, Sequence
from urllib.parse import parse_qs, urlparse


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


@dataclass(frozen=True)
class ImportedGoogleDriveFile:
    """Verified bytes and immutable Drive identity resolved from a shared URL."""

    file: StorageObject
    content: bytes


_GOOGLE_DRIVE_FILE_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")


def parse_google_drive_file_id(url: str) -> str:
    """Return a Google Drive file id from common user-facing Drive URLs.

    Folder URLs are deliberately rejected: intake needs one immutable file, not
    a mutable collection.  The parser does not contact Google and therefore is
    safe to use while validating a form submission.
    """
    return _google_drive_url_context(url)[0]


def sanitize_google_drive_url(url: str) -> str:
    """Return a stable Drive URL without tracking or unrelated query values."""
    file_id, resource_key = _google_drive_url_context(url)
    suffix = f"&resourcekey={resource_key}" if resource_key else ""
    return "https://" + "drive.google.com/open?id=" + file_id + suffix


def _google_drive_url_context(url: str) -> tuple[str, str | None]:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"drive.google.com", "docs.google.com"}:
        raise StorageAdapterError("Expected an HTTPS Google Drive file URL")
    segments = [segment for segment in parsed.path.split("/") if segment]
    file_id: str | None = None
    if len(segments) >= 3 and segments[0] == "file" and segments[1] == "d":
        file_id = segments[2]
    elif len(segments) >= 3 and segments[0] in {"document", "spreadsheets", "presentation"} and segments[1] == "d":
        file_id = segments[2]
    elif segments and segments[0] in {"open", "uc"}:
        file_id = (parse_qs(parsed.query).get("id") or [None])[0]
    if not file_id or not _GOOGLE_DRIVE_FILE_ID.fullmatch(file_id):
        raise StorageAdapterError("Google Drive URL does not identify one file")
    resource_key = (parse_qs(parsed.query).get("resourcekey") or [None])[0]
    if resource_key is not None and not _GOOGLE_DRIVE_FILE_ID.fullmatch(resource_key):
        resource_key = None
    return file_id, resource_key


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
        folder_id: str | None = None,
        folder_path: Sequence[str] = (),
        display_name: str | None = None,
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

    def verify(
        self,
        locator: str,
        expected_sha256: str,
    ) -> StorageObject:
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
    """Google Drive backend keyed by fileId.

    ``credential_mode=service_account`` is intentionally read-only and is for
    adopting a pre-existing archive.  ``oauth`` has the narrow ``drive.file``
    scope and is the only mode that may create new Drive files.
    """

    def __init__(
        self,
        *,
        root_folder_id: str,
        token_file: Path,
        client_secret_file: Path | None = None,
        supports_all_drives: bool = False,
        credential_mode: Literal["oauth", "service_account"] = "oauth",
    ) -> None:
        self.root_folder_id = root_folder_id
        self.token_file = token_file
        self.client_secret_file = client_secret_file
        self.supports_all_drives = supports_all_drives
        if credential_mode not in {"oauth", "service_account"}:
            raise ValueError("credential_mode must be oauth or service_account")
        self.credential_mode = credential_mode
        self._service: Any | None = None

    def stat(self, locator: str, *, resource_key: str | None = None) -> StorageObject:
        try:
            request = self._files().get(
                fileId=locator,
                fields="id,name,parents,size,mimeType,md5Checksum,sha256Checksum,headRevisionId,webViewLink,trashed,resourceKey",
                supportsAllDrives=self.supports_all_drives,
            )
            if resource_key:
                request.uri += f"&resourceKey={resource_key}"
            payload = request.execute()
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
                "name": str(payload.get("name") or ""),
                "parent_ids": [str(value) for value in (payload.get("parents") or [])],
                "md5_checksum": str(payload.get("md5Checksum") or ""),
                "sha256_checksum": str(payload.get("sha256Checksum") or ""),
                "resource_key": str(payload.get("resourceKey") or resource_key or ""),
            },
        )

    def read_bytes(self, locator: str, *, resource_key: str | None = None) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload
        import io

        try:
            stream = io.BytesIO()
            request = self._files().get_media(fileId=locator, supportsAllDrives=self.supports_all_drives)
            if resource_key:
                request.uri += f"&resourceKey={resource_key}"
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
        folder_id: str | None = None,
        folder_path: tuple[str, ...] = (),
        display_name: str | None = None,
    ) -> StorageObject:
        if self.credential_mode != "oauth":
            raise StorageAdapterError("Service-account Google Drive archive is read-only")
        from googleapiclient.http import MediaFileUpload

        source_file = source.expanduser().resolve(strict=True)
        if sha256_file(source_file) != content_sha256:
            raise StorageObjectCorruptError("Source file does not match declared SHA-256")
        parent_id = self._upload_parent(folder_id=folder_id, folder_path=folder_path)
        existing = self._find_existing(logical_file_id, content_sha256, parent_id=parent_id)
        if existing is not None:
            return self.stat(existing)
        upload_name = _google_drive_filename(display_name or source_file.name)
        try:
            response = self._files().create(
                body={
                    # Preserve a human-recognisable source name.  The digest is
                    # immutable catalogue metadata rather than a Drive filename.
                    "name": upload_name,
                    "parents": [parent_id],
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
        uploaded = self.stat(str(response["id"]))
        metadata = dict(uploaded.metadata)
        metadata.update(
            {
                "upload_root_folder_id": folder_id or self.root_folder_id,
                "upload_parent_folder_id": parent_id,
                "archive_relative_path": "/".join((*folder_path, upload_name)),
            }
        )
        return StorageObject(
            locator=uploaded.locator,
            version=uploaded.version,
            size_bytes=uploaded.size_bytes,
            web_url=uploaded.web_url,
            metadata=metadata,
        )

    def import_url(
        self,
        url: str,
        *,
        expected_sha256: str | None = None,
        max_bytes: int | None = None,
    ) -> ImportedGoogleDriveFile:
        """Resolve a Drive URL, read it, and optionally prove its content hash."""
        locator, resource_key = _google_drive_url_context(url)
        item = self.stat(locator, resource_key=resource_key)
        if max_bytes is not None and item.size_bytes > max_bytes:
            raise StorageAdapterError("Google Drive file exceeds the configured intake size limit")
        content = self.read_bytes(locator, resource_key=resource_key)
        actual = hashlib.sha256(content).hexdigest()
        if expected_sha256 is not None and actual != expected_sha256.lower():
            raise StorageObjectCorruptError(
                f"Google Drive object SHA-256 mismatch: expected {expected_sha256}, got {actual}"
            )
        metadata = dict(item.metadata)
        metadata["content_sha256"] = actual
        metadata["submitted_source_url"] = sanitize_google_drive_url(url)
        if resource_key and not metadata.get("resource_key"):
            metadata["resource_key"] = resource_key
        return ImportedGoogleDriveFile(
            file=StorageObject(
                locator=item.locator,
                version=item.version,
                size_bytes=item.size_bytes,
                web_url=item.web_url or _canonical_google_drive_url(item.locator),
                metadata=metadata,
            ),
            content=content,
        )

    def verify(
        self,
        locator: str,
        expected_sha256: str,
        *,
        resource_key: str | None = None,
    ) -> StorageObject:
        payload = self.read_bytes(locator, resource_key=resource_key)
        actual = hashlib.sha256(payload).hexdigest()
        if actual != expected_sha256:
            raise StorageObjectCorruptError(
                f"Google Drive object SHA-256 mismatch: expected {expected_sha256}, got {actual}"
            )
        return self.stat(locator, resource_key=resource_key)

    def is_within_root(self, locator: str, item: StorageObject | None = None) -> bool:
        """Prove that a file is inside this backend's archive tree.

        Drive returns only direct parents, so walk folder parents rather than
        trusting a filename or an arbitrary file ID supplied by a caller.
        """
        current = item or self.stat(locator)
        pending = [str(value) for value in current.metadata.get("parent_ids", [])]
        visited = {locator}
        while pending:
            parent_id = pending.pop()
            if parent_id == self.root_folder_id:
                return True
            if parent_id in visited:
                continue
            visited.add(parent_id)
            if len(visited) > 100:
                raise StorageAdapterError("Google Drive parent hierarchy is unexpectedly deep")
            parent = self.stat(parent_id)
            pending.extend(str(value) for value in parent.metadata.get("parent_ids", []))
        return False

    def _upload_parent(self, *, folder_id: str | None, folder_path: Sequence[str]) -> str:
        root = folder_id or self.root_folder_id
        if not _GOOGLE_DRIVE_FILE_ID.fullmatch(root):
            raise StorageAdapterError("Google Drive destination folder id is invalid")
        parent = root
        for segment in folder_path:
            name = _google_drive_folder_name(segment)
            parent = self._find_or_create_folder(parent, name)
        return parent

    def _find_or_create_folder(self, parent_id: str, name: str) -> str:
        query = (
            "trashed = false and "
            "mimeType = 'application/vnd.google-apps.folder' and "
            f"'{_drive_query_quote(parent_id)}' in parents and "
            f"name = '{_drive_query_quote(name)}'"
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
            folders = response.get("files") or []
            if len(folders) > 1:
                raise StorageAdapterError(f"Multiple Google Drive folders match {name!r}")
            if folders:
                return str(folders[0]["id"])
            response = self._files().create(
                body={
                    "name": name,
                    "parents": [parent_id],
                    "mimeType": "application/vnd.google-apps.folder",
                },
                fields="id",
                supportsAllDrives=self.supports_all_drives,
            ).execute()
            return str(response["id"])
        except StorageAdapterError:
            raise
        except Exception as exc:
            raise StorageAdapterError(f"Google Drive folder setup failed for {name!r}") from exc

    def _find_existing(
        self,
        logical_file_id: str,
        content_sha256: str,
        *,
        parent_id: str,
    ) -> str | None:
        query = (
            "trashed = false and "
            f"'{_drive_query_quote(parent_id)}' in parents and "
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
            if self.credential_mode == "service_account":
                from google.oauth2 import service_account

                credentials = service_account.Credentials.from_service_account_file(
                    str(self.token_file),
                    scopes=["https://www.googleapis.com/auth/drive.readonly"],
                )
            else:
                credentials = Credentials.from_authorized_user_file(
                    str(self.token_file),
                    scopes=["https://www.googleapis.com/auth/drive.file"],
                )
                if credentials.expired and credentials.refresh_token:
                    from google.auth.transport.requests import Request

                    credentials.refresh(Request())
                    self.token_file.write_text(credentials.to_json(), encoding="utf-8")
                    self.token_file.chmod(0o600)
            # Service-account credentials obtain their short-lived access token
            # lazily when the first Drive request is executed.  ``valid`` is
            # therefore false before that request despite a usable key file.
            if self.credential_mode == "oauth" and not credentials.valid:
                raise StorageAdapterError("Google OAuth credential is expired or invalid")
            self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        return self._service.files()


def _canonical_google_drive_url(file_id: str) -> str:
    return "https://" + "drive.google.com/open?id=" + file_id


def _drive_query_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _google_drive_filename(value: str) -> str:
    name = Path(value).name.strip()
    if not name or name in {".", ".."}:
        raise StorageAdapterError("Google Drive upload requires a safe display name")
    return name


def _google_drive_folder_name(value: str) -> str:
    name = value.strip()
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise StorageAdapterError("Google Drive archive folder name is invalid")
    return name
