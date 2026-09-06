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


class FinancialsService:
    def dashboard(self, period_key: str) -> dict[str, Any]:
        period = _validate_period(period_key)
        today = date.today()
        cached = self._load_cached_dashboard(period)
        with closing(self._connect()) as connection:
            period_row = self._period_row(connection, period)
            context_builder = self._status_context(connection, today=today)
            totals = self._transaction_totals(connection, period_row["period_id"])
            expense_rows = self._transactions(
                connection,
                period_id=period_row["period_id"],
                entry_type="expense",
                limit=-1,
                today=today,
                context_builder=context_builder,
            )
            recent = self._transactions(
                connection,
                period_id=period_row["period_id"],
                limit=8,
                today=today,
                context_builder=context_builder,
            )
            open_issues = self._issues(
                connection,
                period_id=period_row["period_id"],
                limit=6,
                context_builder=context_builder,
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
            posting_preview = context_builder.preview_payload(period)
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
                context_builder=context_builder,
            )
            review_summary = _review_summary(review_rows)
        return {
            "period": dict(period_row),
            "totals": totals,
            "expense_summary": expense_summary(expense_rows),
            "expense_view_as_of": today.isoformat(),
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

    def taxes(self, period_key: str, *, as_of: date | None = None) -> dict[str, Any]:
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
            period_details = _tax_period_status(connection, period)
            if period_details is None:  # pragma: no cover - guarded by _period_row
                raise ServiceError(f"Unknown quarter: {period}")
            period_state, tax_summary = build_tax_summary(
                connection,
                period=period_details,
                obligations=obligations,
                tax_forms=tax_forms,
                cached=cached,
                as_of=as_of or date.today(),
            )
        return {
            "period": period,
            "period_state": period_state,
            "obligations": obligations,
            "tax_preview": _compact_tax_preview(cached),
            "tax_forms": tax_forms,
            "tax_summary": tax_summary,
            "forecast_as_of": cached.get("as_of") if cached else None,
            "warnings": list(cached.get("warnings", [])) if cached else [],
        }

    def analytics(self, period_key: str, *, as_of: str | None = None) -> dict[str, Any]:
        period = _validate_period(period_key)
        if as_of is None:
            as_of_date = date.today()
        else:
            try:
                as_of_date = date.fromisoformat(as_of)
            except ValueError as exc:
                raise ServiceError(f"Invalid as_of date: {as_of}") from exc
        year = int(period[:4])
        quarters = [f"{year}-Q{index}" for index in range(1, int(period[-1]) + 1)]
        analytics_query = AnalyticsQuery(period_key=period, as_of=as_of_date)
        with closing(self._connect()) as connection:

            def load_obligations(quarter_key: str) -> list[dict[str, Any]]:
                row = connection.execute(
                    "SELECT period_id FROM periods"
                    " WHERE period_key = ? AND period_type = 'quarter'",
                    (quarter_key,),
                ).fetchone()
                if row is None:
                    return []
                return self._obligations(connection, period_id=row["period_id"])

            year_forms = year_form_results(
                connection,
                quarters=quarters,
                load_cached=self._load_cached_dashboard,
                load_obligations=load_obligations,
            )
            return build_analytics(connection, analytics_query, year_forms=year_forms)

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
        return [
            dict(row) | {"ui_context": simple_context("obligation", dict(row))}
            for row in rows
        ]

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
