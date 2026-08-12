from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import errno
from email.parser import BytesParser
from email.policy import default as email_policy
import hashlib
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
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

try:
    import yaml
except Exception:  # pragma: no cover - dependency guard
    yaml = None

from .ledger_db import open as open_ledger_db
from .review_packet import (
    ReviewPacketError,
    classify_review_packet_failure,
    packet_json,
    prepare_review_work_item,
)


QUARTER_RE = re.compile(r"^\d{4}-Q[1-4]$")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{32,36}$")
UPLOAD_KINDS = {"expense_invoice", "income_invoice"}
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
_SO_EXCLUSIVEADDRUSE = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)


class LocalWebError(ValueError):
    pass


class LocalWebApiError(LocalWebError):
    def __init__(self, status: HTTPStatus, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class LocalWebConfig:
    project_root: Path
    database: Path
    inbox_root: Path | None
    archive_root: Path | None
    cache_root: Path
    static_root: Path


def load_config(
    project_root: Path,
    *,
    database: Path | None = None,
    inbox_root: Path | None = None,
    archive_root: Path | None = None,
    cache_root: Path | None = None,
) -> LocalWebConfig:
    root = project_root.resolve()
    private_config = root / ".local" / "config.yaml"
    values: Mapping[str, Any] = {}
    if private_config.is_file():
        if yaml is None:
            raise LocalWebError("PyYAML is required to read .local/config.yaml")
        loaded = yaml.safe_load(private_config.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, Mapping):
            raise LocalWebError(".local/config.yaml must contain a mapping")
        values = loaded

    resolved_database = (
        database
        or _optional_path(values.get("ledger_db"))
        or root / ".local" / "autonomo.sqlite"
    ).resolve()
    resolved_inbox = inbox_root or _optional_path(values.get("inbox_root"))
    resolved_archive = archive_root or _optional_path(
        values.get("drive_evidence_dir") or values.get("archive_root")
    )
    return LocalWebConfig(
        project_root=root,
        database=resolved_database,
        inbox_root=resolved_inbox.resolve() if resolved_inbox else None,
        archive_root=resolved_archive.resolve() if resolved_archive else None,
        cache_root=(
            cache_root or root / ".local" / "web" / "dashboard"
        ).resolve(),
        static_root=(Path(__file__).resolve().parent / "web_ui").resolve(),
    )


class LocalAccountingApp:
    def __init__(self, config: LocalWebConfig, *, session_token: str | None = None) -> None:
        if not config.database.is_file():
            raise FileNotFoundError(f"SQLite database does not exist: {config.database}")
        self.config = config
        self.session_token = session_token or secrets.token_urlsafe(32)

    def bootstrap(self) -> dict[str, Any]:
        with self._connect() as connection:
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
        with self._connect() as connection:
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
            posting_summary = _posting_summary(review_rows)
        cached = self._load_cached_dashboard(period)
        return {
            "period": dict(period_row),
            "totals": totals,
            "recent_transactions": recent,
            "open_issues": open_issues,
            "obligations": obligations,
            "document_counts": document_counts,
            "tax_preview": _compact_tax_preview(cached),
            "forecast_as_of": cached.get("as_of") if cached else None,
            "filing_ready": bool(cached and cached.get("filing_ready")),
            "submission_ready": bool(cached and cached.get("submission_ready")),
            "readyCount": posting_summary["ready"],
            "posting_summary": posting_summary,
        }

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
        with self._connect() as connection:
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
        with self._connect() as connection:
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
                    row["source_path"] and Path(row["source_path"]).is_file()
                ),
                "source_path": None,
            }
            for row in rows
        ]

    def issues(self, period_key: str) -> list[dict[str, Any]]:
        period = _validate_period(period_key)
        with self._connect() as connection:
            period_row = self._period_row(connection, period)
            return self._issues(
                connection,
                period_id=period_row["period_id"],
                limit=250,
            )

    def assets(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
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
        with self._connect() as connection:
            period_row = self._period_row(connection, period)
            obligations = self._obligations(
                connection,
                period_id=period_row["period_id"],
            )
        cached = self._load_cached_dashboard(period)
        return {
            "period": period,
            "obligations": obligations,
            "tax_preview": _compact_tax_preview(cached),
            "forecast_as_of": cached.get("as_of") if cached else None,
            "warnings": list(cached.get("warnings", [])) if cached else [],
        }

    def counterparties(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
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
    ) -> dict[str, Any]:
        if self.config.inbox_root is None or self.config.archive_root is None:
            raise LocalWebError(
                "Intake requires inbox_root and drive_evidence_dir in .local/config.yaml"
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

    def review_work_item(self, review_id: str) -> dict[str, Any]:
        try:
            with open_ledger_db(self.config.database, read_only=True) as db:
                return prepare_review_work_item(db, review_id)
        except ReviewPacketError as exc:
            raise self._review_api_error(exc) from exc

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
        with self._connect() as connection:
            row = connection.execute(
                "SELECT source_path, mime_type FROM documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
        if row is None or not row["source_path"]:
            raise FileNotFoundError("Document source is unavailable")
        path = Path(row["source_path"]).resolve()
        allowed_roots = [
            root
            for root in (
                self.config.archive_root,
                self.config.inbox_root,
                self.config.project_root / "evidence",
            )
            if root is not None
        ]
        if not any(_is_relative_to(path, root.resolve()) for root in allowed_roots):
            raise LocalWebError("Document source is outside configured evidence roots")
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
        candidates = (
            self.config.cache_root / period_key / "dashboard.json",
            self.config.project_root
            / ".local"
            / "dashboard"
            / period_key
            / "dashboard.json",
        )
        for path in candidates:
            if path.is_file():
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if value.get("period") == period_key:
                    return value
        return {}

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

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        try:
            self._guard_host()
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._serve_static("index.html", set_cookie=True)
                return
            if parsed.path in {"/app.js", "/styles.css"}:
                self._serve_static(parsed.path.removeprefix("/"))
                return
            if parsed.path == "/favicon.ico":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._require_session()
            query = parse_qs(parsed.query)
            if parsed.path == "/api/bootstrap":
                self._send_json(self.server.app.bootstrap())
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
            self._guard_host()
            self._require_session()
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
                self._send_json(
                    self.server.app.ingest_upload(
                        fields=fields,
                        filename=filename,
                        content=content,
                    ),
                    status=HTTPStatus.CREATED,
                )
            elif parsed.path == "/api/review/validate":
                self._send_json(self.server.app.review_validate(self._read_review_packet()))
            elif parsed.path == "/api/review/apply":
                self._send_json(self.server.app.review_apply(self._read_review_packet()))
            elif parsed.path == "/api/review/apply-fx":
                self._send_json(self.server.app.review_apply_fx(self._read_fx_review()))
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

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(
            f"[autonomo-web] {self.address_string()} "
            f"{self.log_date_time_string()} {format % args}\n"
        )

    def _guard_host(self) -> None:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]").lower()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise LocalWebError("Only loopback Host headers are accepted")

    def _guard_origin(self) -> None:
        origin = self.headers.get("Origin")
        if origin is None:
            return
        parsed = urlparse(origin)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
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
            default_port=self.server.server_address[1],
        )
        if (
            parsed.scheme != "http"
            or parsed.hostname != host_name
            or (parsed.port or 80) != host_port
        ):
            raise LocalWebApiError(
                HTTPStatus.FORBIDDEN,
                "cross_origin_forbidden",
                "Cross-origin review writes are not allowed",
            )

    def _require_session(self) -> None:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        value = cookie.get("autonomo_session")
        if value is None or not secrets.compare_digest(
            value.value,
            self.server.app.session_token,
        ):
            raise LocalWebError("Local session is missing or expired")

    def _serve_static(self, name: str, *, set_cookie: bool = False) -> None:
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
            self.send_header(
                "Set-Cookie",
                "autonomo_session="
                f"{self.server.app.session_token}; Path=/; HttpOnly; SameSite=Strict",
            )
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
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'; connect-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )


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
        database=args.db,
        inbox_root=args.inbox_root,
        archive_root=args.archive_root,
        cache_root=args.cache_root,
    )
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


def _posting_summary(rows: list[Mapping[str, Any]]) -> dict[str, int]:
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


def _single_query(query: Mapping[str, list[str]], name: str) -> str:
    values = query.get(name, [])
    if len(values) != 1:
        raise LocalWebError(f"Query parameter {name!r} is required once")
    return values[0]


def _host_header_parts(value: str, *, default_port: int) -> tuple[str, int]:
    parsed = urlparse("//" + value.strip())
    if parsed.hostname is None:
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
