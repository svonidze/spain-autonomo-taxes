from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping
import uuid

from .ledger_db import LedgerDB
from .money import cents
from .storage_service import register_local_source_replica


@dataclass(frozen=True)
class NonInvoiceExpensePreset:
    kind: str
    document_type: str
    default_counterparty_name: str | None
    default_counterparty_country: str | None
    default_description: str
    default_business_purpose: str | None
    aeat_expense_concept: str


PRESETS = {
    "social-security": NonInvoiceExpensePreset(
        kind="social-security",
        document_type="social_security_evidence",
        default_counterparty_name="Synthetic Party 011",
        default_counterparty_country="ES",
        default_description="Autonomo social security contribution",
        default_business_purpose=(
            "Mandatory social security contribution for the registered business activity"
        ),
        aeat_expense_concept="G45",
    ),
    "bank-fee": NonInvoiceExpensePreset(
        kind="bank-fee",
        document_type="bank_fee_evidence",
        default_counterparty_name=None,
        default_counterparty_country=None,
        default_description="Bank fee for business activity",
        default_business_purpose=None,
        aeat_expense_concept="G24",
    ),
}

IDENTITY_AEAT_TYPES = {
    "vat_id": "02",
    "passport": "03",
    "official_id": "04",
    "residence_certificate": "05",
    "other_proof": "06",
}


@dataclass(frozen=True)
class NonInvoiceExpenseInput:
    kind: str
    evidence_sha256: str
    evidence_mime_type: str
    archived_path: Path
    transaction_date: date
    gross_eur: Decimal
    deductible_eur: Decimal
    reference: str
    business_purpose: str
    description: str
    note: str | None = None
    period_key: str | None = None
    counterparty_id: str | None = None
    counterparty_name: str | None = None
    counterparty_country: str | None = None
    counterparty_tax_id: str | None = None
    counterparty_identity_kind: str | None = None
    counterparty_identifier: str | None = None
    business_activity_id: str | None = None
    storage_root: Path | None = None


class NonInvoiceExpenseError(ValueError):
    """Raised when non-invoice evidence cannot produce a reviewed ledger row."""


