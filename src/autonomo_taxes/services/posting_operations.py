"""Shared accounting operations; no HTTP, CLI dispatch or UI dependency."""

from __future__ import annotations
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping
from ..intake import (
    ExpenseInboxCleanupCandidate,
    InboxCleanupError,
    cleanup_expense_inbox_source,
)
from ..ledger_db import (
    LedgerDB,
    LedgerDbError,
    StaleRowVersionError,
    open as open_ledger_db,
)
from ..posting import prevalidate_expense_inbox_cleanup
from .operation_utils import _parse_review_id
import io


def post_batch(
    request,
    *,
    database: Path,
    period: str,
    inbox_root: Path | None = None,
    archive_root: Path | None = None,
    show_paths: bool = False,
):
    request = _load_post_batch_request(
        io.StringIO(json.dumps(request)), expected_period=period
    )
    requested = request["items"]
    results: list[dict[str, Any]] = []
    summary = {
        "requested": len(requested),
        "posted": 0,
        "already_posted": 0,
        "already_finalized": 0,
        "skipped": 0,
        "failed": 0,
        "interrupted": 0,
        "not_attempted": 0,
    }
    committed_any = False
    try:
        with open_ledger_db(database) as db:
            preflight = _preflight_post_batch_period(
                db, period_key=period, requested=requested
            )
            if preflight is not None:
                return (preflight, 2)
            for index, item in enumerate(requested):
                review_id, expected_row_version = _parse_post_batch_item(item)
                review_kind, subject_id = _parse_review_id(review_id)
                if review_kind != "transaction":
                    raise ValueError(
                        "Only transaction:<uuid> review items can be posted"
                    )
                try:
                    outcome, result, committed = _post_transaction_review(
                        db,
                        review_id=review_id,
                        transaction_id=subject_id,
                        expected_row_version=expected_row_version,
                        expected_period=period,
                        inbox_root=inbox_root,
                        archive_root=archive_root,
                        show_paths=show_paths,
                        allow_already_posted=True,
                    )
                except StaleRowVersionError as exc:
                    outcome = "skipped"
                    result = _post_batch_failure_result(
                        db,
                        transaction_id=subject_id,
                        review_id=review_id,
                        expected_row_version=expected_row_version,
                        error=exc,
                    )
                    result["reason_code"] = "skipped_stale"
                    committed = False
                except (LedgerDbError, ValueError) as exc:
                    if (
                        not committed_any
                        and _post_batch_reason_code(exc) == "period_not_open"
                    ):
                        preflight = _preflight_post_batch_period(
                            db, period_key=period, requested=requested
                        )
                        if preflight is not None:
                            return (preflight, 2)
                    outcome = "failed"
                    result = _post_batch_failure_result(
                        db,
                        transaction_id=subject_id,
                        review_id=review_id,
                        expected_row_version=expected_row_version,
                        error=exc,
                    )
                    committed = False
                except Exception as exc:
                    if not committed_any and _is_sqlite_busy(exc):
                        return (
                            {
                                "status": "retry_later",
                                "period": period,
                                "error": "db_busy",
                                "message": str(exc),
                            },
                            2,
                        )
                    if not committed_any:
                        return (
                            {
                                "status": "internal_error",
                                "error": "internal_error",
                                "period": period,
                                "message": str(exc),
                            },
                            2,
                        )
                    results.append(
                        {
                            "index": index,
                            "review_id": review_id,
                            "expected_row_version": expected_row_version,
                            "outcome": "failed",
                            "result": _post_batch_failure_result(
                                db,
                                transaction_id=subject_id,
                                review_id=review_id,
                                expected_row_version=expected_row_version,
                                error=exc,
                            ),
                        }
                    )
                    summary["failed"] += 1
                    remaining = len(requested) - (index + 1)
                    for remainder in range(index + 1, len(requested)):
                        next_review_id, next_expected = _parse_post_batch_item(
                            requested[remainder]
                        )
                        results.append(
                            {
                                "index": remainder,
                                "review_id": next_review_id,
                                "expected_row_version": next_expected,
                                "outcome": "not_attempted",
                                "result": {
                                    "reason_code": "internal_error",
                                    "error_type": type(exc).__name__,
                                    "message": str(exc),
                                },
                                "transaction_id": None,
                                "previous_row_version": None,
                                "new_row_version": None,
                                "cleanup_status": None,
                            }
                        )
                    summary["not_attempted"] = remaining
                    payload = {
                        "status": "interrupted",
                        "interrupted": True,
                        "period": period,
                        "summary": summary,
                        "results": results,
                    }
                    return (payload, 0)
                committed_any = committed_any or committed
                if outcome == "skipped":
                    summary["skipped"] += 1
                else:
                    summary[outcome] += 1
                if (
                    outcome == "posted"
                    and result.get("reason_code") == "posted_cleanup_failed"
                ):
                    summary["interrupted"] += 1
                results.append(
                    _post_batch_result_row(
                        index=index,
                        review_id=review_id,
                        expected_row_version=expected_row_version,
                        outcome=outcome,
                        result=result,
                    )
                )
    except sqlite3.OperationalError as exc:
        if _is_sqlite_busy(exc):
            return (
                {
                    "status": "retry_later",
                    "period": period,
                    "error": "db_busy",
                    "message": str(exc),
                },
                2,
            )
        return (
            {
                "status": "internal_error",
                "error": "internal_error",
                "period": period,
                "message": str(exc),
            },
            2,
        )
    payload = {
        "status": _post_batch_status(summary),
        "interrupted": summary["not_attempted"] > 0,
        "period": period,
        "summary": summary,
        "results": results,
    }
    return (payload, 0)


