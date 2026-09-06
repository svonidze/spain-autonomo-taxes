"""Shared accounting operations; no HTTP, CLI dispatch or UI dependency."""

from __future__ import annotations
from ..counterparty_names import CounterpartyMatchError, find_name_candidates
from dataclasses import asdict, dataclass
from datetime import date
import hashlib
from pathlib import Path
import sqlite3
from typing import Any
from ..intake import archive_evidence, inspect_document
from ..intake_bundle import (
    InvoiceAmounts,
    extract_invoice_amounts,
    extract_invoice_number,
    review_requirements,
)
from ..ledger_db import LedgerDB, LedgerDbError, open as open_ledger_db
from ..parsers import LedgerEntry, parse_any_date, parse_expense, parse_income_invoice
from ..storage_service import register_local_source_replica


@dataclass
class IntakeRequest:
    db: Path
    path: Path
    kind: str
    period: str | None = None
    issued_on: str | None = None
    document_number: str | None = None
    counterparty_id: str | None = None
    counterparty_name: str | None = None
    defer_counterparty: bool = False
    drive_file_id: str | None = None
    archive_root: Path | None = None
    tesseract_command: str = "tesseract"
    gross: Any = None
    taxable_base: Any = None
    vat: Any = None
    currency: str | None = None
    document_only: bool = False


def ingest_fields(config, fields, path, drive_file_id=None):
    from decimal import Decimal

    values = {
        name: str(fields.get(name, "")).strip() or None
        for name in (
            "period",
            "issued_on",
            "document_number",
            "counterparty_name",
            "currency",
        )
    }
    amounts = {
        name: Decimal(str(fields[name])) if str(fields.get(name, "")).strip() else None
        for name in ("gross", "taxable_base", "vat")
    }
    return _ingest_document(
        IntakeRequest(
            db=config.database,
            path=path,
            kind=fields["kind"],
            archive_root=config.archive_root,
            drive_file_id=drive_file_id,
            defer_counterparty=fields.get("defer_counterparty") == "1",
            **values,
            **amounts,
        )
    )


