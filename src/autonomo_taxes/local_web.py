from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
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
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from uuid import UUID

from .ui_assets import UiAssets
from .services.application import AccountingService
from .services.common import ServiceApiError as LocalWebApiError, ServiceError as LocalWebError, PostingOperationError as LocalWebPostingCommandError, _basis_points_percent, _counterparty_api_state, _current_or_latest_period, _empty_scope, _exact_object_fields, _file_sha256, _is_relative_to, _minor_to_text, _posting_preview_summary, _quarter_key, _ratio_percent, _review_summary, _row_dict, _safe_filename, _store_upload, _validate_period, _validated_counterparty_id, _validated_intake_fields, normalize_google_drive_url

from . import expense_workflow
from .expense_workflow import ExpenseWorkflowError

try:
    import yaml
except Exception:  # pragma: no cover - dependency guard
    yaml = None

from .analytics_series import AnalyticsQuery, build_analytics
from .account_settings import AccountSettingsError, read_settings, save_backups, save_profile
from .fx_reference import ECBRateObservation, FXReferenceError, fetch_eur_rate
from .expense_view import enrich_expense_context, expense_page, expense_summary
from .ledger_db import LedgerDB, LedgerDbError, StaleRowVersionError, open as open_ledger_db
from .legacy_paths import LegacyPathResolver
from .posting import build_posting_preview
from .status_context import StatusContext, reason as status_reason, simple_context
from .private_paths import (
    PrivatePathError,
    config_path as resolve_config_path,
    configured_private_root,
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
from .tax_result_view import (
    build_tax_summary,
    compact_tax_preview as _compact_tax_preview,
    form_results as _dashboard_tax_forms,
    period_status as _tax_period_status,
    year_form_results,
)


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
    "/settings",
}

MAX_SETTINGS_BYTES = 16 * 1024


def _is_spa_route(path: str) -> bool:
    if path in SPA_TOP_LEVEL_ROUTES:
        return True
    for prefix in ("/expenses/", "/review/", "/contacts/"):
        if path.startswith(prefix):
            return UUID_RE.fullmatch(path[len(prefix):]) is not None
    return False


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
MAX_REVIEW_PACKET_BYTES = 64 * 1024
MAX_GOOGLE_DRIVE_URL_BYTES = 2048
_SO_EXCLUSIVEADDRUSE = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
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
from .services.common import _POSTING_BATCH_LOCK
TRUSTED_PROXY_MODES = {"tailscale_serve"}
REMOTE_COOKIE_NAME = "__Host-autonomo_session"
LOCAL_COOKIE_NAME = "autonomo_session"
TAILSCALE_LOGIN_HEADER = "Tailscale-User-Login"
FORWARDED_PROTO_HEADER = "X-Forwarded-Proto"












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
    private_root: Path | None = None
    allow_test_ui: bool = False


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
    # Ops uses this explicit environment root, including when --config points
    # into a separately managed SOPS generation. Never derive it from config.
    backup_root = None
    if os.environ.get("AUTONOMO_PRIVATE_ROOT", "").strip():
        try:
            backup_root = configured_private_root()
        except PrivatePathError:
            pass
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
        private_root=backup_root,
    )


