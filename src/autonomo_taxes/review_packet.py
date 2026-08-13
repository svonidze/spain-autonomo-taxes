from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from .fx_policy import ALLOWED_PRODUCTION_SOURCES, XOLO_RECORDED_PRODUCTION_THROUGH
from .ledger_db import LedgerDB, VALID_LIFECYCLE_TRANSITIONS
from .tax_engine import MODELO303_SUPPORTED_CODES, WITHHOLDING_TYPE_BY_TAX_CODE


PACKET_VERSION = 1
PACKET_PRIVACY = "private_ephemeral_do_not_commit"
REVIEW_OUTCOMES = {"approve", "reject"}
ASSET_DECISIONS = {"current_expense", "asset", "not_applicable"}
ROI_STATUSES = {"unknown", "registered", "not_registered"}
LEGAL_FORMS = {"unknown", "individual", "legal_entity", "public_body"}
INCOME_REVIEW_TAX_CODES = {
    "domestic_output",
    "domestic_output_zero",
    "eu_goods_income",
    "eu_service_income",
    "export",
    "outside_scope",
}
EXPENSE_REVIEW_TAX_CODES = (
    set(MODELO303_SUPPORTED_CODES) - INCOME_REVIEW_TAX_CODES
) | set(WITHHOLDING_TYPE_BY_TAX_CODE)
REVIEW_TAX_CODES = INCOME_REVIEW_TAX_CODES | EXPENSE_REVIEW_TAX_CODES
AEAT_INVOICE_TYPES = {
    "F1", "F2", "F3", "F4", "F5", "F6", "R1", "R2", "R3", "R4", "R5",
    "SF", "DV", "AJ", "LC",
}
AEAT_OPERATION_QUALIFICATIONS = {"S1", "S2", "N1", "N2"}
AEAT_EXEMPTION_CODES = {"E1", "E2", "E3", "E4", "E5", "E6"}
COUNTERPARTY_CHANGE_FIELDS = {
    "country_code",
    "tax_id",
    "vat_id",
    "roi_status",
    "legal_form",
    "professional_supplier",
    "retention_expected",
}
TREATMENT_DECISION_FIELDS = (
    "tax_code",
    "aeat_invoice_type",
    "aeat_operation_key",
    "aeat_operation_qualification",
    "aeat_exemption_code",
    "aeat_reverse_charge",
    "aeat_expense_concept",
    "rate_basis_points",
    "deductible_ratio",
    "taxable_base_minor",
    "vat_minor",
    "deductible_irpf_minor",
    "deductible_vat_minor",
    "withholding_minor",
    "include_modelo130",
    "include_modelo303",
    "include_modelo347",
    "rule_version_id",
    "notes",
)
DECISION_FIELDS = {
    "outcome",
    "reason",
    "business_purpose",
    "document_valid",
    "asset_decision",
    "asset_id",
    "counterparty_changes",
    "tax_treatment",
    "issue_resolutions",
}
PACKET_FIELDS = {
    "packet_version",
    "privacy",
    "review_id",
    "snapshot_hash",
    "state",
    "required_decisions",
    "allowed_values",
    "decision",
}
SUPPORTED_WORK_ITEM_STEP_CODES = {
    "attach_official_or_settlement_fx",
    "confirm_business_purpose",
    "confirm_counterparty_tax_profile",
    "confirm_income_recognition",
    "confirm_irpf_deductible_amount",
    "confirm_iva_treatment",
    "decide_expense_or_amortizable_asset",
}
ISSUE_REQUIREMENT_STEP_CODES = {
    "counterparty_tax_profile_review": {"confirm_counterparty_tax_profile"},
    "transaction_tax_review": set(),
}


class ReviewPacketError(ValueError):
    """Raised when a review packet is stale, incomplete, or unsafe to apply."""


def prepare_review_packet(db: LedgerDB, review_id: str) -> dict[str, Any]:
    """Build a deterministic, path-free review projection from one read snapshot."""
    connection = db.connection
    connection.execute("BEGIN")
    try:
        packet = _build_packet(db, review_id)
        _verify_archived_document(db, packet["state"])
    finally:
        connection.rollback()
    return packet


def prepare_review_work_item(db: LedgerDB, review_id: str) -> dict[str, Any]:
    packet = prepare_review_packet(db, review_id)
    requirement_step_codes, unsupported_reasons = _derive_requirement_step_codes(packet)
    return {
        "review_id": packet["review_id"],
        "packet": packet,
        "requirement_step_codes": requirement_step_codes,
        "requirements": [
            {"code": code, "supported": code in SUPPORTED_WORK_ITEM_STEP_CODES}
            for code in requirement_step_codes
        ],
        "supported": not unsupported_reasons,
        "unsupported_reasons": unsupported_reasons,
    }


