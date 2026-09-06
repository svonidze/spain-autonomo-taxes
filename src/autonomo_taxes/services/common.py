"""Shared validation and error contracts, independent of HTTP/UI."""

from __future__ import annotations
from datetime import date
import hashlib
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import UUID

QUARTER_RE = re.compile(r"^\d{4}-Q[1-4]$")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{32,36}$")
UPLOAD_KINDS = {"expense_invoice", "income_invoice"}
INTAKE_FIELD_NAMES = {
    "defer_counterparty",
    "period",
    "kind",
    "issued_on",
    "document_number",
    "counterparty_name",
    "gross",
    "taxable_base",
    "vat",
    "currency",
}
GOOGLE_DRIVE_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,256}$")
UPLOAD_SUFFIXES = {
    ".bmp",
    ".csv",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".tif",
    ".tiff",
    ".txt",
    ".webp",
}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_GOOGLE_DRIVE_URL_BYTES = 2048
_POSTING_BATCH_LOCK = threading.Lock()


class ServiceError(ValueError):
    pass


class ServiceApiError(ServiceError):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        current: Mapping[str, Any] | None = None,
        message_code: str | None = None,
        params: Mapping[str, Any] | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.current = current
        self.message_code = message_code
        self.params = params
        self.field = field


def _validated_counterparty_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ServiceApiError(
            400,
            "invalid_counterparty_id",
            "Counterparty ID must be a UUID",
        ) from exc


def _counterparty_api_state(
    connection: sqlite3.Connection, counterparty_id: str
) -> dict[str, Any]:
    row = connection.execute(
        """SELECT counterparty_id, display_name, row_version, name_is_manual
           FROM counterparties WHERE counterparty_id = ?""",
        (counterparty_id,),
    ).fetchone()
    if row is None:
        raise ServiceApiError(404, "counterparty_not_found", "Counterparty not found")
    return {**dict(row), "name_is_manual": bool(row["name_is_manual"])}


class PostingOperationError(ServiceError):
    def __init__(self, payload: Mapping[str, Any]) -> None:
        status = str(payload.get("status") or "").strip().lower()
        detail = (
            str(payload.get("message") or payload.get("error") or status or "CLI error")
        ).strip()
        super().__init__(detail)
        self.payload = dict(payload)
        self.status = status


def _validate_period(value: str) -> str:
    period = value.strip().upper()
    if not QUARTER_RE.fullmatch(period):
        raise ServiceError("Quarter must use YYYY-QN")
    return period


def _quarter_key(value: date) -> str:
    return f"{value.year}-Q{((value.month - 1) // 3) + 1}"


def _current_or_latest_period(period_keys: list[str]) -> str | None:
    current = _quarter_key(date.today())
    if current in period_keys:
        return current
    return period_keys[0] if period_keys else None


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _empty_scope() -> dict[str, Any]:
    return {
        "transaction_count": 0,
        "income_transaction_count": 0,
        "expense_transaction_count": 0,
        "income_eur": "0.00",
        "expense_gross_eur": "0.00",
        "deductible_irpf_eur": "0.00",
        "deductible_vat_eur": "0.00",
    }


def _review_summary(rows: list[Mapping[str, Any]]) -> dict[str, int]:
    summary = {"needs_review": 0, "ready": 0, "later": 0, "blocked": 0}
    for row in rows:
        state = (row.get("ui_context") or {}).get("state", "blocked")
        key = (
            "later" if state == "deferred" else state if state in summary else "blocked"
        )
        summary[key] += 1
    return summary


def _minor_to_text(value: int | None) -> str | None:
    if value is None:
        return None
    sign = "-" if value < 0 else ""
    absolute = abs(int(value))
    return f"{sign}{absolute // 100}.{absolute % 100:02d}"


def _ratio_percent(value: float | None) -> str | None:
    return f"{value * 100:.2f}" if value is not None else None


def _basis_points_percent(value: int | None) -> str | None:
    return f"{value / 100:.2f}" if value is not None else None


def _posting_preview_summary(preview: Mapping[str, Any]) -> dict[str, Any]:
    summary = preview.get("summary")
    if isinstance(summary, Mapping):
        return dict(summary)
    counts = preview.get("counts")
    if isinstance(counts, Mapping):
        return dict(counts)
    ready = preview.get("ready")
    deferred = preview.get("deferred")
    blocked = preview.get("blocked")
    if (
        isinstance(ready, list)
        or isinstance(deferred, list)
        or isinstance(blocked, list)
    ):
        return {
            "ready_to_post": len(ready) if isinstance(ready, list) else 0,
            "deferred": len(deferred) if isinstance(deferred, list) else 0,
            "blocked": len(blocked) if isinstance(blocked, list) else 0,
        }
    items = preview.get("items")
    if isinstance(items, list):
        return {"items": len(items)}
    return {}


