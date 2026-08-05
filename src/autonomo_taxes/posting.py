from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from .fx_policy import XOLO_RECORDED_PRODUCTION_THROUGH
from .intake import (
    ExpenseInboxCleanupCandidate,
    InboxCleanupError,
    validate_expense_inbox_cleanup,
)
from .ledger_db import LedgerDB, LedgerDbError, LifecycleError

DOCUMENT_POSTABLE_STATUSES = {"approved", "posted", "included_in_snapshot"}
REVIEW_READY_STATUSES = {"extracted", "needs_review"}
REVIEW_LIST_TRANSACTION_STATUSES = ("received", "extracted", "needs_review", "approved")


@dataclass(frozen=True)
class PostingBlocker:
    code: str
    message: str
    subject_table: str | None = None
    subject_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PostingEvaluation:
    review_id: str
    kind: str
    period: str
    period_status: str
    transaction_id: str
    lifecycle_status: str
    row_version: int
    transaction_date: str
    entry_type: str
    description: str
    amount_minor: int
    currency: str
    document_id: str | None
    effective_amount_eur_minor: int | None
    effective_amount_eur_formula: str
    tax_treatments: tuple[dict[str, Any], ...]
    blocking_issues: tuple[dict[str, Any], ...]
    linked_document_approved: bool
    ready_to_approve: bool
    ready_to_post: bool
    posting_deferred_until: str | None
    blockers: tuple[PostingBlocker, ...]

    def assert_postable(self) -> None:
        for blocker in self.blockers:
            if blocker.code == "blocking_issue":
                issue_codes = sorted(
                    {
                        str(item.details.get("issue_code"))
                        for item in self.blockers
                        if item.code == "blocking_issue"
                    }
                )
                raise LifecycleError(
                    "Open blocking review issues prevent posting: "
                    + ", ".join(issue_codes)
                )
            raise LifecycleError(blocker.message)


@dataclass(frozen=True)
class CleanupPrevalidation:
    cleanup_row: dict[str, Any] | None
    candidate: ExpenseInboxCleanupCandidate | None
    status: str
    message: str | None = None
    reason_code: str | None = None
    blocking: bool = False


def build_posting_preview(
    db: LedgerDB,
    *,
    period_key: str,
    today: date | None = None,
    inbox_root: Path | None = None,
    archive_root: Path | None = None,
) -> dict[str, Any]:
    effective_today = today or date.today()
    period = db.connection.execute(
        "SELECT period_id, period_key, status FROM periods WHERE period_key = ?",
        (period_key,),
    ).fetchone()
    if period is None:
        raise LedgerDbError(f"Unknown period: {period_key}")
    items = [
        evaluate_transaction_posting(db, transaction, today=today)
        for transaction in db.list_transactions(period_key=period_key)
        if transaction["lifecycle_status"] in REVIEW_LIST_TRANSACTION_STATUSES
    ]
    serialized = [
        _posting_preview_item(
            item,
            prevalidate_expense_inbox_cleanup(
                db,
                item.transaction_id,
                inbox_root=inbox_root,
                archive_root=archive_root,
            ),
            inbox_root=inbox_root,
            archive_root=archive_root,
        )
        for item in items
    ]
    approved = [item for item in serialized if item["lifecycle_status"] == "approved"]
    ready = [item for item in approved if item["preview_bucket"] == "ready"]
    deferred = [item for item in approved if item["preview_bucket"] == "deferred"]
    blocked = [item for item in approved if item["preview_bucket"] == "blocked"]
    return {
        "period": period_key,
        "period_status": str(period["status"]),
        "reason_code": "period_not_open" if str(period["status"]) in {"closed", "amended"} else None,
        "as_of": effective_today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "items": len(serialized),
            "approved_count": len(approved),
            "ready_count": len(ready),
            "deferred_count": len(deferred),
            "blocked_count": len(blocked),
            "ready_income_eur_minor": sum(
                int(item["effective_amount_eur_minor"] or 0)
                for item in ready
                if item["entry_type"] == "income"
            ),
            "ready_expense_eur_minor": sum(
                int(item["effective_amount_eur_minor"] or 0)
                for item in ready
                if item["entry_type"] == "expense"
            ),
            "cleanup_count": sum(1 for item in approved if item["cleanup"]["applicable"]),
            "cleanup_blocked_count": sum(
                1 for item in approved if item["cleanup"]["blocking"]
            ),
            "ready_total_eur": _eur_string(
                sum(int(item["effective_amount_eur_minor"] or 0) for item in ready)
            ),
        },
        "cleanup_roots": {
            "inbox_root": str(inbox_root) if inbox_root is not None else None,
            "archive_root": str(archive_root) if archive_root is not None else None,
        },
        "ready": ready,
        "deferred": deferred,
        "blocked": blocked,
        "items": serialized,
    }