def record_non_invoice_expense(
    database: LedgerDB,
    request: NonInvoiceExpenseInput,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Atomically create one reviewed, unposted expense backed by non-invoice evidence."""
    normalized = _normalize_request(request)
    connection = database.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        result = _record(
            database,
            normalized,
            verify_archive=not dry_run,
            register_replica=not dry_run,
        )
        if dry_run:
            connection.rollback()
            result["status"] = "dry_run"
        else:
            connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    return result


def _record(
    database: LedgerDB,
    request: NonInvoiceExpenseInput,
    *,
    verify_archive: bool,
    register_replica: bool,
) -> dict[str, Any]:
    connection = database.connection
    preset = PRESETS[request.kind]
    period_key = request.period_key or quarter_key(request.transaction_date)
    expected_period = quarter_key(request.transaction_date)
    if period_key != expected_period:
        raise NonInvoiceExpenseError(
            f"Expense belongs to {expected_period}, not requested {period_key}"
        )
    period = _ensure_open_period(connection, period_key, request.transaction_date)
    activity_id = _resolve_activity(
        connection,
        request.transaction_date,
        request.business_activity_id,
    )

    existing_document = connection.execute(
        "SELECT * FROM documents WHERE source_hash = ?",
        (request.evidence_sha256,),
    ).fetchone()
    existing_transaction = None
    if existing_document is not None:
        existing_transaction = connection.execute(
            "SELECT * FROM transactions WHERE document_id = ?",
            (existing_document["document_id"],),
        ).fetchone()
        if existing_transaction is None:
            raise NonInvoiceExpenseError(
                "Evidence is already registered as a document without the expected expense transaction"
            )

    counterparty = _resolve_counterparty(
        connection,
        request,
        existing_transaction=existing_transaction,
    )
    counterparty_id = str(counterparty["counterparty_id"])
    transaction_hash = _stable_hash(
        {
            "workflow": "non_invoice_expense_v1",
            "kind": request.kind,
            "evidence_sha256": request.evidence_sha256,
            "period_key": period_key,
            "transaction_date": request.transaction_date.isoformat(),
            "gross_minor": _minor(request.gross_eur),
            "deductible_minor": _minor(request.deductible_eur),
            "reference": request.reference,
            "business_purpose": request.business_purpose,
            "description": request.description,
            "note": request.note,
            "counterparty_id": counterparty_id,
            "business_activity_id": activity_id,
        }
    )
    treatment_note = _treatment_note(request, preset)
    treatment_hash = _stable_hash(
        {
            "transaction_source_hash": transaction_hash,
            "tax_code": "deductible_expense",
            "aeat_invoice_type": "SF",
            "aeat_operation_key": "01",
            "aeat_reverse_charge": False,
            "aeat_expense_concept": preset.aeat_expense_concept,
            "deductible_irpf_minor": _minor(request.deductible_eur),
            "include_modelo130": request.deductible_eur > 0,
            "notes": treatment_note,
        }
    )

    if existing_document is not None:
        result = _validate_existing(
            connection,
            request=request,
            preset=preset,
            period=period,
            activity_id=activity_id,
            counterparty_id=counterparty_id,
            transaction_hash=transaction_hash,
            treatment_hash=treatment_hash,
            existing_document=existing_document,
            existing_transaction=existing_transaction,
            verify_archive=verify_archive,
        )
        result["status"] = "already_recorded"
        return result

    _verify_archive(request.archived_path, request.evidence_sha256, verify_archive)
    now = _utc_now()
    batch_id = _deterministic_id("non-invoice-import", request.evidence_sha256)
    document_id = _deterministic_id("non-invoice-document", request.evidence_sha256)
    transaction_id = _deterministic_id("non-invoice-transaction", request.evidence_sha256)
    treatment_id = _deterministic_id("non-invoice-treatment", request.evidence_sha256)
    source_id = _deterministic_id("non-invoice-document-source", request.evidence_sha256)

    existing_batch = connection.execute(
        "SELECT import_batch_id FROM import_batches WHERE source_hash = ?",
        (request.evidence_sha256,),
    ).fetchone()
    if existing_batch is None:
        connection.execute(
            """
            INSERT INTO import_batches (
                import_batch_id, source_name, batch_key, imported_at, notes,
                source_hash, row_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                batch_id,
                str(request.archived_path),
                f"non-invoice:{request.evidence_sha256}",
                now,
                f"Typed {request.kind} evidence intake",
                request.evidence_sha256,
                now,
                now,
            ),
        )
    else:
        batch_id = str(existing_batch["import_batch_id"])

    connection.execute(
        """
        INSERT INTO documents (
            document_id, external_key, counterparty_id, import_batch_id,
            document_type, document_number, issued_on, period_id, currency,
            total_minor, lifecycle_status, source_hash, row_version,
            created_at, updated_at, source_path, drive_file_id, mime_type,
            rectifies_document_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'EUR', ?, 'approved', ?, 1, ?, ?, ?, NULL, ?, NULL)
        """,
        (
            document_id,
            f"non-invoice-evidence:{request.evidence_sha256}",
            counterparty_id,
            batch_id,
            preset.document_type,
            request.reference,
            request.transaction_date.isoformat(),
            period["period_id"],
            _minor(request.gross_eur),
            request.evidence_sha256,
            now,
            now,
            str(request.archived_path),
            request.evidence_mime_type,
        ),
    )
    connection.execute(
        """
        INSERT INTO document_sources (
            document_source_id, document_id, import_batch_id,
            source_book_line_id, source_file, source_row_number,
            source_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
        """,
        (
            source_id,
            document_id,
            batch_id,
            f"non-invoice:{request.evidence_sha256}",
            str(request.archived_path),
            _stable_hash({"document_source": request.evidence_sha256}),
            now,
        ),
    )
    if register_replica:
        register_local_source_replica(
            database,
            document_id=document_id,
            source_path=request.archived_path,
            media_type=request.evidence_mime_type,
            storage_root=request.storage_root or request.archived_path.parent,
        )
    gross_minor = _minor(request.gross_eur)
    connection.execute(
        """
        INSERT INTO transactions (
            transaction_id, external_key, period_id, transaction_date,
            booking_date, entry_type, description, amount_minor, currency,
            direction, lifecycle_status, document_id, counterparty_id,
            correction_of_transaction_id, correction_kind, included_snapshot_id,
            source_hash, row_version, created_at, updated_at,
            amount_original_minor, original_currency, amount_eur_minor,
            fx_rate_id, business_activity_id
        ) VALUES (?, ?, ?, ?, ?, 'expense', ?, ?, 'EUR', 'debit', 'approved', ?, ?,
                  NULL, NULL, NULL, ?, 1, ?, ?, ?, 'EUR', ?, NULL, ?)
        """,
        (
            transaction_id,
            f"non-invoice-expense:{request.evidence_sha256}",
            period["period_id"],
            request.transaction_date.isoformat(),
            request.transaction_date.isoformat(),
            request.description,
            gross_minor,
            document_id,
            counterparty_id,
            transaction_hash,
            now,
            now,
            gross_minor,
            gross_minor,
            activity_id,
        ),
    )
    deductible_minor = _minor(request.deductible_eur)
    connection.execute(
        """
        INSERT INTO tax_treatments (
            treatment_id, transaction_id, treatment_type, jurisdiction,
            rate_basis_points, deductible_ratio, notes, source_hash,
            row_version, created_at, updated_at, tax_code,
            taxable_base_minor, vat_minor, deductible_irpf_minor,
            deductible_vat_minor, withholding_minor, include_modelo130,
            include_modelo303, include_modelo347, rule_version_id,
            aeat_invoice_type, aeat_operation_key,
            aeat_operation_qualification, aeat_exemption_code,
            aeat_reverse_charge, aeat_expense_concept
        ) VALUES (?, ?, 'non_invoice_review', 'ES', NULL, ?, ?, ?, 1, ?, ?,
                  'deductible_expense', 0, 0, ?, 0, 0, ?, 0, 0, NULL,
                  'SF', '01', NULL, NULL, 0, ?)
        """,
        (
            treatment_id,
            transaction_id,
            float(request.deductible_eur / request.gross_eur),
            treatment_note,
            treatment_hash,
            now,
            now,
            deductible_minor,
            int(request.deductible_eur > 0),
            preset.aeat_expense_concept,
        ),
    )
    return _result(
        request=request,
        preset=preset,
        document_id=document_id,
        transaction_id=transaction_id,
        transaction_row_version=1,
        counterparty_id=counterparty_id,
        business_activity_id=activity_id,
        status="created",
        transaction_lifecycle_status="approved",
    )


