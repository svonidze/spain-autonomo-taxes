"""Shared application operations for CLI and HTTP adapters."""

from __future__ import annotations
from contextlib import closing
from datetime import date
import json
import mimetypes
from pathlib import Path
import sqlite3
from typing import Any, Mapping
from uuid import UUID
from ..analytics_series import AnalyticsQuery, build_analytics
from ..expense_view import enrich_expense_context, expense_page, expense_summary
from ..ledger_db import StaleRowVersionError, open as open_ledger_db
from ..posting import build_posting_preview
from ..status_context import StatusContext, reason as status_reason, simple_context
from ..storage_service import (
    resolve_verified_filesystem_replica,
    resolve_verified_replica,
)
from ..tax_result_view import (
    build_tax_summary,
    compact_tax_preview as _compact_tax_preview,
    form_results as _dashboard_tax_forms,
    period_status as _tax_period_status,
    year_form_results,
)
from .common import (
    QUARTER_RE,
    ServiceApiError,
    ServiceError,
    UUID_RE,
    _basis_points_percent,
    _counterparty_api_state,
    _current_or_latest_period,
    _empty_scope,
    _exact_object_fields,
    _is_relative_to,
    _minor_to_text,
    _posting_preview_summary,
    _ratio_percent,
    _review_summary,
    _row_dict,
    _validate_period,
    _validated_counterparty_id,
)