def _post_transaction_review(
    db: LedgerDB,
    *,
    review_id: str,
    transaction_id: str,
    expected_row_version: int,
    expected_period: str | None,
    inbox_root: Path | None,
    archive_root: Path | None,
    show_paths: bool,
    allow_already_posted: bool,
) -> tuple[str, dict[str, Any], bool]:
    current = _transaction_for_cleanup_output(db, transaction_id)
    if expected_period is not None and current["period_key"] != expected_period:
        return (
            "failed",
            {
                **current,
                "error_type": "ValueError",
                "message": (
                    "Review item period does not match the requested batch period: "
                    f"{current['period_key']} != {expected_period}"
                ),
            },
            False,
        )
    if allow_already_posted and current["lifecycle_status"] == "included_in_snapshot":
        return (
            "already_finalized",
            {
                **current,
                "reason_code": "already_finalized",
                "inbox_cleanup": _inbox_cleanup_payload(
                    "not_applicable",
                    message="Transaction is already included in a snapshot.",
                    show_paths=show_paths,
                ),
            },
            False,
        )
    if allow_already_posted and current["lifecycle_status"] == "posted":
        return _summarize_already_posted_transaction(
            current,
            prevalidate_expense_inbox_cleanup(
                db,
                transaction_id,
                inbox_root=inbox_root,
                archive_root=archive_root,
            ),
            show_paths=show_paths,
        )

    cleanup = prevalidate_expense_inbox_cleanup(
        db,
        transaction_id,
        inbox_root=inbox_root,
        archive_root=archive_root,
    )
    if cleanup.status == "failed":
        return (
            "failed",
            {
                **current,
                "reason_code": cleanup.reason_code,
                "inbox_cleanup": _inbox_cleanup_payload(
                    "failed",
                    cleanup_row=cleanup.cleanup_row,
                    candidate=cleanup.candidate,
                    message=cleanup.message,
                    show_paths=show_paths,
                ),
            },
            False,
        )
    row = db.transition_transaction(
        transaction_id,
        lifecycle_status="posted",
        expected_row_version=expected_row_version,
    )
    return _finalize_posted_transaction(
        row,
        cleanup,
        show_paths=show_paths,
    )


def _finalize_posted_transaction(
    row: Mapping[str, Any],
    cleanup: Any,
    *,
    show_paths: bool,
) -> tuple[str, dict[str, Any], bool]:
    if cleanup.candidate is None:
        return (
            "posted",
            {
                **dict(row),
                "reason_code": None,
                "inbox_cleanup": _inbox_cleanup_payload(
                    cleanup.status,
                    cleanup_row=cleanup.cleanup_row,
                    candidate=cleanup.candidate,
                    message=cleanup.message,
                    show_paths=show_paths,
                ),
            },
            True,
        )
    try:
        cleanup_status = cleanup_expense_inbox_source(cleanup.candidate)
    except Exception as exc:
        return (
            "posted",
            {
                **dict(row),
                "reason_code": "posted_cleanup_failed",
                "inbox_cleanup": _inbox_cleanup_payload(
                    "failed",
                    cleanup_row=cleanup.cleanup_row,
                    candidate=cleanup.candidate,
                    message=_inbox_cleanup_error_message(
                        exc,
                        show_paths=show_paths,
                    ),
                    show_paths=show_paths,
                ),
            },
            True,
        )
    return (
        "posted",
        {
            **dict(row),
            "reason_code": None,
            "inbox_cleanup": _inbox_cleanup_payload(
                cleanup_status,
                cleanup_row=cleanup.cleanup_row,
                candidate=cleanup.candidate,
                show_paths=show_paths,
            ),
        },
        True,
    )