def _ingest_document(args: IntakeRequest) -> dict[str, Any]:
    if getattr(args, "defer_counterparty", False) and args.kind != "expense_invoice":
        raise ValueError("Deferred supplier selection is only supported for expenses")
    if args.document_only and any(
        value is not None
        for value in (args.gross, args.taxable_base, args.vat, args.currency)
    ):
        raise ValueError(
            "Invoice amount overrides cannot be combined with --document-only"
        )
    result = inspect_document(
        args.path, args.kind, tesseract_command=args.tesseract_command
    )
    suggestion = _document_suggestion(args.path, args.kind, result.extracted_text)
    amounts = extract_invoice_amounts(
        result.extracted_text,
        suggestion,
        gross_override=args.gross,
        taxable_base_override=args.taxable_base,
        vat_override=args.vat,
        currency_override=args.currency,
    )
    requirements = review_requirements(args.kind, suggestion, amounts)
    parsed_date = (
        suggestion.date
        if suggestion is not None and suggestion.date is not None
        else parse_any_date(result.extracted_text)
    )
    issued_on = args.issued_on or (
        parsed_date.isoformat() if parsed_date is not None else None
    )
    if issued_on is None:
        raise ValueError(
            "--issued-on is required when the document parser cannot identify a date"
        )
    period_key = _quarter_key(date.fromisoformat(issued_on))
    if args.period is not None and args.period != period_key:
        raise ValueError(
            f"Document belongs to {period_key}, not requested {args.period}"
        )
    archived_path = (
        archive_evidence(
            args.path,
            args.archive_root,
            period_key=period_key,
            evidence_kind=args.kind,
            digest=result.sha256,
        )
        if args.archive_root is not None
        else Path(result.source_path)
    )
    with open_ledger_db(args.db) as db:
        batch = db.add_import_batch(
            source_name=str(args.path.resolve()),
            source_hash=result.sha256,
            batch_key=f"document:{result.sha256}",
            notes=f"Extraction method: {result.extraction_method}",
        )
        counterparty_id = args.counterparty_id
        if counterparty_id is None and not getattr(args, "defer_counterparty", False):
            counterparty_id = _upsert_intake_counterparty(
                db,
                suggestion,
                preferred_name=getattr(args, "counterparty_name", None),
            )
        document_number = args.document_number or extract_invoice_number(
            result.extracted_text
        )
        if document_number is None and suggestion is not None:
            parsed_description = suggestion.description or ""
            if args.kind == "income_invoice" or parsed_description != args.path.name:
                document_number = parsed_description or None
        existing_document = db.connection.execute(
            """
            SELECT d.*, p.period_key
            FROM documents d
            LEFT JOIN periods p ON p.period_id = d.period_id
            WHERE d.source_hash = ?
            """,
            (result.sha256,),
        ).fetchone()
        is_new_document = existing_document is None
        if existing_document is None:
            document = db.upsert_document(
                external_key=f"sha256:{result.sha256}",
                counterparty_id=counterparty_id,
                import_batch_id=batch["import_batch_id"],
                document_type=args.kind,
                document_number=document_number,
                issued_on=issued_on,
                period_key=period_key,
                currency=amounts.currency
                or (suggestion.currency if suggestion is not None else ""),
                total_minor=(
                    int(amounts.gross * 100) if amounts.gross is not None else None
                ),
                lifecycle_status=result.status,
                source_hash=result.sha256,
            )
        else:
            document = _backfill_existing_intake_document(
                db,
                existing_document,
                import_batch_id=batch["import_batch_id"],
                counterparty_id=counterparty_id,
                document_kind=args.kind,
                period_key=period_key,
                issued_on=issued_on,
                document_number=document_number,
                amounts=amounts,
            )
        document = db.set_document_storage(
            document["document_id"],
            source_path=str(archived_path),
            drive_file_id=args.drive_file_id,
            mime_type=result.mime_type,
            expected_row_version=document["row_version"],
        )
        # Keep the legacy path during the rollout, while registering the same
        # immutable bytes in the provider-neutral storage catalogue.
        register_local_source_replica(
            db,
            document_id=str(document["document_id"]),
            source_path=archived_path,
            media_type=result.mime_type,
            storage_root=args.archive_root or archived_path.parent,
        )
        if is_new_document and result.needs_review:
            db.add_validation_issue(
                period_key=period_key,
                issue_code="document_structural_review",
                severity="error",
                message="; ".join(result.structural_errors),
                subject_table="documents",
                subject_id=document["document_id"],
                blocking=True,
                source_hash=result.sha256,
            )
        if is_new_document and suggestion is not None and suggestion.review_required:
            db.add_validation_issue(
                period_key=period_key,
                issue_code="document_classification_review",
                severity="warning",
                message=suggestion.notes or "Document requires classification review",
                subject_table="documents",
                subject_id=document["document_id"],
                blocking=True,
                source_hash=result.sha256,
            )
        if is_new_document and counterparty_id is not None:
            counterparty = db.connection.execute(
                "SELECT country_code FROM counterparties WHERE counterparty_id = ?",
                (counterparty_id,),
            ).fetchone()
            if counterparty["country_code"] == "ZZ":
                db.add_validation_issue(
                    period_key=period_key,
                    issue_code="counterparty_tax_profile_review",
                    severity="warning",
                    message="Review country and tax identity before posting this counterparty.",
                    subject_table="counterparties",
                    subject_id=counterparty_id,
                    blocking=True,
                    source_hash=result.sha256,
                )
        transaction = _intake_transaction_draft(
            db,
            document=document,
            document_kind=args.kind,
            period_key=period_key,
            issued_on=issued_on,
            document_number=document_number,
            counterparty_id=counterparty_id,
            suggestion=suggestion,
            amounts=amounts,
            requirements=requirements,
            source_hash=result.sha256,
            structural_review=result.needs_review,
            document_only=args.document_only,
            create_missing_issue=is_new_document,
        )
    return {
        **asdict(result),
        "archived_path": str(archived_path),
        "period": period_key,
        "issued_on": issued_on,
        "suggested_entry": suggestion.as_row() if suggestion is not None else None,
        "invoice_amounts": amounts.as_dict(),
        "review_requirements": list(requirements),
        "document_id": document["document_id"],
        "document_created": is_new_document,
        "document_lifecycle_status": document["lifecycle_status"],
        "row_version": document["row_version"],
        "transaction": transaction,
    }


def _document_suggestion(path: Path, kind: str, text: str) -> LedgerEntry | None:
    if kind == "income_invoice":
        return parse_income_invoice(path, text)
    if kind == "expense_invoice":
        extraction_error = None if text.strip() else "No readable text extracted"
        suggestion = parse_expense(path, text, extraction_error=extraction_error)
        if suggestion.date is None and path.suffix.casefold() in {
            ".csv",
            ".txt",
            ".tsv",
        }:
            return LedgerEntry(
                kind="expense",
                date=parse_any_date(text),
                document=str(path),
                counterparty="Unknown",
                description=path.name,
                amount_original=None,
                currency="",
                amount_eur=None,
                deductible_eur=None,
                category="unknown",
                confidence="low",
                review_required=True,
                notes="Plain-text expense candidate; no supported supplier parser matched",
            )
        return suggestion
    return None