class QueryService:
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
                "SELECT full_name FROM taxpayer_profile ORDER BY created_at, taxpayer_profile_id LIMIT 1"
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
            raise ServiceError("entry_type must be income or expense")
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
            raise ServiceError("Unsupported lifecycle status")
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

    def expenses(
        self,
        period_key: str,
        *,
        query: str | None = None,
        offset: int = 0,
        limit: int = 250,
        today: date | None = None,
    ) -> dict[str, Any]:
        period = _validate_period(period_key)
        if offset < 0 or not 1 <= limit <= 500:
            raise ServiceError(
                "Expense page requires offset >= 0 and limit between 1 and 500"
            )
        effective_today = today or date.today()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            period_row = self._period_row(connection, period)
            rows = self._transactions(
                connection,
                period_id=period_row["period_id"],
                entry_type="expense",
                limit=-1,
                today=effective_today,
            )
            return expense_page(
                rows,
                period=period,
                today=effective_today,
                query=query,
                offset=offset,
                limit=limit,
            )

    def transaction_detail(self, transaction_id: str) -> dict[str, Any]:
        """Return one stored transaction projection without invoking review workflows."""
        try:
            normalized_id = str(UUID(str(transaction_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ServiceApiError(
                400,
                "invalid_transaction_id",
                "transaction_id must be a UUID",
            ) from exc

        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            try:
                row = connection.execute(
                    """
                    SELECT
                        t.transaction_id,
                        t.entry_type,
                        t.transaction_date,
                        t.booking_date,
                        t.description,
                        t.lifecycle_status,
                        t.row_version,
                        t.amount_minor,
                        t.currency,
                        t.amount_original_minor,
                        t.original_currency,
                        t.amount_eur_minor,
                        p.period_key,
                        p.status AS period_status,
                        d.document_id,
                        d.document_number,
                        d.document_type,
                        d.issued_on,
                        d.lifecycle_status AS document_lifecycle_status,
                        c.display_name AS counterparty_display_name,
                        c.country_code AS counterparty_country_code
                    FROM transactions t
                    JOIN periods p ON p.period_id = t.period_id
                    LEFT JOIN documents d ON d.document_id = t.document_id
                    LEFT JOIN counterparties c ON c.counterparty_id = t.counterparty_id
                    WHERE t.transaction_id = ?
                    """,
                    (normalized_id,),
                ).fetchone()
                if row is None:
                    raise ServiceApiError(
                        404,
                        "transaction_not_found",
                        "Transaction was not found",
                    )
                action = connection.execute(
                    "SELECT result_json FROM expense_actions WHERE json_extract(result_json,'$.transaction_id')=? ORDER BY created_at DESC LIMIT 1",
                    (normalized_id,),
                ).fetchone()
                follow_up = json.loads(action[0]) if action else None
                treatments = connection.execute(
                    """
                    SELECT
                        treatment_id,
                        treatment_type,
                        jurisdiction,
                        tax_code,
                        deductible_irpf_minor,
                        deductible_vat_minor,
                        include_modelo130,
                        include_modelo303,
                        include_modelo347,
                        vat_investment_good,
                        notes
                    FROM tax_treatments
                    WHERE transaction_id = ?
                    ORDER BY treatment_type, jurisdiction, treatment_id
                    """,
                    (normalized_id,),
                ).fetchall()
            finally:
                connection.rollback()

        return {
            "workflow_follow_up": follow_up,
            "transaction": {
                "transaction_id": row["transaction_id"],
                "entry_type": row["entry_type"],
                "transaction_date": row["transaction_date"],
                "booking_date": row["booking_date"],
                "description": row["description"],
                "lifecycle_status": row["lifecycle_status"],
                "row_version": row["row_version"],
                "amount_minor": row["amount_minor"],
                "currency": row["currency"],
                "amount_original_minor": row["amount_original_minor"],
                "original_currency": row["original_currency"],
                "amount_eur_minor": row["amount_eur_minor"],
            },
            "period": {
                "period_key": row["period_key"],
                "status": row["period_status"],
            },
            "document": (
                None
                if row["document_id"] is None
                else {
                    "document_id": row["document_id"],
                    "document_number": row["document_number"],
                    "document_type": row["document_type"],
                    "issued_on": row["issued_on"],
                    "lifecycle_status": row["document_lifecycle_status"],
                }
            ),
            "counterparty": (
                None
                if row["counterparty_display_name"] is None
                else {
                    "display_name": row["counterparty_display_name"],
                    "country_code": row["counterparty_country_code"],
                }
            ),
            "tax_treatments": [
                {
                    "treatment_id": treatment["treatment_id"],
                    "treatment_type": treatment["treatment_type"],
                    "jurisdiction": treatment["jurisdiction"],
                    "tax_code": treatment["tax_code"],
                    "deductible_irpf_minor": treatment["deductible_irpf_minor"],
                    "deductible_vat_minor": treatment["deductible_vat_minor"],
                    "include_modelo130": bool(treatment["include_modelo130"]),
                    "include_modelo303": bool(treatment["include_modelo303"]),
                    "include_modelo347": bool(treatment["include_modelo347"]),
                    "vat_investment_good": (
                        None
                        if treatment["vat_investment_good"] is None
                        else bool(treatment["vat_investment_good"])
                    ),
                    "notes": treatment["notes"],
                }
                for treatment in treatments
            ],
        }

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
            builder = self._status_context(connection)
            contexts = {r["document_id"]: builder.document(dict(r)) for r in rows}
        return [
            {
                **_row_dict(row),
                "ui_context": contexts[row["document_id"]],
                "total_eur": (
                    _minor_to_text(row["total_minor"])
                    if row["currency"] == "EUR"
                    else None
                ),
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

    def _status_context(
        self, connection: sqlite3.Connection, *, today: date | None = None
    ) -> StatusContext:
        return StatusContext(
            connection,
            inbox_root=self.config.inbox_root,
            archive_root=self.config.archive_root,
            resolve_path=self.resolve_document_path,
            today=today,
        )

    def assets(self, period_key: str | None = None) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            periods = [
                r[0]
                for r in connection.execute(
                    "SELECT period_key FROM periods WHERE period_type='quarter' AND period_key LIKE '%-Q_' ORDER BY period_key DESC"
                )
            ]
            period = (
                _validate_period(period_key)
                if period_key is not None
                else _current_or_latest_period(periods)
            )
            if period is not None and period not in periods:
                raise ServiceApiError(404, "period_not_found", "Unknown quarter")
            rows = connection.execute(
                """
                SELECT a.*, d.counterparty_id, c.display_name AS counterparty_name
                FROM assets a LEFT JOIN documents d ON d.document_id=a.document_id
                LEFT JOIN counterparties c ON c.counterparty_id=d.counterparty_id
                ORDER BY a.placed_in_service_on DESC,a.asset_code
            """
            ).fetchall()
            contexts = self._status_context(connection)
            result = []
            for raw in rows:
                row = dict(raw)
                context = contexts.asset(row, period)
                schedule = context["amortization"]
                scheduled = schedule["book_minor"] + schedule["excluded_minor"]
                result.append(
                    {
                        **{
                            k: row[k]
                            for k in (
                                "asset_id",
                                "asset_code",
                                "description",
                                "placed_in_service_on",
                                "currency",
                                "cost_minor",
                                "amortizable_base_minor",
                                "business_use_ratio",
                                "annual_rate_basis_points",
                                "depreciation_method",
                                "advisor_decision",
                                "source_invoice_number",
                                "counterparty_name",
                            )
                        },
                        "period": period,
                        "schedule_rows": schedule["count"],
                        "scheduled_minor": scheduled,
                        "scheduled": _minor_to_text(scheduled),
                        "cost": _minor_to_text(row["cost_minor"]),
                        "amortizable_base": _minor_to_text(
                            row["amortizable_base_minor"]
                        ),
                        "business_use_percent": _ratio_percent(
                            row["business_use_ratio"]
                        ),
                        "annual_rate_percent": _basis_points_percent(
                            row["annual_rate_basis_points"]
                        ),
                        "ui_context": context,
                    }
                )
            return result

    def document_file(self, document_id: str) -> tuple[Path, str]:
        if not UUID_RE.fullmatch(document_id):
            raise ServiceError("Invalid document id")
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
            raise ServiceError("Document source is outside configured evidence roots")
        for root in self.config.read_only_document_roots:
            resolved_root = root.resolve()
            if _is_relative_to(path, resolved_root) and not path.is_file():
                raise ServiceApiError(
                    503,
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
            raise ServiceError(f"Unknown quarter: {period_key}")
        return row

    def _transactions(
        self,
        connection: sqlite3.Connection,
        *,
        period_id: str | None,
        entry_type: str | None = None,
        lifecycle_status: str | None = None,
        query: str | None = None,
        limit: int,
        counterparty_id: str | None = None,
        offset: int = 0,
        today: date | None = None,
        context_builder: StatusContext | None = None,
    ) -> list[dict[str, Any]]:
        where = ["1 = 1"]
        parameters: list[Any] = []
        if period_id is not None:
            where.append("t.period_id = ?")
            parameters.append(period_id)
        if counterparty_id is not None:
            where.append("t.counterparty_id = ?")
            parameters.append(counterparty_id)
        if entry_type:
            where.append("(t.entry_type = ? OR t.entry_type LIKE ?)")
            parameters.extend((entry_type, entry_type + "_%"))
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
        parameters.extend((limit, offset))
        rows = connection.execute(
            f"""
            SELECT
                t.transaction_id,
                t.counterparty_id,
                t.transaction_date,
                t.entry_type,
                t.description,
                t.amount_minor,
                t.currency,
                t.amount_eur_minor,
                t.lifecycle_status,
                t.row_version,
                t.document_id,
                p.period_key,
                c.display_name AS counterparty_name,
                c.country_code,
                d.document_number,
                d.issued_on AS document_issued_on,
                d.total_minor AS document_total_minor,
                d.currency AS document_currency,
                d.document_type,
                d.lifecycle_status AS document_status,
                tt.tax_code,
                tt.deductible_irpf_minor,
                tt.deductible_vat_minor,
                tt.include_modelo130,
                tt.include_modelo303,
                COUNT(DISTINCT i.validation_issue_id) AS open_issue_count,
                COUNT(DISTINCT CASE WHEN i.blocking=1 THEN i.validation_issue_id END) AS blocking_issue_count
            FROM transactions t
            JOIN periods p ON p.period_id = t.period_id
            LEFT JOIN counterparties c ON c.counterparty_id = t.counterparty_id
            LEFT JOIN documents d ON d.document_id = t.document_id
            LEFT JOIN (
                SELECT transaction_id,
                  CASE WHEN COUNT(DISTINCT tax_code)=1 THEN MAX(tax_code) ELSE 'unknown' END AS tax_code,
                  CASE WHEN COUNT(DISTINCT deductible_irpf_minor)<=1 THEN MAX(deductible_irpf_minor) END AS deductible_irpf_minor,
                  CASE WHEN COUNT(DISTINCT deductible_vat_minor)<=1 THEN MAX(deductible_vat_minor) END AS deductible_vat_minor,
                  MAX(include_modelo130) AS include_modelo130, MAX(include_modelo303) AS include_modelo303
                FROM tax_treatments x WHERE treatment_type <> 'invoice_review' OR NOT EXISTS (SELECT 1 FROM tax_treatments preferred WHERE preferred.transaction_id=x.transaction_id AND preferred.treatment_type <> 'invoice_review')
                GROUP BY transaction_id
            ) tt ON tt.transaction_id=t.transaction_id
            LEFT JOIN validation_issues i
                ON i.issue_status = 'open'
                AND (
                    (i.subject_table = 'transactions' AND i.subject_id = t.transaction_id)
                    OR (i.subject_table = 'documents' AND i.subject_id = t.document_id)
                    OR (i.subject_table = 'counterparties' AND i.subject_id = t.counterparty_id)
                )
            WHERE {" AND ".join(where)}
            GROUP BY t.transaction_id
            ORDER BY t.transaction_date DESC, t.created_at DESC, t.transaction_id DESC
            LIMIT ? OFFSET ?
            """,
            parameters,
        ).fetchall()
        effective_today = today or date.today()
        builder = context_builder or self._status_context(
            connection, today=effective_today
        )
        builder.prime_transactions([row["transaction_id"] for row in rows])
        result = [
            {
                **_row_dict(row),
                "document_amount_eur": (
                    _minor_to_text(row["document_total_minor"])
                    if row["document_currency"] == "EUR"
                    else None
                ),
                "ui_context": builder.transaction(row["transaction_id"]),
                "amount_original": _minor_to_text(row["amount_minor"]),
                "amount_eur": _minor_to_text(
                    row["amount_eur_minor"]
                    if row["amount_eur_minor"] is not None
                    else row["amount_minor"] if row["currency"] == "EUR" else None
                ),
                "deductible_irpf_eur": _minor_to_text(row["deductible_irpf_minor"]),
                "deductible_vat_eur": _minor_to_text(row["deductible_vat_minor"]),
            }
            for row in rows
        ]
        if period_id is not None:
            return enrich_expense_context(
                connection,
                result,
                period_id=period_id,
                today=effective_today,
            )
        for period_key in dict.fromkeys(row["period_key"] for row in result):
            enrich_expense_context(
                connection,
                [row for row in result if row["period_key"] == period_key],
                period_id=self._period_row(connection, period_key)["period_id"],
                today=effective_today,
            )
        return result

    def _issues(
        self,
        connection: sqlite3.Connection,
        *,
        period_id: str,
        limit: int,
        context_builder: StatusContext | None = None,
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
        builder = context_builder or self._status_context(connection)
        result = []
        for raw in rows:
            row = dict(raw)
            context = {
                "domain": "issue",
                "state": "blocked" if row["blocking"] else "warning",
                "subject_id": row["validation_issue_id"],
                "reasons": [status_reason(row)],
                "actions": [],
            }
            if row["subject_table"] == "transactions":
                context = dict(builder.transaction(row["subject_id"]))
            elif row["subject_table"] == "documents":
                document = connection.execute(
                    "SELECT * FROM documents WHERE document_id=?", (row["subject_id"],)
                ).fetchone()
                if document is not None:
                    context = builder.document(dict(document))
            context.update(
                {
                    "domain": "issue",
                    "subject_id": row["validation_issue_id"],
                    "state": "blocked" if row["blocking"] else "warning",
                    "reasons": [status_reason(row)],
                }
            )
            row["ui_context"] = context
            result.append(row)
        return result

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
            raise ServiceError("Posting preview must be a JSON object")
        return preview

    def export_original(self, document_id: str, output: Path) -> dict[str, Any]:
        import hashlib
        import os
        import tempfile
        from .common import private_output

        output = private_output(self.config, output)
        path, media_type = self.document_file(document_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT source_hash FROM documents WHERE document_id=?", (document_id,)
            ).fetchone()
        expected = row["source_hash"] if row else None
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = None
        try:
            digest = hashlib.sha256()
            with tempfile.NamedTemporaryFile(
                dir=output.parent, prefix=".original-", delete=False
            ) as target:
                temporary = Path(target.name)
                with path.open("rb") as original:
                    for chunk in iter(lambda: original.read(65536), b""):
                        target.write(chunk)
                        digest.update(chunk)
            actual = digest.hexdigest()
            if expected and actual != expected:
                raise ServiceApiError(
                    409, "source_mismatch", "Original differs from its recorded hash"
                )
            os.link(
                temporary, output
            )  # Exclusive publication; never replace another file.
            return {
                "document_id": document_id,
                "file_name": output.name,
                "sha256": actual,
                "source_hash_matched": bool(expected),
                "media_type": media_type,
            }
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
