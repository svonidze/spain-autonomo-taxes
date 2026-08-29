from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import errno
from email.parser import BytesParser
from email.policy import default as email_policy
import hashlib
import hmac
import ipaddress
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs, urlencode, urlparse

try:
    import yaml
except Exception:  # pragma: no cover - dependency guard
    yaml = None

from .fx_reference import FXReferenceError, fetch_eur_rate
from .ledger_db import LedgerDB, LedgerDbError, open as open_ledger_db
from .legacy_paths import LegacyPathResolver
from .posting import build_posting_preview
from .private_paths import (
    PrivatePathError,
    config_path as resolve_config_path,
    resolve_private_paths,
)
from .review_packet import (
    ReviewPacketError,
    build_fx_suggestion,
    classify_review_packet_failure,
    confirm_review_packet,
    packet_json,
    prepare_review_packet,
    prepare_review_work_item,
)
from .storage_service import resolve_verified_filesystem_replica, resolve_verified_replica
from .storage_migration import StorageMigrationError, assert_storage_startup_ready


QUARTER_RE = re.compile(r"^\d{4}-Q[1-4]$")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{32,36}$")
SPA_TOP_LEVEL_ROUTES = {
    "/dashboard",
    "/income",
    "/expenses",
    "/review",
    "/assets",
    "/taxes",
    "/contacts",
}


def _is_spa_route(path: str) -> bool:
    if path in SPA_TOP_LEVEL_ROUTES:
        return True
    if path.startswith("/review/"):
        return UUID_RE.fullmatch(path[len("/review/"):]) is not None
    return False


