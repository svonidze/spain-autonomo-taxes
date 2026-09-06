"""Shared accounting operations; no HTTP, CLI dispatch or UI dependency."""

from __future__ import annotations
from pathlib import Path
from typing import Any, Mapping
from ..ledger_db import open as open_ledger_db
from .operation_utils import _parse_review_id


def apply_fx(database: Path, payload: Mapping[str, Any]):
    expected_fields = {
        "review_id",
        "expected_row_version",
        "rate_date",
        "rate",
        "rate_source",
        "source_reference",
    }
    unexpected = sorted(set(payload) - expected_fields)
    missing = sorted(expected_fields - set(payload))
    if unexpected or missing:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        raise ValueError("Invalid FX review request (" + "; ".join(details) + ")")
    review_kind, subject_id = _parse_review_id(str(payload["review_id"]))
    with open_ledger_db(database) as db:
        transaction_id = subject_id
        if review_kind == "document":
            rows = db.connection.execute(
                "SELECT transaction_id FROM transactions WHERE document_id = ?",
                (subject_id,),
            ).fetchall()
            if len(rows) != 1:
                raise ValueError(
                    f"Document FX review requires exactly one linked transaction; found {len(rows)}"
                )
            transaction_id = str(rows[0]["transaction_id"])
        result = db.review_transaction_fx_rate(
            transaction_id,
            expected_row_version=int(payload["expected_row_version"]),
            rate_date=str(payload["rate_date"]),
            rate=str(payload["rate"]),
            rate_source=str(payload["rate_source"]),
            source_reference=str(payload["source_reference"]),
        )
    return (result, 0)
