"""Read-only presentation of existing expenses; never a tax or posting engine."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import json
import sqlite3
from typing import Any


EXPENSE_KINDS = ("purchase", "amortization")
POSTED_STATUSES = {"posted", "included_in_snapshot"}


def _asset_context(connection: sqlite3.Connection, period_id: str) -> dict[str, dict[str, Any]]:
    # One candidate row per asset at the strongest available link level. In
    # particular, do not join assets into the transaction/validation-issue query.
    rows = connection.execute(
        """
        WITH target AS (
            SELECT t.transaction_id, t.document_id, t.counterparty_id, d.issued_on
            FROM transactions t
            LEFT JOIN documents d ON d.document_id = t.document_id
            WHERE t.period_id = ? AND t.entry_type = 'expense'
        ), candidates AS (
            SELECT t.transaction_id, a.asset_id, 1 AS priority
            FROM target t JOIN assets a ON a.acquisition_transaction_id = t.transaction_id
            UNION
            SELECT t.transaction_id, a.asset_id, 1 AS priority
            FROM target t JOIN assets a ON a.document_id = t.document_id
            WHERE a.acquisition_transaction_id IS NULL
            UNION
            SELECT t.transaction_id, a.asset_id, 2 AS priority
            FROM target t
            JOIN documents d ON d.counterparty_id = t.counterparty_id AND d.issued_on = t.issued_on
            JOIN assets a ON a.document_id = d.document_id
        ), preferred AS (
            SELECT transaction_id, MIN(priority) AS priority
            FROM candidates GROUP BY transaction_id
        )
        SELECT c.transaction_id, COUNT(DISTINCT c.asset_id) AS asset_match_count,
               CASE WHEN COUNT(DISTINCT c.asset_id) = 1 THEN MIN(c.asset_id) END AS asset_id,
               CASE WHEN COUNT(DISTINCT c.asset_id) = 1 THEN MIN(COALESCE(NULLIF(a.description, ''), a.asset_code)) END AS asset_description,
               CASE WHEN p.priority = 1 THEN 'linked' ELSE 'inferred' END AS asset_match_method
        FROM candidates c
        JOIN preferred p ON p.transaction_id = c.transaction_id AND p.priority = c.priority
        JOIN assets a ON a.asset_id = c.asset_id
        GROUP BY c.transaction_id, p.priority
        """,
        (period_id,),
    ).fetchall()
    return {row["transaction_id"]: dict(row) for row in rows}


def enrich_expense_context(
    connection: sqlite3.Connection,
    rows: list[dict[str, Any]],
    *,
    period_id: str,
    today: date,
) -> list[dict[str, Any]]:
    assets = (
        _asset_context(connection, period_id)
        if any(row["entry_type"] == "expense" and row["tax_code"] == "historical_g03" for row in rows)
        else {}
    )
    for row in rows:
        kind = None
        if row["entry_type"] == "expense":
            kind = "amortization" if row["tax_code"] == "historical_g03" else "purchase"
        context = assets.get(row["transaction_id"], {}) if kind == "amortization" else {}
        row.update(
            expense_kind=kind,
            view_as_of=today.isoformat(),
            is_future_dated=bool(row["transaction_date"] and row["transaction_date"] > today.isoformat()),
            asset_id=context.get("asset_id"),
            asset_description=context.get("asset_description"),
            asset_match_count=context.get("asset_match_count", 0),
            asset_match_method=context.get("asset_match_method"),
        )
    return rows


def _amount_minor(row: dict[str, Any]) -> int | None:
    if row["expense_kind"] == "amortization":
        return row["deductible_irpf_minor"]
    if row["amount_eur_minor"] is not None:
        return row["amount_eur_minor"]
    return row["amount_minor"] if row["currency"] == "EUR" else None


def expense_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        kind: {
            scope: {"count": 0, "amount_minor": 0, "missing_amount_count": 0}
            for scope in ("posted", "approved", "future_approved", "reviewed_total")
        }
        for kind in EXPENSE_KINDS
    }
    for row in rows:
        status = row["lifecycle_status"]
        if status not in POSTED_STATUSES | {"approved"}:
            continue
        scopes = ["posted" if status in POSTED_STATUSES else "approved", "reviewed_total"]
        if status == "approved" and row["is_future_dated"]:
            scopes.append("future_approved")
        amount = _amount_minor(row)
        for scope in scopes:
            bucket = summary[row["expense_kind"]][scope]
            bucket["count"] += 1
            if amount is None:
                bucket["missing_amount_count"] += 1
            else:
                bucket["amount_minor"] += amount
    for scopes in summary.values():
        for bucket in scopes.values():
            bucket["amount_eur"] = (
                None if bucket["missing_amount_count"] else format(Decimal(bucket["amount_minor"]) / 100, ".2f")
            )
            del bucket["amount_minor"]
    return summary


def expense_page(
    rows: list[dict[str, Any]], *, period: str, today: date,
    query: str | None, offset: int, limit: int,
) -> dict[str, Any]:
    needle = (query or "").strip().casefold()
    matches = [
        row for row in rows
        if not needle or any(
            needle in str(row.get(field) or "").casefold()
            for field in ("description", "counterparty_name", "document_number", "asset_description")
        )
    ]
    selected = matches[offset:offset + limit]
    return {
        "period": period,
        "as_of": today.isoformat(),
        "view_revision": hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "rows": selected,
        "offset": offset,
        "limit": limit,
        "matching_count": len(matches),
        "matching_counts": {kind: sum(row["expense_kind"] == kind for row in matches) for kind in EXPENSE_KINDS},
        "period_counts": {kind: sum(row["expense_kind"] == kind for row in rows) for kind in EXPENSE_KINDS},
        "has_more": offset + len(selected) < len(matches),
        "next_offset": offset + len(selected),
        "summary": expense_summary(rows),
    }
