"""Read-only UI projections. None of these explanations authorizes a write."""

from __future__ import annotations

import sqlite3
from datetime import date
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .ledger_db import LedgerDB
from .posting import build_posting_preview

# Exact compatibility aliases for existing operational issue codes. Never inspect
# an issue's message to decide its meaning, severity or permissible actions.
ISSUE_ALIASES = {
    "rossellimac_advance_documents_overlap": "advance_documents_overlap",
    "rossellimac_iva_prior_deduction_unconfirmed": "iva_prior_deduction_unconfirmed",
}


def reason(row: dict[str, Any]) -> dict[str, Any]:
    details = dict(row.get("details") or {})
    code = str(
        details.get("issue_code")
        or row.get("issue_code")
        or row.get("code")
        or "unknown"
    )
    return {
        "code": ISSUE_ALIASES.get(code, code),
        "source_code": code,
        "blocker_code": row.get("code"),
        "issue_id": row.get("validation_issue_id") or row.get("issue_id"),
        "subject_table": row.get("subject_table"),
        "subject_id": row.get("subject_id"),
        "details": details,
        "message": row.get("message") or "",
        "blocking": bool(row.get("blocking", True)),
    }


class StatusContext:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        inbox_root: Path | None = None,
        archive_root: Path | None = None,
        resolve_path: Callable[[str], Path] = Path,
        today: date | None = None,
    ) -> None:
        self.connection = connection
        self.db = LedgerDB(connection)
        self.inbox_root = inbox_root
        self.archive_root = archive_root
        self.resolve_path = resolve_path
        self.today = today
        self._previews: dict[str, dict[str, dict[str, Any]]] = {}
        self._preview_payloads: dict[str, dict[str, Any]] = {}
        self._transactions: dict[str, dict[str, Any]] = {}
        self._rows: dict[str, sqlite3.Row] = {}
        self._documents: dict[str, list[dict[str, Any]]] = {}
        self._issues: dict[tuple[str, str], list[dict[str, Any]]] | None = None
        self._reviewable: set[str] | None = None

    def review_navigation_available(self, transaction_id: str) -> bool:
        # Navigation capability is deliberately cheaper than opening a review.
        # The actual work-item endpoint still validates the immutable evidence
        # and supported decisions when the user follows the link.
        if self._reviewable is None:
            self._reviewable = {
                row[0]
                for row in self.connection.execute(
                    """SELECT t.transaction_id FROM transactions t JOIN tax_treatments tt USING(transaction_id)
                   WHERE t.entry_type IN ('income','expense')
                     AND t.lifecycle_status IN ('received','extracted','needs_review','approved')
                     AND tt.treatment_type='invoice_review'
                   GROUP BY t.transaction_id HAVING COUNT(*)=1"""
                )
            }
        return transaction_id in self._reviewable

    def review_actions(self, transaction_id: str, period: str) -> list[dict[str, Any]]:
        if not self.review_navigation_available(transaction_id):
            return []
        return [{"kind": "review", "transaction_id": transaction_id, "period": period}]

    def prime_transactions(self, ids: list[str]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        rows = self.connection.execute(
            f"""SELECT t.*,p.period_key,d.document_number FROM transactions t JOIN periods p USING(period_id)
               LEFT JOIN documents d USING(document_id) WHERE transaction_id IN ({placeholders})""",
            ids,
        )
        self._rows.update({row["transaction_id"]: row for row in rows})

    def preview_payload(self, period: str) -> dict[str, Any]:
        self.preview(period)
        return self._preview_payloads[period]

    def preview(self, period: str) -> dict[str, dict[str, Any]]:
        if period not in self._previews:
            result = build_posting_preview(
                self.db,
                period_key=period,
                inbox_root=self.inbox_root,
                archive_root=self.archive_root,
                today=self.today,
            )
            self._previews[period] = {r["transaction_id"]: r for r in result["items"]}
            self._preview_payloads[period] = result
        return self._previews[period]

    def issues(self, subjects: list[tuple[str, str | None]]) -> list[dict[str, Any]]:
        if self._issues is None:
            self._issues = {}
            for raw in self.connection.execute(
                "SELECT * FROM validation_issues WHERE issue_status='open'"
            ):
                self._issues.setdefault(
                    (raw["subject_table"], raw["subject_id"]), []
                ).append(dict(raw))
        result = {}
        for table, subject in subjects:
            if not subject:
                continue
            for row in self._issues.get((table, subject), []):
                result[row["validation_issue_id"]] = reason(dict(row))
        return sorted(result.values(), key=lambda r: (not r["blocking"], r["code"]))

    def documents(self, document_id: str | None) -> list[dict[str, Any]]:
        if not document_id:
            return []
        if document_id in self._documents:
            return self._documents[document_id]
        rows = self.connection.execute(
            """SELECT DISTINCT d.document_id,d.document_number,d.issued_on,d.document_type,
                       d.source_path,d.lifecycle_status,d.total_minor,d.currency,
                       EXISTS(SELECT 1 FROM document_attachments da
                         JOIN file_replicas fr USING(file_id)
                         WHERE da.document_id=d.document_id AND da.attachment_role='source'
                           AND fr.replica_status='available') AS catalog_available
                 FROM documents d WHERE d.document_id=? OR d.document_id IN (
                   SELECT peer.document_id FROM document_attachments own
                   JOIN document_attachments peer ON peer.file_id=own.file_id
                   WHERE own.document_id=? AND peer.attachment_role='source')
                 ORDER BY d.issued_on,d.document_id LIMIT 50""",
            (document_id, document_id),
        ).fetchall()
        result = []
        for raw in rows:
            try:
                local_available = bool(
                    raw["source_path"]
                    and self.resolve_path(raw["source_path"]).is_file()
                )
            except (OSError, ValueError):
                local_available = False
            result.append(
                {
                    k: v
                    for k, v in dict(raw).items()
                    if k not in {"source_path", "catalog_available"}
                }
                | {
                    "available": bool(local_available or raw["catalog_available"]),
                    "availability": "local"
                    if local_available
                    else "catalog"
                    if raw["catalog_available"]
                    else "unavailable",
                }
            )
        self._documents[document_id] = result
        return result

    def transaction(self, transaction_id: str) -> dict[str, Any]:
        if transaction_id in self._transactions:
            return self._transactions[transaction_id]
        row = self._rows.get(transaction_id)
        if row is None:
            row = self.connection.execute(
                """SELECT t.*,p.period_key,d.document_number FROM transactions t JOIN periods p USING(period_id)
               LEFT JOIN documents d USING(document_id) WHERE transaction_id=?""",
                (transaction_id,),
            ).fetchone()
        if row is None:
            return {
                "domain": "transaction",
                "state": "unknown",
                "reasons": [],
                "actions": [],
            }
        lifecycle = row["lifecycle_status"]
        preview = self.preview(row["period_key"]).get(transaction_id)
        issues = self.issues(
            [
                ("transactions", transaction_id),
                ("documents", row["document_id"]),
                ("counterparties", row["counterparty_id"]),
            ]
        )
        blockers = [reason(b) for b in preview["blockers"]] if preview else []
        # Lifecycle is not posting readiness: an unreviewed invoice can be reviewed
        # while posting is blocked. Keep both facts in the projection.
        state = (
            lifecycle
            if lifecycle
            in {"posted", "included_in_snapshot", "duplicate", "rejected", "void"}
            else (
                preview["preview_bucket"]
                if lifecycle == "approved" and preview
                else "needs_review"
            )
        )
        actions = self.review_actions(transaction_id, row["period_key"])
        context = {
            "domain": "transaction",
            "subject_id": transaction_id,
            "title": row["document_number"] or row["description"],
            "period": row["period_key"],
            "state": state,
            "lifecycle_status": lifecycle,
            "reasons": issues,
            "posting": {
                "preview_bucket": preview["preview_bucket"],
                "posting_deferred_until": preview["posting_deferred_until"],
                "blockers": blockers,
                "ready_to_post": preview["ready_to_post"],
            }
            if preview
            else None,
            "documents": self.documents(row["document_id"]),
            "actions": actions,
            "facts": {
                "transaction_date": row["transaction_date"],
                "amount_minor": row["amount_minor"],
                "currency": row["currency"],
                "lifecycle_status": lifecycle,
            },
        }
        self._transactions[transaction_id] = context
        return context

    def document(self, row: dict[str, Any]) -> dict[str, Any]:
        transactions = self.connection.execute(
            "SELECT t.transaction_id,p.period_key FROM transactions t JOIN periods p USING(period_id) WHERE document_id=? ORDER BY transaction_id",
            (row["document_id"],),
        ).fetchall()
        return {
            "domain": "document",
            "subject_id": row["document_id"],
            "title": row.get("document_number"),
            "state": row["lifecycle_status"],
            "reasons": self.issues([("documents", row["document_id"])]),
            "documents": self.documents(row["document_id"]),
            "actions": [
                a
                for r in transactions
                for a in self.review_actions(r["transaction_id"], r["period_key"])
            ],
            "facts": {
                "issued_on": row["issued_on"],
                "amount_minor": row["total_minor"],
                "currency": row["currency"],
            },
        }

    def asset(self, row: dict[str, Any], period: str | None) -> dict[str, Any]:
        context = (
            self.transaction(row["acquisition_transaction_id"])
            if row.get("acquisition_transaction_id")
            else {
                "state": "recorded_information",
                "reasons": [],
                "actions": [],
                "posting": None,
            }
        )
        context = dict(context)
        issues = self.issues(
            [
                ("assets", row["asset_id"]),
                ("documents", row["document_id"]),
                ("transactions", row.get("acquisition_transaction_id")),
                ("counterparties", row.get("counterparty_id")),
            ]
        )
        entries = [
            dict(r)
            for r in self.connection.execute(
                """SELECT ae.entry_kind,ae.include_in_books,ae.amount_minor,ae.tax_year,
                      p.period_key,ae.source_book_line_id FROM amortization_entries ae
               JOIN periods p USING(period_id) WHERE asset_id=? ORDER BY p.starts_on,ae.entry_kind""",
                (row["asset_id"],),
            )
        ]
        selected = [
            r
            for r in entries
            if r["period_key"] == period
            and r["entry_kind"] in {"quarter_schedule", "adjustment"}
        ]
        annual = [
            r
            for r in entries
            if r["entry_kind"] == "annual_evidence"
            and period
            and r["tax_year"] == int(period[:4])
        ]
        context.update(
            {
                "domain": "asset",
                "subject_id": row["asset_id"],
                "title": row.get("description") or row["asset_code"],
                "period": period,
                "reasons": issues,
                "documents": self.documents(row["document_id"]),
                "facts": {
                    "placed_in_service_on": row["placed_in_service_on"],
                    "cost_minor": row["cost_minor"],
                    "amortizable_base_minor": row["amortizable_base_minor"],
                    "currency": row["currency"],
                    "business_use_ratio": row["business_use_ratio"],
                    "annual_rate_basis_points": row["annual_rate_basis_points"],
                    "advisor_decision": row["advisor_decision"],
                },
                "amortization": {
                    "period": period,
                    "rows": selected,
                    "annual_evidence": annual,
                    "book_minor": sum(
                        r["amount_minor"] for r in selected if r["include_in_books"]
                    ),
                    "excluded_minor": sum(
                        r["amount_minor"] for r in selected if not r["include_in_books"]
                    ),
                    "adjustment_minor": sum(
                        r["amount_minor"]
                        for r in selected
                        if r["entry_kind"] == "adjustment"
                    ),
                    "count": len(selected),
                },
            }
        )
        if not row.get("acquisition_transaction_id") and any(
            r["blocking"] for r in issues
        ):
            context["state"] = "needs_review"
        return context


def simple_context(domain: str, row: dict[str, Any]) -> dict[str, Any]:
    if domain == "obligation":
        state = (
            "filed"
            if row["filing_status"] == "filed"
            else (
                "not_due" if row["determination"] == "not_due" else row["filing_status"]
            )
        )
        return {
            "domain": domain,
            "state": state,
            "subject_id": row["obligation_id"],
            "title": "Modelo " + row["obligation_code"],
            "reasons": [],
            "actions": [],
            "facts": {
                k: row.get(k)
                for k in (
                    "determination",
                    "filing_status",
                    "filed_at",
                    "statutory_due_on",
                    "direct_debit_cutoff_on",
                    "deadline_status",
                )
            },
            "source_message": row.get("explanation"),
        }
    return {
        "domain": "counterparty",
        "state": row.get("roi_status") or "unknown",
        "subject_id": row["counterparty_id"],
        "title": row["display_name"],
        "reasons": [],
        "actions": [],
        "facts": {
            k: row.get(k) for k in ("country_code", "tax_id", "vat_id", "roi_status")
        },
    }
