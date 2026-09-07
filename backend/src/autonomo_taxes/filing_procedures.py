from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Mapping, TYPE_CHECKING

from .money import cents

if TYPE_CHECKING:
    from .ledger_db import LedgerDB


PROCEDURE_SNAPSHOT_STATUSES = {
    "submitted": "procedure_submitted",
    "resolved": "procedure_resolved",
    "rejected": "procedure_rejected",
}
SUPPORTED_PROCEDURES = {"rectification"}
PAYABLE_KEYS = {"130": "19"}
PROCEDURE_IDENTITY_KEYS = (
    "form",
    "period",
    "evidence_kind",
    "procedure",
    "stage",
    "occurred_on",
    "procedure_reference",
    "procedure_amount_eur",
    "notes",
)


def build_tax_procedure_payload(
    database: LedgerDB,
    *,
    period_key: str,
    form_code: str,
    procedure: str,
    stage: str,
    occurred_on: str,
    procedure_reference: str,
    amount_eur: Decimal | None = None,
    notes: str | None = None,
) -> tuple[dict[str, Any], str]:
    form = _normalize_form(form_code)
    normalized_procedure = procedure.strip().lower()
    normalized_stage = stage.strip().lower()
    reference = procedure_reference.strip()
    if normalized_procedure not in SUPPORTED_PROCEDURES:
        raise ValueError(f"Unsupported tax procedure: {procedure}")
    if normalized_stage not in PROCEDURE_SNAPSHOT_STATUSES:
        raise ValueError(f"Unsupported tax procedure stage: {stage}")
    if not reference:
        raise ValueError("Tax procedure reference is required")
    try:
        date.fromisoformat(occurred_on)
    except ValueError as exc:
        raise ValueError("Tax procedure date must use YYYY-MM-DD") from exc
    normalized_amount = cents(amount_eur) if amount_eur is not None else None
    if normalized_amount is not None and normalized_amount < Decimal("0.00"):
        raise ValueError("Tax procedure amount cannot be negative")

    lineage, original_payable = _original_filing_lineage(
        database,
        period_key=period_key,
        form_code=form,
    )
    payload: dict[str, Any] = {
        "schema_version": 1,
        "form": form,
        "period": period_key,
        "evidence_kind": "tax_procedure_receipt",
        "procedure": normalized_procedure,
        "stage": normalized_stage,
        "occurred_on": occurred_on,
        "procedure_reference": reference,
        "original_filing_lineage": lineage,
    }
    if original_payable is not None:
        payload["original_payable_eur"] = f"{original_payable:.2f}"
    if normalized_amount is not None:
        payload["procedure_amount_eur"] = f"{normalized_amount:.2f}"
    if notes and notes.strip():
        payload["notes"] = notes.strip()
    return payload, PROCEDURE_SNAPSHOT_STATUSES[normalized_stage]


def tax_procedure_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: payload.get(key) for key in PROCEDURE_IDENTITY_KEYS}


def _original_filing_lineage(
    database: LedgerDB,
    *,
    period_key: str,
    form_code: str,
) -> tuple[list[dict[str, Any]], Decimal | None]:
    rows = database.connection.execute(
        """
        SELECT fs.filing_snapshot_id, fs.status, fs.filed_on, fs.source_hash,
               fs.form_code, fs.payload_json
        FROM filing_snapshots fs
        JOIN periods p ON p.period_id = fs.period_id
        WHERE p.period_key = ?
          AND fs.status IN ('baseline', 'filed', 'submitted', 'final')
        ORDER BY fs.created_at, fs.filing_snapshot_id
        """,
        (period_key,),
    ).fetchall()
    lineage: list[dict[str, Any]] = []
    payable_amounts: set[Decimal] = set()
    payable_key = PAYABLE_KEYS.get(form_code)
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or ""))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        row_form = _normalize_form(str(row["form_code"] or payload.get("form") or ""))
        if row_form != form_code:
            continue
        lineage.append(
            {
                "filing_snapshot_id": row["filing_snapshot_id"],
                "status": row["status"],
                "filed_on": row["filed_on"],
                "source_hash": row["source_hash"],
            }
        )
        filed_values = payload.get("filed_values")
        if payable_key and isinstance(filed_values, Mapping) and payable_key in filed_values:
            try:
                payable_amounts.add(cents(Decimal(str(filed_values[payable_key]))))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(
                    f"Original Modelo {form_code} has a non-monetary casilla {payable_key}"
                ) from exc

    if not lineage:
        raise ValueError(
            f"No original filing snapshot found for Modelo {form_code} {period_key}"
        )
    if payable_key and len(payable_amounts) > 1:
        values = ", ".join(f"{value:.2f}" for value in sorted(payable_amounts))
        raise ValueError(
            f"Original Modelo {form_code} payable amount is ambiguous: {values}"
        )
    if payable_key and not payable_amounts:
        raise ValueError(
            f"Original Modelo {form_code} is missing filed casilla {payable_key}"
        )
    original_payable = next(iter(payable_amounts)) if payable_amounts else None
    return lineage, original_payable


def _normalize_form(value: str) -> str:
    return value.lower().replace("modelo", "").strip()
