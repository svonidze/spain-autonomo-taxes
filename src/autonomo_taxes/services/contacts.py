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


class ContactsService:
    def counterparties(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT
                    c.counterparty_id,
                    c.display_name,
                    c.row_version,
                    c.name_is_manual,
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
            builder = self._status_context(connection)
            actions_by_counterparty: dict[str, list[dict[str, Any]]] = {}
            for transaction in connection.execute(
                "SELECT t.transaction_id,t.counterparty_id,p.period_key FROM transactions t JOIN periods p USING(period_id) WHERE t.lifecycle_status='needs_review' ORDER BY t.transaction_date DESC"
            ):
                actions = actions_by_counterparty.setdefault(
                    transaction["counterparty_id"], []
                )
                if len(actions) < 10:
                    actions.extend(
                        builder.review_actions(
                            transaction["transaction_id"], transaction["period_key"]
                        )
                    )
            result = []
            for raw in rows:
                row = dict(raw)
                context = simple_context("counterparty", row)
                context["reasons"] = builder.issues(
                    [("counterparties", row["counterparty_id"])]
                )
                context["actions"] = actions_by_counterparty.get(
                    row["counterparty_id"], []
                )
                result.append(row | {"ui_context": context})
            return result

    def counterparty_detail(self, counterparty_id: str) -> dict[str, Any]:
        counterparty_id = _validated_counterparty_id(counterparty_id)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            _counterparty_api_state(connection, counterparty_id)
            row = dict(
                connection.execute(
                    """SELECT counterparty_id, display_name, row_version, name_is_manual,
                          country_code, tax_id, vat_id, roi_status, legal_form, email, phone,
                          professional_supplier, retention_expected
                   FROM counterparties WHERE counterparty_id = ?""",
                    (counterparty_id,),
                ).fetchone()
            )
            builder = self._status_context(connection)
            context = simple_context("counterparty", row)
            context["reasons"] = builder.issues([("counterparties", counterparty_id)])
            periods = [
                item["period_key"]
                for item in connection.execute(
                    """SELECT DISTINCT p.period_key FROM transactions t
                   JOIN periods p ON p.period_id=t.period_id
                   WHERE t.counterparty_id = ? ORDER BY p.period_key DESC""",
                    (counterparty_id,),
                )
            ]
            return {"counterparty": row | {"ui_context": context}, "periods": periods}

    def counterparty_transactions(
        self,
        counterparty_id: str,
        *,
        period_key: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        counterparty_id = _validated_counterparty_id(counterparty_id)
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ServiceError(
                "Counterparty page requires offset >= 0 and limit between 1 and 100"
            )
        period = _validate_period(period_key) if period_key is not None else None
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            _counterparty_api_state(connection, counterparty_id)
            period_id = (
                self._period_row(connection, period)["period_id"] if period else None
            )
            count = connection.execute(
                """SELECT COUNT(*) FROM transactions
                   WHERE counterparty_id = ? AND (? IS NULL OR period_id = ?)""",
                (counterparty_id, period_id, period_id),
            ).fetchone()[0]
            rows = self._transactions(
                connection,
                period_id=period_id,
                counterparty_id=counterparty_id,
                offset=offset,
                limit=limit,
            )
            return {
                "counterparty_id": counterparty_id,
                "period": period,
                "rows": rows,
                "offset": offset,
                "limit": limit,
                "matching_count": count,
                "has_more": offset + len(rows) < count,
                "next_offset": offset + len(rows),
            }

    def counterparty_name_history(self, counterparty_id: str) -> dict[str, Any]:
        counterparty_id = _validated_counterparty_id(counterparty_id)
        with open_ledger_db(self.config.database, read_only=True) as db:
            _counterparty_api_state(db.connection, counterparty_id)
            return {
                "counterparty_id": counterparty_id,
                "changes": db.counterparty_name_history(counterparty_id),
            }

    def rename_counterparty(
        self,
        counterparty_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        change_source: str = "web",
    ) -> dict[str, Any]:
        if change_source not in {"web", "cli"}:
            raise ServiceApiError(400, "invalid_request", "Unsupported change source")
        counterparty_id = _validated_counterparty_id(counterparty_id)
        _exact_object_fields(
            payload, {"display_name", "expected_row_version"}, "rename request"
        )
        version = payload["expected_row_version"]
        if type(version) is not int or version < 1:
            raise ServiceApiError(
                400,
                "invalid_request",
                "expected_row_version must be a positive integer",
            )
        try:
            with open_ledger_db(self.config.database) as db, db.transaction():
                _counterparty_api_state(db.connection, counterparty_id)
                try:
                    updated = db.rename_counterparty(
                        counterparty_id,
                        display_name=payload["display_name"],
                        expected_row_version=version,
                        change_source=change_source,
                        actor=actor,
                    )
                except StaleRowVersionError as exc:
                    raise ServiceApiError(
                        409,
                        "stale_counterparty",
                        "Counterparty has changed; review the current name",
                        current=_counterparty_api_state(db.connection, counterparty_id),
                    ) from exc
                except ValueError as exc:
                    raise ServiceApiError(400, "invalid_name", str(exc)) from exc
                return {
                    **_counterparty_api_state(db.connection, counterparty_id),
                    "changed": updated["row_version"] != version,
                }
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise ServiceApiError(
                    503,
                    "counterparty_busy",
                    "Database is busy; retry saving",
                ) from exc
            raise