def _store_upload(
    inbox_root: Path,
    *,
    period: str,
    kind: str,
    filename: str,
    content: bytes,
) -> Path:
    safe_name = _safe_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in UPLOAD_SUFFIXES:
        raise ServiceError(f"Unsupported document type: {suffix or '<none>'}")
    target_dir = (inbox_root / period / kind).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = (target_dir / safe_name).resolve()
    if not _is_relative_to(target, target_dir):
        raise ServiceError("Upload filename escapes the Inbox")
    digest = hashlib.sha256(content).hexdigest()
    if target.exists():
        if _file_sha256(target) == digest:
            return target
        target = target.with_name(f"{target.stem}-{digest[:8]}{target.suffix.lower()}")
    try:
        with target.open("xb") as handle:
            handle.write(content)
    except FileExistsError:
        if _file_sha256(target) != digest:
            raise ServiceError(f"Upload target already exists: {target.name}")
    if _file_sha256(target) != digest:
        target.unlink(missing_ok=True)
        raise ServiceError("Uploaded file failed SHA-256 verification")
    return target


def _safe_filename(value: str) -> str:
    name = Path(value.replace("\\", "/")).name
    name = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._ ()-]+", "_", name).strip(" .")
    if not name:
        raise ServiceError("Uploaded file has no usable filename")
    return name[:180]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _exact_object_fields(
    value: Mapping[str, Any],
    expected: set[str],
    label: str,
) -> None:
    missing = sorted(expected - set(value))
    unexpected = sorted(set(value) - expected)
    if not missing and not unexpected:
        return
    details = []
    if missing:
        details.append("missing: " + ", ".join(missing))
    if unexpected:
        details.append("unexpected: " + ", ".join(unexpected))
    raise ServiceApiError(
        400,
        "invalid_request",
        f"Invalid {label} (" + "; ".join(details) + ")",
    )


def normalize_google_drive_url(value: str) -> tuple[str, str]:
    """Return a canonical Drive open URL and its file ID.

    This deliberately accepts only documented Drive/Docs file URL shapes.  It
    rejects arbitrary HTTPS URLs so the intake endpoint cannot become an SSRF
    proxy or persist a URL containing an access token.
    """
    raw = str(value).strip()
    if not raw or len(raw.encode("utf-8")) > MAX_GOOGLE_DRIVE_URL_BYTES:
        raise ServiceError("Google Drive URL is invalid")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        raise ServiceError("Google Drive URL is invalid")
    file_id: str | None = None
    path_parts = [part for part in parsed.path.split("/") if part]
    if host == "drive.google.com":
        if len(path_parts) >= 3 and path_parts[:2] == ["file", "d"]:
            file_id = path_parts[2]
        elif path_parts and path_parts[0] in {"open", "uc"}:
            values = parse_qs(parsed.query, keep_blank_values=True)
            ids = values.get("id", [])
            if len(ids) == 1:
                file_id = ids[0]
    elif host == "docs.google.com":
        if (
            len(path_parts) >= 3
            and path_parts[0]
            in {
                "document",
                "spreadsheets",
                "presentation",
                "drawings",
            }
            and path_parts[1] == "d"
        ):
            file_id = path_parts[2]
    if not file_id or not GOOGLE_DRIVE_FILE_ID_RE.fullmatch(file_id):
        raise ServiceError("Google Drive URL is invalid")
    resource_keys = parse_qs(parsed.query, keep_blank_values=True).get(
        "resourcekey", []
    )
    if len(resource_keys) > 1 or (
        resource_keys and not GOOGLE_DRIVE_FILE_ID_RE.fullmatch(resource_keys[0])
    ):
        raise ServiceError("Google Drive URL is invalid")
    query = {"id": file_id}
    if resource_keys:
        query["resourcekey"] = resource_keys[0]
    return ("https://" + "drive.google.com/open?" + urlencode(query), file_id)


def _validated_intake_fields(value: Mapping[str, Any]) -> dict[str, str]:
    unexpected = set(value) - INTAKE_FIELD_NAMES
    if unexpected:
        raise ServiceError("Google Drive intake fields are invalid")
    fields: dict[str, str] = {}
    for name, raw in value.items():
        if not isinstance(name, str) or not isinstance(raw, str):
            raise ServiceError("Google Drive intake fields are invalid")
        if len(raw) > 4096:
            raise ServiceError("Google Drive intake fields are invalid")
        fields[name] = raw
    # Apply the same non-provider validation as local uploads before calling
    # the importer, so both intake modes enforce identical business limits.
    period = _validate_period(fields.get("period", ""))
    kind = fields.get("kind", "")
    if kind not in UPLOAD_KINDS:
        raise ServiceError("kind must be expense_invoice or income_invoice")
    issued_on = fields.get("issued_on", "").strip()
    if issued_on:
        parsed_date = date.fromisoformat(issued_on)
        if _quarter_key(parsed_date) != period:
            raise ServiceError(
                f"Invoice date belongs to {_quarter_key(parsed_date)}, not {period}"
            )
    return fields


def private_output(config, path: Path) -> Path:
    if config.private_root is None:
        raise ServiceApiError(
            400,
            "private_output_required",
            "Configure a private root for generated files",
        )
    root = config.private_root.resolve()
    target = path.resolve()
    if target == root or not target.is_relative_to(root):
        raise ServiceApiError(
            400,
            "private_output_required",
            "Output must be inside the configured private root",
        )
    return target