def _intake_transaction_draft(
    db: LedgerDB,
    *,
    document: dict[str, Any],
    document_kind: str,
    period_key: str,
    issued_on: str,
    document_number: str | None,
    counterparty_id: str | None,
    suggestion: LedgerEntry | None,
    amounts: InvoiceAmounts,
    requirements: tuple[str, ...],
    source_hash: str,
    structural_review: bool,
    document_only: bool,
    create_missing_issue: bool,
) -> dict[str, Any] | None:
    if document_only or document_kind not in {"income_invoice", "expense_invoice"}:
        return None

    external_key = f"intake-document:{document['document_id']}"
    existing = db.connection.execute(
        """
        SELECT t.*, p.period_key
        FROM transactions t
        JOIN periods p ON p.period_id = t.period_id
        WHERE t.external_key = ?
        """,
        (external_key,),
    ).fetchone()
    if existing is not None:
        expected = {
            "period_key": period_key,
            "transaction_date": issued_on,
            "entry_type": "income" if document_kind == "income_invoice" else "expense",
            "document_id": document["document_id"],
        }
        changed = [key for key, value in expected.items() if existing[key] != value]
        if amounts.gross is not None and existing["amount_minor"] != int(
            amounts.gross * 100
        ):
            changed.append("amount_minor")
        if amounts.currency and existing["currency"] != amounts.currency:
            changed.append("currency")
        if changed:
            raise LedgerDbError(
                "Existing intake transaction conflicts with the same source document: "
                + ", ".join(sorted(set(changed)))
            )
        treatment = db.connection.execute(
            """
            SELECT * FROM tax_treatments
            WHERE transaction_id = ? AND treatment_type = 'invoice_review'
              AND jurisdiction = 'ES'
            """,
            (existing["transaction_id"],),
        ).fetchone()
        if treatment is not None and treatment["tax_code"] == "unknown":
            treatment_changes: list[str] = []
            backfill_base = treatment["taxable_base_minor"]
            backfill_vat = treatment["vat_minor"]
            if amounts.taxable_base is not None and treatment[
                "taxable_base_minor"
            ] not in {
                None,
                int(amounts.taxable_base * 100),
            }:
                treatment_changes.append("taxable_base_minor")
            elif (
                amounts.taxable_base is not None
                and treatment["taxable_base_minor"] is None
            ):
                backfill_base = int(amounts.taxable_base * 100)
            if amounts.vat is not None and treatment["vat_minor"] not in {
                None,
                int(amounts.vat * 100),
            }:
                treatment_changes.append("vat_minor")
            elif amounts.vat is not None and treatment["vat_minor"] is None:
                backfill_vat = int(amounts.vat * 100)
            if treatment_changes:
                raise LedgerDbError(
                    "Existing invoice review treatment conflicts with corrected re-ingest: "
                    + ", ".join(treatment_changes)
                )
            if (
                backfill_base != treatment["taxable_base_minor"]
                or backfill_vat != treatment["vat_minor"]
            ):
                treatment = db.add_detailed_tax_treatment(
                    transaction_id=existing["transaction_id"],
                    treatment_type=treatment["treatment_type"],
                    tax_code=treatment["tax_code"],
                    jurisdiction=treatment["jurisdiction"],
                    rate_basis_points=treatment["rate_basis_points"],
                    deductible_ratio=treatment["deductible_ratio"],
                    taxable_base_minor=backfill_base,
                    vat_minor=backfill_vat,
                    deductible_irpf_minor=treatment["deductible_irpf_minor"],
                    deductible_vat_minor=treatment["deductible_vat_minor"],
                    withholding_minor=treatment["withholding_minor"],
                    include_modelo130=bool(treatment["include_modelo130"]),
                    include_modelo303=bool(treatment["include_modelo303"]),
                    include_modelo347=bool(treatment["include_modelo347"]),
                    rule_version_id=treatment["rule_version_id"],
                    notes=(treatment["notes"] or "")
                    + "; candidate amounts backfilled by corrected re-ingest",
                    expected_row_version=treatment["row_version"],
                )
                amount_summary = amounts.as_dict()
                db.add_validation_issue(
                    period_key=period_key,
                    issue_code="transaction_tax_review",
                    severity="warning",
                    message=(
                        "Review required after corrected re-ingest; "
                        f"gross={amount_summary['gross']} {amounts.currency}; "
                        f"taxable_base={amount_summary['taxable_base']}; "
                        f"vat={amount_summary['vat']}; decisions="
                        + ", ".join(requirements)
                    ),
                    subject_table="transactions",
                    subject_id=existing["transaction_id"],
                    blocking=True,
                    source_hash=source_hash,
                )
        return _intake_transaction_summary(
            dict(existing),
            created=False,
            treatment=dict(treatment) if treatment is not None else None,
        )

    if document["lifecycle_status"] in {
        "approved",
        "posted",
        "included_in_snapshot",
        "duplicate",
        "rejected",
        "void",
    }:
        return {
            "created": False,
            "reason": f"document_lifecycle:{document['lifecycle_status']}",
        }

    if not amounts.draft_ready:
        if create_missing_issue:
            db.add_validation_issue(
                period_key=period_key,
                issue_code="transaction_draft_missing_amount",
                severity="error",
                message="; ".join(amounts.errors) or "Invoice amount is incomplete",
                subject_table="documents",
                subject_id=document["document_id"],
                blocking=True,
                source_hash=source_hash,
            )
        return {"created": False, "reason": "invoice_amounts_incomplete"}

    assert amounts.gross is not None
    entry_type = "income" if document_kind == "income_invoice" else "expense"
    effective_counterparty_id = document.get("counterparty_id") or counterparty_id
    description = (
        document_number
        or (suggestion.description if suggestion is not None else None)
        or Path(document.get("source_path") or "invoice").name
    )
    transaction = db.add_transaction(
        external_key=external_key,
        period_key=period_key,
        transaction_date=issued_on,
        booking_date=issued_on,
        entry_type=entry_type,
        description=description,
        amount_minor=int(amounts.gross * 100),
        currency=amounts.currency,
        amount_original_minor=int(amounts.gross * 100),
        original_currency=amounts.currency,
        amount_eur_minor=(
            int(amounts.gross * 100) if amounts.currency == "EUR" else None
        ),
        direction="credit" if entry_type == "income" else "debit",
        lifecycle_status="received",
        document_id=document["document_id"],
        counterparty_id=effective_counterparty_id,
        source_hash=hashlib.sha256(
            f"intake-transaction:{source_hash}".encode("utf-8")
        ).hexdigest(),
    )
    transaction = db.transition_transaction(
        transaction["transaction_id"],
        lifecycle_status="extracted",
        expected_row_version=transaction["row_version"],
    )
    needs_review = bool(
        structural_review
        or amounts.errors
        or suggestion is None
        or suggestion.review_required
        or effective_counterparty_id is None
    )
    if needs_review:
        transaction = db.transition_transaction(
            transaction["transaction_id"],
            lifecycle_status="needs_review",
            expected_row_version=transaction["row_version"],
        )
    treatment = db.add_detailed_tax_treatment(
        transaction_id=transaction["transaction_id"],
        treatment_type="invoice_review",
        tax_code="unknown",
        jurisdiction="ES",
        taxable_base_minor=(
            int(amounts.taxable_base * 100)
            if amounts.taxable_base is not None
            else None
        ),
        vat_minor=int(amounts.vat * 100) if amounts.vat is not None else None,
        notes=(
            "Extraction candidate only; gross_source="
            f"{amounts.gross_source or 'unknown'}; base_source="
            f"{amounts.taxable_base_source or 'unknown'}; vat_source="
            f"{amounts.vat_source or 'unknown'}"
        ),
        source_hash=hashlib.sha256(
            f"intake-treatment:{source_hash}".encode("utf-8")
        ).hexdigest(),
    )
    amount_summary = amounts.as_dict()
    db.add_validation_issue(
        period_key=period_key,
        issue_code="transaction_tax_review",
        severity="warning",
        message=(
            "Review required before approval; "
            f"gross={amount_summary['gross']} {amounts.currency}; "
            f"taxable_base={amount_summary['taxable_base']}; "
            f"vat={amount_summary['vat']}; decisions=" + ", ".join(requirements)
        ),
        subject_table="transactions",
        subject_id=transaction["transaction_id"],
        blocking=True,
        source_hash=source_hash,
    )
    _resolve_open_intake_issue(
        db,
        issue_code="transaction_draft_missing_amount",
        subject_table="documents",
        subject_id=document["document_id"],
        reason="Reviewed invoice amounts supplied during corrected re-ingest.",
    )
    return _intake_transaction_summary(
        transaction,
        created=True,
        treatment=treatment,
    )