UPLOAD_KINDS = {"expense_invoice", "income_invoice"}
INTAKE_FIELD_NAMES = {
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
MAX_REVIEW_PACKET_BYTES = 64 * 1024
MAX_GOOGLE_DRIVE_URL_BYTES = 2048
_SO_EXCLUSIVEADDRUSE = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
TAX_FORM_KEYS = {
    "130": "modelo130",
    "303": "modelo303",
}
AUTHORITATIVE_SNAPSHOT_STATUSES = ("baseline", "filed", "submitted", "final")
SNAPSHOT_STATUS_RANKS = {
    "baseline": 1,
    "submitted": 2,
    "filed": 3,
    "final": 4,
}
POSTING_ERROR_STATUS_CODES = {
    "request_error": HTTPStatus.BAD_REQUEST,
    "busy": HTTPStatus.CONFLICT,
    "retry_later": HTTPStatus.CONFLICT,
    "closed": HTTPStatus.CONFLICT,
    "period_not_open": HTTPStatus.CONFLICT,
    "fatal": HTTPStatus.INTERNAL_SERVER_ERROR,
    "internal_error": HTTPStatus.INTERNAL_SERVER_ERROR,
    "unknown": HTTPStatus.NOT_FOUND,
    "unknown_period": HTTPStatus.NOT_FOUND,
}
_POSTING_BATCH_LOCK = threading.Lock()
TRUSTED_PROXY_MODES = {"tailscale_serve"}
REMOTE_COOKIE_NAME = "__Host-autonomo_session"
LOCAL_COOKIE_NAME = "autonomo_session"
TAILSCALE_LOGIN_HEADER = "Tailscale-User-Login"
FORWARDED_PROTO_HEADER = "X-Forwarded-Proto"


class LocalWebError(ValueError):
    pass


class LocalWebApiError(LocalWebError):
    def __init__(self, status: HTTPStatus, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class LocalWebPostingCommandError(LocalWebError):
    def __init__(self, payload: Mapping[str, Any]) -> None:
        status = str(payload.get("status") or "").strip().lower()
        detail = (
            str(payload.get("message") or payload.get("error") or status or "CLI error")
        ).strip()
        super().__init__(detail)
        self.payload = dict(payload)
        self.status = status


@dataclass(frozen=True)
class LocalWebConfig:
    project_root: Path
    database: Path
    inbox_root: Path | None
    archive_root: Path | None
    cache_root: Path
    static_root: Path
    trusted_proxy_mode: str | None = None
    allowed_tailscale_logins: tuple[str, ...] = ()
    read_only_document_roots: tuple[Path, ...] = ()
    legacy_path_map_file: Path | None = None
    google_picker_developer_key: str | None = None
    google_picker_app_id: str | None = None


def load_config(
    project_root: Path,
    *,
    config_path: Path | None = None,
    database: Path | None = None,
    inbox_root: Path | None = None,
    archive_root: Path | None = None,
    cache_root: Path | None = None,
) -> LocalWebConfig:
    root = project_root.resolve()
    try:
        private_paths = resolve_private_paths(
            project_root=root,
            explicit_config=config_path,
        )
    except PrivatePathError as exc:
        raise LocalWebError(str(exc)) from exc
    private_config = private_paths.config_path
    values: Mapping[str, Any] = {}
    if private_config is not None:
        if not private_config.is_file():
            raise LocalWebError(f"Config file does not exist: {private_config}")
        if yaml is None:
            raise LocalWebError(f"PyYAML is required to read {private_config}")
        loaded = yaml.safe_load(private_config.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, Mapping):
            raise LocalWebError(f"{private_config} must contain a mapping")
        values = loaded
    trusted_proxy_mode = _configured_proxy_mode(values.get("trusted_proxy_mode"))
    allowed_tailscale_logins = _configured_string_list(
        values.get("allowed_tailscale_logins"),
        field_name="allowed_tailscale_logins",
    )
    if trusted_proxy_mode == "tailscale_serve" and not allowed_tailscale_logins:
        raise LocalWebError(
            "allowed_tailscale_logins must not be empty in tailscale_serve mode"
        )
    read_only_document_roots = tuple(
        _configured_path(item, private_config).resolve()
        for item in _configured_string_list(
            values.get("read_only_document_roots"),
            field_name="read_only_document_roots",
        )
    )
    legacy_path_map = _configured_path(values.get("legacy_path_map_file"), private_config)
    google_picker_developer_key = _configured_google_picker_developer_key(
        values.get("google_picker_developer_key")
    )
    google_picker_app_id = _configured_google_picker_app_id(
        values.get("google_picker_app_id")
    )
    if bool(google_picker_developer_key) != bool(google_picker_app_id):
        raise LocalWebError(
            "google_picker_developer_key and google_picker_app_id must be configured together"
        )

    resolved_database = (
        database
        or _configured_path(values.get("ledger_db"), private_config)
        or private_paths.database
    ).resolve()
    resolved_inbox = (
        inbox_root
        or _configured_path(values.get("inbox_root"), private_config)
        or private_paths.inbox
    )
    resolved_archive = (
        archive_root
        or _configured_path(
            values.get("archive_root") or values.get("drive_evidence_dir"),
            private_config,
        )
        or private_paths.evidence
    )
    return LocalWebConfig(
        project_root=root,
        database=resolved_database,
        inbox_root=resolved_inbox.resolve() if resolved_inbox else None,
        archive_root=resolved_archive.resolve() if resolved_archive else None,
        cache_root=(
            cache_root
            or _configured_path(values.get("cache_root"), private_config)
            or private_paths.cache / "web" / "dashboard"
        ).resolve(),
        static_root=(Path(__file__).resolve().parent / "web_ui").resolve(),
        trusted_proxy_mode=trusted_proxy_mode,
        allowed_tailscale_logins=allowed_tailscale_logins,
        read_only_document_roots=read_only_document_roots,
        legacy_path_map_file=legacy_path_map.resolve() if legacy_path_map else None,
        google_picker_developer_key=google_picker_developer_key,
        google_picker_app_id=google_picker_app_id,
    )


class LocalAccountingApp:
    def __init__(
        self,
        config: LocalWebConfig,
        *,
        session_token: str | None = None,
        principal_session_secret: str | bytes | None = None,
    ) -> None:
        if not config.database.is_file():
            raise FileNotFoundError(f"SQLite database does not exist: {config.database}")
        self.config = config
        self.session_token = session_token or secrets.token_urlsafe(32)
        self.principal_session_secret = (
            _coerce_session_secret(principal_session_secret)
            if self.config.trusted_proxy_mode == "tailscale_serve"
            else None
        )
        self.legacy_path_resolver = LegacyPathResolver.from_json_file(
            self.config.legacy_path_map_file
        )

    @property
    def uses_trusted_proxy(self) -> bool:
        return self.config.trusted_proxy_mode == "tailscale_serve"

    @property
    def cookie_name(self) -> str:
        return REMOTE_COOKIE_NAME if self.uses_trusted_proxy else LOCAL_COOKIE_NAME

    @property
    def document_roots(self) -> tuple[Path, ...]:
        roots: list[Path] = []
        for root in (
            self.config.archive_root,
            self.config.inbox_root,
            *self.config.read_only_document_roots,
        ):
            if root is not None and root not in roots:
                roots.append(root)
        return tuple(roots)

    def resolve_document_path(self, value: str | None) -> Path:
        if value is None or not str(value).strip():
            raise FileNotFoundError("Document source is unavailable")
        direct = Path(str(value)).resolve(strict=False)
        if direct.exists():
            return direct
        resolved = self.legacy_path_resolver.resolve(str(value))
        if resolved is not None:
            return resolved.resolve(strict=False)
        return direct

    def session_cookie_value(self, principal: str | None) -> str:
        if not self.uses_trusted_proxy:
            return self.session_token
        if not principal:
            raise LocalWebError("Tailscale identity is required")
        secret = self.principal_session_secret
        if secret is None:
            raise LocalWebError("Tailscale session secret is not configured")
        payload = f"{self.session_token}\0{principal}".encode("utf-8")
        return hmac.new(secret, payload, hashlib.sha256).hexdigest()

    def session_cookie_header(self, principal: str | None) -> str:
        value = self.session_cookie_value(principal)
        parts = [f"{self.cookie_name}={value}", "Path=/", "HttpOnly", "SameSite=Strict"]
        if self.uses_trusted_proxy:
            parts.insert(1, "Secure")
        return "; ".join(parts)

    def bootstrap(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            periods = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT period_key, status, starts_on, ends_on
                    FROM periods
                    WHERE period_type = 'quarter'
                    ORDER BY starts_on DESC
                    """
                ).fetchall()
                if QUARTER_RE.fullmatch(str(row["period_key"]))
            ]
            profile = connection.execute(
                "SELECT full_name FROM taxpayer_profile ORDER BY created_at LIMIT 1"
            ).fetchone()
            counts = {
                table: int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                for table in (
                    "transactions",
                    "documents",
                    "assets",
                    "validation_issues",
                )
            }
        period_keys = [row["period_key"] for row in periods]
        default_period = _current_or_latest_period(period_keys)
        return {
            "profile_name": profile["full_name"] if profile else "Autónomo",
            "periods": periods,
            "default_period": default_period,
            "counts": counts,
            "intake_enabled": bool(
                self.config.inbox_root is not None
                and self.config.archive_root is not None
            ),
        }

    def dashboard(self, period_key: str) -> dict[str, Any]:
        period = _validate_period(period_key)
        cached = self._load_cached_dashboard(period)
        with closing(self._connect()) as connection:
            period_row = self._period_row(connection, period)
            totals = self._transaction_totals(connection, period_row["period_id"])
            recent = self._transactions(
                connection,
                period_id=period_row["period_id"],
                limit=8,
            )
            open_issues = self._issues(
                connection,
                period_id=period_row["period_id"],
                limit=6,
            )
            obligations = self._obligations(
                connection,
                period_id=period_row["period_id"],
            )
            tax_forms = _dashboard_tax_forms(
                connection,
                period_key=period,
                obligations=obligations,
                cached=cached,
            )
            posting_preview = self._build_posting_preview(period)
            document_counts = {
                row["lifecycle_status"]: int(row["count"])
                for row in connection.execute(
                    """
                    SELECT lifecycle_status, COUNT(*) AS count
                    FROM documents
                    WHERE period_id = ?
                    GROUP BY lifecycle_status
                    """,
                    (period_row["period_id"],),
                ).fetchall()
            }
            review_rows = self._transactions(
                connection,
                period_id=period_row["period_id"],
                lifecycle_status="review",
                limit=500,
            )
            review_summary = _review_summary(review_rows)
        return {
            "period": dict(period_row),
            "totals": totals,
            "recent_transactions": recent,
            "open_issues": open_issues,
            "obligations": obligations,
            "document_counts": document_counts,
            "tax_preview": _compact_tax_preview(cached),
            "tax_forms": tax_forms,
            "forecast_as_of": cached.get("as_of") if cached else None,
            "filing_ready": bool(cached and cached.get("filing_ready")),
            "posting_preview_summary": _posting_preview_summary(posting_preview),
            "submission_ready": bool(cached and cached.get("submission_ready")),
            "readyCount": review_summary["ready"],
            "posting_summary": review_summary,
        }

    def posting_preview(self, period_key: str) -> dict[str, Any]:
        period = _validate_period(period_key)
        try:
            return self._build_posting_preview(period)
        except LedgerDbError as exc:
            if str(exc).startswith("Unknown period: "):
                raise FileNotFoundError(str(exc)) from exc
            raise

    def post_ready(
        self,
        *,
        period_key: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        period = _validate_period(period_key)
        if not items:
            raise LocalWebError("Posting preview has no ready items")
        if not _POSTING_BATCH_LOCK.acquire(blocking=False):
            return {
                "error": "posting_in_progress",
                "message": "Another posting batch is already running",
                "status": "busy",
            }
        try:
            command = [
                sys.executable,
                "-m",
                "autonomo_taxes.cli",
                "review",
                "post-batch",
                "--db",
                str(self.config.database),
                "--period",
                period,
            ]
            if self.config.inbox_root is not None:
                command.extend(("--inbox-root", str(self.config.inbox_root)))
            if self.config.archive_root is not None:
                command.extend(("--archive-root", str(self.config.archive_root)))
            return self._run_cli_json(
                command,
                stdin_json={
                    "period": period,
                    "items": items,
                },
            )
        finally:
            _POSTING_BATCH_LOCK.release()

    def transactions(
        self,
        period_key: str,
        *,
        entry_type: str | None = None,
        lifecycle_status: str | None = None,
        query: str | None = None,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        period = _validate_period(period_key)
        if entry_type not in {None, "income", "expense"}:
            raise LocalWebError("entry_type must be income or expense")
        allowed_statuses = {
            "received",
            "extracted",
            "needs_review",
            "approved",
            "posted",
            "included_in_snapshot",
            "duplicate",
            "rejected",
            "void",
            "review",
        }
        if lifecycle_status not in allowed_statuses | {None}:
            raise LocalWebError("Unsupported lifecycle status")
        with closing(self._connect()) as connection:
            period_row = self._period_row(connection, period)
            return self._transactions(
                connection,
                period_id=period_row["period_id"],
                entry_type=entry_type,
                lifecycle_status=lifecycle_status,
                query=query,
                limit=min(max(limit, 1), 500),
            )

    def documents(
        self,
        period_key: str,
        *,
        document_type: str | None = None,
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        period = _validate_period(period_key)
        parameters: list[Any] = [period]
        where = ["p.period_key = ?"]
        if document_type:
            where.append("d.document_type = ?")
            parameters.append(document_type)
        parameters.append(min(max(limit, 1), 500))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    d.document_id,
                    d.document_type,
                    d.document_number,
                    d.issued_on,
                    d.currency,
                    d.total_minor,
                    d.lifecycle_status,
                    d.mime_type,
                    d.source_path,
                    c.display_name AS counterparty_name,
                    SUM(
                        CASE
                            WHEN i.issue_status = 'open' THEN 1
                            ELSE 0
                        END
                    ) AS open_issue_count
                FROM documents d
                JOIN periods p ON p.period_id = d.period_id
                LEFT JOIN counterparties c ON c.counterparty_id = d.counterparty_id
                LEFT JOIN validation_issues i
                    ON i.subject_table = 'documents'
                    AND i.subject_id = d.document_id
                WHERE {" AND ".join(where)}
                GROUP BY d.document_id
                ORDER BY d.issued_on DESC, d.created_at DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [
            {
                **_row_dict(row),
                "total_eur": _minor_to_text(row["total_minor"])
                if row["currency"] == "EUR"
                else None,
                "source_available": bool(
                    row["source_path"]
                    and self.resolve_document_path(str(row["source_path"])).is_file()
                ),
                "source_path": None,
            }
            for row in rows
        ]

    def issues(self, period_key: str) -> list[dict[str, Any]]:
        period = _validate_period(period_key)
        with closing(self._connect()) as connection:
            period_row = self._period_row(connection, period)
            return self._issues(
                connection,
                period_id=period_row["period_id"],
                limit=250,
            )

    def assets(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT
                    a.asset_id,
                    a.asset_code,
                    a.description,
                    a.placed_in_service_on,
                    a.currency,
                    a.cost_minor,
                    a.amortizable_base_minor,
                    a.business_use_ratio,
                    a.annual_rate_basis_points,
                    a.depreciation_method,
                    a.advisor_decision,
                    a.source_invoice_number,
                    c.display_name AS counterparty_name,
                    COUNT(am.amortization_entry_id) AS schedule_rows,
                    COALESCE(SUM(am.amount_minor), 0) AS scheduled_minor
                FROM assets a
                LEFT JOIN documents d ON d.document_id = a.document_id
                LEFT JOIN counterparties c ON c.counterparty_id = d.counterparty_id
                LEFT JOIN amortization_entries am ON am.asset_id = a.asset_id
                GROUP BY a.asset_id
                ORDER BY a.placed_in_service_on DESC, a.asset_code
                """
            ).fetchall()
        return [
            {
                **_row_dict(row),
                "cost": _minor_to_text(row["cost_minor"]),
                "amortizable_base": _minor_to_text(row["amortizable_base_minor"]),
                "scheduled": _minor_to_text(row["scheduled_minor"]),
                "business_use_percent": _ratio_percent(row["business_use_ratio"]),
                "annual_rate_percent": _basis_points_percent(
                    row["annual_rate_basis_points"]
                ),
            }
            for row in rows
        ]

    def taxes(self, period_key: str) -> dict[str, Any]:
        period = _validate_period(period_key)
        cached = self._load_cached_dashboard(period)
        with closing(self._connect()) as connection:
            period_row = self._period_row(connection, period)
            obligations = self._obligations(
                connection,
                period_id=period_row["period_id"],
            )
            tax_forms = _dashboard_tax_forms(
                connection,
                period_key=period,
                obligations=obligations,
                cached=cached,
            )
        return {
            "period": period,
            "obligations": obligations,
            "tax_preview": _compact_tax_preview(cached),
            "tax_forms": tax_forms,
            "forecast_as_of": cached.get("as_of") if cached else None,
            "warnings": list(cached.get("warnings", [])) if cached else [],
        }

    def counterparties(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT
                    c.counterparty_id,
                    c.display_name,
                    c.country_code,
                    c.tax_id,
                    c.vat_id,
                    c.roi_status,
                    c.professional_supplier,
                    c.retention_expected,
                    COUNT(DISTINCT t.transaction_id) AS transaction_count,
                    MAX(t.transaction_date) AS last_transaction_on
                FROM counterparties c
                LEFT JOIN transactions t ON t.counterparty_id = c.counterparty_id
                GROUP BY c.counterparty_id
                ORDER BY c.display_name COLLATE NOCASE
                """
            ).fetchall()
        return [_row_dict(row) for row in rows]

    def refresh_dashboard(self, period_key: str, *, as_of: str | None = None) -> dict[str, Any]:
        period = _validate_period(period_key)
        effective_as_of = date.fromisoformat(as_of) if as_of else date.today()
        output_dir = self.config.cache_root / period
        command = [
            sys.executable,
            "-m",
            "autonomo_taxes.cli",
            "period",
            "dashboard",
            period,
            "--db",
            str(self.config.database),
            "--as-of",
            effective_as_of.isoformat(),
            "--out-dir",
            str(output_dir),
        ]
        self._run_cli(command)
        dashboard_path = output_dir / "dashboard.json"
        if not dashboard_path.is_file():
            raise LocalWebError("Dashboard command did not create dashboard.json")
        return json.loads(dashboard_path.read_text(encoding="utf-8"))

    def ingest_upload(
        self,
        *,
        fields: Mapping[str, str],
        filename: str,
        content: bytes,
        google_folder_id: str | None = None,
    ) -> dict[str, Any]:
        if self.config.inbox_root is None or self.config.archive_root is None:
            raise LocalWebError(
                "Intake requires configured private inbox and evidence roots"
            )
        period = _validate_period(fields.get("period", ""))
        kind = fields.get("kind", "")
        if kind not in UPLOAD_KINDS:
            raise LocalWebError("kind must be expense_invoice or income_invoice")
        if not content:
            raise LocalWebError("Uploaded file is empty")
        if len(content) > MAX_UPLOAD_BYTES:
            raise LocalWebError("Uploaded file exceeds the 30 MB limit")
        issued_on = fields.get("issued_on", "").strip()
        if issued_on:
            parsed_date = date.fromisoformat(issued_on)
            if _quarter_key(parsed_date) != period:
                raise LocalWebError(
                    f"Invoice date belongs to {_quarter_key(parsed_date)}, not {period}"
                )

        stored_path = _store_upload(
            self.config.inbox_root,
            period=period,
            kind=kind,
            filename=filename,
            content=content,
        )
        command = [
            sys.executable,
            "-m",
            "autonomo_taxes.cli",
            "ingest",
            str(stored_path),
            "--db",
            str(self.config.database),
            "--kind",
            kind,
            "--period",
            period,
            "--archive-root",
            str(self.config.archive_root),
        ]
        option_map = {
            "issued_on": "--issued-on",
            "document_number": "--document-number",
            "counterparty_name": "--counterparty-name",
            "gross": "--gross",
            "taxable_base": "--taxable-base",
            "vat": "--vat",
            "currency": "--currency",
        }
        for field_name, option in option_map.items():
            value = fields.get(field_name, "").strip()
            if value:
                command.extend((option, value))
        result = self._run_cli(command)
        if google_folder_id:
            document_id = str(result.get("document_id") or "")
            if not document_id:
                raise LocalWebApiError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "storage_sync_failed",
                    "Document intake did not return an id for cloud archival",
                )
            self._sync_upload_to_selected_google_folder(
                document_id=document_id,
                google_folder_id=google_folder_id,
            )
        return {
            "status": "accepted_for_review",
            "period": period,
            "kind": kind,
            "file_name": stored_path.name,
            "document_id": result.get("document_id"),
            "transaction_id": (result.get("transaction") or {}).get("transaction_id"),
            "document_lifecycle_status": result.get("document_lifecycle_status"),
            "review_requirements": result.get("review_requirements", []),
            "system_marker": result.get("document_id"),
        }

    def _sync_upload_to_selected_google_folder(
        self,
        *,
        document_id: str,
        google_folder_id: str,
    ) -> None:
        """Create verified Google and Yandex copies for an explicitly chosen folder."""
        from .storage_reconcile import reconcile_file_to_backend

        try:
            with LedgerDB.open(self.config.database) as db:
                file_row = db.connection.execute(
                    """
                    SELECT da.file_id
                    FROM document_attachments da
                    WHERE da.document_id = ? AND da.attachment_role = 'source'
                    ORDER BY da.created_at, da.document_attachment_id
                    LIMIT 1
                    """,
                    (document_id,),
                ).fetchone()
                writer = db.connection.execute(
                    """
                    SELECT backend_key
                    FROM storage_backends
                    WHERE enabled = 1
                      AND driver_key = 'google_drive'
                      AND access_mode = 'read_write'
                      AND json_extract(config_json, '$.credential_mode') = 'oauth'
                    ORDER BY read_priority, backend_key
                    LIMIT 1
                    """
                ).fetchone()
                if file_row is None or writer is None:
                    raise RuntimeError("Google Drive writer is not configured")
                file_id = str(file_row["file_id"])
                google = reconcile_file_to_backend(
                    db,
                    backend_key=str(writer["backend_key"]),
                    file_id=file_id,
                    google_folder_id=google_folder_id,
                    document_id=document_id,
                )
                yandex = reconcile_file_to_backend(
                    db,
                    backend_key="yandex_evidence",
                    file_id=file_id,
                    document_id=document_id,
                )
                if int(google.get("failed", 0)) or int(yandex.get("failed", 0)):
                    raise RuntimeError("Verified cloud replica could not be created")
        except Exception as exc:
            raise LocalWebApiError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "storage_sync_failed",
                "Document was accepted locally but cloud archival is not verified",
            ) from exc

    def google_picker_config(self) -> dict[str, Any]:
        """Return browser-only Picker credentials for the authenticated tailnet UI.

        Refresh credentials remain server-side.  The access token is deliberately
        fetched only on demand and never stored in the database or browser
        storage.
        """
        developer_key = self.config.google_picker_developer_key
        app_id = self.config.google_picker_app_id
        if not self.uses_trusted_proxy or not developer_key or not app_id:
            return {"enabled": False}
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """
                    SELECT config_json, credential_ref
                    FROM storage_backends
                    WHERE enabled = 1
                      AND driver_key = 'google_drive'
                      AND access_mode = 'read_write'
                    ORDER BY read_priority, backend_key
                    """
                ).fetchall()
            for row in rows:
                config = json.loads(str(row["config_json"]))
                if not isinstance(config, Mapping) or config.get("credential_mode", "oauth") != "oauth":
                    continue
                credential_ref = str(row["credential_ref"] or "")
                if not credential_ref.startswith("file:"):
                    continue
                token_file = Path(credential_ref.removeprefix("file:"))
                if not token_file.is_file() or token_file.is_symlink():
                    continue
                return _google_picker_token(token_file, developer_key, app_id)
        except (OSError, sqlite3.Error, json.JSONDecodeError, ValueError):
            pass
        return {"enabled": False}

    def ingest_google_drive_url(
        self,
        *,
        fields: Mapping[str, str],
        drive_url: str,
    ) -> dict[str, Any]:
        """Ingest an existing Drive file without creating another Drive copy.

        The provider integration owns download, checksum verification, archival
        metadata, and replica registration.  Keeping the web boundary limited
        to a canonical file ID prevents arbitrary remote fetches from the
        private web service.
        """
        normalized_url, file_id = normalize_google_drive_url(drive_url)
        normalized_fields = _validated_intake_fields(fields)
        try:
            from .storage_import import ingest_google_drive_url as import_google_drive_url

            result = import_google_drive_url(
                config=self.config,
                fields=normalized_fields,
                drive_url=normalized_url,
                file_id=file_id,
            )
        except ImportError as exc:  # pragma: no cover - incomplete deployment guard
            raise LocalWebError("Google Drive intake is not configured") from exc
        except LocalWebError:
            raise
        except Exception as exc:
            # The provider may include remote URLs or credential context in its
            # exception.  Never reflect either back through the web API.
            raise LocalWebError("Google Drive file could not be imported") from exc
        if not isinstance(result, Mapping):
            raise LocalWebError("Google Drive intake returned an invalid result")
        return dict(result)

    def review_work_item(self, review_id: str) -> dict[str, Any]:
        try:
            with open_ledger_db(self.config.database, read_only=True) as db:
                packet = prepare_review_packet(db, review_id)
                fx_suggestion = self._fx_suggestion_for(db, packet)
                return prepare_review_work_item(db, review_id, fx_suggestion=fx_suggestion)
        except ReviewPacketError as exc:
            raise self._review_api_error(exc) from exc

    def review_confirm(
        self,
        packet: Mapping[str, Any],
        fx: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        self._ensure_supported_review_packet(packet)
        try:
            with open_ledger_db(self.config.database) as db:
                return confirm_review_packet(db, packet, fx, ecb_verify=self._ecb_verify)
        except ReviewPacketError as exc:
            raise self._review_api_error(exc) from exc

    def _fx_suggestion_for(
        self, db: Any, packet: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        state = packet["state"]
        transaction = state["transaction"]
        original_currency = str(
            transaction["original_currency"] or transaction["currency"] or "EUR"
        ).upper()
        if original_currency == "EUR":
            return None
        if transaction["fx_rate_id"]:
            provenance = db.fx_provenance_for_rate(str(transaction["fx_rate_id"]))
            return build_fx_suggestion(state, provenance=provenance)
        transaction_date = date.fromisoformat(str(transaction["transaction_date"])[:10])
        try:
            ecb_result = fetch_eur_rate(original_currency, transaction_date)
            ecb_error: str | None = None
        except FXReferenceError as exc:
            ecb_result = None
            ecb_error = str(exc)
        return build_fx_suggestion(state, ecb_result=ecb_result, ecb_error=ecb_error)

    def _ecb_verify(self, currency: str, rate_date: date) -> Decimal | None:
        """Re-verify a submitted official rate against a fresh ECB lookup.

        Returns the official EUR-per-unit rate when the exact requested date
        is published; ``None`` when the date itself has no observation.
        """

        result = fetch_eur_rate(currency, rate_date)
        if result.status != "exact":
            return None
        return result.observation.eur_per_unit

    def review_validate(self, packet: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_supported_review_packet(packet)
        return self._run_review_apply(packet, dry_run=True)

    def review_apply(self, packet: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_supported_review_packet(packet)
        return self._run_review_apply(packet, dry_run=False)

    def review_apply_fx(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        review_id = str(payload["review_id"]).strip()
        self.review_work_item(review_id)
        command = [
            sys.executable,
            "-m",
            "autonomo_taxes.cli",
            "review",
            "apply-fx",
            "--db",
            str(self.config.database),
            "--input",
            "-",
        ]
        try:
            self._run_cli_with_input(
                command,
                input_text=json.dumps(dict(payload), ensure_ascii=False) + "\n",
            )
        except LocalWebError as exc:
            message = str(exc)
            if message.startswith("Expected row_version"):
                raise LocalWebApiError(
                    HTTPStatus.CONFLICT, "stale_snapshot", message
                ) from exc
            if message.startswith("Conflicting FX"):
                raise LocalWebApiError(
                    HTTPStatus.CONFLICT, "fx_rate_conflict", message
                ) from exc
            if "immutable after close" in message or "lifecycle_status=" in message:
                raise LocalWebApiError(
                    HTTPStatus.CONFLICT, "review_conflict", message
                ) from exc
            raise LocalWebApiError(
                HTTPStatus.BAD_REQUEST, "fx_review_invalid", message
            ) from exc
        return self.review_work_item(review_id)

    def document_file(self, document_id: str) -> tuple[Path, str]:
        if not UUID_RE.fullmatch(document_id):
            raise LocalWebError("Invalid document id")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT source_path, mime_type FROM documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            replica = resolve_verified_filesystem_replica(
                connection,
                document_id=document_id,
            )
        allowed_roots = self.document_roots
        if replica is not None and any(
            _is_relative_to(replica.path, root.resolve()) for root in allowed_roots
        ):
            return replica.path, replica.media_type
        with closing(self._connect()) as connection:
            remote_replica = resolve_verified_replica(
                connection,
                document_id=document_id,
                cache_root=self.config.cache_root / "storage",
            )
        if remote_replica is not None:
            return remote_replica.path, remote_replica.media_type
        if row is None or not row["source_path"]:
            raise FileNotFoundError("Document source is unavailable")
        path = self.resolve_document_path(str(row["source_path"]))
        if not any(_is_relative_to(path, root.resolve()) for root in allowed_roots):
            raise LocalWebError("Document source is outside configured evidence roots")
        for root in self.config.read_only_document_roots:
            resolved_root = root.resolve()
            if _is_relative_to(path, resolved_root) and not path.is_file():
                raise LocalWebApiError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "document_root_unavailable",
                    "Document source root is temporarily unavailable",
                )
        if not path.is_file():
            raise FileNotFoundError(path)
        mime_type = row["mime_type"] or mimetypes.guess_type(path.name)[0]
        return path, mime_type or "application/octet-stream"

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.config.database.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _period_row(
        self, connection: sqlite3.Connection, period_key: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM periods WHERE period_key = ? AND period_type = 'quarter'",
            (period_key,),
        ).fetchone()
        if row is None:
            raise LocalWebError(f"Unknown quarter: {period_key}")
        return row

    def _transaction_totals(
        self, connection: sqlite3.Connection, period_id: str
    ) -> dict[str, dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT
                CASE
                    WHEN t.lifecycle_status IN ('posted', 'included_in_snapshot')
                        THEN 'actual'
                    WHEN t.lifecycle_status = 'approved'
                        THEN 'forecast'
                    ELSE 'review'
                END AS scope,
                t.entry_type,
                COUNT(*) AS transaction_count,
                COALESCE(SUM(
                    CASE
                        WHEN t.amount_eur_minor IS NOT NULL THEN t.amount_eur_minor
                        WHEN t.currency = 'EUR' THEN t.amount_minor
                        ELSE 0
                    END
                ), 0) AS gross_minor,
                COALESCE(SUM(tt.deductible_irpf_minor), 0) AS deductible_irpf_minor,
                COALESCE(SUM(tt.deductible_vat_minor), 0) AS deductible_vat_minor
            FROM transactions t
            LEFT JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
            WHERE t.period_id = ?
              AND t.lifecycle_status NOT IN ('duplicate', 'rejected', 'void')
            GROUP BY scope, t.entry_type
            """,
            (period_id,),
        ).fetchall()
        result: dict[str, dict[str, Any]] = {
            "actual": _empty_scope(),
            "forecast": _empty_scope(),
            "review": _empty_scope(),
        }
        for row in rows:
            scope = result[row["scope"]]
            scope["transaction_count"] += int(row["transaction_count"])
            if row["entry_type"] == "income":
                scope["income_transaction_count"] += int(row["transaction_count"])
                scope["income_eur"] = _minor_to_text(row["gross_minor"])
            elif row["entry_type"] == "expense":
                scope["expense_transaction_count"] += int(row["transaction_count"])
                scope["expense_gross_eur"] = _minor_to_text(row["gross_minor"])
                scope["deductible_irpf_eur"] = _minor_to_text(
                    row["deductible_irpf_minor"]
                )
                scope["deductible_vat_eur"] = _minor_to_text(
                    row["deductible_vat_minor"]
                )
        return result

    def _transactions(
        self,
        connection: sqlite3.Connection,
        *,
        period_id: str,
        entry_type: str | None = None,
        lifecycle_status: str | None = None,
        query: str | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        where = ["t.period_id = ?"]
        parameters: list[Any] = [period_id]
        if entry_type:
            where.append("t.entry_type = ?")
            parameters.append(entry_type)
        if lifecycle_status == "review":
            where.append(
                "t.lifecycle_status IN ('received', 'extracted', 'needs_review', 'approved')"
            )
        elif lifecycle_status:
            where.append("t.lifecycle_status = ?")
            parameters.append(lifecycle_status)
        if query:
            where.append(
                "(t.description LIKE ? OR c.display_name LIKE ? OR d.document_number LIKE ?)"
            )
            needle = f"%{query.strip()}%"
            parameters.extend((needle, needle, needle))
        parameters.append(limit)
        rows = connection.execute(
            f"""
            SELECT
                t.transaction_id,
                t.transaction_date,
                t.entry_type,
                t.description,
                t.amount_minor,
                t.currency,
                t.amount_eur_minor,
                t.lifecycle_status,
                t.row_version,
                t.document_id,
                c.display_name AS counterparty_name,
                c.country_code,
                d.document_number,
                d.document_type,
                d.lifecycle_status AS document_status,
                tt.tax_code,
                tt.deductible_irpf_minor,
                tt.deductible_vat_minor,
                tt.include_modelo130,
                tt.include_modelo303,
                SUM(
                    CASE
                        WHEN i.issue_status = 'open' THEN 1
                        ELSE 0
                    END
                ) AS open_issue_count
            FROM transactions t
            LEFT JOIN counterparties c ON c.counterparty_id = t.counterparty_id
            LEFT JOIN documents d ON d.document_id = t.document_id
            LEFT JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
            LEFT JOIN validation_issues i
                ON i.issue_status = 'open'
                AND (
                    (i.subject_table = 'transactions' AND i.subject_id = t.transaction_id)
                    OR (i.subject_table = 'documents' AND i.subject_id = t.document_id)
                    OR (i.subject_table = 'counterparties' AND i.subject_id = t.counterparty_id)
                )
            WHERE {" AND ".join(where)}
            GROUP BY t.transaction_id
            ORDER BY t.transaction_date DESC, t.created_at DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return [
            {
                **_row_dict(row),
                "amount_original": _minor_to_text(row["amount_minor"]),
                "amount_eur": _minor_to_text(
                    row["amount_eur_minor"]
                    if row["amount_eur_minor"] is not None
                    else row["amount_minor"]
                    if row["currency"] == "EUR"
                    else None
                ),
                "deductible_irpf_eur": _minor_to_text(
                    row["deductible_irpf_minor"]
                ),
                "deductible_vat_eur": _minor_to_text(
                    row["deductible_vat_minor"]
                ),
            }
            for row in rows
        ]

    def _issues(
        self,
        connection: sqlite3.Connection,
        *,
        period_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT
                validation_issue_id,
                subject_table,
                subject_id,
                issue_code,
                severity,
                message,
                blocking,
                issue_status,
                created_at
            FROM validation_issues
            WHERE period_id = ? AND issue_status = 'open'
            ORDER BY blocking DESC, severity DESC, created_at DESC
            LIMIT ?
            """,
            (period_id, limit),
        ).fetchall()
        return [_row_dict(row) for row in rows]

    def _obligations(
        self,
        connection: sqlite3.Connection,
        *,
        period_id: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT
                o.obligation_id,
                o.obligation_code,
                o.determination,
                o.filing_status,
                o.blocking,
                o.explanation,
                o.filed_at,
                COALESCE(tc.internal_due_on, o.due_on) AS internal_due_on,
                tc.direct_debit_cutoff_on,
                COALESCE(tc.statutory_due_on, o.due_on) AS statutory_due_on,
                tc.deadline_status,
                tc.source_url
            FROM obligations o
            LEFT JOIN tax_calendar_entries tc
                ON tc.period_id = o.period_id
                AND tc.form_code = o.obligation_code
            WHERE o.period_id = ?
            ORDER BY
                CASE o.determination
                    WHEN 'due' THEN 0
                    WHEN 'unknown' THEN 1
                    ELSE 2
                END,
                o.obligation_code
            """,
            (period_id,),
        ).fetchall()
        return [_row_dict(row) for row in rows]

    def _load_cached_dashboard(self, period_key: str) -> dict[str, Any]:
        candidates = (self.config.cache_root / period_key / "dashboard.json",)
        for path in candidates:
            if path.is_file():
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if value.get("period") == period_key:
                    return value
        return {}

    def _build_posting_preview(
        self,
        period_key: str,
    ) -> dict[str, Any]:
        with open_ledger_db(self.config.database, read_only=True) as db:
            preview = build_posting_preview(
                db,
                period_key=period_key,
                inbox_root=self.config.inbox_root,
                archive_root=self.config.archive_root,
            )
        if not isinstance(preview, dict):
            raise LocalWebError("Posting preview must be a JSON object")
        return preview

    def _run_cli(self, command: list[str]) -> dict[str, Any]:
        return self._run_cli_with_input(command, input_text=None)

    def _run_cli_with_input(
        self,
        command: list[str],
        *,
        input_text: str | None,
    ) -> dict[str, Any]:
        environment = os.environ.copy()
        source_root = str(self.config.project_root / "src")
        environment["PYTHONPATH"] = os.pathsep.join(
            part
            for part in (source_root, environment.get("PYTHONPATH", ""))
            if part
        )
        run = subprocess.run(
            command,
            cwd=self.config.project_root,
            env=environment,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=180,
        )
        if run.returncode != 0:
            detail = _cli_error_detail(run.stderr, run.stdout)
            raise LocalWebError(detail)
        try:
            result = json.loads(run.stdout)
        except json.JSONDecodeError as exc:
            raise LocalWebError("CLI returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise LocalWebError("CLI returned an unexpected response")
        return result

    def _run_review_apply(
        self,
        packet: Mapping[str, Any],
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        command = [
            sys.executable,
            "-m",
            "autonomo_taxes.cli",
            "review",
            "apply",
            "--db",
            str(self.config.database),
            "--input",
            "-",
        ]
        if dry_run:
            command.append("--dry-run")
        try:
            return self._run_cli_with_input(command, input_text=packet_json(packet))
        except LocalWebError as exc:
            code, status = classify_review_packet_failure(str(exc))
            raise LocalWebApiError(HTTPStatus(status), code, str(exc)) from exc

    def _ensure_supported_review_packet(self, packet: Mapping[str, Any]) -> None:
        review_id = str(packet.get("review_id", "")).strip()
        if not review_id:
            return
        work_item = self.review_work_item(review_id)
        if work_item["supported"]:
            return
        reasons = ", ".join(str(value) for value in work_item["unsupported_reasons"])
        raise LocalWebApiError(
            HTTPStatus.CONFLICT,
            "unsupported_work_item",
            "Review work item is not supported by the local web workflow: "
            + reasons,
        )

    def _review_api_error(self, exc: ReviewPacketError) -> LocalWebApiError:
        code, status = classify_review_packet_failure(str(exc))
        return LocalWebApiError(HTTPStatus(status), code, str(exc))

    def _run_cli_json(
        self,
        command: list[str],
        *,
        stdin_json: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        environment = os.environ.copy()
        source_root = str(self.config.project_root / "src")
        environment["PYTHONPATH"] = os.pathsep.join(
            part
            for part in (source_root, environment.get("PYTHONPATH", ""))
            if part
        )
        run = subprocess.run(
            command,
            cwd=self.config.project_root,
            env=environment,
            input=(
                json.dumps(stdin_json, ensure_ascii=False, separators=(",", ":"))
                if stdin_json is not None
                else None
            ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=180,
        )
        if run.returncode != 0:
            payload = _parse_cli_json_payload(run.stdout) or _parse_cli_json_payload(
                run.stderr
            )
            if payload is not None:
                raise LocalWebPostingCommandError(payload)
            detail = run.stderr.strip() or run.stdout.strip() or "unknown CLI error"
            raise LocalWebError(detail)
        payload = _parse_cli_json_payload(run.stdout)
        if payload is None:
            raise LocalWebError("CLI returned invalid JSON")
        return payload


class LocalAccountingServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = _SO_EXCLUSIVEADDRUSE is None

    def __init__(
        self,
        server_address: tuple[str, int],
        app: LocalAccountingApp,
    ) -> None:
        self.app = app
        super().__init__(server_address, LocalAccountingHandler)

    def server_bind(self) -> None:
        if _SO_EXCLUSIVEADDRUSE is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, _SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class LocalAccountingHandler(BaseHTTPRequestHandler):
    server: LocalAccountingServer

    def _external_default_port(self) -> int:
        return 443 if self.server.app.uses_trusted_proxy else self.server.server_address[1]

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        try:
            principal = self._require_request_principal()
            self._guard_host()
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._serve_static("index.html", set_cookie=True, principal=principal)
                return
            if parsed.path in {"/app.js", "/styles.css"}:
                self._serve_static(parsed.path.removeprefix("/"))
                return
            if parsed.path == "/favicon.ico":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if _is_spa_route(parsed.path):
                # SPA routes serve the app shell plus the session cookie so a
                # deep link opens directly; everything else keeps the 404.
                self._serve_static("index.html", set_cookie=True, principal=principal)
                return
            self._require_session(principal)
            query = parse_qs(parsed.query)
            if parsed.path == "/api/bootstrap":
                self._send_json(self.server.app.bootstrap())
            elif parsed.path == "/api/google-picker/config":
                self._send_json(self.server.app.google_picker_config())
            elif parsed.path == "/api/dashboard":
                self._send_json(
                    self.server.app.dashboard(_single_query(query, "period"))
                )
            elif parsed.path == "/api/transactions":
                self._send_json(
                    self.server.app.transactions(
                        _single_query(query, "period"),
                        entry_type=_optional_query(query, "entry_type"),
                        lifecycle_status=_optional_query(query, "status"),
                        query=_optional_query(query, "q"),
                    )
                )
            elif parsed.path == "/api/documents":
                self._send_json(
                    self.server.app.documents(
                        _single_query(query, "period"),
                        document_type=_optional_query(query, "document_type"),
                    )
                )
            elif parsed.path == "/api/issues":
                self._send_json(
                    self.server.app.issues(_single_query(query, "period"))
                )
            elif parsed.path == "/api/assets":
                self._send_json(self.server.app.assets())
            elif parsed.path == "/api/taxes":
                self._send_json(
                    self.server.app.taxes(_single_query(query, "period"))
                )
            elif parsed.path == "/api/review/posting-preview":
                self._send_json(
                    self.server.app.posting_preview(_single_query(query, "period"))
                )
            elif parsed.path == "/api/counterparties":
                self._send_json(self.server.app.counterparties())
            elif parsed.path == "/api/review/work-item":
                self._send_json(
                    self.server.app.review_work_item(_single_query(query, "review_id"))
                )
            elif parsed.path.startswith("/api/document/") and parsed.path.endswith(
                "/content"
            ):
                document_id = parsed.path.split("/")[3]
                self._serve_document(document_id)
            elif parsed.path.startswith("/api/"):
                self._send_error_json(HTTPStatus.NOT_FOUND, "Unknown API endpoint")
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except LocalWebApiError as exc:
            self._send_error_json(exc.status, str(exc), code=exc.code)
        except FileNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except (LocalWebError, ValueError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # pragma: no cover - final HTTP boundary
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"{type(exc).__name__}: {exc}",
            )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        try:
            principal = self._require_request_principal()
            self._guard_host()
            self._require_session(principal)
            self._guard_origin()
            parsed = urlparse(self.path)
            if parsed.path == "/api/dashboard/refresh":
                payload = self._read_json()
                result = self.server.app.refresh_dashboard(
                    str(payload.get("period", "")),
                    as_of=str(payload["as_of"]) if payload.get("as_of") else None,
                )
                self._send_json(
                    {
                        "status": "refreshed",
                        "period": result.get("period"),
                        "as_of": result.get("as_of"),
                    }
                )
            elif parsed.path == "/api/intake":
                fields, filename, content = self._read_multipart()
                google_folder_id = _optional_google_folder_id(
                    fields.pop("google_folder_id", None)
                )
                self._send_json(
                    self.server.app.ingest_upload(
                        fields=fields,
                        filename=filename,
                        content=content,
                        google_folder_id=google_folder_id,
                    ),
                    status=HTTPStatus.CREATED,
                )
            elif parsed.path == "/api/intake/google-drive":
                self._require_same_origin()
                self._require_json_content_type()
                payload = self._read_json(max_bytes=MAX_REVIEW_PACKET_BYTES)
                _exact_object_fields(payload, {"fields", "drive_url"}, "Google Drive intake request")
                fields = payload["fields"]
                if not isinstance(fields, Mapping):
                    raise LocalWebApiError(
                        HTTPStatus.BAD_REQUEST,
                        "invalid_request",
                        "fields must be a JSON object",
                    )
                self._send_json(
                    self.server.app.ingest_google_drive_url(
                        fields=_validated_intake_fields(fields),
                        drive_url=str(payload["drive_url"]),
                    ),
                    status=HTTPStatus.CREATED,
                )
            elif parsed.path == "/api/review/validate":
                self._send_json(self.server.app.review_validate(self._read_review_packet()))
            elif parsed.path == "/api/review/apply":
                self._send_json(self.server.app.review_apply(self._read_review_packet()))
            elif parsed.path == "/api/review/apply-fx":
                self._send_json(self.server.app.review_apply_fx(self._read_fx_review()))
            elif parsed.path == "/api/review/confirm":
                self._require_same_origin()
                self._require_json_content_type()
                payload = self._read_json(max_bytes=MAX_REVIEW_PACKET_BYTES)
                _exact_object_fields(payload, {"packet", "fx"}, "confirm request")
                packet = payload["packet"]
                if not isinstance(packet, Mapping):
                    raise LocalWebApiError(
                        HTTPStatus.BAD_REQUEST,
                        "review_packet_invalid",
                        "packet must be a JSON object",
                    )
                fx = payload["fx"]
                if fx is not None and not isinstance(fx, Mapping):
                    raise LocalWebApiError(
                        HTTPStatus.BAD_REQUEST,
                        "fx_invalid",
                        "fx must be null or a JSON object",
                    )
                self._send_json(self.server.app.review_confirm(dict(packet), fx))
            elif parsed.path == "/api/review/post-ready":
                self._require_same_origin()
                self._require_json_content_type()
                payload = self._read_json()
                period = _validate_post_ready_period(payload.get("period"))
                items = _validate_post_ready_items(payload.get("items"))
                result = self.server.app.post_ready(period_key=period, items=items)
                status = (
                    POSTING_ERROR_STATUS_CODES["busy"]
                    if result.get("error") == "posting_in_progress"
                    else HTTPStatus.OK
                )
                self._send_json(result, status=status)
            elif parsed.path.startswith("/api/"):
                self._send_error_json(HTTPStatus.NOT_FOUND, "Unknown API endpoint")
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except LocalWebApiError as exc:
            self._send_error_json(exc.status, str(exc), code=exc.code)
        except FileNotFoundError as exc:
            self._send_error_json(HTTPStatus.NOT_FOUND, str(exc))
        except LocalWebPostingCommandError as exc:
            self._send_json(
                exc.payload,
                status=POSTING_ERROR_STATUS_CODES.get(
                    exc.status,
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                ),
            )
        except (LocalWebError, ValueError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # pragma: no cover - final HTTP boundary
            self._send_error_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"{type(exc).__name__}: {exc}",
            )

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(
            f"[autonomo-web] {self.address_string()} "
            f"{self.log_date_time_string()} {format % args}\n"
        )

    def _require_request_principal(self) -> str | None:
        if not self.server.app.uses_trusted_proxy:
            return None
        if not _is_loopback_peer(self.client_address[0]):
            raise LocalWebApiError(
                HTTPStatus.FORBIDDEN,
                "tailscale_identity_required",
                "Tailscale identity is required",
            )
        principal = _single_header_value(self.headers, TAILSCALE_LOGIN_HEADER)
        forwarded_proto = _single_header_value(self.headers, FORWARDED_PROTO_HEADER)
        if not principal or forwarded_proto.lower() != "https":
            raise LocalWebApiError(
                HTTPStatus.FORBIDDEN,
                "tailscale_identity_required",
                "Tailscale identity is required",
            )
        if principal not in self.server.app.config.allowed_tailscale_logins:
            raise LocalWebApiError(
                HTTPStatus.FORBIDDEN,
                "tailscale_identity_forbidden",
                "Tailscale identity is not allowed",
            )
        return principal

    def _guard_host(self) -> None:
        host, _port = _host_header_parts(
            self.headers.get("Host", ""),
            default_port=self._external_default_port(),
        )
        if self.server.app.uses_trusted_proxy:
            return
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise LocalWebError("Only loopback Host headers are accepted")

    def _guard_origin(self) -> None:
        origin = self.headers.get("Origin")
        if origin is None:
            return
        parsed = urlparse(origin)
        if self.server.app.uses_trusted_proxy:
            host_name, host_port = _host_header_parts(
                self.headers.get("Host", ""),
                default_port=self._external_default_port(),
            )
            if (
                parsed.scheme != "https"
                or parsed.hostname != host_name
                or (parsed.port or 443) != host_port
            ):
                raise LocalWebError("Cross-origin writes are not allowed")
            return
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise LocalWebError("Cross-origin writes are not allowed")

    def _require_same_origin(self) -> None:
        origin = self.headers.get("Origin")
        if not origin:
            raise LocalWebApiError(
                HTTPStatus.FORBIDDEN,
                "origin_required",
                "Origin header is required for review writes",
            )
        parsed = urlparse(origin)
        host_name, host_port = _host_header_parts(
            self.headers.get("Host", ""),
            default_port=self._external_default_port(),
        )
        expected_scheme = "https" if self.server.app.uses_trusted_proxy else "http"
        expected_port = 443 if expected_scheme == "https" else 80
        if (
            parsed.scheme != expected_scheme
            or parsed.hostname != host_name
            or (parsed.port or expected_port) != host_port
        ):
            raise LocalWebApiError(
                HTTPStatus.FORBIDDEN,
                "cross_origin_forbidden",
                "Cross-origin review writes are not allowed",
            )

    def _require_session(self, principal: str | None) -> None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        value = cookie.get(self.server.app.cookie_name)
        if value is None:
            self._raise_session_error()
        expected = self.server.app.session_cookie_value(principal)
        if not secrets.compare_digest(value.value, expected):
            self._raise_session_error()

    def _raise_session_error(self) -> None:
        if self.server.app.uses_trusted_proxy:
            raise LocalWebApiError(
                HTTPStatus.FORBIDDEN,
                "session_forbidden",
                "Session is missing or expired",
            )
        raise LocalWebError("Local session is missing or expired")

    def _serve_static(
        self,
        name: str,
        *,
        set_cookie: bool = False,
        principal: str | None = None,
    ) -> None:
        path = (self.server.app.config.static_root / name).resolve()
        if not _is_relative_to(path, self.server.app.config.static_root):
            raise LocalWebError("Invalid static path")
        if not path.is_file():
            raise FileNotFoundError(path)
        content = path.read_bytes()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", f"{mime_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", self.server.app.session_cookie_header(principal))
        self.end_headers()
        self.wfile.write(content)

    def _serve_document(self, document_id: str) -> None:
        path, mime_type = self.server.app.document_file(document_id)
        size = path.stat().st_size
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header("Content-Type", mime_type)
        self.send_header(
            "Content-Disposition",
            f'inline; filename="{_header_filename(path.name)}"',
        )
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "private, no-store")
        self.end_headers()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                self.wfile.write(chunk)

    def _read_json(self, *, max_bytes: int = 1024 * 1024) -> dict[str, Any]:
        length = _content_length(self.headers)
        if length > max_bytes:
            raise LocalWebError("JSON request is too large")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise LocalWebError("Request body is not valid JSON") from exc
        if not isinstance(value, dict):
            raise LocalWebError("JSON request must be an object")
        return value

    def _read_review_packet(self) -> dict[str, Any]:
        self._require_same_origin()
        self._require_json_content_type()
        payload = self._read_json(max_bytes=MAX_REVIEW_PACKET_BYTES)
        _exact_object_fields(payload, {"packet"}, "review request")
        packet = payload["packet"]
        if not isinstance(packet, Mapping):
            raise LocalWebApiError(
                HTTPStatus.BAD_REQUEST,
                "review_packet_invalid",
                "packet must be a JSON object",
            )
        return dict(packet)

    def _read_fx_review(self) -> dict[str, Any]:
        self._require_same_origin()
        self._require_json_content_type()
        payload = self._read_json(max_bytes=MAX_REVIEW_PACKET_BYTES)
        _exact_object_fields(
            payload,
            {
                "review_id",
                "expected_row_version",
                "rate_date",
                "rate",
                "rate_source",
                "source_reference",
            },
            "FX review request",
        )
        return payload

    def _require_json_content_type(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        mime_type = content_type.split(";", 1)[0].strip().lower()
        if mime_type != "application/json":
            raise LocalWebApiError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "unsupported_content_type",
                "Review writes require Content-Type: application/json",
            )

    def _read_multipart(self) -> tuple[dict[str, str], str, bytes]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            raise LocalWebError("Intake requires multipart/form-data")
        length = _content_length(self.headers)
        if length > MAX_UPLOAD_BYTES + 1024 * 1024:
            raise LocalWebError("Multipart request exceeds the upload limit")
        body = self.rfile.read(length)
        message = BytesParser(policy=email_policy).parsebytes(
            (
                f"Content-Type: {content_type}\r\n"
                "MIME-Version: 1.0\r\n\r\n"
            ).encode("ascii")
            + body
        )
        fields: dict[str, str] = {}
        filename = ""
        content = b""
        for part in message.iter_parts():
            field_name = part.get_param("name", header="content-disposition")
            if not field_name:
                continue
            part_filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if part_filename is not None:
                if field_name != "file":
                    continue
                filename = part_filename
                content = payload
            else:
                fields[field_name] = payload.decode(
                    part.get_content_charset() or "utf-8",
                    errors="replace",
                )
        if not filename:
            raise LocalWebError("No file was uploaded")
        return fields, filename, content

    def _send_json(
        self,
        value: Any,
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        content = (
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _send_error_json(
        self,
        status: HTTPStatus,
        message: str,
        *,
        code: str | None = None,
    ) -> None:
        if self.wfile.closed:
            return
        payload: dict[str, Any] = {"error": message}
        if code is not None:
            payload["code"] = code
        self._send_json(payload, status=status)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        picker_csp = ""
        if self.server.app.config.google_picker_developer_key:
            picker_csp = (
                " https://apis.google.com; frame-src https://drive.google.com "
                "https://docs.google.com; connect-src 'self' https://www.googleapis.com;"
            )
        if picker_csp:
            policy = (
                "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                "script-src 'self'" + picker_csp +
                " object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            )
        else:
            policy = (
                "default-src 'self'; img-src 'self' data:; "
                "style-src 'self'; script-src 'self'; connect-src 'self'; "
                "object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            )
        self.send_header("Content-Security-Policy", policy)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autonomo-web",
        description="Run the loopback-only accounting interface.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_default_project_root(),
    )
    parser.add_argument("--config", type=Path, help="Optional private YAML config file")
    parser.add_argument("--db", type=Path)
    parser.add_argument("--inbox-root", type=Path)
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("autonomo-web only accepts a loopback --host")
    config = load_config(
        args.project_root,
        config_path=args.config,
        database=args.db,
        inbox_root=args.inbox_root,
        archive_root=args.archive_root,
        cache_root=args.cache_root,
    )
    if os.environ.get("AUTONOMO_REQUIRE_STORAGE_MIGRATION") == "1":
        try:
            assert_storage_startup_ready(config.database)
        except StorageMigrationError as exc:
            raise SystemExit(str(exc)) from exc
    app = LocalAccountingApp(config)
    display_host = f"[{args.host}]" if ":" in args.host else args.host
    bind_url = f"http://{display_host}:{args.port}"
    try:
        server = LocalAccountingServer((args.host, args.port), app)
    except OSError as exc:
        if exc.errno in {errno.EADDRINUSE, errno.EACCES}:
            raise SystemExit(
                f"autonomo-web cannot bind {bind_url} (errno {exc.errno}): "
                "the address is already in use or reserved. "
                "Stop the process holding it "
                f"(Get-NetTCPConnection -LocalPort {args.port} -State Listen), "
                "or start on another port with --port <port>."
            ) from exc
        raise SystemExit(f"autonomo-web cannot bind {bind_url}: {exc}") from exc
    host, port = server.server_address[:2]
    print(f"Autónomo accounting: http://{host}:{port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _default_project_root() -> Path:
    candidate = Path(__file__).resolve().parents[2]
    return candidate if (candidate / "pyproject.toml").is_file() else Path.cwd()


def _optional_path(value: Any) -> Path | None:
    if value is None or not str(value).strip():
        return None
    return Path(str(value))


def _configured_proxy_mode(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    mode = str(value).strip().lower()
    if mode not in TRUSTED_PROXY_MODES:
        allowed = ", ".join(sorted(TRUSTED_PROXY_MODES))
        raise LocalWebError(f"trusted_proxy_mode must be one of: {allowed}")
    return mode


def _configured_google_picker_developer_key(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value.strip():
        raise LocalWebError("google_picker_developer_key must be a non-empty string")
    return value.strip()


def _configured_google_picker_app_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    app_id = str(value).strip()
    if not re.fullmatch(r"[0-9]{6,20}", app_id):
        raise LocalWebError("google_picker_app_id must be a Google Cloud project number")
    return app_id


def _configured_string_list(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        raise LocalWebError(f"{field_name} must be a string or list of strings")
    if any(not isinstance(item, str) for item in items):
        raise LocalWebError(f"{field_name} must contain only strings")
    normalized = tuple(item.strip() for item in items if item.strip())
    if len(normalized) != len(set(normalized)):
        raise LocalWebError(f"{field_name} must not contain duplicates")
    return normalized


def _configured_path(value: Any, config_file: Path | None) -> Path | None:
    path = _optional_path(value)
    if path is None:
        return None
    if config_file is None:
        return path.resolve()
    return resolve_config_path(path, config_file=config_file)


def _optional_google_folder_id(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    folder_id = str(value).strip()
    if not GOOGLE_DRIVE_FILE_ID_RE.fullmatch(folder_id):
        raise LocalWebError("Google Drive folder selection is invalid")
    return folder_id


def _google_picker_token(
    token_file: Path,
    developer_key: str,
    app_id: str,
) -> dict[str, Any]:
    """Refresh a user OAuth credential without exposing its refresh token."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        credentials = Credentials.from_authorized_user_file(
            str(token_file),
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            token_file.write_text(credentials.to_json(), encoding="utf-8")
            token_file.chmod(0o600)
        if not credentials.valid or not credentials.token:
            return {"enabled": False}
    except Exception:
        return {"enabled": False}
    return {
        "enabled": True,
        "developer_key": developer_key,
        "app_id": app_id,
        "access_token": str(credentials.token),
    }


def _coerce_session_secret(value: str | bytes | None) -> bytes:
    if value is None:
        env_file = os.environ.get("AUTONOMO_SESSION_PRINCIPAL_SECRET_FILE", "").strip()
        if env_file:
            value = Path(env_file).read_text(encoding="utf-8").strip()
        else:
            value = os.environ.get("AUTONOMO_SESSION_PRINCIPAL_SECRET", "").strip()
    if isinstance(value, bytes):
        secret = value
    elif value:
        secret = str(value).encode("utf-8")
    else:
        secret = secrets.token_bytes(32)
    if not secret:
        raise LocalWebError("Session principal secret must not be empty")
    return secret


def _validate_period(value: str) -> str:
    period = value.strip().upper()
    if not QUARTER_RE.fullmatch(period):
        raise LocalWebError("Quarter must use YYYY-QN")
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
    today = date.today().isoformat()
    for row in rows:
        lifecycle_status = str(row.get("lifecycle_status") or "")
        if lifecycle_status != "approved":
            summary["needs_review"] += 1
            continue
        document_status = row.get("document_status")
        blocked = (
            int(row.get("open_issue_count") or 0) > 0
            or str(row.get("tax_code") or "unknown") == "unknown"
            or document_status not in {None, "approved", "posted", "included_in_snapshot"}
            or (
                str(row.get("currency") or "EUR").upper() != "EUR"
                and not row.get("amount_eur")
            )
        )
        if blocked:
            summary["blocked"] += 1
        elif str(row.get("transaction_date") or "") > today:
            summary["later"] += 1
        else:
            summary["ready"] += 1
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


def _compact_tax_preview(cached: Mapping[str, Any]) -> dict[str, Any]:
    if not cached:
        return {}
    preview = (
        cached.get("tax_arithmetic_preview", {})
        .get("projected_reviewed", {})
    )
    modelo130 = preview.get("modelo130", {})
    modelo303 = preview.get("modelo303", {})
    return {
        "modelo130": {
            "blocked": bool(modelo130.get("blocked")),
            "values": modelo130.get("values", {}),
            "warnings": modelo130.get("warnings", []),
        },
        "modelo303": {
            "blocked": bool(modelo303.get("blocked")),
            "values": modelo303.get("values", {}),
            "warnings": modelo303.get("warnings", []),
        },
    }


def _form_preview_from_cache(cached: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    preview = _compact_tax_preview(cached)
    preview_as_of = cached.get("as_of") if cached else None
    result: dict[str, dict[str, Any]] = {}
    for form_code, form_key in TAX_FORM_KEYS.items():
        values = _coerce_money_values(preview.get(form_key, {}).get("values"))
        headline_value, headline_detail = _headline_fields(form_code, values)
        result[form_key] = {
            "form_code": form_code,
            "display_state": "preview" if values else "unavailable",
            "filed_on": None,
            "snapshot_status": None,
            "snapshot_hash": None,
            "extraction_status": None,
            "values": values,
            "headline_value": headline_value,
            "headline_detail": headline_detail,
            "preview_as_of": preview_as_of if values else None,
        }
    return result


def _validate_post_ready_period(value: Any) -> str:
    if not isinstance(value, str):
        raise LocalWebError("period must be a string")
    return _validate_period(value)


def _validate_post_ready_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise LocalWebError("items must be a list")
    if not value:
        raise LocalWebError("Posting preview has no ready items")
    seen_transaction_ids: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, raw_item in enumerate(value):
        if not isinstance(raw_item, Mapping):
            raise LocalWebError(f"items[{index}] must be an object")
        if "expected_row_version" not in raw_item:
            raise LocalWebError(
                f"items[{index}] must include transaction_id or review_id plus expected_row_version"
            )
        review_id = str(raw_item.get("review_id") or "").strip()
        transaction_id = str(raw_item.get("transaction_id") or "").strip()
        expected_row_version = raw_item.get("expected_row_version")
        if review_id:
            review_kind, separator, subject_id = review_id.partition(":")
            if (
                review_kind != "transaction"
                or separator != ":"
                or UUID_RE.fullmatch(subject_id) is None
            ):
                raise LocalWebError(
                    f"items[{index}].review_id must be transaction:<uuid>"
                )
            if transaction_id and transaction_id != subject_id:
                raise LocalWebError(
                    f"items[{index}].transaction_id must match review_id"
                )
            transaction_id = subject_id
        if not transaction_id:
            raise LocalWebError(
                f"items[{index}] must include transaction_id or review_id"
            )
        if UUID_RE.fullmatch(transaction_id) is None:
            raise LocalWebError(
                f"items[{index}].transaction_id must be a UUID"
            )
        if type(expected_row_version) is not int or expected_row_version < 0:
            raise LocalWebError(
                f"items[{index}].expected_row_version must be a non-negative integer"
            )
        if transaction_id in seen_transaction_ids:
            raise LocalWebError("items must not contain duplicate transaction IDs")
        seen_transaction_ids.add(transaction_id)
        result.append(
            {
                "transaction_id": transaction_id,
                "expected_row_version": expected_row_version,
            }
        )
    return result


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
    if isinstance(ready, list) or isinstance(deferred, list) or isinstance(blocked, list):
        return {
            "ready_to_post": len(ready) if isinstance(ready, list) else 0,
            "deferred": len(deferred) if isinstance(deferred, list) else 0,
            "blocked": len(blocked) if isinstance(blocked, list) else 0,
        }
    items = preview.get("items")
    if isinstance(items, list):
        return {"items": len(items)}
    return {}


def _parse_cli_json_payload(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    candidates = [stripped, *reversed(stripped.splitlines())]
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _normalize_filed_on(raw: Any) -> str | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(raw or ""))
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(0)).isoformat()
    except ValueError:
        return None


def _select_filed_form_snapshot(
    connection: sqlite3.Connection,
    *,
    period_key: str,
    form_code: str,
) -> dict[str, Any] | None:
    normalized_form_code = _normalize_form_code(form_code)
    authoritative_statuses = ", ".join(
        f"'{status}'" for status in AUTHORITATIVE_SNAPSHOT_STATUSES
    )
    rows = connection.execute(
        f"""
        SELECT
            fs.rowid AS snapshot_rowid,
            fs.snapshot_hash,
            fs.filed_on,
            fs.status,
            fs.created_at,
            fs.form_code,
            fs.payload_json
        FROM filing_snapshots fs
        JOIN periods p ON p.period_id = fs.period_id
        WHERE p.period_key = ?
          AND fs.status IN ({authoritative_statuses})
        """,
        (period_key,),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        candidate_form = _normalize_form_code(payload.get("form") or row["form_code"] or "")
        if candidate_form != normalized_form_code:
            continue
        candidates.append(
            {
                "snapshot_rowid": int(row["snapshot_rowid"]),
                "snapshot_hash": str(row["snapshot_hash"] or ""),
                "filed_on": row["filed_on"],
                "normalized_filed_on": _normalize_filed_on(row["filed_on"]),
                "status": str(row["status"] or ""),
                "created_at": str(row["created_at"] or ""),
                "payload": payload,
            }
        )
    if not candidates:
        return None
    ordered = sorted(candidates, key=_snapshot_sort_key, reverse=True)
    authoritative = ordered[0]
    selected = authoritative
    values: dict[str, str] = {}
    if normalized_form_code == "303":
        phase_candidates = (
            [
                row
                for row in ordered
                if row["normalized_filed_on"] == authoritative["normalized_filed_on"]
            ]
            if authoritative["normalized_filed_on"]
            else [authoritative]
        )
        ranked = sorted(
            (
                (
                    _modelo303_extraction_rank(row),
                    _snapshot_sort_key(row),
                    row,
                )
                for row in phase_candidates
            ),
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
        for rank, _, row in ranked:
            if rank <= 0:
                continue
            candidate_values = _extract_modelo303_values(row["payload"])
            if candidate_values:
                selected = row
                values = candidate_values
                break
    else:
        values = _extract_modelo130_values(authoritative["payload"])
    headline_value, headline_detail = _headline_fields(form_code, values)
    payload = selected["payload"] if values else authoritative["payload"]
    return {
        "form_code": form_code,
        "filed_on": authoritative["normalized_filed_on"],
        "snapshot_status": authoritative["status"] or None,
        "snapshot_hash": (
            selected["snapshot_hash"] if values else authoritative["snapshot_hash"]
        )
        or None,
        "extraction_status": _optional_text(payload.get("extraction_status")),
        "values": values,
        "headline_value": headline_value,
        "headline_detail": headline_detail,
        "preview_as_of": None,
    }


def _dashboard_tax_forms(
    connection: sqlite3.Connection,
    *,
    period_key: str,
    obligations: list[dict[str, Any]],
    cached: Mapping[str, Any],
) -> dict[str, Any]:
    preview_forms = _form_preview_from_cache(cached)
    obligations_by_code = {
        str(row.get("obligation_code") or ""): row for row in obligations
    }
    result: dict[str, Any] = {}
    for form_code, form_key in TAX_FORM_KEYS.items():
        obligation = obligations_by_code.get(form_code)
        snapshot = _select_filed_form_snapshot(
            connection,
            period_key=period_key,
            form_code=form_code,
        )
        preview_form = dict(preview_forms[form_key])
        if obligation and obligation.get("filing_status") == "filed":
            if snapshot and snapshot["values"]:
                result[form_key] = {
                    **snapshot,
                    "display_state": "filed",
                }
                continue
            if snapshot:
                result[form_key] = {
                    **snapshot,
                    "display_state": "filed_without_values",
                }
                continue
        if snapshot and snapshot["values"]:
            result[form_key] = {
                **snapshot,
                "display_state": "snapshot_only",
            }
            continue
        result[form_key] = preview_form
    return result


def _normalize_form_code(raw: Any) -> str:
    return str(raw or "").lower().replace("modelo", "").strip()


def _coerce_money_values(
    raw_values: Any,
    *,
    blank_keys: Iterable[Any] = (),
) -> dict[str, str]:
    if not isinstance(raw_values, Mapping):
        raw_values = {}
    values: dict[str, str] = {}
    for key, value in raw_values.items():
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not amount.is_finite():
            continue
        values[str(key)] = f"{amount:.2f}"
    if not isinstance(blank_keys, (list, tuple, set)):
        blank_keys = ()
    for key in blank_keys:
        values.setdefault(str(key), "0.00")
    return values


def _headline_fields(
    form_code: str,
    values: Mapping[str, str],
) -> tuple[str | None, str | None]:
    normalized_form_code = _normalize_form_code(form_code)
    if normalized_form_code == "130":
        return values.get("19"), None
    headline_value = (
        values.get("result")
        or values.get("71")
        or values.get("69")
        or values.get("46")
    )
    headline_detail = (
        values.get("compensation_carryforward") or values.get("72")
    )
    return headline_value, headline_detail


def _snapshot_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    raw_filed_on = str(row.get("filed_on") or "")
    normalized_filed_on = row.get("normalized_filed_on")
    return (
        SNAPSHOT_STATUS_RANKS.get(str(row.get("status") or ""), 0),
        1 if normalized_filed_on else 0,
        normalized_filed_on or "",
        raw_filed_on,
        str(row.get("created_at") or ""),
        int(row.get("snapshot_rowid") or 0),
    )


def _extract_modelo130_values(payload: Mapping[str, Any]) -> dict[str, str]:
    filed_values = payload.get("filed_values")
    if isinstance(filed_values, Mapping):
        values = _coerce_money_values(filed_values)
        if values:
            return values
    values = payload.get("values")
    if isinstance(values, Mapping):
        return _coerce_money_values(values)
    return {}


def _modelo303_extraction_rank(row: Mapping[str, Any]) -> int:
    payload = row["payload"]
    receipt = payload.get("receipt_verification")
    if not isinstance(receipt, Mapping):
        receipt = {}
    values = payload.get("values")
    schema = str(payload.get("value_extraction_schema") or "")
    filed_values = payload.get("filed_values")
    if receipt.get("status") == "matched" and isinstance(values, Mapping):
        return 4
    if schema == "modelo303_v4" and isinstance(filed_values, Mapping):
        return 3
    if isinstance(values, Mapping):
        return 2
    if isinstance(filed_values, Mapping):
        return 1
    return 0


def _extract_modelo303_values(payload: Mapping[str, Any]) -> dict[str, str]:
    receipt = payload.get("receipt_verification")
    if not isinstance(receipt, Mapping):
        receipt = {}
    values = payload.get("values")
    schema = str(payload.get("value_extraction_schema") or "")
    filed_values = payload.get("filed_values")
    if receipt.get("status") == "matched" and isinstance(values, Mapping):
        return _coerce_money_values(values)
    if schema == "modelo303_v4" and isinstance(filed_values, Mapping):
        return _coerce_money_values(
            filed_values,
            blank_keys=payload.get("blank_casillas") or (),
        )
    if isinstance(values, Mapping):
        return _coerce_money_values(values)
    if isinstance(filed_values, Mapping):
        return _coerce_money_values(filed_values)
    return {}


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


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
        raise LocalWebError(f"Unsupported document type: {suffix or '<none>'}")
    target_dir = (inbox_root / period / kind).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = (target_dir / safe_name).resolve()
    if not _is_relative_to(target, target_dir):
        raise LocalWebError("Upload filename escapes the Inbox")
    digest = hashlib.sha256(content).hexdigest()
    if target.exists():
        if _file_sha256(target) == digest:
            return target
        target = target.with_name(
            f"{target.stem}-{digest[:8]}{target.suffix.lower()}"
        )
    try:
        with target.open("xb") as handle:
            handle.write(content)
    except FileExistsError:
        if _file_sha256(target) != digest:
            raise LocalWebError(f"Upload target already exists: {target.name}")
    if _file_sha256(target) != digest:
        target.unlink(missing_ok=True)
        raise LocalWebError("Uploaded file failed SHA-256 verification")
    return target


def _safe_filename(value: str) -> str:
    name = Path(value.replace("\\", "/")).name
    name = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._ ()-]+", "_", name).strip(" .")
    if not name:
        raise LocalWebError("Uploaded file has no usable filename")
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


def _is_loopback_peer(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return value == "localhost"


def _single_header_value(headers: Mapping[str, Any], name: str) -> str:
    values = []
    getter = getattr(headers, "get_all", None)
    if callable(getter):
        values = [item.strip() for item in getter(name, []) if str(item).strip()]
    elif name in headers:
        values = [str(headers[name]).strip()]
    if len(values) != 1:
        return ""
    parts = [item.strip() for item in values[0].split(",") if item.strip()]
    return parts[0] if len(parts) == 1 else ""


def _single_query(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name, [])
    if len(values) != 1:
        raise LocalWebError(f"Query parameter {name!r} is required once")
    return values[0]


def _host_header_parts(value: str, *, default_port: int) -> tuple[str, int]:
    raw = value.strip()
    parsed = urlparse("//" + raw)
    if (
        not raw
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise LocalWebApiError(
            HTTPStatus.BAD_REQUEST,
            "invalid_host",
            "Host header is invalid",
        )
    try:
        port = parsed.port or default_port
    except ValueError as exc:
        raise LocalWebApiError(
            HTTPStatus.BAD_REQUEST,
            "invalid_host",
            "Host header is invalid",
        ) from exc
    return parsed.hostname.lower(), port


def _optional_query(query: Mapping[str, list[str]], name: str) -> str | None:
    values = query.get(name, [])
    if not values:
        return None
    if len(values) != 1:
        raise LocalWebError(f"Query parameter {name!r} must appear once")
    return values[0]


def _content_length(headers: Mapping[str, str]) -> int:
    raw = headers.get("Content-Length")
    if raw is None:
        raise LocalWebError("Content-Length is required")
    try:
        value = int(raw)
    except ValueError as exc:
        raise LocalWebError("Invalid Content-Length") from exc
    if value < 0:
        raise LocalWebError("Invalid Content-Length")
    return value


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
    raise LocalWebApiError(
        HTTPStatus.BAD_REQUEST,
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
        raise LocalWebError("Google Drive URL is invalid")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        raise LocalWebError("Google Drive URL is invalid")
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
        if len(path_parts) >= 3 and path_parts[0] in {
            "document",
            "spreadsheets",
            "presentation",
            "drawings",
        } and path_parts[1] == "d":
            file_id = path_parts[2]
    if not file_id or not GOOGLE_DRIVE_FILE_ID_RE.fullmatch(file_id):
        raise LocalWebError("Google Drive URL is invalid")
    resource_keys = parse_qs(parsed.query, keep_blank_values=True).get("resourcekey", [])
    if len(resource_keys) > 1 or (
        resource_keys and not GOOGLE_DRIVE_FILE_ID_RE.fullmatch(resource_keys[0])
    ):
        raise LocalWebError("Google Drive URL is invalid")
    query = {"id": file_id}
    if resource_keys:
        query["resourcekey"] = resource_keys[0]
    return ("https://" + "drive.google.com/open?" + urlencode(query), file_id)


def _validated_intake_fields(value: Mapping[str, Any]) -> dict[str, str]:
    unexpected = set(value) - INTAKE_FIELD_NAMES
    if unexpected:
        raise LocalWebError("Google Drive intake fields are invalid")
    fields: dict[str, str] = {}
    for name, raw in value.items():
        if not isinstance(name, str) or not isinstance(raw, str):
            raise LocalWebError("Google Drive intake fields are invalid")
        if len(raw) > 4096:
            raise LocalWebError("Google Drive intake fields are invalid")
        fields[name] = raw
    # Apply the same non-provider validation as local uploads before calling
    # the importer, so both intake modes enforce identical business limits.
    period = _validate_period(fields.get("period", ""))
    kind = fields.get("kind", "")
    if kind not in UPLOAD_KINDS:
        raise LocalWebError("kind must be expense_invoice or income_invoice")
    issued_on = fields.get("issued_on", "").strip()
    if issued_on:
        parsed_date = date.fromisoformat(issued_on)
        if _quarter_key(parsed_date) != period:
            raise LocalWebError(
                f"Invoice date belongs to {_quarter_key(parsed_date)}, not {period}"
            )
    return fields


def _cli_error_detail(stderr: str, stdout: str) -> str:
    for raw in (stdout, stderr):
        text = raw.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            detail = payload.get("error") or payload.get("message")
            if detail:
                return str(detail)
    lines = [line.strip() for line in (stderr or stdout).splitlines() if line.strip()]
    if not lines:
        return "CLI command failed without an error message"
    last_line = lines[-1]
    match = re.match(r"^[\w.]+(?:Error|Exception):\s*(.+)$", last_line)
    return match.group(1) if match else last_line


def _header_filename(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._ -]+", "_", value).strip() or "document"


if __name__ == "__main__":
    raise SystemExit(main())
