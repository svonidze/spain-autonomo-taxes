from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Mapping, Sequence

from .tax_payments import tax_settlement_payment_summary


TAX_FORM_KEYS = {
    "130": "modelo130",
    "303": "modelo303",
}
AUTHORITATIVE_SNAPSHOT_STATUSES = ("baseline", "filed", "submitted", "final")
SNAPSHOT_STATUS_RANKS = {
    "baseline": 1,
    "submitted": 2,
    "filed": 3,
    "final": 4,
}


def _normalize_form_code(raw: Any) -> str:
    return str(raw or "").lower().replace("modelo", "").strip()


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalize_filed_on(raw: Any) -> str | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(raw or ""))
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(0)).isoformat()
    except ValueError:
        return None


def coerce_money_values(
    raw_values: Any,
    *,
    blank_keys: Iterable[Any] = (),
) -> dict[str, str]:
    if not isinstance(raw_values, Mapping):
        raw_values = {}
    values: dict[str, str] = {}
    for key, value in raw_values.items():
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not amount.is_finite():
            continue
        values[str(key)] = f"{amount:.2f}"
    if not isinstance(blank_keys, (list, tuple, set)):
        blank_keys = ()
    for key in blank_keys:
        values.setdefault(str(key), "0.00")
    return values


def _headline_fields(
    form_code: str,
    values: Mapping[str, str],
) -> tuple[str | None, str | None]:
    normalized_form_code = _normalize_form_code(form_code)
    if normalized_form_code == "130":
        return values.get("19"), None
    headline_value = (
        values.get("result")
        or values.get("71")
        or values.get("69")
        or values.get("46")
    )
    headline_detail = (
        values.get("compensation_carryforward") or values.get("72")
    )
    return headline_value, headline_detail


def _snapshot_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    raw_filed_on = str(row.get("filed_on") or "")
    normalized_filed_on = row.get("normalized_filed_on")
    return (
        SNAPSHOT_STATUS_RANKS.get(str(row.get("status") or ""), 0),
        1 if normalized_filed_on else 0,
        normalized_filed_on or "",
        raw_filed_on,
        str(row.get("created_at") or ""),
        int(row.get("snapshot_rowid") or 0),
    )


def _extract_modelo130_values(payload: Mapping[str, Any]) -> dict[str, str]:
    filed_values = payload.get("filed_values")
    if isinstance(filed_values, Mapping):
        values = coerce_money_values(filed_values)
        if values:
            return values
    values = payload.get("values")
    if isinstance(values, Mapping):
        return coerce_money_values(values)
    return {}


def _modelo303_extraction_rank(row: Mapping[str, Any]) -> int:
    payload = row["payload"]
    receipt = payload.get("receipt_verification")
    if not isinstance(receipt, Mapping):
        receipt = {}
    values = payload.get("values")
    schema = str(payload.get("value_extraction_schema") or "")
    filed_values = payload.get("filed_values")
    if receipt.get("status") == "matched" and isinstance(values, Mapping):
        return 4
    if schema == "modelo303_v4" and isinstance(filed_values, Mapping):
        return 3
    if isinstance(values, Mapping):
        return 2
    if isinstance(filed_values, Mapping):
        return 1
    return 0


def _extract_modelo303_values(payload: Mapping[str, Any]) -> dict[str, str]:
    receipt = payload.get("receipt_verification")
    if not isinstance(receipt, Mapping):
        receipt = {}
    values = payload.get("values")
    schema = str(payload.get("value_extraction_schema") or "")
    filed_values = payload.get("filed_values")
    if receipt.get("status") == "matched" and isinstance(values, Mapping):
        return coerce_money_values(values)
    if schema == "modelo303_v4" and isinstance(filed_values, Mapping):
        return coerce_money_values(
            filed_values,
            blank_keys=payload.get("blank_casillas") or (),
        )
    if isinstance(values, Mapping):
        return coerce_money_values(values)
    if isinstance(filed_values, Mapping):
        return coerce_money_values(filed_values)
    return {}


def compact_tax_preview(cached: Mapping[str, Any]) -> dict[str, Any]:
    if not cached:
        return {}
    preview = (
        cached.get("tax_arithmetic_preview", {})
        .get("projected_reviewed", {})
    )
    modelo130 = preview.get("modelo130", {})
    modelo303 = preview.get("modelo303", {})
    return {
        "modelo130": {
            "blocked": bool(modelo130.get("blocked")),
            "values": modelo130.get("values", {}),
            "warnings": modelo130.get("warnings", []),
        },
        "modelo303": {
            "blocked": bool(modelo303.get("blocked")),
            "values": modelo303.get("values", {}),
            "warnings": modelo303.get("warnings", []),
        },
    }