def _backfill_existing_intake_document(
    db: LedgerDB,
    document: sqlite3.Row,
    *,
    import_batch_id: str,
    counterparty_id: str | None,
    document_kind: str,
    period_key: str,
    issued_on: str,
    document_number: str | None,
    amounts: InvoiceAmounts,
) -> dict[str, Any]:
    expected = {
        "document_type": document_kind,
        "period_key": period_key,
        "issued_on": issued_on,
    }
    changed = [key for key, value in expected.items() if document[key] != value]
    if document_number and document["document_number"] not in {
        None,
        "",
        document_number,
    }:
        changed.append("document_number")
    if amounts.gross is not None and document["total_minor"] not in {
        None,
        int(amounts.gross * 100),
    }:
        changed.append("total_minor")
    if amounts.currency and document["currency"] not in {None, "", amounts.currency}:
        changed.append("currency")
    if changed:
        raise LedgerDbError(
            "Existing intake document conflicts with the same content hash: "
            + ", ".join(sorted(set(changed)))
        )

    active = document["lifecycle_status"] in {"received", "extracted", "needs_review"}
    desired_counterparty_id = document["counterparty_id"] or counterparty_id
    desired_import_batch_id = document["import_batch_id"] or import_batch_id
    desired_document_number = document["document_number"] or document_number
    desired_currency = document["currency"] or amounts.currency
    desired_total_minor = document["total_minor"]
    if desired_total_minor is None and amounts.gross is not None:
        desired_total_minor = int(amounts.gross * 100)
    backfill_changed = any(
        (
            desired_counterparty_id != document["counterparty_id"],
            desired_import_batch_id != document["import_batch_id"],
            desired_document_number != document["document_number"],
            desired_currency != document["currency"],
            desired_total_minor != document["total_minor"],
        )
    )
    if not active or not backfill_changed:
        return dict(document)
    return db.upsert_document(
        document_id=document["document_id"],
        external_key=document["external_key"],
        counterparty_id=desired_counterparty_id,
        import_batch_id=desired_import_batch_id,
        document_type=document["document_type"],
        document_number=desired_document_number,
        issued_on=document["issued_on"],
        period_key=document["period_key"],
        currency=desired_currency,
        total_minor=desired_total_minor,
        lifecycle_status=document["lifecycle_status"],
        source_hash=document["source_hash"],
        expected_row_version=document["row_version"],
    )


