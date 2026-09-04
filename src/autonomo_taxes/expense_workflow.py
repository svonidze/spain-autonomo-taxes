"""Typed expense commands. No deployment, privileged execution or backup control."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import UUID, uuid4

from .depreciation import CALCULATION_VERSION, minor, schedule
from .ledger_db import LedgerDB
from .review_packet import (
    TREATMENT_DECISION_FIELDS, DECISION_FIELDS, prepare_review_packet, confirm_review_packet,
    _build_packet, _verify_archived_document,
    _apply_confirmed_fx, _resolve_confirmed_fx, _validate_fx_spec_shape,
)
from .posting import prevalidate_expense_inbox_cleanup

FACT_FIELDS = {"document_number", "issued_on", "transaction_date", "booking_date", "currency", "gross_minor", "counterparty_id", "business_activity_id"}
PAYLOAD_FIELDS = {"facts", "supplier", "decision", "asset", "fx", "manual_review_reason", "change_reason"}
ASSET_FIELDS = {"description", "basis_minor", "business_use_ratio", "annual_rate_basis_points", "placed_in_service_on", "method", "new_equipment", "aeat_asset_type"}
MANUAL_ISSUES = {"document_structural_review", "document_classification_review"}
TAX_ISSUES = {"transaction_tax_review", "counterparty_tax_profile_review"}
EDITABLE = {"received", "extracted", "needs_review", "approved"}


class ExpenseWorkflowError(ValueError):
    def __init__(self, message: str, *, code: str = "expense_invalid", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def require(condition: Any, message: str, *, code: str = "expense_invalid", status: int = 400) -> None:
    if not condition:
        raise ExpenseWorkflowError(message, code=code, status=status)


def text(value: Any, name: str, *, optional: bool = False) -> str:
    require(isinstance(value, str), f"{name} must be text")
    result = value.strip()
    require(len(result) <= 4000 and (optional or result), f"{name} is required or too long")
    return result


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _one(db: LedgerDB, query: str, parameters: tuple = ()) -> dict[str, Any]:
    row = db.connection.execute(query, parameters).fetchone()
    require(row is not None, "Expense record was not found", status=404)
    return dict(row)


def _packet(db: LedgerDB, identifier: str) -> dict[str, Any]:
    try:
        UUID(identifier)
    except (ValueError, TypeError) as exc:
        raise ExpenseWorkflowError("Invalid transaction identifier") from exc
    packet = prepare_review_packet(db, "transaction:" + identifier)
    require(packet["state"]["transaction"]["entry_type"] == "expense", "This workflow only accepts expenses")
    return packet


def _editable(db: LedgerDB, packet: Mapping[str, Any]) -> None:
    state = packet["state"]
    require(state["period"]["status"] == "open", "The accounting period is closed", code="period_closed", status=409)
    require(state["transaction"]["lifecycle_status"] in EDITABLE and state["document"] and state["document"]["lifecycle_status"] in EDITABLE,
            "Posted or finalized records cannot be edited", code="expense_finalized", status=409)
    count = db.connection.execute("SELECT COUNT(*) FROM transactions WHERE document_id=?", (state["document"]["document_id"],)).fetchone()[0]
    require(count == 1, "A document shared by several transactions requires a separate correction workflow", status=409)
    require(len(state["assets"]) <= 1, "One asset per expense is supported")


def _seed(packet: Mapping[str, Any], activities: list[dict[str, Any]]) -> dict[str, Any]:
    state = packet["state"]
    transaction, document = state["transaction"], state["document"]
    activity = transaction.get("business_activity_id")
    if not activity and len(activities) == 1:
        activity = activities[0]["business_activity_id"]
    return {
        "facts": {
            "document_number": document.get("document_number") or "", "issued_on": document.get("issued_on"),
            "transaction_date": transaction["transaction_date"], "booking_date": transaction["booking_date"],
            "currency": transaction.get("original_currency") or transaction["currency"],
            "gross_minor": transaction.get("amount_original_minor") if transaction.get("amount_original_minor") is not None else transaction["amount_minor"],
            "counterparty_id": transaction.get("counterparty_id"), "business_activity_id": activity,
        },
        "supplier": None, "decision": deepcopy(packet["decision"]), "asset": None, "fx": None,
        "manual_review_reason": "", "change_reason": "",
    }


def get_draft(db: LedgerDB, transaction_id: str) -> dict[str, Any]:
    packet = _packet(db, transaction_id)
    activities = db.list_business_activities()
    draft = db.connection.execute("SELECT * FROM expense_drafts WHERE transaction_id=?", (transaction_id,)).fetchone()
    seed = _seed(packet, activities)
    return {
        "transaction_id": transaction_id, "draft_version": draft["row_version"] if draft else 0,
        "source_snapshot_hash": draft["source_snapshot_hash"] if draft else packet["snapshot_hash"],
        "current_snapshot_hash": packet["snapshot_hash"], "source_values": seed,
        "payload": json.loads(draft["payload_json"]) if draft else seed,
        "source": packet["state"], "allowed_values": packet["allowed_values"], "activities": activities,
        "editable": packet["state"]["transaction"]["lifecycle_status"] in EDITABLE and packet["state"]["period"]["status"] == "open",
        "conflict": bool(draft and draft["source_snapshot_hash"] != packet["snapshot_hash"]),
    }


def _shape(payload: Any) -> dict[str, Any]:
    require(isinstance(payload, dict) and set(payload) == PAYLOAD_FIELDS, "Invalid expense draft fields")
    require(isinstance(payload["facts"], dict) and set(payload["facts"]) == FACT_FIELDS, "Invalid expense fact fields")
    require(isinstance(payload["decision"], dict) and set(payload["decision"]) == DECISION_FIELDS, "Review decision fields are invalid")
    require(payload["asset"] is None or isinstance(payload["asset"], dict) and set(payload["asset"]) == ASSET_FIELDS, "Invalid equipment fields")
    require(payload["supplier"] is None or isinstance(payload["supplier"], dict) and set(payload["supplier"]) == {"display_name", "tax_id", "vat_id", "country_code"}, "Invalid supplier fields")
    require(payload["fx"] is None or isinstance(payload["fx"], dict), "Invalid FX decision")
    for name in ("manual_review_reason", "change_reason"):
        text(payload[name], name, optional=True)
    # Reject NaN and unsupported JSON values before any write.
    require(len(json.dumps(payload, allow_nan=False)) <= 100_000, "Expense draft is too large")
    return deepcopy(payload)


def save_draft(db: LedgerDB, transaction_id: str, *, payload: dict[str, Any], expected_version: int,
               source_snapshot_hash: str, actor: str) -> dict[str, Any]:
    payload = _shape(payload)
    require(type(expected_version) is int and expected_version >= 0, "Invalid draft version")
    with db.transaction():
        packet = _packet(db, transaction_id)
        _editable(db, packet)
        require(packet["snapshot_hash"] == source_snapshot_hash, "Source records changed; reload before saving", code="expense_stale", status=409)
        current = db.connection.execute("SELECT * FROM expense_drafts WHERE transaction_id=?", (transaction_id,)).fetchone()
        require((current["row_version"] if current else 0) == expected_version, "Draft changed in another session", code="expense_stale", status=409)
        changed = current is None or json.loads(current["payload_json"]) != payload or current["source_snapshot_hash"] != source_snapshot_hash
        if changed and packet["state"]["transaction"]["lifecycle_status"] == "approved":
            text(payload["change_reason"], "Reason for invalidating approval")
            db.connection.execute("UPDATE transactions SET lifecycle_status='needs_review',row_version=row_version+1,updated_at=? WHERE transaction_id=?", (stamp(), transaction_id))
            db.connection.execute("UPDATE documents SET lifecycle_status='needs_review',row_version=row_version+1,updated_at=? WHERE document_id=?", (stamp(), packet["state"]["document"]["document_id"]))
            source_snapshot_hash = _packet(db, transaction_id)["snapshot_hash"]
        if changed:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            db.connection.execute("INSERT INTO expense_draft_events VALUES(?,?,?,?,?,?,?)", (
                str(uuid4()), transaction_id, current["payload_json"] if current else "{}", encoded,
                payload["change_reason"] or "Save unposted expense draft", actor, stamp()))
            db.connection.execute("""INSERT INTO expense_drafts VALUES(?,?,?,?,?,?)
                ON CONFLICT(transaction_id) DO UPDATE SET payload_json=excluded.payload_json,
                source_snapshot_hash=excluded.source_snapshot_hash,row_version=excluded.row_version,
                actor=excluded.actor,updated_at=excluded.updated_at""",
                (transaction_id, encoded, source_snapshot_hash, expected_version + 1, actor, stamp()))
    return get_draft(db, transaction_id)


def _supplier(db: LedgerDB, payload: Mapping[str, Any]) -> dict[str, Any] | None:
    identifier = payload["facts"]["counterparty_id"]
    if identifier:
        require(payload["supplier"] is None, "Choose an existing supplier or a new supplier, not both")
        return _one(db, "SELECT * FROM counterparties WHERE counterparty_id=?", (identifier,))
    supplier = payload["supplier"]
    require(supplier is not None, "Choose or create a supplier")
    country = text(supplier["country_code"], "Supplier country").upper()
    require(len(country) == 2 and country.isalpha() and country != "ZZ", "Use a reviewed supplier country")
    tax_id = text(supplier["tax_id"], "Supplier tax identifier").upper()
    def normalized(value: Any) -> str:
        value = "".join(char for char in str(value or "").upper() if char.isalnum())
        return value.removeprefix("ES") if country == "ES" else value
    identifiers = {normalized(tax_id)}
    if supplier["vat_id"]:
        identifiers.add(normalized(supplier["vat_id"]))
    # The underlying registry resolves tax identifiers globally. Never let its
    # upsert reinterpret a conflicting country as permission to rename a card.
    for row in db.connection.execute("SELECT * FROM counterparties"):
        if identifiers & {normalized(row["tax_id"]), normalized(row["vat_id"])}:
            raise ExpenseWorkflowError("A supplier with this identifier exists; select that card", code="supplier_exists", status=409)
    text(supplier["display_name"], "Supplier name")
    return None


def _saved(db: LedgerDB, transaction_id: str, version: int, *, as_of: date | None = None) -> tuple[dict[str, Any], dict[str, Any], str]:
    packet = _packet(db, transaction_id)
    _editable(db, packet)
    draft = _one(db, "SELECT * FROM expense_drafts WHERE transaction_id=?", (transaction_id,))
    require(type(version) is int and draft["row_version"] == version and draft["source_snapshot_hash"] == packet["snapshot_hash"],
            "Draft or source changed; reload before confirming", code="expense_stale", status=409)
    payload = _shape(json.loads(draft["payload_json"]))
    supplier = _supplier(db, payload)
    activity = _one(db, "SELECT * FROM business_activities WHERE business_activity_id=?", (payload["facts"]["business_activity_id"],))
    token = digest({"source": packet["snapshot_hash"], "draft": draft, "supplier": supplier, "activity": activity,
                    "calculation_version": CALCULATION_VERSION, "as_of": (as_of or date.today()).isoformat()})
    return packet, payload, token


def _facts(db: LedgerDB, packet: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    state, facts = packet["state"], payload["facts"]
    for key in ("issued_on", "transaction_date", "booking_date"):
        date.fromisoformat(text(facts[key], key))
    number = text(facts["document_number"], "Document number")
    currency = text(facts["currency"], "Currency").upper()
    require(len(currency) == 3 and currency.isalpha(), "Currency must be a three-letter code")
    amount = facts["gross_minor"]
    require(type(amount) is int and amount > 0, "Invoice gross must be positive minor units")
    tx = state["transaction"]
    period_key = f'{facts["transaction_date"][:4]}-Q{(date.fromisoformat(facts["transaction_date"]).month-1)//3+1}'
    period = _one(db, "SELECT * FROM periods WHERE period_key=?", (period_key,))
    require(period["status"] == "open", "The target period is closed", status=409)
    activity = _one(db, "SELECT * FROM business_activities WHERE business_activity_id=?", (facts["business_activity_id"],))
    require(activity["starts_on"] <= facts["transaction_date"] and (not activity["ends_on"] or activity["ends_on"] >= facts["transaction_date"]), "Activity is not valid for this date")
    supplier = _supplier(db, payload)
    if supplier is None:
        values = payload["supplier"]
        supplier = db.upsert_counterparty(external_key="expense-supplier:" + digest(values),
            display_name=text(values["display_name"], "Supplier name"), tax_id=text(values["tax_id"], "Supplier tax identifier").upper(),
            country_code=text(values["country_code"], "Supplier country").upper())
        if values["vat_id"]:
            supplier = db.update_counterparty_review(supplier["counterparty_id"],
                display_name=supplier["display_name"], tax_id=supplier["tax_id"], country_code=supplier["country_code"],
                vat_id=text(values["vat_id"], "Supplier VAT identifier").upper(), roi_status="unknown",
                professional_supplier=None, retention_expected=None, email=None, phone=None,
                reviewed_from="expense_workflow", expected_row_version=supplier["row_version"])
    changed = any(facts[key] != expected for key, expected in (
        ("document_number", state["document"]["document_number"]), ("issued_on", state["document"]["issued_on"]),
        ("transaction_date", tx["transaction_date"]), ("booking_date", tx["booking_date"]),
        ("currency", tx.get("original_currency") or tx["currency"]),
        ("gross_minor", tx.get("amount_original_minor") if tx.get("amount_original_minor") is not None else tx["amount_minor"]),
        ("counterparty_id", tx.get("counterparty_id")),
    ))
    if changed:
        text(payload["change_reason"], "Explain the source fact correction")
    original_amount = tx.get("amount_original_minor") if tx.get("amount_original_minor") is not None else tx["amount_minor"]
    invalidate_fx = currency != (tx.get("original_currency") or tx["currency"]) or amount != original_amount or facts["transaction_date"] != tx["transaction_date"]
    eur = amount if currency == "EUR" else None if invalidate_fx else tx["amount_eur_minor"]
    fx = None if currency == "EUR" or invalidate_fx else tx.get("fx_rate_id")
    db.connection.execute("""UPDATE documents SET document_number=?,issued_on=?,currency=?,total_minor=?,
        counterparty_id=?,period_id=?,row_version=row_version+1,updated_at=? WHERE document_id=?""",
        (number, facts["issued_on"], currency, amount, supplier["counterparty_id"], period["period_id"], stamp(), state["document"]["document_id"]))
    db.connection.execute("""UPDATE transactions SET transaction_date=?,booking_date=?,period_id=?,currency=?,
        original_currency=?,amount_minor=?,amount_original_minor=?,amount_eur_minor=?,fx_rate_id=?,
        counterparty_id=?,business_activity_id=?,row_version=row_version+1,updated_at=? WHERE transaction_id=?""",
        (facts["transaction_date"], facts["booking_date"], period["period_id"], currency, currency, amount, amount, eur, fx,
         supplier["counterparty_id"], activity["business_activity_id"], stamp(), tx["transaction_id"]))


def _reconcile_amounts(packet: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    tx = packet["state"]["transaction"]
    tax = payload["decision"]["tax_treatment"]
    gross = tx["amount_eur_minor"]
    require(type(gross) is int and gross > 0, "Confirm the EUR conversion before reviewing amounts")
    for name in ("taxable_base_minor", "vat_minor", "deductible_vat_minor", "withholding_minor", "rate_basis_points"):
        require(type(tax.get(name)) is int and tax[name] >= 0, f"Explicit {name} in EUR is required")
    require(tax["deductible_vat_minor"] <= tax["vat_minor"], "IVA deduction exceeds the invoice quota")
    reverse = tax["tax_code"] in {"eu_service_expense", "eu_goods_expense", "non_eu_service_expense", "domestic_reverse_charge_expense"}
    require(tax["aeat_reverse_charge"] is reverse,
            "Reverse-charge classification must agree with the reviewed tax code")
    total = tax["taxable_base_minor"] + (0 if reverse else tax["vat_minor"]) - tax["withholding_minor"]
    require(abs(total - gross) <= 1, "Tax amounts must reconcile to the invoice gross in EUR; review the base, IVA, withholding and FX")
    expected_vat = minor(Decimal(tax["taxable_base_minor"]) * Decimal(tax["rate_basis_points"]) / 10000)
    require(abs(expected_vat - tax["vat_minor"]) <= 1, "The single-rate IVA amount does not match its base and rate")


def _asset(db: LedgerDB, packet: Mapping[str, Any], payload: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    values = payload["asset"]
    if values is None:
        require(not packet["state"]["assets"], "The expense already has an asset; keep the equipment branch")
        return None, []
    require(not packet["state"]["assets"], "Existing asset acquisition requires its existing review workflow", status=409)
    tx = packet["state"]["transaction"]
    require(values["placed_in_service_on"] >= tx["transaction_date"], "In-service date cannot precede acquisition")
    text(values["description"], "Equipment description")
    require(values["aeat_asset_type"] in {"21", "23", "24", "25", "28", "29"}, "Choose an equipment category")
    tax = payload["decision"]["tax_treatment"]
    for name in ("taxable_base_minor", "vat_minor", "deductible_vat_minor"):
        require(type(tax.get(name)) is int and tax[name] >= 0, f"Explicit {name} in EUR is required")
    require(tax["deductible_vat_minor"] <= tax["vat_minor"], "Deductible IVA exceeds invoice IVA")
    cost = tax["taxable_base_minor"] + tax["vat_minor"] - tax["deductible_vat_minor"]
    require(type(values["basis_minor"]) is int and 0 < values["basis_minor"] <= cost, "Amortizable basis exceeds acquisition cost after recoverable IVA")
    rows = schedule(basis_minor=values["basis_minor"], business_use_ratio=values["business_use_ratio"],
        annual_rate_basis_points=values["annual_rate_basis_points"], placed_in_service_on=values["placed_in_service_on"], method=values["method"])
    if values["method"] == "immediate":
        require(values["annual_rate_basis_points"] == 10000, "Immediate depreciation uses a full-rate decision")
        require(values["new_equipment"] is True and cost <= 30000, "Immediate deduction requires a new object costing at most EUR 300 before business share")
        activity = _one(db, "SELECT irpf_method FROM business_activities WHERE business_activity_id=?", (tx["business_activity_id"],))
        require(activity["irpf_method"] in {"estimacion_directa", "estimacion_directa_normal", "estimacion_directa_simplificada"}, "Immediate depreciation requires the reviewed direct-estimation activity")
        # Conservatively include every recorded low-value acquisition in this year.
        inventory = db.connection.execute("SELECT cost_minor,currency FROM assets WHERE substr(placed_in_service_on,1,4)=?", (values["placed_in_service_on"][:4],)).fetchall()
        require(all(row["currency"] == "EUR" for row in inventory), "Review foreign-currency asset bases before applying the annual limit")
        used = sum(row["cost_minor"] for row in inventory if row["cost_minor"] <= 30000)
        require(used + cost <= 2500000, "Annual low-value asset limit would be exceeded")
    asset = db.add_asset(asset_code="expense-" + tx["transaction_id"], cost_minor=cost, currency="EUR",
        depreciation_method=values["method"], source_hash=packet["state"]["document"]["source_hash"],
        document_id=tx["document_id"], acquisition_transaction_id=tx["transaction_id"],
        placed_in_service_on=values["placed_in_service_on"], amortizable_base_minor=values["basis_minor"],
        iva_treatment="reviewed_in_acquisition", business_use_ratio=values["business_use_ratio"],
        annual_rate_basis_points=values["annual_rate_basis_points"], advisor_decision=payload["decision"]["reason"],
        advisor_decision_on=date.today().isoformat())
    asset = db.update_asset_book_profile(asset["asset_id"], business_activity_id=tx["business_activity_id"],
        aeat_asset_type=values["aeat_asset_type"], description=values["description"], aeat_asset_identifier=asset["asset_code"],
        aeat_amortization_method="06" if values["method"] == "immediate" else "01",
        source_invoice_number=packet["state"]["document"]["document_number"], acquisition_taxable_base_minor=tax["taxable_base_minor"],
        acquisition_vat_rate_basis_points=tax["rate_basis_points"], acquisition_deductible_vat_minor=tax["deductible_vat_minor"],
        iva_treatment="reviewed_in_acquisition", business_use_ratio=values["business_use_ratio"],
        book_profile_source_reference="document:" + tx["document_id"], book_profile_source_hash=packet["state"]["document"]["source_hash"],
        expected_row_version=asset["row_version"])
    db.connection.execute("INSERT INTO asset_depreciation_plans VALUES(?,?,?,?,?)", (asset["asset_id"], values["method"], CALCULATION_VERSION, json.dumps(values), digest(values)))
    for row in rows:
        entry = db.add_amortization_entry(asset_id=asset["asset_id"], period_key=row["period_key"], amount_minor=row["amount_minor"],
            source_hash=digest({"parameters": values, "row": row, "version": CALCULATION_VERSION}), include_in_books=False)
        db.connection.execute("UPDATE amortization_entries SET recognition_on=? WHERE amortization_entry_id=?", (row["recognition_on"], entry["amortization_entry_id"]))
        row["amortization_entry_id"] = entry["amortization_entry_id"]
    return asset, rows


def _decision(db: LedgerDB, packet: dict[str, Any], payload: Mapping[str, Any], asset: Mapping[str, Any] | None) -> None:
    decision = deepcopy(payload["decision"])
    decision.update(outcome="approve", asset_decision="asset" if asset else "current_expense", asset_id=asset["asset_id"] if asset else None)
    tax = decision["tax_treatment"]
    require(set(tax) == set(TREATMENT_DECISION_FIELDS), "Invalid tax treatment fields")
    if asset:
        tax.update(deductible_irpf_minor=0, include_modelo130=False)
    resolutions = []
    for issue in packet["state"]["issues"]:
        code = issue["issue_code"]
        if code in MANUAL_ISSUES:
            reason = text(payload["manual_review_reason"], "Explain the manual review of the readable original")
        elif code in TAX_ISSUES:
            reason = text(decision["reason"], "Review reason")
        else:
            require(not issue["blocking"], "An unsupported blocking issue requires resolution", code="expense_blocked", status=409)
            resolutions.append({"issue_id": issue["validation_issue_id"], "action": None, "reason": None})
            continue
        resolutions.append({"issue_id": issue["validation_issue_id"], "action": "resolve", "reason": reason})
    decision["issue_resolutions"] = resolutions
    packet["decision"] = decision


class _RollbackPreview(Exception):
    def __init__(self, result: dict[str, Any]):
        self.result = result


def _resolve_draft_fx(db: LedgerDB, transaction_id: str, version: int, *,
                      ecb_verify: Callable | None = None) -> dict[str, Any] | None:
    """Resolve saved FX before opening the accounting write transaction."""
    _, payload, _ = _saved(db, transaction_id, version)
    if not payload["fx"]:
        return None
    _validate_fx_spec_shape(payload["fx"])
    return _resolve_confirmed_fx(
        payload["fx"],
        snapshot_currency=text(payload["facts"]["currency"], "Currency").upper(),
        transaction_date=date.fromisoformat(text(payload["facts"]["transaction_date"], "Transaction date")),
        ecb_verify=ecb_verify,
    )


def preview(db: LedgerDB, transaction_id: str, *, expected_version: int,
            ecb_verify: Callable | None = None) -> dict[str, Any]:
    resolved_fx = _resolve_draft_fx(db, transaction_id, expected_version, ecb_verify=ecb_verify)
    try:
        with db.transaction():
            result = _prepare(db, transaction_id, expected_version, resolved_fx=resolved_fx)
            raise _RollbackPreview(result["preview"])
    except _RollbackPreview as completed:
        return completed.result
    raise RuntimeError("Preview transaction did not roll back")


def _prepare(db: LedgerDB, transaction_id: str, version: int, *, resolved_fx: Mapping[str, Any] | None = None) -> dict[str, Any]:
    evaluation_date = date.today()
    packet, payload, token = _saved(db, transaction_id, version, as_of=evaluation_date)
    _facts(db, packet, payload)
    packet = _build_packet(db, "transaction:" + transaction_id)
    if payload["fx"]:
        if resolved_fx is None:
            raise ExpenseWorkflowError("FX must be resolved before preparing the expense")
        _apply_confirmed_fx(db, packet["state"], resolved_fx)
        packet = _build_packet(db, packet["review_id"])
    _reconcile_amounts(packet, payload)
    asset, rows = _asset(db, packet, payload)
    packet = _build_packet(db, packet["review_id"])
    _decision(db, packet, payload, asset)
    confirm_review_packet(db, packet)
    tx = _one(db, "SELECT * FROM transactions WHERE transaction_id=?", (transaction_id,))
    # The same readiness gate is exercised during preview and confirmation.
    db.transition_transaction(transaction_id, lifecycle_status="posted", expected_row_version=tx["row_version"])
    due = [row for row in rows if row["recognition_on"] <= evaluation_date.isoformat()]
    for row in due:
        period = _one(db, "SELECT status FROM periods WHERE period_key=?", (row["period_key"],))
        require(period["status"] == "open", "A due depreciation period is closed", status=409)
    treatment = packet["decision"]["tax_treatment"]
    return {"asset": asset, "due": due, "payload": payload, "preview": {
        "transaction_id": transaction_id, "draft_version": version, "preview_token": token,
        "gross_minor": payload["facts"]["gross_minor"], "currency": payload["facts"]["currency"],
        "deductible_vat_minor": treatment["deductible_vat_minor"] if treatment["include_modelo303"] else 0,
        "deductible_irpf_minor": sum(row["amount_minor"] for row in due) if asset else treatment["deductible_irpf_minor"] if treatment["include_modelo130"] else 0,
        "schedule": [{k: v for k, v in row.items() if k != "amortization_entry_id"} for row in rows],
        "future_depreciation_minor": sum(row["amount_minor"] for row in rows if row not in due),
        "supplier": packet["state"]["counterparty"]["display_name"], "period": packet["state"]["period"]["period_key"],
        "posted": False,
    }}


def _request_id(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError) as exc:
        raise ExpenseWorkflowError("A UUID request_id is required") from exc


def _cached(db: LedgerDB, request_id: str, request_hash: str) -> dict[str, Any] | None:
    existing = db.connection.execute("SELECT * FROM expense_actions WHERE request_id=?", (request_id,)).fetchone()
    if existing:
        require(existing["request_hash"] == request_hash, "Request key was reused with different input", code="expense_stale", status=409)
        return json.loads(existing["result_json"])
    return None


def _remember(db: LedgerDB, request_id: str, request_hash: str, kind: str, subject_id: str, result: dict[str, Any], actor: str) -> None:
    db.connection.execute("INSERT INTO expense_actions VALUES(?,?,?,?,?,?,?)", (request_id, kind, subject_id, request_hash, json.dumps(result), actor, stamp()))


def _recognize(db: LedgerDB, entry_id: str, archive_root: Path, actor: str) -> dict[str, Any]:
    from .storage_service import register_local_source_replica
    entry = _one(db, """SELECT ae.*,p.period_key,p.status AS period_status,a.acquisition_transaction_id,
        a.description,a.asset_code,a.aeat_asset_type FROM amortization_entries ae JOIN periods p ON p.period_id=ae.period_id
        JOIN assets a ON a.asset_id=ae.asset_id WHERE amortization_entry_id=?""", (entry_id,))
    if entry["recognition_transaction_id"]:
        existing = _one(db, "SELECT lifecycle_status FROM transactions WHERE transaction_id=?", (entry["recognition_transaction_id"],))
        require(existing["lifecycle_status"] in {"posted", "included_in_snapshot"}, "Existing recognition is not posted", status=409)
        return {"amortization_entry_id": entry_id, "transaction_id": entry["recognition_transaction_id"], "posted": True}
    plan = _one(db, "SELECT * FROM asset_depreciation_plans WHERE asset_id=?", (entry["asset_id"],))
    parameters = json.loads(plan["parameters_json"])
    require(plan["calculation_version"] == CALCULATION_VERSION and plan["source_hash"] == digest(parameters),
            "The reviewed schedule version or parameters changed", status=409)
    expected_rows = schedule(**{key: parameters[key] for key in (
        "basis_minor", "business_use_ratio", "annual_rate_basis_points", "placed_in_service_on", "method")})
    expected_row = {key: entry[key] for key in ("period_key", "recognition_on", "amount_minor")}
    require(expected_row in expected_rows and entry["source_hash"] == digest({
        "parameters": parameters, "row": expected_row, "version": CALCULATION_VERSION}),
        "The schedule row no longer matches its reviewed calculation", status=409)
    asset = _one(db, "SELECT * FROM assets WHERE asset_id=?", (entry["asset_id"],))
    require(all(asset[key] == parameters[parameter] for key, parameter in (
        ("amortizable_base_minor", "basis_minor"), ("business_use_ratio", "business_use_ratio"),
        ("annual_rate_basis_points", "annual_rate_basis_points"), ("placed_in_service_on", "placed_in_service_on"),
        ("depreciation_method", "method"), ("aeat_asset_type", "aeat_asset_type"))),
        "The asset changed after its schedule was reviewed", status=409)
    require(entry["period_status"] == "open", "Depreciation period is closed", status=409)
    require(entry["recognition_on"] and entry["recognition_on"] <= date.today().isoformat(), "Depreciation date has not arrived", status=409)
    require(entry["amount_minor"] > 0, "Zero depreciation is not a posting action")
    acquisition = _one(db, "SELECT * FROM transactions WHERE transaction_id=?", (entry["acquisition_transaction_id"],))
    require(acquisition["lifecycle_status"] in {"posted", "included_in_snapshot"}, "Acquisition must be posted first")
    acquisition_packet = _build_packet(db, "transaction:" + acquisition["transaction_id"])
    _verify_archived_document(db, acquisition_packet["state"])
    basis = _one(db, "SELECT amortizable_base_minor,business_use_ratio FROM assets WHERE asset_id=?", (entry["asset_id"],))
    used = db.connection.execute("""SELECT COALESCE(SUM(ae.amount_minor),0) FROM amortization_entries ae
        JOIN transactions t ON t.transaction_id=ae.recognition_transaction_id
        WHERE ae.asset_id=? AND t.lifecycle_status IN ('posted','included_in_snapshot')""", (entry["asset_id"],)).fetchone()[0]
    require(used + entry["amount_minor"] <= minor(Decimal(basis["amortizable_base_minor"]) * Decimal(str(basis["business_use_ratio"]))), "Depreciation exceeds remaining basis")
    content = (f"Internal depreciation record\nAsset: {entry['asset_code']}\nPeriod: {entry['period_key']}\n"
               f"Recognition date: {entry['recognition_on']}\nAmount EUR minor: {entry['amount_minor']}\n"
               f"Source acquisition: {acquisition['transaction_id']}\nOperator: {actor}\n"
               "This is not another purchase, supplier invoice, payment or tax filing.\n").encode()
    source_hash = hashlib.sha256(content).hexdigest()
    directory = archive_root / entry["period_key"] / "generated"
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / f"{source_hash}.txt"
    if not source.exists():
        with source.open("xb") as stream:
            stream.write(content)
        source.chmod(0o600)
    require(hashlib.sha256(source.read_bytes()).hexdigest() == source_hash, "Generated evidence hash mismatch")
    document = db.upsert_document(external_key="sha256:" + source_hash, document_type="other",
        document_number="AMORT-" + entry_id, issued_on=date.today().isoformat(), period_key=entry["period_key"],
        currency="EUR", total_minor=entry["amount_minor"], counterparty_id=acquisition["counterparty_id"],
        lifecycle_status="extracted", source_hash=source_hash)
    document = db.set_document_storage(document["document_id"], source_path=str(source), mime_type="text/plain", expected_row_version=document["row_version"])
    register_local_source_replica(db, document_id=document["document_id"], source_path=source, media_type="text/plain", storage_root=archive_root)
    tx = db.add_transaction(external_key="native-amortization:" + entry_id, period_key=entry["period_key"],
        transaction_date=entry["recognition_on"], booking_date=date.today().isoformat(), entry_type="expense",
        description="Amortization: " + (entry["description"] or entry["asset_code"]), amount_minor=entry["amount_minor"],
        currency="EUR", amount_eur_minor=entry["amount_minor"], amount_original_minor=entry["amount_minor"], original_currency="EUR",
        document_id=document["document_id"], counterparty_id=acquisition["counterparty_id"], business_activity_id=acquisition["business_activity_id"],
        lifecycle_status="extracted", source_hash=source_hash)
    db.add_detailed_tax_treatment(transaction_id=tx["transaction_id"], treatment_type="invoice_review", tax_code="unknown", source_hash=source_hash)
    packet = _build_packet(db, "transaction:" + tx["transaction_id"])
    decision = packet["decision"]
    decision.update(outcome="approve", reason="Recognize the explicitly approved asset schedule", business_purpose="Depreciation of the linked professional asset", document_valid=True, asset_decision="current_expense", asset_id=None)
    decision["tax_treatment"] = dict(tax_code="domestic_input", aeat_invoice_type="AJ", aeat_operation_key="01", aeat_operation_qualification="S1", aeat_exemption_code=None,
        aeat_reverse_charge=False, vat_investment_good=False, aeat_expense_concept={"21": "G29", "23": "G31", "24": "G28", "25": "G28", "28": "G32", "29": "G28"}[entry["aeat_asset_type"]], rate_basis_points=0, deductible_ratio=1.0,
        taxable_base_minor=0, vat_minor=0, deductible_irpf_minor=entry["amount_minor"], deductible_vat_minor=0, withholding_minor=0,
        include_modelo130=True, include_modelo303=False, include_modelo347=False, rule_version_id=None, notes="Native schedule: " + entry_id)
    require(not packet["state"]["issues"], "An unexpected issue blocks depreciation", status=409)
    confirm_review_packet(db, packet)
    current = _one(db, "SELECT row_version FROM transactions WHERE transaction_id=?", (tx["transaction_id"],))
    db.transition_transaction(tx["transaction_id"], lifecycle_status="posted", expected_row_version=current["row_version"])
    db.connection.execute("UPDATE amortization_entries SET recognition_transaction_id=?,include_in_books=1,row_version=row_version+1,updated_at=? WHERE amortization_entry_id=?",
        (tx["transaction_id"], stamp(), entry_id))
    return {"amortization_entry_id": entry_id, "transaction_id": tx["transaction_id"], "posted": True}


def confirm_and_post(db: LedgerDB, transaction_id: str, *, expected_version: int, preview_token: str, request_id: str,
                     actor: str, archive_root: Path, inbox_root: Path | None = None, ecb_verify: Callable | None = None) -> dict[str, Any]:
    request_id = _request_id(request_id)
    fingerprint = digest({"kind": "expense", "id": transaction_id, "version": expected_version, "preview": preview_token})
    cached = _cached(db, request_id, fingerprint)
    if cached:
        return cached
    resolved_fx = _resolve_draft_fx(db, transaction_id, expected_version, ecb_verify=ecb_verify)
    with db.transaction():
        cached = _cached(db, request_id, fingerprint)
        if cached:
            return cached
        packet, payload, token = _saved(db, transaction_id, expected_version)
        require(token == preview_token, "Preview is stale; review the updated result", code="expense_stale", status=409)
        cleanup = prevalidate_expense_inbox_cleanup(db, transaction_id, inbox_root=inbox_root, archive_root=archive_root)
        require(not cleanup.blocking, cleanup.message or "Original archive is not ready", status=409)
        prepared = _prepare(db, transaction_id, expected_version, resolved_fx=resolved_fx)
        require(prepared["preview"]["preview_token"] == preview_token, "Preview date changed; review the updated result", code="expense_stale", status=409)
        recognitions = [_recognize(db, row["amortization_entry_id"], archive_root, actor) for row in prepared["due"]]
        result = {"transaction_id": transaction_id, "asset_id": prepared["asset"]["asset_id"] if prepared["asset"] else None,
                  "recognitions": recognitions, "posted": True, "period": prepared["preview"]["period"], "follow_up_pending": True,
                  "cleanup_period_key": cleanup.cleanup_row["period_key"] if cleanup.cleanup_row else None}
        _remember(db, request_id, fingerprint, "expense", transaction_id, result, actor)
    return result


def recognize_period(db: LedgerDB, entry_id: str, *, expected_version: int, request_id: str, actor: str, archive_root: Path) -> dict[str, Any]:
    require(type(expected_version) is int and expected_version > 0, "A current schedule version is required")
    request_id = _request_id(request_id)
    fingerprint = digest({"kind": "depreciation", "id": entry_id, "version": expected_version})
    with db.transaction():
        cached = _cached(db, request_id, fingerprint)
        if cached:
            return cached
        entry = _one(db, "SELECT * FROM amortization_entries WHERE amortization_entry_id=?", (entry_id,))
        if not entry["recognition_transaction_id"]:
            require(entry["row_version"] == expected_version, "Schedule changed; reload before posting", code="expense_stale", status=409)
        result = _recognize(db, entry_id, archive_root, actor)
        result["follow_up_pending"] = True
        _remember(db, request_id, fingerprint, "depreciation", entry_id, result, actor)
    return result


def asset_schedule(db: LedgerDB, asset_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in db.connection.execute("""SELECT ae.*,p.period_key,p.status AS period_status,
        t.lifecycle_status AS recognition_status FROM amortization_entries ae JOIN periods p ON p.period_id=ae.period_id
        LEFT JOIN transactions t ON t.transaction_id=ae.recognition_transaction_id WHERE asset_id=? ORDER BY recognition_on,p.starts_on""", (asset_id,))]