def form_preview_from_cache(cached: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    preview = compact_tax_preview(cached)
    preview_as_of = cached.get("as_of") if cached else None
    result: dict[str, dict[str, Any]] = {}
    for form_code, form_key in TAX_FORM_KEYS.items():
        values = coerce_money_values(preview.get(form_key, {}).get("values"))
        headline_value, headline_detail = _headline_fields(form_code, values)
        result[form_key] = {
            "form_code": form_code,
            "display_state": "preview" if values else "unavailable",
            "filed_on": None,
            "snapshot_status": None,
            "snapshot_hash": None,
            "extraction_status": None,
            "values": values,
            "headline_value": headline_value,
            "headline_detail": headline_detail,
            "preview_as_of": preview_as_of if values else None,
        }
    return result


def select_filed_form_snapshot(
    connection: sqlite3.Connection,
    *,
    period_key: str,
    form_code: str,
) -> dict[str, Any] | None:
    normalized_form_code = _normalize_form_code(form_code)
    authoritative_statuses = ", ".join(
        f"'{status}'" for status in AUTHORITATIVE_SNAPSHOT_STATUSES
    )
    rows = connection.execute(
        f"""
        SELECT
            fs.rowid AS snapshot_rowid,
            fs.snapshot_hash,
            fs.filed_on,
            fs.status,
            fs.created_at,
            fs.form_code,
            fs.payload_json
        FROM filing_snapshots fs
        JOIN periods p ON p.period_id = fs.period_id
        WHERE p.period_key = ?
          AND fs.status IN ({authoritative_statuses})
        """,
        (period_key,),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        candidate_form = _normalize_form_code(payload.get("form") or row["form_code"] or "")
        if candidate_form != normalized_form_code:
            continue
        candidates.append(
            {
                "snapshot_rowid": int(row["snapshot_rowid"]),
                "snapshot_hash": str(row["snapshot_hash"] or ""),
                "filed_on": row["filed_on"],
                "normalized_filed_on": _normalize_filed_on(row["filed_on"]),
                "status": str(row["status"] or ""),
                "created_at": str(row["created_at"] or ""),
                "payload": payload,
            }
        )
    if not candidates:
        return None
    ordered = sorted(candidates, key=_snapshot_sort_key, reverse=True)
    authoritative = ordered[0]
    selected = authoritative
    values: dict[str, str] = {}
    if normalized_form_code == "303":
        phase_candidates = (
            [
                row
                for row in ordered
                if row["normalized_filed_on"] == authoritative["normalized_filed_on"]
            ]
            if authoritative["normalized_filed_on"]
            else [authoritative]
        )
        ranked = sorted(
            (
                (
                    _modelo303_extraction_rank(row),
                    _snapshot_sort_key(row),
                    row,
                )
                for row in phase_candidates
            ),
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
        for rank, _, row in ranked:
            if rank <= 0:
                continue
            candidate_values = _extract_modelo303_values(row["payload"])
            if candidate_values:
                selected = row
                values = candidate_values
                break
    else:
        values = _extract_modelo130_values(authoritative["payload"])
    headline_value, headline_detail = _headline_fields(form_code, values)
    payload = selected["payload"] if values else authoritative["payload"]
    return {
        "form_code": form_code,
        "filed_on": authoritative["normalized_filed_on"],
        "snapshot_status": authoritative["status"] or None,
        "snapshot_hash": (
            selected["snapshot_hash"] if values else authoritative["snapshot_hash"]
        )
        or None,
        "extraction_status": _optional_text(payload.get("extraction_status")),
        "values": values,
        "headline_value": headline_value,
        "headline_detail": headline_detail,
        "preview_as_of": None,
    }


def form_results(
    connection: sqlite3.Connection,
    *,
    period_key: str,
    obligations: list[dict[str, Any]],
    cached: Mapping[str, Any],
) -> dict[str, Any]:
    preview_forms = form_preview_from_cache(cached)
    obligations_by_code = {
        str(row.get("obligation_code") or ""): row for row in obligations
    }
    result: dict[str, Any] = {}
    for form_code, form_key in TAX_FORM_KEYS.items():
        obligation = obligations_by_code.get(form_code)
        snapshot = select_filed_form_snapshot(
            connection,
            period_key=period_key,
            form_code=form_code,
        )
        preview_form = dict(preview_forms[form_key])
        if obligation and obligation.get("filing_status") == "filed":
            if snapshot and snapshot["values"]:
                result[form_key] = {
                    **snapshot,
                    "display_state": "filed",
                }
                continue
            if snapshot:
                result[form_key] = {
                    **snapshot,
                    "display_state": "filed_without_values",
                }
                continue
        if snapshot and snapshot["values"]:
            result[form_key] = {
                **snapshot,
                "display_state": "snapshot_only",
            }
            continue
        result[form_key] = preview_form
    return result


def _money_to_minor(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite():
        return None
    return int((amount * 100).to_integral_value())


def _form_result_minor(form_code: str, values: Mapping[str, Any]) -> int | None:
    if form_code == "130":
        return _money_to_minor(values.get("19"))
    return _money_to_minor(
        values.get("71") or values.get("result") or values.get("69")
    )


def _payment_evidence_by_obligation(
    connection: sqlite3.Connection,
    *,
    period_key: str,
) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            pay.payment_id,
            pay.obligation_id,
            pay.paid_on,
            pay.amount_minor,
            pay.currency,
            pay.match_status,
            pay.source_system,
            pay.external_id,
            pay.source_row_json
        FROM payments pay
        JOIN obligations o ON o.obligation_id = pay.obligation_id
        JOIN periods p ON p.period_id = o.period_id
        WHERE p.period_key = ?
        ORDER BY pay.paid_on, pay.payment_id
        """,
        (period_key,),
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["obligation_id"]), []).append(
            tax_settlement_payment_summary(row)
        )

    result: dict[str, dict[str, Any]] = {}
    for obligation_id, payments in grouped.items():
        confirmed = [row for row in payments if row["evidence_ready"]]
        result[obligation_id] = {
            "confirmed_paid_minor": sum(int(row["amount_minor"]) for row in confirmed),
            "latest_confirmed_paid_on": max(
                (str(row["paid_on"]) for row in confirmed),
                default=None,
            ),
            "unconfirmed_payment_count": len(payments) - len(confirmed),
        }
    return result


def _period_phase(period: Mapping[str, Any], as_of: date) -> str:
    starts_on = date.fromisoformat(str(period["starts_on"]))
    ends_on = date.fromisoformat(str(period["ends_on"]))
    if as_of < starts_on:
        return "future"
    if as_of <= ends_on:
        return "current"
    return "past"


def _settlement_status(
    *,
    phase: str,
    payable_minor: int | None,
    confirmed_paid_minor: int,
    unconfirmed_payment_count: int,
    filed: bool,
) -> str:
    if phase == "future":
        return "not_started"
    if payable_minor is None:
        return "undetermined"
    if confirmed_paid_minor > payable_minor:
        return "overpaid"
    if payable_minor == 0:
        return "no_payment_required"
    if confirmed_paid_minor == payable_minor:
        return "paid"
    if confirmed_paid_minor > 0:
        return "partially_paid"
    if unconfirmed_payment_count:
        return "evidence_unavailable"
    if filed:
        return "payment_unconfirmed"
    return "unfiled" if phase == "past" else "planned"


def _tax_form_summary(
    *,
    form_code: str,
    form: Mapping[str, Any],
    preview_form: Mapping[str, Any],
    obligation: Mapping[str, Any] | None,
    payment_evidence: Mapping[str, Mapping[str, Any]],
    phase: str,
) -> dict[str, Any]:
    determination = str((obligation or {}).get("determination") or "unknown")
    filing_status = str((obligation or {}).get("filing_status") or "unknown")
    preview_values = preview_form.get("values") or {}
    filed_values = (
        form.get("values") or {}
        if filing_status == "filed" and form.get("display_state") == "filed"
        else {}
    )
    preview_result_minor = _form_result_minor(form_code, preview_values)
    filed_result_minor = _form_result_minor(form_code, filed_values)

    if phase == "future":
        result_minor = None
        calculation_source = "not_started"
    elif phase == "past" and filed_result_minor is not None:
        result_minor = filed_result_minor
        calculation_source = "filed"
    elif preview_result_minor is not None:
        result_minor = preview_result_minor
        calculation_source = "preview"
    elif filed_result_minor is not None:
        result_minor = filed_result_minor
        calculation_source = "filed"
    else:
        result_minor = None
        calculation_source = "unavailable"

    if determination in {"not_due", "waived"}:
        payable_minor = 0
        calculation_source = "not_required"
    elif determination != "due":
        payable_minor = None
        calculation_source = "unavailable"
    else:
        payable_minor = max(result_minor, 0) if result_minor is not None else None

    obligation_id = str((obligation or {}).get("obligation_id") or "")
    payment = payment_evidence.get(obligation_id) or {}
    confirmed_paid_minor = int(payment.get("confirmed_paid_minor") or 0)
    unconfirmed_payment_count = int(payment.get("unconfirmed_payment_count") or 0)
    selected_values = (
        filed_values if calculation_source == "filed" else preview_values
    )
    generated_credit_minor = _money_to_minor(selected_values.get("72"))
    carryforward_minor = _money_to_minor(
        selected_values.get("compensation_carryforward")
        or selected_values.get("72")
    )
    refund_requested_minor = _money_to_minor(selected_values.get("73"))
    if form_code != "303" or result_minor is None:
        disposition = None
    elif result_minor > 0:
        disposition = "payable"
    elif (refund_requested_minor or 0) > 0:
        disposition = "refund"
    elif result_minor < 0 or (carryforward_minor or 0) > 0:
        disposition = "carryforward"
    else:
        disposition = "none"

    outstanding_minor = (
        max(payable_minor - confirmed_paid_minor, 0)
        if payable_minor is not None
        else None
    )
    overpaid_minor = (
        max(confirmed_paid_minor - payable_minor, 0)
        if payable_minor is not None
        else None
    )
    return {
        "form_code": form_code,
        "determination": determination,
        "filing_status": filing_status,
        "calculation_source": calculation_source,
        "preview_minor": preview_result_minor,
        "filed_minor": filed_result_minor,
        "result_minor": result_minor,
        "payable_minor": payable_minor,
        "confirmed_paid_minor": confirmed_paid_minor,
        "outstanding_minor": outstanding_minor,
        "overpaid_minor": overpaid_minor,
        "settlement_status": _settlement_status(
            phase=phase,
            payable_minor=payable_minor,
            confirmed_paid_minor=confirmed_paid_minor,
            unconfirmed_payment_count=unconfirmed_payment_count,
            filed=filing_status == "filed",
        ),
        "unconfirmed_payment_count": unconfirmed_payment_count,
        "latest_confirmed_paid_on": payment.get("latest_confirmed_paid_on"),
        "filed_on": form.get("filed_on"),
        "direct_debit_cutoff_on": (obligation or {}).get("direct_debit_cutoff_on"),
        "statutory_due_on": (obligation or {}).get("statutory_due_on"),
        "generated_credit_minor": generated_credit_minor,
        "carryforward_minor": carryforward_minor,
        "refund_requested_minor": refund_requested_minor,
        "disposition": disposition,
    }


def build_tax_summary(
    connection: sqlite3.Connection,
    *,
    period: Mapping[str, Any],
    obligations: list[dict[str, Any]],
    tax_forms: Mapping[str, Mapping[str, Any]],
    cached: Mapping[str, Any],
    as_of: date,
) -> tuple[dict[str, Any], dict[str, Any]]:
    phase = _period_phase(period, as_of)
    obligation_by_code = {
        str(row.get("obligation_code") or ""): row for row in obligations
    }
    preview_forms = form_preview_from_cache(cached)
    payment_evidence = _payment_evidence_by_obligation(
        connection,
        period_key=str(period["period_key"]),
    )
    forms = {
        form_code: _tax_form_summary(
            form_code=form_code,
            form=tax_forms.get(form_key) or {},
            preview_form=preview_forms.get(form_key) or {},
            obligation=obligation_by_code.get(form_code),
            payment_evidence=payment_evidence,
            phase=phase,
        )
        for form_code, form_key in TAX_FORM_KEYS.items()
    }
    due_obligations = [
        row for row in obligations if row.get("determination") == "due"
    ]
    unsupported_due_forms = sorted(
        str(row.get("obligation_code"))
        for row in due_obligations
        if str(row.get("obligation_code")) not in TAX_FORM_KEYS
    )
    unresolved_obligations = sorted(
        str(row.get("obligation_code"))
        for row in obligations
        if row.get("determination") == "unknown"
    )
    due_forms = [
        forms[str(row["obligation_code"])]
        for row in due_obligations
        if str(row.get("obligation_code")) in forms
    ]
    complete = (
        phase != "future"
        and not unsupported_due_forms
        and not unresolved_obligations
        and all(row["payable_minor"] is not None for row in due_forms)
    )
    total_payable_minor = (
        sum(int(row["payable_minor"]) for row in due_forms)
        if complete
        else None
    )
    total_confirmed_paid_minor = sum(
        int((payment_evidence.get(str(row["obligation_id"])) or {}).get("confirmed_paid_minor") or 0)
        for row in due_obligations
    )
    total_unconfirmed_payments = sum(
        int((payment_evidence.get(str(row["obligation_id"])) or {}).get("unconfirmed_payment_count") or 0)
        for row in due_obligations
    )
    all_due_filed = bool(due_obligations) and all(
        row.get("filing_status") == "filed" for row in due_obligations
    )
    settlement_status = _settlement_status(
        phase=phase,
        payable_minor=total_payable_minor,
        confirmed_paid_minor=total_confirmed_paid_minor,
        unconfirmed_payment_count=total_unconfirmed_payments,
        filed=all_due_filed,
    )
    sources = {
        str(row["calculation_source"])
        for row in due_forms
        if row["calculation_source"] != "not_required"
    }
    calculation_source = (
        "not_started"
        if phase == "future"
        else "not_required"
        if not due_forms and not unsupported_due_forms and not unresolved_obligations
        else next(iter(sources))
        if len(sources) == 1
        else "mixed"
        if sources
        else "unavailable"
    )
    outstanding_minor = (
        max(total_payable_minor - total_confirmed_paid_minor, 0)
        if total_payable_minor is not None
        else None
    )
    overpaid_minor = (
        max(total_confirmed_paid_minor - total_payable_minor, 0)
        if total_payable_minor is not None
        else None
    )
    period_state = {
        "period_key": str(period["period_key"]),
        "phase": phase,
        "accounting_status": str(period["status"]),
        "starts_on": period["starts_on"],
        "ends_on": period["ends_on"],
        "amendment_period_key": period.get("amendment_period_key"),
        "amendment_reason": period.get("amendment_reason"),
    }
    return period_state, {
        "currency": "EUR",
        "calculation_source": calculation_source,
        "calculated_as_of": cached.get("as_of") if "preview" in sources else None,
        "total_payable_minor": total_payable_minor,
        "total_confirmed_paid_minor": total_confirmed_paid_minor,
        "outstanding_minor": outstanding_minor,
        "overpaid_minor": overpaid_minor,
        "settlement_status": settlement_status,
        "requires_reconciliation": str(period["status"]) == "amended",
        "unsupported_due_forms": unsupported_due_forms,
        "unresolved_obligations": unresolved_obligations,
        "forms": forms,
    }


def period_status(
    connection: sqlite3.Connection,
    period_key: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT
            p.period_key,
            p.status,
            p.starts_on,
            p.ends_on,
            p.amendment_reason,
            amendment.period_key AS amendment_period_key
        FROM periods p
        LEFT JOIN periods amendment
            ON amendment.period_id = p.amendment_period_id
        WHERE p.period_key = ? AND p.period_type = 'quarter'
        """,
        (period_key,),
    ).fetchone()
    if row is None:
        return None
    return {
        "period_key": str(row["period_key"]),
        "status": str(row["status"]),
        "starts_on": row["starts_on"],
        "ends_on": row["ends_on"],
        "amendment_period_key": row["amendment_period_key"],
        "amendment_reason": row["amendment_reason"],
    }


def year_form_results(
    connection: sqlite3.Connection,
    *,
    quarters: Sequence[str],
    load_cached: Callable[[str], Mapping[str, Any]],
    load_obligations: Callable[[str], list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for period_key in quarters:
        period = period_status(connection, period_key)
        obligations = load_obligations(period_key) if period else []
        cached = load_cached(period_key)
        results[period_key] = {
            "period": period,
            "obligations": obligations,
            "forms": form_results(
                connection,
                period_key=period_key,
                obligations=obligations,
                cached=cached,
            ),
        }
    return results