def _normalize_request(request: NonInvoiceExpenseInput) -> NonInvoiceExpenseInput:
    if request.kind not in PRESETS:
        raise NonInvoiceExpenseError(f"Unsupported non-invoice expense kind: {request.kind}")
    digest = request.evidence_sha256.strip().lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise NonInvoiceExpenseError("Evidence SHA-256 must contain 64 hexadecimal characters")
    if request.transaction_date > date.today():
        raise NonInvoiceExpenseError("Future-dated expenses cannot be approved")
    gross = cents(request.gross_eur)
    deductible = cents(request.deductible_eur)
    if gross <= 0:
        raise NonInvoiceExpenseError("Expense gross amount must be positive")
    if deductible < 0 or deductible > gross:
        raise NonInvoiceExpenseError(
            "IRPF deductible amount must be between zero and the gross amount"
        )
    preset = PRESETS[request.kind]
    reference = request.reference.strip()
    if not reference:
        raise NonInvoiceExpenseError("Evidence reference is required")
    business_purpose = request.business_purpose.strip()
    if not business_purpose:
        raise NonInvoiceExpenseError("Business purpose is required")
    description = request.description.strip()
    if not description:
        raise NonInvoiceExpenseError("Expense description is required")
    country = (request.counterparty_country or preset.default_counterparty_country or "").strip().upper()
    if country and (len(country) != 2 or not country.isalpha()):
        raise NonInvoiceExpenseError("Counterparty country must be an ISO alpha-2 code")
    identity_kind = (
        request.counterparty_identity_kind.strip().lower()
        if request.counterparty_identity_kind
        else None
    )
    if identity_kind is not None and identity_kind not in IDENTITY_AEAT_TYPES:
        raise NonInvoiceExpenseError(f"Unsupported counterparty identity kind: {identity_kind}")
    return NonInvoiceExpenseInput(
        kind=request.kind,
        evidence_sha256=digest,
        evidence_mime_type=request.evidence_mime_type.strip() or "application/octet-stream",
        archived_path=request.archived_path.resolve(),
        transaction_date=request.transaction_date,
        gross_eur=gross,
        deductible_eur=deductible,
        reference=reference,
        business_purpose=business_purpose,
        description=description,
        note=request.note.strip() if request.note and request.note.strip() else None,
        period_key=request.period_key.strip().upper() if request.period_key else None,
        counterparty_id=request.counterparty_id.strip() if request.counterparty_id else None,
        counterparty_name=(
            request.counterparty_name.strip()
            if request.counterparty_name and request.counterparty_name.strip()
            else preset.default_counterparty_name
        ),
        counterparty_country=country or None,
        counterparty_tax_id=(
            request.counterparty_tax_id.strip().upper()
            if request.counterparty_tax_id and request.counterparty_tax_id.strip()
            else None
        ),
        counterparty_identity_kind=identity_kind,
        counterparty_identifier=(
            request.counterparty_identifier.strip()
            if request.counterparty_identifier and request.counterparty_identifier.strip()
            else None
        ),
        business_activity_id=(
            request.business_activity_id.strip() if request.business_activity_id else None
        ),
        storage_root=request.storage_root.resolve() if request.storage_root else None,
    )