def evaluate_transaction_posting(
    db: LedgerDB,
    transaction: Mapping[str, Any],
    *,
    today: date | None = None,
    include_transition_blockers: bool = True,
    include_period_blocker: bool = True,
) -> PostingEvaluation:
    effective_today = today or date.today()
    transaction_date = date.fromisoformat(str(transaction["transaction_date"]))
    transaction_id = str(transaction["transaction_id"])
    period_key = str(transaction["period_key"])
    period_status = str(
        db.connection.execute(
            "SELECT status FROM periods WHERE period_id = ?",
            (transaction["period_id"],),
        ).fetchone()["status"]
    )
    treatments = tuple(_fetch_treatments(db, transaction_id))
    tax_reviewed = bool(treatments) and all(
        (row.get("tax_code") or "unknown") != "unknown" for row in treatments
    )
    effective_amount_eur_minor, effective_amount_eur_formula = _effective_amount_eur_fields(
        transaction
    )

    linked_document_approved = True
    document_id = transaction.get("document_id")
    if document_id is not None:
        document = db.connection.execute(
            "SELECT lifecycle_status FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        linked_document_approved = document is not None and document["lifecycle_status"] in DOCUMENT_POSTABLE_STATUSES

    blocking_issues = tuple(_blocking_issue_rows(db, transaction))
    blockers: list[PostingBlocker] = []
    lifecycle_status = str(transaction["lifecycle_status"])
    if include_transition_blockers and lifecycle_status != "approved":
        blockers.append(
            PostingBlocker(
                code="lifecycle_not_approved",
                message=(
                    "Transaction lifecycle_status must be approved before posting "
                    f"(current: {lifecycle_status})"
                ),
                subject_table="transactions",
                subject_id=transaction_id,
            )
        )
    if include_period_blocker and period_status != "open":
        blockers.append(
            PostingBlocker(
                code="period_not_open",
                message=f"Period {period_key} is immutable after close",
                subject_table="periods",
                subject_id=str(transaction["period_id"]),
            )
        )
    if transaction_date > effective_today:
        blockers.append(
            PostingBlocker(
                code="future_dated",
                message=(
                    "A future-dated transaction cannot be posted before "
                    f"{transaction_date.isoformat()}"
                ),
                subject_table="transactions",
                subject_id=transaction_id,
            )
        )
    if not treatments:
        blockers.append(
            PostingBlocker(
                code="tax_treatment_missing",
                message="A reviewed tax treatment is required before posting",
                subject_table="transactions",
                subject_id=transaction_id,
            )
        )
    elif not tax_reviewed:
        blockers.append(
            PostingBlocker(
                code="tax_treatment_unknown",
                message="Unknown tax treatment cannot be posted",
                subject_table="transactions",
                subject_id=transaction_id,
            )
        )

    original_currency = str(
        transaction.get("original_currency") or transaction["currency"]
    ).upper()
    if original_currency != "EUR":
        if transaction.get("amount_eur_minor") is None:
            blockers.append(
                PostingBlocker(
                    code="fx_missing",
                    message="A foreign-currency transaction requires an EUR amount before posting",
                    subject_table="transactions",
                    subject_id=transaction_id,
                )
            )
        if transaction.get("fx_rate_id") is None:
            blockers.append(
                PostingBlocker(
                    code="fx_unsourced",
                    message="A foreign-currency transaction requires sourced FX before posting",
                    subject_table="transactions",
                    subject_id=transaction_id,
                )
            )
        else:
            rate = db.connection.execute(
                "SELECT rate_source FROM fx_rates WHERE fx_rate_id = ?",
                (transaction["fx_rate_id"],),
            ).fetchone()
            if rate is None:
                blockers.append(
                    PostingBlocker(
                        code="fx_unsourced",
                        message="A foreign-currency transaction requires sourced FX before posting",
                        subject_table="transactions",
                        subject_id=transaction_id,
                    )
                )
            elif (
                rate["rate_source"] == "xolo_recorded"
                and transaction_date > XOLO_RECORDED_PRODUCTION_THROUGH
            ):
                blockers.append(
                    PostingBlocker(
                        code="fx_historical_only",
                        message=(
                            "xolo_recorded FX is historical-only after "
                            f"{XOLO_RECORDED_PRODUCTION_THROUGH.isoformat()}"
                        ),
                        subject_table="transactions",
                        subject_id=transaction_id,
                    )
                )

    if document_id is not None and not linked_document_approved:
        blockers.append(
            PostingBlocker(
                code="document_not_ready",
                message="The linked document must be approved before posting the transaction",
                subject_table="documents",
                subject_id=str(document_id),
            )
        )

    if blocking_issues:
        for issue in blocking_issues:
            blockers.append(
                PostingBlocker(
                    code="blocking_issue",
                    message=str(issue["message"]),
                    subject_table=str(issue["subject_table"]),
                    subject_id=str(issue["subject_id"]),
                    details={
                        "issue_id": issue["issue_id"],
                        "issue_code": issue["code"],
                        "issue_row_version": issue["row_version"],
                        "issue_message": issue["message"],
                    },
                )
            )

    posting_date_reached = transaction_date <= effective_today
    return PostingEvaluation(
        review_id=f"transaction:{transaction_id}",
        kind="transaction",
        period=period_key,
        period_status=period_status,
        transaction_id=transaction_id,
        lifecycle_status=lifecycle_status,
        row_version=int(transaction["row_version"]),
        transaction_date=str(transaction["transaction_date"]),
        entry_type=str(transaction["entry_type"]),
        description=str(transaction["description"]),
        amount_minor=int(transaction["amount_minor"]),
        currency=str(transaction["currency"]),
        document_id=str(document_id) if document_id is not None else None,
        effective_amount_eur_minor=effective_amount_eur_minor,
        effective_amount_eur_formula=effective_amount_eur_formula,
        tax_treatments=treatments,
        blocking_issues=blocking_issues,
        linked_document_approved=linked_document_approved,
        ready_to_approve=(
            lifecycle_status in REVIEW_READY_STATUSES
            and tax_reviewed
            and linked_document_approved
            and not blocking_issues
        ),
        ready_to_post=(
            lifecycle_status == "approved"
            and posting_date_reached
            and not blockers
        ),
        posting_deferred_until=(
            str(transaction["transaction_date"])
            if lifecycle_status == "approved" and not posting_date_reached
            else None
        ),
        blockers=tuple(blockers),
    )


def assert_transaction_postable(
    db: LedgerDB,
    transaction: Mapping[str, Any],
    *,
    today: date | None = None,
) -> None:
    evaluate_transaction_posting(
        db,
        transaction,
        today=today,
        include_transition_blockers=False,
        include_period_blocker=True,
    ).assert_postable()


def expense_inbox_cleanup_row(
    db: LedgerDB,
    transaction_id: str,
) -> dict[str, Any] | None:
    row = db.connection.execute(
        """
        SELECT t.transaction_id, p.period_key, d.document_type,
               d.source_path AS archive_path,
               d.source_hash AS evidence_sha256,
               ib.source_name AS inbox_source_path,
               ir.intake_tab
        FROM transactions t
        JOIN periods p ON p.period_id = t.period_id
        LEFT JOIN documents d ON d.document_id = t.document_id
        LEFT JOIN import_batches ib ON ib.import_batch_id = d.import_batch_id
        LEFT JOIN intake_receipts ir ON ir.transaction_id = t.transaction_id
        WHERE t.transaction_id = ?
        """,
        (transaction_id,),
    ).fetchone()
    if (
        row is None
        or row["intake_tab"] != "expense_intake"
        or row["document_type"] != "expense_invoice"
    ):
        return None
    return dict(row)


def build_expense_inbox_cleanup_candidate(
    row: Mapping[str, Any],
    *,
    inbox_root: Path | None,
    archive_root: Path | None,
) -> ExpenseInboxCleanupCandidate:
    if inbox_root is None:
        raise InboxCleanupError(
            "--inbox-root is required for expense_intake cleanup "
            "(or set inbox_root in .local/config.yaml)"
        )
    if archive_root is None:
        raise InboxCleanupError(
            "--archive-root is required for expense_intake cleanup "
            "(or set archive_root in .local/config.yaml)"
        )
    source_path = str(row.get("inbox_source_path") or "").strip()
    archive_path = str(row.get("archive_path") or "").strip()
    evidence_sha256 = str(row.get("evidence_sha256") or "").strip()
    period_key = str(row.get("period_key") or "").strip()
    if not source_path:
        raise InboxCleanupError("Expense intake is missing its stored Inbox source path")
    if not archive_path:
        raise InboxCleanupError("Expense document is missing its Evidence archive path")
    if not period_key:
        raise InboxCleanupError("Expense transaction is missing its accounting period")
    return ExpenseInboxCleanupCandidate(
        source_path=Path(source_path),
        archive_path=Path(archive_path),
        inbox_root=inbox_root,
        archive_root=archive_root,
        period_key=period_key,
        sha256=evidence_sha256,
    )


def prevalidate_expense_inbox_cleanup(
    db: LedgerDB,
    transaction_id: str,
    *,
    inbox_root: Path | None,
    archive_root: Path | None,
) -> CleanupPrevalidation:
    cleanup_row = expense_inbox_cleanup_row(db, transaction_id)
    if cleanup_row is None:
        return CleanupPrevalidation(
            cleanup_row=None,
            candidate=None,
            status="not_applicable",
            message="Automatic cleanup applies only to expense_intake expense invoices.",
            reason_code=None,
            blocking=False,
        )
    candidate: ExpenseInboxCleanupCandidate | None = None
    try:
        candidate = build_expense_inbox_cleanup_candidate(
            cleanup_row,
            inbox_root=inbox_root,
            archive_root=archive_root,
        )
        status = validate_expense_inbox_cleanup(candidate)
    except InboxCleanupError as exc:
        message = str(exc)
        reason_code = (
            "inbox_roots_not_configured"
            if "--inbox-root is required" in message or "--archive-root is required" in message
            else "cleanup_precondition_failed"
        )
        return CleanupPrevalidation(
            cleanup_row=cleanup_row,
            candidate=candidate,
            status="failed",
            message=message,
            reason_code=reason_code,
            blocking=True,
        )
    return CleanupPrevalidation(
        cleanup_row=cleanup_row,
        candidate=candidate,
        status=status,
        message=None,
        reason_code=None,
        blocking=False,
    )


def _fetch_treatments(db: LedgerDB, transaction_id: str) -> list[dict[str, Any]]:
    return [
        {
            "treatment_id": row["treatment_id"],
            "row_version": row["row_version"],
            "tax_code": row["tax_code"] or "unknown",
        }
        for row in db.connection.execute(
            "SELECT * FROM tax_treatments WHERE transaction_id = ? ORDER BY treatment_id",
            (transaction_id,),
        ).fetchall()
    ]


def _blocking_issue_rows(
    db: LedgerDB,
    transaction: Mapping[str, Any],
) -> list[dict[str, Any]]:
    related_issue_rows: dict[str, dict[str, Any]] = {}
    for subject_table, subject_id in (
        ("transactions", transaction["transaction_id"]),
        ("documents", transaction.get("document_id")),
        ("counterparties", transaction.get("counterparty_id")),
    ):
        if subject_id is None:
            continue
        rows = db.connection.execute(
            """
            SELECT validation_issue_id, row_version, issue_code, message
            FROM validation_issues
            WHERE subject_table = ? AND subject_id = ?
              AND blocking = 1 AND issue_status = 'open'
            ORDER BY issue_code
            """,
            (subject_table, subject_id),
        ).fetchall()
        for issue in rows:
            related_issue_rows[issue["validation_issue_id"]] = {
                "issue_id": issue["validation_issue_id"],
                "row_version": issue["row_version"],
                "subject_table": subject_table,
                "subject_id": subject_id,
                "code": issue["issue_code"],
                "message": issue["message"],
            }
    return list(related_issue_rows.values())


def _effective_amount_eur_fields(
    transaction: Mapping[str, Any],
) -> tuple[int | None, str]:
    if transaction.get("amount_eur_minor") is not None:
        return int(transaction["amount_eur_minor"]), "amount_eur_minor"
    currency = str(transaction.get("original_currency") or transaction["currency"]).upper()
    if currency == "EUR":
        return int(transaction["amount_minor"]), "amount_minor_when_currency_is_eur"
    return 0, "missing_foreign_exchange"


def _posting_preview_item(
    evaluation: PostingEvaluation,
    cleanup: CleanupPrevalidation,
    *,
    inbox_root: Path | None,
    archive_root: Path | None,
) -> dict[str, Any]:
    cleanup_blocker = (
        {
            "code": cleanup.reason_code,
            "message": cleanup.message,
            "subject_table": "transactions",
            "subject_id": evaluation.transaction_id,
            "details": {},
        }
        if cleanup.blocking and cleanup.reason_code is not None
        else None
    )
    blockers = [
        {
            "code": blocker.code,
            "message": blocker.message,
            "subject_table": blocker.subject_table,
            "subject_id": blocker.subject_id,
            "details": blocker.details,
        }
        for blocker in evaluation.blockers
    ]
    if cleanup_blocker is not None:
        blockers.append(cleanup_blocker)
    preview_bucket = _preview_bucket(
        lifecycle_status=evaluation.lifecycle_status,
        blockers=blockers,
    )
    return {
        "review_id": evaluation.review_id,
        "kind": evaluation.kind,
        "period": evaluation.period,
        "period_status": evaluation.period_status,
        "transaction_id": evaluation.transaction_id,
        "lifecycle_status": evaluation.lifecycle_status,
        "row_version": evaluation.row_version,
        "expected_row_version": evaluation.row_version,
        "transaction_date": evaluation.transaction_date,
        "entry_type": evaluation.entry_type,
        "description": evaluation.description,
        "amount_minor": evaluation.amount_minor,
        "currency": evaluation.currency,
        "document_id": evaluation.document_id,
        "effective_amount_eur_minor": evaluation.effective_amount_eur_minor,
        "effective_amount_eur_formula": evaluation.effective_amount_eur_formula,
        "amount_eur": _eur_string(int(evaluation.effective_amount_eur_minor or 0)),
        "tax_treatments": list(evaluation.tax_treatments),
        "blocking_issues": list(evaluation.blocking_issues),
        "linked_document_approved": evaluation.linked_document_approved,
        "ready_to_approve": evaluation.ready_to_approve,
        "ready_to_post": preview_bucket == "ready",
        "posting_deferred_until": evaluation.posting_deferred_until,
        "preview_bucket": preview_bucket,
        "blockers": blockers,
        "cleanup": {
            "applicable": cleanup.cleanup_row is not None,
            "status": cleanup.status,
            "message": cleanup.message,
            "blocking": cleanup.blocking,
            "reason_code": cleanup.reason_code,
            "file_name": (
                cleanup.candidate.source_path.name
                if cleanup.candidate is not None
                else (
                    Path(str(cleanup.cleanup_row["inbox_source_path"])).name
                    if cleanup.cleanup_row is not None
                    and cleanup.cleanup_row.get("inbox_source_path")
                    else None
                )
            ),
            "roots": {
                "inbox_root": str(inbox_root) if inbox_root is not None else None,
                "archive_root": str(archive_root) if archive_root is not None else None,
            },
        },
    }


def _preview_bucket(
    *,
    lifecycle_status: str,
    blockers: list[dict[str, Any]],
) -> str:
    if lifecycle_status != "approved":
        return "blocked"
    if not blockers:
        return "ready"
    if len(blockers) == 1 and blockers[0]["code"] == "future_dated":
        return "deferred"
    return "blocked"


def _eur_string(amount_minor: int) -> str:
    return f"{(Decimal(amount_minor) / Decimal(100)):.2f}"