class LocalAccountingApp(AccountingService):
    def __init__(
        self,
        config: LocalWebConfig,
        *,
        session_token: str | None = None,
        principal_session_secret: str | bytes | None = None,
    ) -> None:
        super().__init__(config)
        self.ui_assets = UiAssets(config.static_root, allow_test=config.allow_test_ui)
        self.session_token = session_token or secrets.token_urlsafe(32)
        self.principal_session_secret = (
            _coerce_session_secret(principal_session_secret)
            if self.config.trusted_proxy_mode == "tailscale_serve"
            else None
        )

    @property
    def uses_trusted_proxy(self) -> bool:
        return self.config.trusted_proxy_mode == "tailscale_serve"

    @property
    def cookie_name(self) -> str:
        return REMOTE_COOKIE_NAME if self.uses_trusted_proxy else LOCAL_COOKIE_NAME

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
            if parsed.path.startswith("/ui-assets/"):
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
            elif parsed.path == "/api/settings":
                self._send_json(read_settings(
                    self.server.app.config.database, self.server.app.config.private_root,
                ))
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
            elif parsed.path == "/api/expenses":
                self._send_json(
                    self.server.app.expenses(
                        _single_query(query, "period"),
                        query=_optional_query(query, "q"),
                        offset=int(_optional_query(query, "offset") or "0"),
                        limit=int(_optional_query(query, "limit") or "250"),
                    )
                )
            elif parsed.path.startswith("/api/transactions/"):
                self._send_json(
                    self.server.app.transaction_detail(
                        parsed.path[len("/api/transactions/"):]
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
            elif match := re.fullmatch(r"/api/expense-workflows/([^/]+)", parsed.path):
                self._send_json(self.server.app.expense_draft(match.group(1)))
            elif match := re.fullmatch(r"/api/assets/([^/]+)/schedule", parsed.path):
                self._send_json(self.server.app.depreciation_schedule(match.group(1)))
            elif parsed.path == "/api/assets":
                self._send_json(self.server.app.assets(_optional_query(query, "period")))
            elif parsed.path == "/api/taxes":
                self._send_json(
                    self.server.app.taxes(_single_query(query, "period"))
                )
            elif parsed.path == "/api/analytics":
                self._send_json(
                    self.server.app.analytics(
                        _single_query(query, "period"),
                        as_of=_optional_query(query, "as_of"),
                    )
                )
            elif parsed.path == "/api/review/posting-preview":
                self._send_json(
                    self.server.app.posting_preview(_single_query(query, "period"))
                )
            elif parsed.path == "/api/counterparties":
                self._send_json(self.server.app.counterparties())
            elif match := re.fullmatch(r"/api/counterparties/([^/]+)/transactions", parsed.path):
                self._send_json(self.server.app.counterparty_transactions(
                    unquote(match.group(1)), period_key=_optional_query(query, "period"),
                    offset=int(_optional_query(query, "offset") or "0"),
                    limit=int(_optional_query(query, "limit") or "50"),
                ))
            elif match := re.fullmatch(r"/api/counterparties/([^/]+)", parsed.path):
                self._send_json(self.server.app.counterparty_detail(unquote(match.group(1))))
            elif match := re.fullmatch(r"/api/counterparties/([^/]+)/name-history", parsed.path):
                self._send_json(self.server.app.counterparty_name_history(unquote(match.group(1))))
            elif parsed.path == "/api/review/work-item":
                self._send_json(
                    self.server.app.review_work_item(_single_query(query, "review_id"))
                )
            elif match := re.fullmatch(r"/api/document/([^/]+)/preview", parsed.path):
                self._serve_document(match.group(1), preview=True)
            elif parsed.path.startswith("/api/document/") and parsed.path.endswith(
                "/content"
            ):
                document_id = parsed.path.split("/")[3]
                self._serve_document(document_id)
            elif parsed.path.startswith("/api/"):
                self._send_error_json(HTTPStatus.NOT_FOUND, "Unknown API endpoint")
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (ExpenseWorkflowError, AccountSettingsError) as exc:
            self._send_error_json(HTTPStatus(exc.status), str(exc), code=exc.code)
        except LocalWebApiError as exc:
            self._send_error_json(exc.status, str(exc), code=exc.code, current=exc.current,
                message_code=exc.message_code, params=exc.params, field=exc.field)
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
            if match := re.fullmatch(r"/api/expense-workflows/([^/]+)/(save|preview|confirm|follow-up)", parsed.path):
                self._require_same_origin()
                self._require_json_content_type()
                payload = self._read_json(max_bytes=150_000)
                identifier, action = match.groups()
                actor = principal or "local-session"
                if action == "save":
                    result = self.server.app.expense_save(identifier, payload, actor)
                elif action == "preview":
                    result = self.server.app.expense_preview(identifier, payload)
                elif action == "confirm":
                    result = self.server.app.expense_confirm(identifier, payload, actor)
                else:
                    _exact_object_fields(payload, set(), "expense follow-up request")
                    result = self.server.app.expense_follow_up(identifier)
                self._send_json(result)
            elif match := re.fullmatch(r"/api/depreciation/([^/]+)/post", parsed.path):
                self._require_same_origin()
                self._require_json_content_type()
                self._send_json(self.server.app.depreciation_post(match.group(1), self._read_json(), principal or "local-session"))
            elif parsed.path in {"/api/settings/profile", "/api/settings/backups"}:
                self._require_same_origin()
                self._require_json_content_type()
                payload = self._read_json(max_bytes=MAX_SETTINGS_BYTES)
                config = self.server.app.config
                result = (
                    save_profile(config.database, payload, actor=principal)
                    if parsed.path.endswith("/profile")
                    else save_backups(config.database, config.private_root, payload)
                )
                self._send_json(result)
            elif parsed.path == "/api/dashboard/refresh":
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
            elif match := re.fullmatch(r"/api/counterparties/([^/]+)/rename", parsed.path):
                self._require_same_origin()
                self._require_json_content_type()
                self._send_json(self.server.app.rename_counterparty(
                    unquote(match.group(1)), self._read_json(max_bytes=MAX_REVIEW_PACKET_BYTES),
                    actor=principal,
                ))
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
        except (ExpenseWorkflowError, AccountSettingsError) as exc:
            self._send_error_json(HTTPStatus(exc.status), str(exc), code=exc.code)
        except LocalWebApiError as exc:
            self._send_error_json(exc.status, str(exc), code=exc.code, current=exc.current,
                message_code=exc.message_code, params=exc.params, field=exc.field)
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
        raise LocalWebApiError(HTTPStatus.BAD_REQUEST, "session_forbidden", "Local session is missing or expired")

    def _serve_static(
        self,
        name: str,
        *,
        set_cookie: bool = False,
        principal: str | None = None,
    ) -> None:
        path = self.server.app.ui_assets.path(name)
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

    def _serve_document(self, document_id: str, *, preview: bool = False) -> None:
        path, mime_type = self.server.app.document_file(document_id)
        if preview:
            signatures = {"application/pdf": b"%PDF-", "image/jpeg": b"\xff\xd8\xff",
                          "image/png": b"\x89PNG\r\n\x1a\n"}
            with path.open("rb") as source:
                header = source.read(16)
            if mime_type not in signatures or not header.startswith(signatures[mime_type]):
                raise LocalWebApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "preview_unavailable",
                    "Inline preview supports verified PDF, JPEG and PNG; use Open original")
        size = path.stat().st_size
        self.send_response(HTTPStatus.OK)
        self._security_headers(document_preview=preview)
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
        current: Mapping[str, Any] | None = None,
        message_code: str | None = None,
        params: Mapping[str, Any] | None = None,
        field: str | None = None,
    ) -> None:
        if self.wfile.closed:
            return
        payload: dict[str, Any] = {"error": message}
        if code is not None:
            payload["code"] = code
        if current is not None:
            payload["current"] = dict(current)
        if message_code is not None:
            payload["message_code"] = message_code
        if params is not None:
            payload["params"] = dict(params)
        if field is not None:
            payload["field"] = field
        self._send_json(payload, status=status)

    def _security_headers(self, *, document_preview: bool = False) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN" if document_preview else "DENY")
        picker_enabled = bool(
            self.server.app.config.google_picker_developer_key
            and self.server.app.config.google_picker_app_id
        )
        self.send_header(
            "Referrer-Policy",
            "strict-origin-when-cross-origin" if picker_enabled else "no-referrer",
        )
        picker_csp = ""
        if picker_enabled:
            picker_csp = (
                " https://apis.google.com; frame-src 'self' https://drive.google.com "
                "https://docs.google.com; connect-src 'self' https://www.googleapis.com;"
            )
        if document_preview:
            policy = "default-src 'none'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'"
        elif picker_csp:
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


def main(argv: list[str] | None = None, *, static_root: Path | None = None) -> int:
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
    if static_root is None:
        try:
            from autonomo_taxes_ui import __file__ as ui_file
        except ImportError:
            raise SystemExit("Install the optional spain-autonomo-taxes-ui package to run the web interface") from None
        from importlib.metadata import version
        if version("spain-autonomo-taxes") != version("spain-autonomo-taxes-ui"):
            raise SystemExit("Core and optional UI package versions must match")
        static_root = Path(ui_file).resolve().parent / "dist"
    from dataclasses import replace
    config = replace(config, static_root=static_root)
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