def _resolve_open_intake_issue(
    db: LedgerDB,
    *,
    issue_code: str,
    subject_table: str,
    subject_id: str,
    reason: str,
) -> None:
    issue = db.connection.execute(
        """
        SELECT * FROM validation_issues
        WHERE issue_code = ? AND subject_table = ? AND subject_id = ?
          AND issue_status = 'open'
        """,
        (issue_code, subject_table, subject_id),
    ).fetchone()
    if issue is None:
        return
    db.resolve_issue(
        issue["validation_issue_id"],
        reason=reason,
        expected_row_version=issue["row_version"],
    )


def _intake_transaction_summary(
    transaction: dict[str, Any],
    *,
    created: bool,
    treatment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = {
        "created": created,
        "transaction_id": transaction["transaction_id"],
        "row_version": transaction["row_version"],
        "lifecycle_status": transaction["lifecycle_status"],
        "entry_type": transaction["entry_type"],
        "amount_minor": transaction["amount_minor"],
        "currency": transaction["currency"],
        "amount_eur_minor": transaction["amount_eur_minor"],
        "document_id": transaction["document_id"],
    }
    if treatment is not None:
        summary["tax_treatment_id"] = treatment["treatment_id"]
        summary["tax_treatment_row_version"] = treatment["row_version"]
        summary["tax_code"] = treatment["tax_code"]
    return summary


def _upsert_intake_counterparty(
    db: LedgerDB,
    suggestion: LedgerEntry | None,
    *,
    preferred_name: str | None = None,
) -> str | None:
    counterparty_name = (preferred_name or "").strip()
    if not counterparty_name and suggestion is not None:
        counterparty_name = suggestion.counterparty
    if not counterparty_name or counterparty_name == "Unknown":
        return None
    matches = find_name_candidates(db.connection, counterparty_name)
    if len(matches) == 1:
        return matches[0]["counterparty_id"]
    if len(matches) > 1:
        raise CounterpartyMatchError(
            "Counterparty name is ambiguous; pass --counterparty-id"
        )
    normalized = "".join(
        character for character in counterparty_name.casefold() if character.isalnum()
    )
    row = db.upsert_counterparty(
        external_key=f"intake-name:{normalized}",
        display_name=counterparty_name,
        country_code="ZZ",
        source_hash=hashlib.sha256(
            f"intake-counterparty:{normalized}".encode("utf-8")
        ).hexdigest(),
    )
    return row["counterparty_id"]


def _quarter_key(value: date) -> str:
    return f"{value.year}-Q{((value.month - 1) // 3) + 1}"