def _ensure_open_period(
    connection: sqlite3.Connection,
    period_key: str,
    transaction_date: date,
) -> Mapping[str, Any]:
    period = connection.execute(
        "SELECT * FROM periods WHERE period_key = ?",
        (period_key,),
    ).fetchone()
    if period is None:
        quarter = ((transaction_date.month - 1) // 3) + 1
        start_month = (quarter - 1) * 3 + 1
        starts_on = date(transaction_date.year, start_month, 1)
        if quarter == 4:
            ends_on = date(transaction_date.year, 12, 31)
        else:
            ends_on = date(transaction_date.year, start_month + 3, 1) - timedelta(days=1)
        now = _utc_now()
        period_id = _deterministic_id("quarter", period_key)
        connection.execute(
            """
            INSERT INTO periods (
                period_id, period_key, period_type, starts_on, ends_on,
                status, source_hash, row_version, created_at, updated_at
            ) VALUES (?, ?, 'quarter', ?, ?, 'open', ?, 1, ?, ?)
            """,
            (
                period_id,
                period_key,
                starts_on.isoformat(),
                ends_on.isoformat(),
                _stable_hash({"period_key": period_key}),
                now,
                now,
            ),
        )
        period = connection.execute(
            "SELECT * FROM periods WHERE period_key = ?",
            (period_key,),
        ).fetchone()
    if period["status"] != "open":
        raise NonInvoiceExpenseError(f"Period {period_key} is immutable after close")
    if not (period["starts_on"] <= transaction_date.isoformat() <= period["ends_on"]):
        raise NonInvoiceExpenseError(f"Expense date is outside period {period_key}")
    return period


def _resolve_activity(
    connection: sqlite3.Connection,
    transaction_date: date,
    requested_id: str | None,
) -> str:
    if requested_id:
        rows = connection.execute(
            """
            SELECT business_activity_id FROM business_activities
            WHERE business_activity_id = ? AND starts_on <= ?
              AND (ends_on IS NULL OR ends_on >= ?)
            """,
            (requested_id, transaction_date.isoformat(), transaction_date.isoformat()),
        ).fetchall()
        if len(rows) != 1:
            raise NonInvoiceExpenseError(
                "Requested business activity is not active on the expense date"
            )
        return str(rows[0]["business_activity_id"])
    rows = connection.execute(
        """
        SELECT business_activity_id FROM business_activities
        WHERE starts_on <= ? AND (ends_on IS NULL OR ends_on >= ?)
        ORDER BY activity_key
        """,
        (transaction_date.isoformat(), transaction_date.isoformat()),
    ).fetchall()
    if len(rows) != 1:
        raise NonInvoiceExpenseError(
            "Expense date must resolve to exactly one reviewed business activity"
        )
    return str(rows[0]["business_activity_id"])


def _resolve_counterparty(
    connection: sqlite3.Connection,
    request: NonInvoiceExpenseInput,
    *,
    existing_transaction: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    if existing_transaction is not None:
        existing_id = existing_transaction["counterparty_id"]
        if not existing_id:
            raise NonInvoiceExpenseError("Existing expense is missing a reviewed counterparty")
        if request.counterparty_id and request.counterparty_id != existing_id:
            raise NonInvoiceExpenseError("Evidence is already linked to another counterparty")
        counterparty = connection.execute(
            "SELECT * FROM counterparties WHERE counterparty_id = ?",
            (existing_id,),
        ).fetchone()
        _validate_counterparty(counterparty, request)
        _require_book_identity(connection, counterparty, request)
        return counterparty

    counterparty = None
    if request.counterparty_id:
        counterparty = connection.execute(
            "SELECT * FROM counterparties WHERE counterparty_id = ?",
            (request.counterparty_id,),
        ).fetchone()
        if counterparty is None:
            raise NonInvoiceExpenseError(f"Unknown counterparty: {request.counterparty_id}")
    elif request.counterparty_tax_id:
        candidates = connection.execute(
            "SELECT * FROM counterparties WHERE upper(tax_id) = ? OR upper(vat_id) = ?",
            (request.counterparty_tax_id, request.counterparty_tax_id),
        ).fetchall()
        if len(candidates) > 1:
            raise NonInvoiceExpenseError("Counterparty tax identity is ambiguous")
        counterparty = candidates[0] if candidates else None
    if counterparty is None and request.counterparty_name:
        candidates = connection.execute(
            """
            SELECT * FROM counterparties
            WHERE lower(display_name) = lower(?)
              AND (? IS NULL OR country_code = ?)
            ORDER BY counterparty_id
            """,
            (
                request.counterparty_name,
                request.counterparty_country,
                request.counterparty_country,
            ),
        ).fetchall()
        if len(candidates) > 1:
            raise NonInvoiceExpenseError("Counterparty name is ambiguous; pass --counterparty-id")
        counterparty = candidates[0] if candidates else None

    if counterparty is not None:
        _validate_counterparty(counterparty, request)
        _require_book_identity(connection, counterparty, request)
        return counterparty

    if not request.counterparty_name or not request.counterparty_country:
        raise NonInvoiceExpenseError(
            "A new counterparty requires name and ISO country, or pass an existing counterparty ID"
        )
    if request.counterparty_country == "ES" and not request.counterparty_tax_id:
        raise NonInvoiceExpenseError(
            "A new Spanish counterparty requires --counterparty-tax-id for the AEAT expense book"
        )
    if request.counterparty_country != "ES" and not (
        request.counterparty_identity_kind and request.counterparty_identifier
    ):
        raise NonInvoiceExpenseError(
            "A new foreign counterparty requires --counterparty-identity-kind and --counterparty-identifier"
        )
    identity_seed = {
        "name": request.counterparty_name,
        "country": request.counterparty_country,
        "tax_id": request.counterparty_tax_id,
        "identity_kind": request.counterparty_identity_kind,
        "identifier": request.counterparty_identifier,
    }
    identity_hash = _stable_hash(identity_seed)
    counterparty_id = _deterministic_id("non-invoice-counterparty", identity_hash)
    now = _utc_now()
    connection.execute(
        """
        INSERT INTO counterparties (
            counterparty_id, external_key, tax_id, display_name, country_code,
            email, phone, source_hash, row_version, created_at, updated_at,
            vat_id, roi_status, professional_supplier, retention_expected
        ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, 1, ?, ?, NULL, 'unknown', NULL, NULL)
        """,
        (
            counterparty_id,
            f"non-invoice-counterparty:{identity_hash}",
            request.counterparty_tax_id,
            request.counterparty_name,
            request.counterparty_country,
            identity_hash,
            now,
            now,
        ),
    )
    if request.counterparty_country != "ES":
        _insert_primary_identity(connection, counterparty_id, request, now)
    return connection.execute(
        "SELECT * FROM counterparties WHERE counterparty_id = ?",
        (counterparty_id,),
    ).fetchone()


def _validate_counterparty(
    counterparty: Mapping[str, Any],
    request: NonInvoiceExpenseInput,
) -> None:
    if request.counterparty_name and str(counterparty["display_name"]).casefold() != request.counterparty_name.casefold():
        raise NonInvoiceExpenseError("Counterparty name conflicts with the existing ledger identity")
    if request.counterparty_country and counterparty["country_code"] != request.counterparty_country:
        raise NonInvoiceExpenseError("Counterparty country conflicts with the existing ledger identity")
    if request.counterparty_tax_id:
        existing_ids = {str(counterparty["tax_id"] or "").upper(), str(counterparty["vat_id"] or "").upper()}
        if request.counterparty_tax_id not in existing_ids:
            raise NonInvoiceExpenseError("Counterparty tax ID conflicts with the existing ledger identity")


def _require_book_identity(
    connection: sqlite3.Connection,
    counterparty: Mapping[str, Any],
    request: NonInvoiceExpenseInput,
) -> None:
    if counterparty["country_code"] == "ES":
        if not (counterparty["tax_id"] or counterparty["vat_id"]):
            raise NonInvoiceExpenseError(
                "Spanish counterparty lacks the reviewed tax identity required by the AEAT expense book"
            )
        return
    identity = connection.execute(
        """
        SELECT * FROM counterparty_identities
        WHERE counterparty_id = ? AND is_primary = 1
        """,
        (counterparty["counterparty_id"],),
    ).fetchone()
    if identity is None:
        if request.counterparty_identity_kind and request.counterparty_identifier:
            _insert_primary_identity(
                connection,
                str(counterparty["counterparty_id"]),
                request,
                _utc_now(),
            )
            return
        raise NonInvoiceExpenseError(
            "Foreign counterparty lacks a primary source-backed AEAT identity"
        )
    if request.counterparty_identity_kind and identity["identity_kind"] != request.counterparty_identity_kind:
        raise NonInvoiceExpenseError("Counterparty identity kind conflicts with the existing ledger identity")
    if request.counterparty_identifier and identity["identifier"] != request.counterparty_identifier:
        raise NonInvoiceExpenseError("Counterparty identifier conflicts with the existing ledger identity")


def _insert_primary_identity(
    connection: sqlite3.Connection,
    counterparty_id: str,
    request: NonInvoiceExpenseInput,
    now: str,
) -> None:
    if not request.counterparty_identity_kind or not request.counterparty_identifier:
        raise NonInvoiceExpenseError("Foreign counterparty identity data is incomplete")
    if len(request.counterparty_identifier) > 20:
        raise NonInvoiceExpenseError("Counterparty identifier cannot exceed 20 characters")
    identity_hash = _stable_hash(
        {
            "counterparty_id": counterparty_id,
            "kind": request.counterparty_identity_kind,
            "country": request.counterparty_country,
            "identifier": request.counterparty_identifier,
            "evidence_sha256": request.evidence_sha256,
        }
    )
    connection.execute(
        """
        INSERT INTO counterparty_identities (
            counterparty_identity_id, counterparty_id, identity_kind,
            aeat_id_type, country_code, identifier, is_primary,
            source_reference, source_hash, row_version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 1, ?, ?)
        """,
        (
            _deterministic_id("non-invoice-counterparty-identity", identity_hash),
            counterparty_id,
            request.counterparty_identity_kind,
            IDENTITY_AEAT_TYPES[request.counterparty_identity_kind],
            request.counterparty_country,
            request.counterparty_identifier,
            str(request.archived_path),
            identity_hash,
            now,
            now,
        ),
    )


def _validate_existing(
    connection: sqlite3.Connection,
    *,
    request: NonInvoiceExpenseInput,
    preset: NonInvoiceExpensePreset,
    period: Mapping[str, Any],
    activity_id: str,
    counterparty_id: str,
    transaction_hash: str,
    treatment_hash: str,
    existing_document: Mapping[str, Any],
    existing_transaction: Mapping[str, Any],
    verify_archive: bool,
) -> dict[str, Any]:
    document_expected = {
        "counterparty_id": counterparty_id,
        "document_type": preset.document_type,
        "document_number": request.reference,
        "issued_on": request.transaction_date.isoformat(),
        "period_id": period["period_id"],
        "currency": "EUR",
        "total_minor": _minor(request.gross_eur),
    }
    transaction_expected = {
        "period_id": period["period_id"],
        "transaction_date": request.transaction_date.isoformat(),
        "booking_date": request.transaction_date.isoformat(),
        "entry_type": "expense",
        "description": request.description,
        "amount_minor": _minor(request.gross_eur),
        "currency": "EUR",
        "direction": "debit",
        "counterparty_id": counterparty_id,
        "business_activity_id": activity_id,
        "source_hash": transaction_hash,
    }
    mismatches = [
        f"document.{key}"
        for key, value in document_expected.items()
        if existing_document[key] != value
    ]
    mismatches.extend(
        f"transaction.{key}"
        for key, value in transaction_expected.items()
        if existing_transaction[key] != value
    )
    if existing_transaction["document_id"] != existing_document["document_id"]:
        mismatches.append("transaction.document_id")
    if existing_document["lifecycle_status"] not in {"approved", "posted", "included_in_snapshot"}:
        mismatches.append("document.lifecycle_status")
    if existing_transaction["lifecycle_status"] not in {"approved", "posted", "included_in_snapshot"}:
        mismatches.append("transaction.lifecycle_status")
    treatment = connection.execute(
        """
        SELECT * FROM tax_treatments
        WHERE transaction_id = ? AND treatment_type = 'non_invoice_review' AND jurisdiction = 'ES'
        """,
        (existing_transaction["transaction_id"],),
    ).fetchone()
    if treatment is None or treatment["source_hash"] != treatment_hash:
        mismatches.append("tax_treatment")
    if mismatches:
        raise NonInvoiceExpenseError(
            "Evidence is already registered with conflicting values: " + ", ".join(mismatches)
        )
    stored_path = Path(existing_document["source_path"] or request.archived_path)
    _verify_archive(stored_path, request.evidence_sha256, verify_archive)
    return _result(
        request=request,
        preset=preset,
        document_id=str(existing_document["document_id"]),
        transaction_id=str(existing_transaction["transaction_id"]),
        transaction_row_version=int(existing_transaction["row_version"]),
        counterparty_id=counterparty_id,
        business_activity_id=activity_id,
        status="already_recorded",
        transaction_lifecycle_status=str(existing_transaction["lifecycle_status"]),
    )


def _result(
    *,
    request: NonInvoiceExpenseInput,
    preset: NonInvoiceExpensePreset,
    document_id: str,
    transaction_id: str,
    transaction_row_version: int,
    counterparty_id: str,
    business_activity_id: str,
    status: str,
    transaction_lifecycle_status: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "kind": request.kind,
        "period": request.period_key or quarter_key(request.transaction_date),
        "evidence_sha256": request.evidence_sha256,
        "document_id": document_id,
        "transaction_id": transaction_id,
        "transaction_row_version": transaction_row_version,
        "transaction_lifecycle_status": transaction_lifecycle_status,
        "counterparty_id": counterparty_id,
        "business_activity_id": business_activity_id,
        "gross_eur": f"{request.gross_eur:.2f}",
        "deductible_irpf_eur": f"{request.deductible_eur:.2f}",
        "aeat_invoice_type": "SF",
        "aeat_expense_concept": preset.aeat_expense_concept,
        "include_modelo130": request.deductible_eur > 0,
        "include_modelo303": False,
        "include_modelo347": False,
        "review_id": f"transaction:{transaction_id}",
    }


def _treatment_note(
    request: NonInvoiceExpenseInput,
    preset: NonInvoiceExpensePreset,
) -> str:
    lines = [
        f"Business purpose: {request.business_purpose}",
        f"Explicit IRPF deductible amount: {request.deductible_eur:.2f} EUR",
        (
            "Classification: AEAT 2026 unified books reference, "
            f"invoice type SF, expense concept {preset.aeat_expense_concept}"
        ),
    ]
    if request.note:
        lines.append(f"Operator note: {request.note}")
    return "\n".join(lines)


def _verify_archive(path: Path, expected_hash: str, required: bool) -> None:
    if not required:
        return
    if not path.is_file():
        raise NonInvoiceExpenseError(f"Archived evidence does not exist: {path}")
    if _sha256_file(path) != expected_hash:
        raise NonInvoiceExpenseError("Archived evidence SHA-256 does not match the intake file")


def quarter_key(value: date) -> str:
    return f"{value.year:04d}-Q{((value.month - 1) // 3) + 1}"


def _minor(value: Decimal) -> int:
    return int(cents(value) * 100)


def _deterministic_id(kind: str, seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"autonomo-taxes:{kind}:{seed}"))


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