def _summarize_already_posted_transaction(
    row: Mapping[str, Any],
    cleanup: Any,
    *,
    show_paths: bool,
) -> tuple[str, dict[str, Any], bool]:
    if cleanup.status == "failed":
        inbox_cleanup = _inbox_cleanup_payload(
            "failed",
            cleanup_row=cleanup.cleanup_row,
            candidate=cleanup.candidate,
            message=cleanup.message,
            show_paths=show_paths,
        )
    elif cleanup.status == "ready":
        inbox_cleanup = _inbox_cleanup_payload(
            "failed",
            cleanup_row=cleanup.cleanup_row,
            candidate=cleanup.candidate,
            message="Transaction is already posted; run inbox cleanup-posted to finish verified cleanup.",
            show_paths=show_paths,
        )
    else:
        inbox_cleanup = _inbox_cleanup_payload(
            cleanup.status,
            cleanup_row=cleanup.cleanup_row,
            candidate=cleanup.candidate,
            message=cleanup.message,
            show_paths=show_paths,
        )
    return (
        "already_posted",
        {
            **dict(row),
            "reason_code": "already_posted",
            "inbox_cleanup": inbox_cleanup,
        },
        False,
    )


def _load_post_batch_request(
    handle,
    *,
    expected_period: str,
) -> dict[str, Any]:
    payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("review post-batch expects a JSON object on stdin")
    period = str(payload.get("period") or "").strip()
    if not period:
        raise ValueError("review post-batch stdin payload requires period")
    if period != expected_period:
        raise ValueError("review post-batch stdin period must match --period")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("review post-batch stdin payload requires an items array")
    return {"period": period, "items": items}


def _parse_post_batch_item(item: Any) -> tuple[str, int]:
    if not isinstance(item, dict):
        raise ValueError("Each post-batch stdin item must be a JSON object")
    review_id = str(item.get("review_id") or "").strip()
    transaction_id = str(item.get("transaction_id") or "").strip()
    if review_id:
        review_kind, subject_id = _parse_review_id(review_id)
        if review_kind != "transaction":
            raise ValueError("Only transaction review items can be posted in batch")
        if transaction_id and transaction_id != subject_id:
            raise ValueError("Each post-batch item transaction_id must match review_id")
        transaction_id = subject_id
    elif transaction_id:
        review_id = f"transaction:{transaction_id}"
    else:
        raise ValueError("Each post-batch item requires transaction_id or review_id")
    if not review_id:
        raise ValueError("Each post-batch item requires transaction_id or review_id")
    raw_version = item.get("expected_row_version")
    if raw_version is None:
        raise ValueError("Each post-batch item requires expected_row_version")
    return review_id, int(raw_version)


def _post_batch_failure_result(
    db: LedgerDB,
    *,
    transaction_id: str,
    review_id: str,
    expected_row_version: int,
    error: Exception,
) -> dict[str, Any]:
    try:
        current = _transaction_for_cleanup_output(db, transaction_id)
    except Exception:
        current = {
            "review_id": review_id,
            "expected_row_version": expected_row_version,
        }
    return {
        **current,
        "reason_code": _post_batch_reason_code(error),
        "error_type": type(error).__name__,
        "message": str(error),
    }