def apply_review_packet(
    db: LedgerDB,
    packet: Mapping[str, Any],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate and apply one decision as an all-or-nothing SQLite transaction."""
    _validate_packet_shape(packet)
    connection = db.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        live_packet = _build_packet(db, str(packet["review_id"]))
        _validate_snapshot(packet, live_packet)
        _verify_archived_document(db, live_packet["state"])
        decision = _validate_decision(packet["decision"], live_packet["state"])
        result = _apply_decision(db, live_packet["state"], decision)
        if dry_run:
            connection.rollback()
        else:
            connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    return {
        "review_id": packet["review_id"],
        "dry_run": dry_run,
        "outcome": decision["outcome"],
        "posted": False,
        **result,
    }


def packet_json(packet: Mapping[str, Any]) -> str:
    return json.dumps(packet, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def classify_review_packet_failure(message: str) -> tuple[str, int]:
    if message.startswith("Unknown transaction") or message.startswith("Unknown document"):
        return "unknown_review", 404
    if message.startswith("Document review requires exactly one linked transaction; found 0"):
        return "unknown_review", 404
    if message.startswith("Review packet is stale") or message.startswith(
        "Review packet state was edited"
    ):
        return "stale_snapshot", 409
    if message.startswith("Terminal ") or message.startswith("Invalid ") or (
        message.startswith("Period ") and message.endswith(" is not open")
    ):
        return "review_conflict", 409
    if message.startswith("Guided review requires exactly one invoice_review tax treatment"):
        return "unsupported_work_item", 409
    return "review_packet_invalid", 400


def _build_packet(db: LedgerDB, review_id: str) -> dict[str, Any]:
    review_kind, subject_id = _parse_review_id(review_id)
    transaction_id = _resolve_transaction_id(db, review_kind, subject_id)
    transaction = _one(
        db,
        """
        SELECT t.*, p.period_key
        FROM transactions t
        JOIN periods p ON p.period_id = t.period_id
        WHERE t.transaction_id = ?
        """,
        (transaction_id,),
        label="transaction",
    )
    period = _one(
        db,
        "SELECT * FROM periods WHERE period_id = ?",
        (transaction["period_id"],),
        label="period",
    )
    document = (
        _one(
            db,
            "SELECT * FROM documents WHERE document_id = ?",
            (transaction["document_id"],),
            label="document",
        )
        if transaction["document_id"]
        else None
    )
    counterparty = (
        _one(
            db,
            "SELECT * FROM counterparties WHERE counterparty_id = ?",
            (transaction["counterparty_id"],),
            label="counterparty",
        )
        if transaction["counterparty_id"]
        else None
    )
    treatments = _all(
        db,
        """
        SELECT * FROM tax_treatments
        WHERE transaction_id = ? AND treatment_type = 'invoice_review'
        ORDER BY jurisdiction, treatment_id
        """,
        (transaction_id,),
    )
    if len(treatments) != 1:
        raise ReviewPacketError(
            "Guided review requires exactly one invoice_review tax treatment; "
            f"found {len(treatments)}"
        )
    treatment = treatments[0]
    identities = (
        _all(
            db,
            """
            SELECT * FROM counterparty_identities
            WHERE counterparty_id = ?
            ORDER BY is_primary DESC, identity_kind, counterparty_identity_id
            """,
            (counterparty["counterparty_id"],),
        )
        if counterparty
        else []
    )
    issues = _linked_open_issues(
        db,
        transaction_id=transaction_id,
        document_id=transaction["document_id"],
        counterparty_id=transaction["counterparty_id"],
    )
    assets = _all(
        db,
        """
        SELECT * FROM assets
        WHERE acquisition_transaction_id = ? OR document_id = ?
        ORDER BY asset_id
        """,
        (transaction_id, transaction["document_id"]),
    )
    fx = (
        _one(
            db,
            "SELECT * FROM fx_rates WHERE fx_rate_id = ?",
            (transaction["fx_rate_id"],),
            label="FX rate",
        )
        if transaction["fx_rate_id"]
        else None
    )
    activity = (
        _one(
            db,
            "SELECT * FROM business_activities WHERE business_activity_id = ?",
            (transaction["business_activity_id"],),
            label="business activity",
        )
        if transaction["business_activity_id"]
        else None
    )
    intake_receipt = (
        db.connection.execute(
            """
            SELECT intake_receipt_id, intake_tab, input_json, created_at
            FROM intake_receipts
            WHERE document_id = ?
            """,
            (transaction["document_id"],),
        ).fetchone()
        if transaction["document_id"]
        else None
    )
    operator_intake = (
        {
            "system_id": intake_receipt["intake_receipt_id"],
            "tab": intake_receipt["intake_tab"],
            "values": json.loads(intake_receipt["input_json"]),
            "accepted_at": intake_receipt["created_at"],
        }
        if intake_receipt is not None
        else None
    )

    state = {
        "period": _select(period, "period_id", "period_key", "status", "row_version", "source_hash"),
        "document": _document_state(document),
        "transaction": _select(
            transaction,
            "transaction_id",
            "row_version",
            "source_hash",
            "period_id",
            "transaction_date",
            "booking_date",
            "entry_type",
            "description",
            "amount_minor",
            "currency",
            "amount_original_minor",
            "original_currency",
            "amount_eur_minor",
            "fx_rate_id",
            "direction",
            "lifecycle_status",
            "document_id",
            "counterparty_id",
            "business_activity_id",
        ),
        "counterparty": _counterparty_state(counterparty),
        "counterparty_identities": [
            _select(
                row,
                "counterparty_identity_id",
                "row_version",
                "source_hash",
                "identity_kind",
                "aeat_identification_type",
                "country_code",
                "identifier",
                "is_primary",
            )
            for row in identities
        ],
        "tax_treatment": _select(
            treatment,
            "treatment_id",
            "row_version",
            "source_hash",
            "transaction_id",
            "treatment_type",
            "jurisdiction",
            *TREATMENT_DECISION_FIELDS,
        ),
        "issues": [
            _select(
                row,
                "validation_issue_id",
                "row_version",
                "source_hash",
                "subject_table",
                "subject_id",
                "issue_code",
                "severity",
                "message",
                "blocking",
                "issue_status",
            )
            for row in issues
        ],
        "assets": [
            _select(
                row,
                "asset_id",
                "row_version",
                "source_hash",
                "document_id",
                "acquisition_transaction_id",
                "asset_code",
                "placed_in_service_on",
                "cost_minor",
                "currency",
                "depreciation_method",
                "useful_life_months",
                "amortizable_base_minor",
                "iva_treatment",
                "business_use_ratio",
                "annual_rate_basis_points",
            )
            for row in assets
        ],
        "fx": (
            _select(
                fx,
                "fx_rate_id",
                "row_version",
                "source_hash",
                "rate_date",
                "base_currency",
                "quote_currency",
                "rate",
                "rate_source",
                "source_reference",
                "rule_version_id",
            )
            if fx
            else None
        ),
        "business_activity": (
            _select(
                activity,
                "business_activity_id",
                "row_version",
                "source_hash",
                "activity_code",
                "valid_from",
                "valid_to",
                "review_status",
            )
            if activity
            else None
        ),
        "operator_intake": operator_intake,
    }
    state = _jsonable(state)
    snapshot_hash = _stable_hash(state)
    entry_type = transaction["entry_type"]
    if entry_type not in {"expense", "income"}:
        raise ReviewPacketError(
            f"Guided invoice review does not support entry_type={entry_type}"
        )
    decision_treatment = {
        key: _decision_treatment_value(key, treatment[key])
        for key in TREATMENT_DECISION_FIELDS
    }
    operator_values = operator_intake["values"] if operator_intake else {}
    suggested_business_purpose = (
        operator_values.get("business_purpose")
        if entry_type == "expense"
        else operator_values.get("service_description")
    )
    return {
        "packet_version": PACKET_VERSION,
        "privacy": PACKET_PRIVACY,
        "review_id": review_id,
        "snapshot_hash": snapshot_hash,
        "state": state,
        "required_decisions": [
            "outcome",
            "reason",
            "business_purpose",
            "document_valid",
            *(["asset_decision"] if entry_type == "expense" else []),
            "tax_treatment",
            "issue_resolutions",
        ],
        "allowed_values": {
            "outcome": sorted(REVIEW_OUTCOMES),
            "asset_decision": sorted(ASSET_DECISIONS),
            "roi_status": sorted(ROI_STATUSES),
            "legal_form": sorted(LEGAL_FORMS),
            "tax_code": sorted(REVIEW_TAX_CODES),
            "aeat_invoice_type": sorted(AEAT_INVOICE_TYPES),
            "aeat_operation_qualification": sorted(AEAT_OPERATION_QUALIFICATIONS),
            "aeat_exemption_code": sorted(AEAT_EXEMPTION_CODES),
            "production_fx_source": sorted(ALLOWED_PRODUCTION_SOURCES),
        },
        "decision": {
            "outcome": None,
            "reason": None,
            "business_purpose": suggested_business_purpose,
            "document_valid": None,
            "asset_decision": "not_applicable" if entry_type == "income" else None,
            "asset_id": None,
            "counterparty_changes": {},
            "tax_treatment": decision_treatment,
            "issue_resolutions": [
                {
                    "issue_id": issue["validation_issue_id"],
                    "action": None,
                    "reason": None,
                }
                for issue in issues
            ],
        },
    }


def _validate_packet_shape(packet: Mapping[str, Any]) -> None:
    if not isinstance(packet, Mapping):
        raise ReviewPacketError("Review packet must be a JSON object")
    _exact_keys(packet, PACKET_FIELDS, "packet")
    if packet["packet_version"] != PACKET_VERSION:
        raise ReviewPacketError(f"Unsupported review packet version: {packet['packet_version']}")
    if packet["privacy"] != PACKET_PRIVACY:
        raise ReviewPacketError("Review packet privacy marker is missing or changed")
    _parse_review_id(str(packet["review_id"]))
    if not isinstance(packet["state"], Mapping):
        raise ReviewPacketError("Review packet state must be an object")
    if not isinstance(packet["decision"], Mapping):
        raise ReviewPacketError("Review packet decision must be an object")
    _exact_keys(packet["decision"], DECISION_FIELDS, "decision")


def _derive_requirement_step_codes(
    packet: Mapping[str, Any],
) -> tuple[list[str], list[str]]:
    state = packet["state"]
    transaction = state["transaction"]
    step_codes: list[str] = []
    if transaction["entry_type"] == "expense":
        step_codes.extend(
            (
                "confirm_business_purpose",
                "confirm_irpf_deductible_amount",
                "confirm_iva_treatment",
            )
        )
        if state["assets"]:
            step_codes.append("decide_expense_or_amortizable_asset")
    elif transaction["entry_type"] == "income":
        step_codes.extend(("confirm_income_recognition", "confirm_iva_treatment"))

    original_currency = str(
        transaction["original_currency"] or transaction["currency"] or "EUR"
    ).upper()
    if original_currency != "EUR":
        step_codes.append("attach_official_or_settlement_fx")

    unsupported_reasons: list[str] = []
    for issue in state["issues"]:
        issue_code = str(issue["issue_code"])
        if issue_code not in ISSUE_REQUIREMENT_STEP_CODES:
            unsupported_reasons.append(f"unsupported_issue:{issue_code}")
            continue
        step_codes.extend(sorted(ISSUE_REQUIREMENT_STEP_CODES[issue_code]))

    ordered = list(dict.fromkeys(step_codes))
    for step_code in ordered:
        if step_code not in SUPPORTED_WORK_ITEM_STEP_CODES:
            unsupported_reasons.append(f"unsupported_requirement:{step_code}")
    return ordered, sorted(set(unsupported_reasons))


def _validate_snapshot(packet: Mapping[str, Any], live: Mapping[str, Any]) -> None:
    if _stable_hash(packet["state"]) != packet["snapshot_hash"]:
        raise ReviewPacketError("Review packet state was edited; only decision may be changed")
    for key in (
        "packet_version",
        "privacy",
        "review_id",
        "snapshot_hash",
        "state",
        "required_decisions",
        "allowed_values",
    ):
        if packet[key] != live[key]:
            raise ReviewPacketError(
                "Review packet is stale or its immutable projection was changed: " + key
            )


def _validate_decision(decision_value: Any, state: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(decision_value, Mapping):
        raise ReviewPacketError("decision must be an object")
    _exact_keys(decision_value, DECISION_FIELDS, "decision")
    decision = dict(decision_value)
    outcome = decision.get("outcome")
    if outcome not in REVIEW_OUTCOMES:
        raise ReviewPacketError("decision.outcome must be approve or reject")
    reason = _required_text(decision.get("reason"), "decision.reason")
    transaction = state["transaction"]
    document = state["document"]
    period = state["period"]
    if period["status"] != "open":
        raise ReviewPacketError(f"Period {period['period_key']} is not open")
    if document is None:
        raise ReviewPacketError("Guided invoice review requires a linked document")

    resolutions = _validate_issue_resolutions(decision.get("issue_resolutions"), state["issues"])
    blocking_issue_ids = {
        issue["validation_issue_id"] for issue in state["issues"] if bool(issue["blocking"])
    }
    resolved_ids = {row["issue_id"] for row in resolutions if row["action"] == "resolve"}
    missing = sorted(blocking_issue_ids - resolved_ids)
    if missing:
        raise ReviewPacketError(
            "Every blocking issue needs an explicit resolve action and reason: " + ", ".join(missing)
        )

    if outcome == "reject":
        _validate_transition(document["lifecycle_status"], "rejected", "document")
        _validate_transition(transaction["lifecycle_status"], "rejected", "transaction")
        if not isinstance(decision.get("document_valid"), bool):
            raise ReviewPacketError(
                "Rejected review requires an explicit document_valid=true or false decision"
            )
        if decision.get("counterparty_changes") not in ({}, None):
            raise ReviewPacketError("Rejected review cannot change counterparty facts")
        tax_decision = decision.get("tax_treatment")
        if isinstance(tax_decision, Mapping) and tax_decision.get("tax_code") not in {None, "unknown"}:
            raise ReviewPacketError("Rejected review cannot assign a deductible tax treatment")
        return {
            **decision,
            "reason": reason,
            "issue_resolutions": resolutions,
            "counterparty_changes": {},
            "tax_treatment": None,
        }

    if decision.get("document_valid") is not True:
        raise ReviewPacketError("Approved review requires document_valid=true")
    business_purpose = _required_text(
        decision.get("business_purpose"),
        "decision.business_purpose",
    )
    if date.fromisoformat(str(transaction["transaction_date"])) > date.today():
        raise ReviewPacketError(
            "A future-dated transaction cannot be approved before "
            + str(transaction["transaction_date"])
        )
    _validate_transition(document["lifecycle_status"], "approved", "document")
    _validate_transition(transaction["lifecycle_status"], "approved", "transaction")
    counterparty_changes = _validate_counterparty_changes(
        decision.get("counterparty_changes"),
        state["counterparty"],
    )
    effective_counterparty = dict(state["counterparty"] or {})
    effective_counterparty.update(counterparty_changes)
    if not effective_counterparty:
        raise ReviewPacketError("Approved invoice requires a counterparty")
    if effective_counterparty.get("country_code") in {None, "", "ZZ"}:
        raise ReviewPacketError("Counterparty country must be reviewed before approval")

    asset_decision = decision.get("asset_decision")
    if transaction["entry_type"] == "expense":
        if asset_decision not in {"current_expense", "asset"}:
            raise ReviewPacketError("Expense review requires current_expense or asset decision")
    elif asset_decision != "not_applicable":
        raise ReviewPacketError("Income review requires asset_decision=not_applicable")
    asset_id = decision.get("asset_id")
    if asset_decision == "asset":
        asset = next((row for row in state["assets"] if row["asset_id"] == asset_id), None)
        if asset is None:
            raise ReviewPacketError("Asset decision requires a linked, already reviewed asset_id")
        _validate_asset(asset)
    elif asset_id is not None:
        raise ReviewPacketError("asset_id is only valid with asset_decision=asset")

    _validate_fx(state)
    tax_treatment = _validate_tax_treatment(
        decision.get("tax_treatment"),
        transaction=transaction,
    )
    if asset_decision == "asset" and (
        tax_treatment["deductible_irpf_minor"] != 0
        or tax_treatment["include_modelo130"]
    ):
        raise ReviewPacketError(
            "Asset acquisition cannot also be deducted as a current Modelo 130 expense"
        )
    return {
        **decision,
        "reason": reason,
        "business_purpose": business_purpose,
        "counterparty_changes": counterparty_changes,
        "tax_treatment": tax_treatment,
        "issue_resolutions": resolutions,
    }


def _apply_decision(
    db: LedgerDB,
    state: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    connection = db.connection
    timestamp = _utc_now()
    transaction = state["transaction"]
    document = state["document"]
    actions: list[str] = []

    if decision["outcome"] == "approve":
        changes = decision["counterparty_changes"]
        if changes:
            current = dict(state["counterparty"])
            merged = {**current, **changes}
            db.update_counterparty_review(
                current["counterparty_id"],
                display_name=merged["display_name"],
                tax_id=merged.get("tax_id"),
                country_code=merged["country_code"],
                vat_id=merged.get("vat_id"),
                roi_status=merged.get("roi_status") or "unknown",
                legal_form=merged.get("legal_form") or "unknown",
                professional_supplier=merged.get("professional_supplier"),
                retention_expected=merged.get("retention_expected"),
                email=merged.get("email"),
                phone=merged.get("phone"),
                reviewed_from="review_packet",
                expected_row_version=current["row_version"],
            )
            actions.append("update_counterparty")

        treatment = state["tax_treatment"]
        proposed = dict(decision["tax_treatment"])
        proposed["notes"] = _review_audit_note(
            proposed["notes"],
            decision,
        )
        treatment_hash = _treatment_hash(transaction, treatment, proposed)
        cursor = connection.execute(
            """
            UPDATE tax_treatments
            SET tax_code = ?, aeat_invoice_type = ?, aeat_operation_key = ?,
                aeat_operation_qualification = ?, aeat_exemption_code = ?,
                aeat_reverse_charge = ?, aeat_expense_concept = ?,
                rate_basis_points = ?, deductible_ratio = ?, taxable_base_minor = ?,
                vat_minor = ?, deductible_irpf_minor = ?, deductible_vat_minor = ?,
                withholding_minor = ?, include_modelo130 = ?, include_modelo303 = ?,
                include_modelo347 = ?, rule_version_id = ?, notes = ?, source_hash = ?,
                row_version = row_version + 1, updated_at = ?
            WHERE treatment_id = ? AND row_version = ?
            """,
            (
                proposed["tax_code"],
                proposed["aeat_invoice_type"],
                proposed["aeat_operation_key"],
                proposed["aeat_operation_qualification"],
                proposed["aeat_exemption_code"],
                _bool_db(proposed["aeat_reverse_charge"]),
                proposed["aeat_expense_concept"],
                proposed["rate_basis_points"],
                proposed["deductible_ratio"],
                proposed["taxable_base_minor"],
                proposed["vat_minor"],
                proposed["deductible_irpf_minor"],
                proposed["deductible_vat_minor"],
                proposed["withholding_minor"],
                int(proposed["include_modelo130"]),
                int(proposed["include_modelo303"]),
                int(proposed["include_modelo347"]),
                proposed["rule_version_id"],
                proposed["notes"],
                treatment_hash,
                timestamp,
                treatment["treatment_id"],
                treatment["row_version"],
            ),
        )
        _expect_one(cursor, "tax treatment")
        actions.append("update_tax_treatment")
    else:
        treatment = state["tax_treatment"]
        rejected = {
            key: treatment[key]
            for key in TREATMENT_DECISION_FIELDS
        }
        rejected["notes"] = _review_audit_note(
            rejected.get("notes"),
            decision,
        )
        cursor = connection.execute(
            """
            UPDATE tax_treatments
            SET notes = ?, source_hash = ?, row_version = row_version + 1, updated_at = ?
            WHERE treatment_id = ? AND row_version = ?
            """,
            (
                rejected["notes"],
                _treatment_hash(transaction, treatment, rejected),
                timestamp,
                treatment["treatment_id"],
                treatment["row_version"],
            ),
        )
        _expect_one(cursor, "rejected tax treatment audit note")
        actions.append("record_rejection_reason")

    for resolution in decision["issue_resolutions"]:
        if resolution["action"] != "resolve":
            continue
        issue = next(
            row for row in state["issues"]
            if row["validation_issue_id"] == resolution["issue_id"]
        )
        cursor = connection.execute(
            """
            UPDATE validation_issues
            SET issue_status = 'resolved', resolution_reason = ?, resolved_at = ?,
                row_version = row_version + 1, updated_at = ?
            WHERE validation_issue_id = ? AND row_version = ? AND issue_status = 'open'
            """,
            (
                resolution["reason"],
                timestamp,
                timestamp,
                issue["validation_issue_id"],
                issue["row_version"],
            ),
        )
        _expect_one(cursor, f"issue {issue['validation_issue_id']}")
        actions.append(f"resolve_issue:{issue['validation_issue_id']}")

    target_status = "approved" if decision["outcome"] == "approve" else "rejected"
    if document["lifecycle_status"] != target_status:
        cursor = connection.execute(
            """
            UPDATE documents
            SET lifecycle_status = ?, row_version = row_version + 1, updated_at = ?
            WHERE document_id = ? AND row_version = ?
            """,
            (
                target_status,
                timestamp,
                document["document_id"],
                document["row_version"],
            ),
        )
        _expect_one(cursor, "document")
        actions.append(f"{target_status}_document")
    if transaction["lifecycle_status"] != target_status:
        cursor = connection.execute(
            """
            UPDATE transactions
            SET lifecycle_status = ?, row_version = row_version + 1, updated_at = ?
            WHERE transaction_id = ? AND row_version = ?
            """,
            (
                target_status,
                timestamp,
                transaction["transaction_id"],
                transaction["row_version"],
            ),
        )
        _expect_one(cursor, "transaction")
        actions.append(f"{target_status}_transaction")

    document_result = _one(
        db,
        "SELECT document_id, lifecycle_status, row_version FROM documents WHERE document_id = ?",
        (document["document_id"],),
        label="document",
    )
    transaction_result = _one(
        db,
        "SELECT transaction_id, lifecycle_status, row_version FROM transactions WHERE transaction_id = ?",
        (transaction["transaction_id"],),
        label="transaction",
    )
    return {
        "actions": actions,
        "document": document_result,
        "transaction": transaction_result,
    }


def _validate_tax_treatment(value: Any, *, transaction: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewPacketError("Approved review requires decision.tax_treatment")
    _exact_keys(value, set(TREATMENT_DECISION_FIELDS), "decision.tax_treatment")
    result = dict(value)
    if result["tax_code"] not in REVIEW_TAX_CODES:
        raise ReviewPacketError(f"Unsupported production tax_code: {result['tax_code']}")
    entry_type = transaction["entry_type"]
    allowed_codes = (
        INCOME_REVIEW_TAX_CODES
        if entry_type == "income"
        else EXPENSE_REVIEW_TAX_CODES
    )
    if result["tax_code"] not in allowed_codes:
        raise ReviewPacketError(
            f"Tax code {result['tax_code']} is not valid for {entry_type} invoice review"
        )
    invoice_type = _optional_upper(result["aeat_invoice_type"])
    if invoice_type not in AEAT_INVOICE_TYPES:
        raise ReviewPacketError("A reviewed AEAT invoice type is required")
    result["aeat_invoice_type"] = invoice_type
    operation_key = str(result["aeat_operation_key"] or "")
    if len(operation_key) != 2 or not operation_key.isdigit():
        raise ReviewPacketError("AEAT operation key must contain two digits")
    result["aeat_operation_key"] = operation_key
    qualification = _optional_upper(result["aeat_operation_qualification"])
    if qualification is not None and qualification not in AEAT_OPERATION_QUALIFICATIONS:
        raise ReviewPacketError("Unsupported AEAT operation qualification")
    result["aeat_operation_qualification"] = qualification
    exemption = _optional_upper(result["aeat_exemption_code"])
    if exemption is not None and exemption not in AEAT_EXEMPTION_CODES:
        raise ReviewPacketError("Unsupported AEAT exemption code")
    result["aeat_exemption_code"] = exemption
    if result["aeat_reverse_charge"] is not None and not isinstance(
        result["aeat_reverse_charge"], bool
    ):
        raise ReviewPacketError("aeat_reverse_charge must be true, false, or null")
    expense_concept = _optional_upper(result["aeat_expense_concept"])
    if transaction["entry_type"] == "expense":
        if expense_concept is None or not (
            expense_concept.startswith("G")
            and 2 <= len(expense_concept) <= 4
            and expense_concept.isalnum()
        ):
            raise ReviewPacketError("Expense review requires an AEAT G-code expense concept")
    result["aeat_expense_concept"] = expense_concept
    if transaction["entry_type"] == "income" and result["tax_code"] == "outside_scope":
        if qualification != "N2":
            raise ReviewPacketError("Outside-scope service income requires qualification N2")

    for field in (
        "rate_basis_points",
        "taxable_base_minor",
        "vat_minor",
        "deductible_irpf_minor",
        "deductible_vat_minor",
        "withholding_minor",
    ):
        result[field] = _optional_nonnegative_int(result[field], field)
    for field in (
        "taxable_base_minor",
        "vat_minor",
        "deductible_irpf_minor",
        "deductible_vat_minor",
        "withholding_minor",
    ):
        if result[field] is None:
            raise ReviewPacketError(f"Explicit {field}=0 or a positive value is required")
    ratio = result["deductible_ratio"]
    if ratio is not None:
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
            raise ReviewPacketError("deductible_ratio must be a number or null")
        if not 0 <= float(ratio) <= 1:
            raise ReviewPacketError("deductible_ratio must be between 0 and 1")
        result["deductible_ratio"] = float(ratio)
    if result["deductible_vat_minor"] > result["vat_minor"]:
        raise ReviewPacketError("Deductible VAT cannot exceed invoice VAT")
    if result["withholding_minor"] > result["taxable_base_minor"]:
        raise ReviewPacketError("Withholding cannot exceed taxable base")
    amount_eur = transaction["amount_eur_minor"]
    if amount_eur is not None and result["deductible_irpf_minor"] > amount_eur:
        raise ReviewPacketError("IRPF deductible amount cannot exceed document gross EUR amount")
    if entry_type == "income" and (
        result["deductible_irpf_minor"] != 0
        or result["deductible_vat_minor"] != 0
    ):
        raise ReviewPacketError("Income invoice review requires zero deductible expense amounts")
    for field in ("include_modelo130", "include_modelo303", "include_modelo347"):
        if not isinstance(result[field], bool):
            raise ReviewPacketError(f"{field} must be true or false")
    result["notes"] = _required_text(result["notes"], "tax_treatment.notes")
    if result["rule_version_id"] is not None and not isinstance(result["rule_version_id"], str):
        raise ReviewPacketError("rule_version_id must be a string or null")
    return result


def _validate_counterparty_changes(value: Any, current: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ReviewPacketError("counterparty_changes must be an object")
    unknown = set(value) - COUNTERPARTY_CHANGE_FIELDS
    if unknown:
        raise ReviewPacketError("Unsupported counterparty changes: " + ", ".join(sorted(unknown)))
    if current is None and value:
        raise ReviewPacketError("Cannot patch a missing counterparty")
    result = dict(value)
    if "country_code" in result:
        country = str(result["country_code"] or "").upper()
        if len(country) != 2 or not country.isalpha() or country == "ZZ":
            raise ReviewPacketError("counterparty country_code must be a reviewed ISO alpha-2 code")
        result["country_code"] = country
    if "roi_status" in result and result["roi_status"] not in ROI_STATUSES:
        raise ReviewPacketError("Unsupported counterparty ROI status")
    if "legal_form" in result and result["legal_form"] not in LEGAL_FORMS:
        raise ReviewPacketError("Unsupported counterparty legal form")
    for field in ("professional_supplier", "retention_expected"):
        if field in result and result[field] is not None and not isinstance(result[field], bool):
            raise ReviewPacketError(f"counterparty {field} must be true, false, or null")
    for field in ("tax_id", "vat_id"):
        if field in result and result[field] is not None and not isinstance(result[field], str):
            raise ReviewPacketError(f"counterparty {field} must be a string or null")
    return result


def _validate_issue_resolutions(value: Any, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ReviewPacketError("issue_resolutions must be a list")
    expected_ids = {row["validation_issue_id"] for row in issues}
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ReviewPacketError("Each issue resolution must be an object")
        _exact_keys(item, {"issue_id", "action", "reason"}, "issue resolution")
        issue_id = str(item["issue_id"])
        if issue_id not in expected_ids or issue_id in seen:
            raise ReviewPacketError(f"Unknown or duplicate issue resolution: {issue_id}")
        action = item["action"]
        if action not in {None, "resolve"}:
            raise ReviewPacketError("Issue action must be resolve or null")
        reason = item["reason"]
        if action == "resolve":
            reason = _required_text(reason, f"resolution reason for {issue_id}")
        elif reason not in {None, ""}:
            raise ReviewPacketError("An unresolved issue cannot carry a resolution reason")
        seen.add(issue_id)
        result.append({"issue_id": issue_id, "action": action, "reason": reason})
    if seen != expected_ids:
        raise ReviewPacketError("issue_resolutions must retain every issue from the packet")
    return result


def _validate_fx(state: Mapping[str, Any]) -> None:
    transaction = state["transaction"]
    original_currency = str(
        transaction["original_currency"] or transaction["currency"]
    ).upper()
    if original_currency == "EUR":
        return
    if transaction["amount_eur_minor"] is None or transaction["fx_rate_id"] is None:
        raise ReviewPacketError("Foreign-currency review requires a sourced EUR conversion")
    fx = state["fx"]
    if fx is None:
        raise ReviewPacketError("Linked FX record is missing")
    if str(fx["base_currency"]).upper() != original_currency:
        raise ReviewPacketError("FX base currency does not match transaction currency")
    if str(fx["quote_currency"]).upper() != "EUR":
        raise ReviewPacketError("FX quote currency must be EUR")
    if fx["rate_source"] not in ALLOWED_PRODUCTION_SOURCES:
        raise ReviewPacketError("FX source is not allowed in production")
    transaction_date = date.fromisoformat(str(transaction["transaction_date"]))
    rate_date = date.fromisoformat(str(fx["rate_date"]))
    if fx["rate_source"] != "actual_settlement" and rate_date > transaction_date:
        raise ReviewPacketError("FX rate cannot be dated after the transaction")
    if fx["rate_source"] == "xolo_recorded" and transaction_date > XOLO_RECORDED_PRODUCTION_THROUGH:
        raise ReviewPacketError("Post-cutover transaction cannot use xolo_recorded FX")
    original_minor = transaction["amount_original_minor"]
    if original_minor is None:
        raise ReviewPacketError("Foreign-currency transaction is missing its original amount")
    expected_eur = int(
        (Decimal(int(original_minor)) * Decimal(str(fx["rate"]))).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )
    if int(transaction["amount_eur_minor"]) != expected_eur:
        raise ReviewPacketError("Stored EUR amount does not reconcile to the linked FX rate")


def _validate_asset(asset: Mapping[str, Any]) -> None:
    required = (
        "placed_in_service_on",
        "amortizable_base_minor",
        "iva_treatment",
        "business_use_ratio",
        "annual_rate_basis_points",
    )
    missing = [field for field in required if asset.get(field) in {None, "", "unknown"}]
    if missing:
        raise ReviewPacketError("Linked asset is incomplete: " + ", ".join(missing))


def _verify_archived_document(db: LedgerDB, state: Mapping[str, Any]) -> None:
    document = state["document"]
    if document is None:
        return
    live = _one(
        db,
        "SELECT external_key, source_path, source_hash FROM documents WHERE document_id = ?",
        (document["document_id"],),
        label="document",
    )
    if not str(live.get("external_key") or "").startswith("sha256:"):
        return
    source_path = live.get("source_path")
    if not source_path:
        raise ReviewPacketError("Intake document has no archived evidence path")
    path = Path(source_path)
    if not path.is_file():
        raise ReviewPacketError("Archived evidence file is unavailable")
    digest = _sha256_file(path)
    if digest.casefold() != str(live["source_hash"]).casefold():
        raise ReviewPacketError("Archived evidence hash no longer matches the ledger")


def _document_state(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    selected = _select(
        document,
        "document_id",
        "row_version",
        "source_hash",
        "counterparty_id",
        "document_type",
        "document_number",
        "issued_on",
        "period_id",
        "currency",
        "total_minor",
        "lifecycle_status",
        "mime_type",
    )
    selected["evidence_filename"] = (
        Path(document["source_path"]).name if document.get("source_path") else None
    )
    return selected


def _counterparty_state(counterparty: dict[str, Any] | None) -> dict[str, Any] | None:
    if counterparty is None:
        return None
    return _select(
        counterparty,
        "counterparty_id",
        "row_version",
        "source_hash",
        "external_key",
        "tax_id",
        "display_name",
        "country_code",
        "email",
        "phone",
        "vat_id",
        "roi_status",
        "legal_form",
        "professional_supplier",
        "retention_expected",
    )


def _linked_open_issues(
    db: LedgerDB,
    *,
    transaction_id: str,
    document_id: str | None,
    counterparty_id: str | None,
) -> list[dict[str, Any]]:
    subjects = {("transactions", transaction_id)}
    if document_id:
        subjects.add(("documents", document_id))
    if counterparty_id:
        subjects.add(("counterparties", counterparty_id))
    rows = _all(
        db,
        """
        SELECT * FROM validation_issues
        WHERE issue_status = 'open'
        ORDER BY subject_table, subject_id, issue_code, validation_issue_id
        """,
    )
    return [row for row in rows if (row["subject_table"], row["subject_id"]) in subjects]


def _resolve_transaction_id(db: LedgerDB, review_kind: str, subject_id: str) -> str:
    if review_kind == "transaction":
        _one(
            db,
            "SELECT transaction_id FROM transactions WHERE transaction_id = ?",
            (subject_id,),
            label="transaction",
        )
        return subject_id
    rows = _all(
        db,
        "SELECT transaction_id FROM transactions WHERE document_id = ? ORDER BY transaction_id",
        (subject_id,),
    )
    if len(rows) != 1:
        raise ReviewPacketError(
            f"Document review requires exactly one linked transaction; found {len(rows)}"
        )
    return str(rows[0]["transaction_id"])


def _parse_review_id(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise ReviewPacketError("Review ID must be document:<uuid> or transaction:<uuid>")
    kind, subject_id = value.split(":", 1)
    if kind not in {"document", "transaction"} or not subject_id:
        raise ReviewPacketError("Review ID must be document:<uuid> or transaction:<uuid>")
    return kind, subject_id


def _validate_transition(current: str, target: str, label: str) -> None:
    if current == target:
        if current in {"duplicate", "rejected", "void", "posted", "included_in_snapshot"}:
            raise ReviewPacketError(f"Terminal {label} lifecycle row is immutable: {current}")
        return
    if target not in VALID_LIFECYCLE_TRANSITIONS.get(current, set()):
        raise ReviewPacketError(f"Invalid {label} lifecycle transition: {current} -> {target}")


def _one(
    db: LedgerDB,
    sql: str,
    params: tuple[Any, ...] = (),
    *,
    label: str,
) -> dict[str, Any]:
    row = db.connection.execute(sql, params).fetchone()
    if row is None:
        raise ReviewPacketError(f"Unknown {label}")
    return dict(row)


def _all(db: LedgerDB, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in db.connection.execute(sql, params).fetchall()]


def _select(row: Mapping[str, Any], *fields: str) -> dict[str, Any]:
    return {field: _jsonable(row.get(field)) for field in fields}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _stable_hash(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decision_treatment_value(key: str, value: Any) -> Any:
    if key == "tax_code" and value == "unknown":
        return None
    if key in {
        "aeat_reverse_charge",
        "include_modelo130",
        "include_modelo303",
        "include_modelo347",
    }:
        return None if value is None else bool(value)
    return _jsonable(value)


def _review_audit_note(base_note: Any, decision: Mapping[str, Any]) -> str:
    lines = []
    if isinstance(base_note, str) and base_note.strip():
        lines.append(base_note.strip())
    lines.extend(
        (
            f"Review outcome: {decision['outcome']}",
            f"Review reason: {decision['reason']}",
            f"Document valid: {str(bool(decision['document_valid'])).lower()}",
        )
    )
    if decision.get("business_purpose"):
        lines.append(f"Business purpose: {decision['business_purpose']}")
    if decision.get("asset_decision"):
        lines.append(f"Asset decision: {decision['asset_decision']}")
    return "\n".join(lines)


def _treatment_hash(
    transaction: Mapping[str, Any],
    treatment: Mapping[str, Any],
    values: Mapping[str, Any],
) -> str:
    return _stable_hash(
        {
            "transaction_id": transaction["transaction_id"],
            "treatment_type": treatment["treatment_type"],
            "jurisdiction": treatment["jurisdiction"],
            **{key: values[key] for key in TREATMENT_DECISION_FIELDS},
        }
    )


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("extra=" + ",".join(extra))
        raise ReviewPacketError(f"Invalid {label} fields: {'; '.join(details)}")


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewPacketError(f"{label} is required")
    return value.strip()


def _optional_upper(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ReviewPacketError("AEAT code values must be strings or null")
    return value.strip().upper() or None


def _optional_nonnegative_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReviewPacketError(f"{label} must be an integer number of cents or null")
    if value < 0:
        raise ReviewPacketError(f"{label} cannot be negative")
    return value


def _bool_db(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _expect_one(cursor: sqlite3.Cursor, label: str) -> None:
    if cursor.rowcount != 1:
        raise ReviewPacketError(f"Concurrent update prevented {label} mutation")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