def _post_batch_result_row(
    *,
    index: int,
    review_id: str,
    expected_row_version: int,
    outcome: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    current_row_version = result.get("row_version")
    if outcome == "posted" and isinstance(current_row_version, int):
        previous_row_version: int | None = expected_row_version
        new_row_version: int | None = current_row_version
    elif isinstance(current_row_version, int):
        previous_row_version = current_row_version
        new_row_version = current_row_version
    else:
        previous_row_version = None
        new_row_version = None
    return {
        "index": index,
        "review_id": review_id,
        "transaction_id": result.get("transaction_id"),
        "expected_row_version": expected_row_version,
        "previous_row_version": previous_row_version,
        "new_row_version": new_row_version,
        "outcome": outcome,
        "reason_code": result.get("reason_code"),
        "cleanup_status": (result.get("inbox_cleanup") or {}).get("status"),
        "result": result,
    }


def _post_batch_status(summary: Mapping[str, int]) -> str:
    if summary["not_attempted"]:
        return "interrupted"
    if summary["failed"] or summary["interrupted"] or summary["skipped"]:
        return "partial"
    return "completed"


def _preflight_post_batch_period(
    db: LedgerDB,
    *,
    period_key: str,
    requested: list[dict[str, Any]],
) -> dict[str, Any] | None:
    row = db.connection.execute(
        "SELECT status FROM periods WHERE period_key = ?",
        (period_key,),
    ).fetchone()
    if row is None:
        return {
            "status": "unknown_period",
            "period": period_key,
            "error": "unknown_period",
            "message": f"Unknown period: {period_key}",
        }
    if row["status"] not in {"closed", "amended"}:
        return None
    message = f"Period {period_key} is immutable after close"
    summary = {
        "requested": len(requested),
        "posted": 0,
        "already_posted": 0,
        "already_finalized": 0,
        "skipped": 0,
        "failed": 0,
        "interrupted": 0,
        "not_attempted": len(requested),
    }
    results: list[dict[str, Any]] = []
    for index, item in enumerate(requested):
        review_id, expected_row_version = _parse_post_batch_item(item)
        results.append(
            {
                "index": index,
                "review_id": review_id,
                "expected_row_version": expected_row_version,
                "outcome": "not_attempted",
                "result": {
                    "reason_code": "period_not_open",
                    "error_type": "ClosedPeriodError",
                    "message": message,
                },
            }
        )
    return {
        "status": "period_not_open",
        "interrupted": False,
        "period": period_key,
        "reason_code": "period_not_open",
        "message": message,
        "summary": summary,
        "results": results,
    }


def _post_batch_reason_code(error: Exception) -> str | None:
    message = str(error)
    if "Expected row_version" in message:
        return "stale_row_version"
    if "immutable after close" in message:
        return "period_not_open"
    if "requires an EUR amount" in message:
        return "fx_missing"
    if "requires sourced FX" in message:
        return "fx_unsourced"
    if "historical-only" in message:
        return "fx_historical_only"
    if "tax treatment is required" in message:
        return "tax_treatment_missing"
    if "Unknown tax treatment" in message:
        return "tax_treatment_unknown"
    if "must be approved before posting" in message:
        return "document_not_ready"
    if "cannot be posted before" in message:
        return "future_dated"
    if "prevent posting" in message:
        return "blocking_issue"
    if "-> posted" in message:
        return "lifecycle_not_approved"
    return None


def _is_sqlite_busy(error: Exception) -> bool:
    return (
        isinstance(error, sqlite3.OperationalError) and "locked" in str(error).lower()
    )


def _transaction_for_cleanup_output(
    db: LedgerDB, transaction_id: str
) -> dict[str, Any]:
    row = db.connection.execute(
        """
        SELECT t.*, p.period_key, p.status AS period_status
        FROM transactions t
        JOIN periods p ON p.period_id = t.period_id
        WHERE t.transaction_id = ?
        """,
        (transaction_id,),
    ).fetchone()
    if row is None:
        raise LedgerDbError(f"Transaction not found: {transaction_id}")
    return dict(row)


def _inbox_cleanup_payload(
    status: str,
    *,
    cleanup_row: Mapping[str, Any] | None = None,
    candidate: ExpenseInboxCleanupCandidate | None = None,
    message: str | None = None,
    show_paths: bool,
) -> dict[str, Any]:
    source_value = (
        str(candidate.source_path)
        if candidate is not None
        else str((cleanup_row or {}).get("inbox_source_path") or "")
    )
    archive_value = (
        str(candidate.archive_path)
        if candidate is not None
        else str((cleanup_row or {}).get("archive_path") or "")
    )
    payload: dict[str, Any] = {
        "status": status,
        "message": message or _inbox_cleanup_message(status),
    }
    if source_value:
        payload["file_name"] = Path(source_value).name
    if show_paths:
        if source_value:
            payload["source_path"] = source_value
        if archive_value:
            payload["archive_path"] = archive_value
    return payload


def _inbox_cleanup_message(status: str) -> str:
    if status == "deleted":
        return "Verified expense source was removed from Inbox; Evidence archive was preserved."
    if status == "already_absent":
        return "Stored Inbox source is already absent; no other files were searched."
    if status == "not_applicable":
        return "Automatic Inbox cleanup does not apply to this transaction."
    return "Inbox cleanup failed."


def _inbox_cleanup_error_message(
    error: InboxCleanupError | OSError,
    *,
    show_paths: bool,
) -> str:
    if isinstance(error, InboxCleanupError) or show_paths:
        return str(error)
    return f"Filesystem cleanup failed ({type(error).__name__}); retry when the file is available."
