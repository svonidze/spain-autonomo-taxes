from __future__ import annotations

from collections import Counter
from datetime import date
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .ledger_db import LedgerDB
from .money import parse_amount
from .tax_payments import tax_settlement_payment_summary


REQUIRED_INVOICE_CHANNEL_CHECKS = (
    "foreign_business_recipient",
    "usd_amount",
    "vat_place_of_supply",
    "series_transition",
    "pdf_qr_archive",
    "sqlite_round_trip",
)

_ACCOUNTING_BLOCKER_KINDS = {
    "validation_issue",
    "document_review",
    "transaction_review",
    "target_derived_fx",
    "xolo_recorded_fx_after_cutover",
    "invalid_amount",
    "out_of_period",
    "supplier_invoice_missing",
    "amortization_asset_link_missing",
    "calendar_deadline_mismatch",
    "confirmed_deadline_missing",
}
_NON_INVOICE_PROOF_TYPES = {"social_security_evidence", "bank_fee_evidence"}


def build_shadow_close_report(
    *,
    period: Mapping[str, Any],
    as_of: date,
    dashboard: Mapping[str, Any],
    aeat_projection: Mapping[str, Any],
    payment_state: Mapping[str, Any],
    offboarding_verification: Mapping[str, Any] | None,
    invoice_channel_assessment: Mapping[str, Any] | None,
    operational_acceptance: Mapping[str, Any] | None = None,
    required_tax_settlements: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    period_ended = as_of > date.fromisoformat(str(period["ends_on"]))
    dashboard_blockers = [dict(row) for row in dashboard.get("blocking_items", [])]
    accounting_items = [
        row for row in dashboard_blockers if row.get("kind") in _ACCOUNTING_BLOCKER_KINDS
    ]
    workflow_items = [
        row
        for row in dashboard_blockers
        if row.get("kind") in {"approved_not_posted", "future_posted_after_as_of"}
    ]
    expected_items = [
        dict(row)
        for row in dashboard.get("expected_items", [])
        if row.get("kind") == "approved_forecast_pending"
    ]
    pending_references = {str(row.get("reference", "")) for row in expected_items}
    accounting_items.extend(
        row
        for row in dashboard_blockers
        if row.get("kind") == "obligation" and row.get("detail") == "unknown"
    )

    aeat_blockers = [dict(row) for row in aeat_projection.get("blockers", [])]
    effective_aeat_blockers = [
        row
        for row in aeat_blockers
        if not (
            row.get("code") == "approved_not_posted"
            and str(row.get("reference", "")) in pending_references
        )
    ]
    for row in aeat_blockers:
        if row.get("code") == "approved_not_posted":
            continue
        accounting_items.append(
            {
                "kind": f"aeat_{row.get('code', 'mapping_blocker')}",
                "reference": row.get("reference", ""),
                "detail": row.get("message", ""),
            }
        )
    accounting_items = _dedupe_items(accounting_items)
    workflow_items = _dedupe_items(workflow_items)

    due_unfiled = [
        _obligation_summary(row)
        for row in dashboard.get("obligations", [])
        if row.get("determination") == "due"
        and row.get("filing_status") not in {"filed", "waived"}
    ]
    unknown_obligations = [
        _obligation_summary(row)
        for row in dashboard.get("obligations", [])
        if row.get("determination") == "unknown"
    ]

    accounting_ready = not accounting_items
    posting_ready = not workflow_items
    aeat_data_ready = bool(aeat_projection.get("data_projection_ready")) or bool(
        aeat_blockers and not effective_aeat_blockers
    )
    payment_ready = bool(payment_state.get("ready"))
    archive_ready = bool(
        offboarding_verification and offboarding_verification.get("ok")
    )
    invoice_channel = _normalize_invoice_channel_assessment(
        invoice_channel_assessment
    )
    invoice_channel_ready = bool(invoice_channel["ready"])
    operational_proof = _normalize_operational_acceptance(operational_acceptance)
    operational_proof_ready = bool(operational_proof["ready"])
    tax_settlements = _normalize_required_tax_settlements(required_tax_settlements)
    tax_settlements_ready = bool(tax_settlements["ready"])
    filing_ready = bool(
        period_ended
        and accounting_ready
        and posting_ready
        and aeat_data_ready
        and not unknown_obligations
    )
    cutover_ready = bool(
        accounting_ready
        and posting_ready
        and aeat_data_ready
        and archive_ready
        and operational_proof_ready
        and tax_settlements_ready
    )

    obligations_status = "ready"
    if unknown_obligations:
        obligations_status = "blocked"
    elif due_unfiled:
        obligations_status = "action_required" if period_ended else "expected_pending"

    return {
        "schema_version": 3,
        "report_type": "quarter_shadow_close_readiness",
        "period": str(period["period_key"]),
        "as_of": as_of.isoformat(),
        "period_state": {
            "starts_on": period["starts_on"],
            "ends_on": period["ends_on"],
            "ledger_status": period["status"],
            "period_ended_as_of": period_ended,
        },
        "summary": {
            "accounting_data_ready": accounting_ready,
            "posting_ready": posting_ready,
            "aeat_data_projection_ready": aeat_data_ready,
            "payment_reconciliation_ready": payment_ready,
            "payment_reconciliation_required": False,
            "offboarding_archive_ready": archive_ready,
            "operational_acceptance_ready": operational_proof_ready,
            "operational_acceptance_required_for_cutover": True,
            "required_tax_settlements_ready": tax_settlements_ready,
            "required_tax_settlements_required_for_cutover": bool(
                tax_settlements["required"]
            ),
            "invoice_channel_ready": invoice_channel_ready,
            "invoice_channel_required_for_cutover": False,
            "filing_ready": filing_ready,
            "cutover_ready": cutover_ready,
        },
        "gates": {
            "accounting_data": {
                "status": "ready" if accounting_ready else "blocked",
                "items": accounting_items,
            },
            "posting": {
                "status": "ready" if posting_ready else "in_progress",
                "items": workflow_items,
                "expected_pending": expected_items,
            },
            "obligations": {
                "status": obligations_status,
                "due_unfiled": due_unfiled,
                "unknown": unknown_obligations,
                "note": (
                    "Due-but-unfiled forms are expected while the quarter is open."
                    if due_unfiled and not period_ended
                    else ""
                ),
            },
            "aeat_books": {
                "status": "ready" if aeat_data_ready else "blocked",
                "counts": dict(aeat_projection.get("counts", {})),
                "blocker_counts": dict(
                    Counter(str(row.get("code", "unknown")) for row in aeat_blockers)
                ),
                "blockers": aeat_blockers,
                "effective_blockers": effective_aeat_blockers,
                "expected_pending": [
                    row for row in aeat_blockers if row not in effective_aeat_blockers
                ],
                "xlsx_generation_supported": bool(
                    aeat_projection.get("xlsx_generation_supported")
                ),
            },
            "payments": {**dict(payment_state), "required": False},
            "offboarding_archive": (
                {"status": "not_checked", "ready": False}
                if offboarding_verification is None
                else {
                    **dict(offboarding_verification),
                    "status": "ready" if archive_ready else "blocked",
                    "ready": archive_ready,
                }
            ),
            "operational_acceptance": operational_proof,
            "required_tax_settlements": tax_settlements,
            "invoice_channel": invoice_channel,
        },
        "quarter_totals": {
            "posted_actual": dict(dashboard.get("posted_actual", {})),
            "approved_forecast_delta": dict(
                dashboard.get("approved_forecast_delta", {})
            ),
            "projected_total": dict(dashboard.get("projected_total", {})),
        },
        "next_actions": _next_actions(
            accounting_items=accounting_items,
            workflow_items=workflow_items,
            payment_ready=payment_ready,
            archive_ready=archive_ready,
            operational_acceptance=operational_proof,
            required_tax_settlements=tax_settlements,
            invoice_channel=invoice_channel,
            due_unfiled=due_unfiled,
            period_ended=period_ended,
        ),
    }


def summarize_required_tax_settlements(
    database: LedgerDB,
    *,
    selectors: Iterable[tuple[str, str]],
) -> dict[str, Any]:
    unique_selectors: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for period_key, form in selectors:
        key = (str(period_key), str(form))
        if key not in seen:
            seen.add(key)
            unique_selectors.append(key)

    if not unique_selectors:
        return {
            "status": "not_required",
            "ready": True,
            "required": False,
            "requirements": [],
            "missing_settlements": [],
        }

    requirements: list[dict[str, Any]] = []
    for period_key, form in unique_selectors:
        selector = f"{period_key}:{form}"
        obligation = database.connection.execute(
            """
            SELECT o.obligation_id, o.obligation_code, o.determination,
                   o.filing_status, o.filed_at, p.period_key
            FROM obligations o
            JOIN periods p ON p.period_id = o.period_id
            WHERE p.period_key = ? AND o.obligation_code = ?
            """,
            (period_key, form),
        ).fetchone()
        if obligation is None:
            requirements.append(
                {
                    "selector": selector,
                    "status": "obligation_not_found",
                    "ready": False,
                    "obligation_id": None,
                    "payments": [],
                }
            )
            continue

        base = {
            "selector": selector,
            "obligation_id": obligation["obligation_id"],
            "determination": obligation["determination"],
            "filing_status": obligation["filing_status"],
            "filed_at": obligation["filed_at"],
        }
        if obligation["determination"] != "due":
            requirements.append(
                {**base, "status": "obligation_not_due", "ready": False, "payments": []}
            )
            continue
        if obligation["filing_status"] != "filed":
            requirements.append(
                {**base, "status": "obligation_not_filed", "ready": False, "payments": []}
            )
            continue

        rows = database.connection.execute(
            """
            SELECT payment_id, paid_on, amount_minor, currency, match_status,
                   source_system, external_id, source_row_json
            FROM payments
            WHERE obligation_id = ?
            ORDER BY paid_on, payment_id
            """,
            (obligation["obligation_id"],),
        ).fetchall()
        payments = [tax_settlement_payment_summary(row) for row in rows]
        evidenced_amount_minor = sum(
            int(row["amount_minor"]) for row in payments if row["evidence_ready"]
        )
        expected_amount_minor, amount_source_status = _filed_payment_amount_minor(
            database,
            period_key=period_key,
            form=form,
        )
        has_evidence = any(row["evidence_ready"] for row in payments)
        amount_matches = (
            expected_amount_minor is None
            or evidenced_amount_minor == expected_amount_minor
        )
        ready = (
            has_evidence
            and amount_source_status not in {"conflict", "missing"}
            and amount_matches
        )
        status = (
            "ready"
            if ready
            else "payment_missing"
            if not payments
            else "payment_evidence_missing"
            if not has_evidence
            else "filed_amount_conflict"
            if amount_source_status == "conflict"
            else "filed_amount_missing"
            if amount_source_status == "missing"
            else "payment_amount_mismatch"
        )
        requirements.append(
            {
                **base,
                "status": status,
                "ready": ready,
                "expected_amount_minor": expected_amount_minor,
                "evidenced_amount_minor": evidenced_amount_minor,
                "amount_check_status": (
                    "pending_payment"
                    if not has_evidence
                    else "conflict"
                    if amount_source_status == "conflict"
                    else "matched"
                    if expected_amount_minor is not None and amount_matches
                    else "mismatch"
                    if expected_amount_minor is not None
                    else amount_source_status
                ),
                "payments": payments,
            }
        )

    missing = [
        {"selector": row["selector"], "reason": row["status"]}
        for row in requirements
        if not row["ready"]
    ]
    return {
        "status": "ready" if not missing else "blocked",
        "ready": not missing,
        "required": True,
        "requirements": requirements,
        "missing_settlements": missing,
    }


def _filed_payment_amount_minor(
    database: LedgerDB,
    *,
    period_key: str,
    form: str,
) -> tuple[int | None, str]:
    payable_key = {"130": "19"}.get(form)
    if payable_key is None:
        return None, "not_available"
    rows = database.connection.execute(
        """
        SELECT fs.form_code, fs.payload_json
        FROM filing_snapshots fs
        JOIN periods p ON p.period_id = fs.period_id
        WHERE p.period_key = ?
          AND fs.status IN ('baseline', 'filed', 'submitted', 'final')
        """,
        (period_key,),
    ).fetchall()
    amounts: set[int] = set()
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or ""))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        payload_form = str(row["form_code"] or payload.get("form") or "").strip()
        if payload_form != form:
            continue
        filed_values = payload.get("filed_values")
        if not isinstance(filed_values, Mapping) or payable_key not in filed_values:
            continue
        try:
            amount_minor = int(parse_amount(str(filed_values[payable_key])) * 100)
        except ValueError:
            continue
        amounts.add(amount_minor)
    if len(amounts) > 1:
        return None, "conflict"
    if not amounts:
        return None, "missing"
    return next(iter(amounts)), "available"


def summarize_operational_acceptance(
    database: LedgerDB,
    *,
    proof_since: date,
    as_of: date,
) -> dict[str, Any]:
    if proof_since > as_of:
        raise ValueError("Operational proof start date cannot be after as-of date")
    rows = database.connection.execute(
        """
        SELECT t.transaction_id, t.transaction_date, t.lifecycle_status,
               d.document_type, d.document_number, d.source_path,
               COALESCE(ib.source_name, '') AS import_source,
               COALESCE(ib.batch_key, '') AS import_batch_key,
               COALESCE(GROUP_CONCAT(tt.notes, ' | '), '') AS treatment_notes
        FROM transactions t
        JOIN documents d ON d.document_id = t.document_id
        LEFT JOIN import_batches ib ON ib.import_batch_id = d.import_batch_id
        LEFT JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
        WHERE t.entry_type = 'expense'
          AND t.lifecycle_status IN ('posted', 'included_in_snapshot')
          AND t.transaction_date BETWEEN ? AND ?
        GROUP BY t.transaction_id, t.transaction_date, t.lifecycle_status,
                 d.document_type, d.document_number, d.source_path,
                 ib.source_name, ib.batch_key
        ORDER BY t.transaction_date, t.transaction_id
        """,
        (proof_since.isoformat(), as_of.isoformat()),
    ).fetchall()
    records = [dict(row) for row in rows]
    independent = [row for row in records if not _is_xolo_derived(row)]
    supplier_rows = [
        row for row in independent if row["document_type"] == "expense_invoice"
    ]
    non_invoice_rows = [
        row for row in independent if row["document_type"] in _NON_INVOICE_PROOF_TYPES
    ]
    operational_rows = supplier_rows + non_invoice_rows
    missing: list[str] = []
    if not operational_rows:
        missing.append("posted_independent_expense")
    ready = not missing
    return {
        "status": "ready" if ready else "in_progress",
        "ready": ready,
        "required": True,
        "proof_since": proof_since.isoformat(),
        "checked_through": as_of.isoformat(),
        "supplier_expenses": [_proof_row(row) for row in supplier_rows],
        "non_invoice_expenses": [_proof_row(row) for row in non_invoice_rows],
        "operational_expenses": [_proof_row(row) for row in operational_rows],
        "excluded_xolo_derived_count": len(records) - len(independent),
        "missing_proofs": missing,
    }


def summarize_payment_state(
    database: LedgerDB,
    *,
    period: Mapping[str, Any],
    as_of: date,
) -> dict[str, Any]:
    period_key = str(period["period_key"])
    raw_batches = database.connection.execute(
        """
        SELECT import_batch_id, notes
        FROM import_batches
        WHERE batch_key LIKE 'zenmoney:%'
        ORDER BY imported_at, import_batch_id
        """,
    ).fetchall()
    batches = [
        parsed
        for row in raw_batches
        if (parsed := _zenmoney_batch_coverage(row["notes"])) is not None
        and parsed["period_key"] == period_key
    ]
    period_start = date.fromisoformat(str(period["starts_on"]))
    period_end = date.fromisoformat(str(period["ends_on"]))
    required_through = min(max(as_of, period_start), period_end)
    for batch in batches:
        starts_on = _optional_iso_date(batch.get("source_starts_on"))
        ends_on = _optional_iso_date(batch.get("source_ends_on"))
        batch["coverage_complete"] = bool(
            starts_on
            and ends_on
            and starts_on <= period_start
            and ends_on >= required_through
        )
    coverage_ready = any(batch["coverage_complete"] for batch in batches)
    rows = database.connection.execute(
        """
        SELECT payment_id, match_status, source_system
        FROM payments
        WHERE paid_on BETWEEN ? AND ?
        ORDER BY paid_on, payment_id
        """,
        (period["starts_on"], period["ends_on"]),
    ).fetchall()
    unmatched = [
        row["payment_id"] for row in rows if row["match_status"] != "exact"
    ]
    ready = coverage_ready and not unmatched
    return {
        "status": (
            "ready"
            if ready
            else "not_populated"
            if not batches
            else "coverage_incomplete"
            if not coverage_ready
            else "blocked"
        ),
        "ready": ready,
        "zenmoney_import_batches": len(batches),
        "coverage_required_through": required_through.isoformat(),
        "coverage_batches": batches,
        "payment_count": len(rows),
        "unmatched_payment_ids": unmatched,
        "source_counts": dict(Counter(row["source_system"] or "unknown" for row in rows)),
        "required": False,
        "note": "ZenMoney and bank matching are optional corroboration, not a tax-recognition source or filing gate.",
    }


def _zenmoney_batch_coverage(notes: str | None) -> dict[str, Any] | None:
    text = str(notes or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"Period (\d{4}-Q[1-4]);", text)
        if match is None:
            return None
        return {
            "period_key": match.group(1),
            "source_starts_on": None,
            "source_ends_on": None,
            "evidence_format": "legacy_without_coverage",
        }
    if not isinstance(payload, dict) or not payload.get("period_key"):
        return None
    return {
        "period_key": str(payload["period_key"]),
        "source_starts_on": payload.get("source_starts_on"),
        "source_ends_on": payload.get("source_ends_on"),
        "business_accounts": payload.get("business_accounts", []),
        "evidence_format": "structured_v1",
    }


def _optional_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def load_invoice_channel_assessment(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invoice-channel assessment must be a JSON object")
    payload["source_path"] = str(source.resolve())
    return payload


def write_shadow_close_report(
    report: Mapping[str, Any], output_dir: str | Path
) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "shadow-close.json"
    markdown_path = root / "shadow-close.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def _normalize_invoice_channel_assessment(
    assessment: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if assessment is None:
        return {
            "status": "not_assessed",
            "ready": False,
            "missing_checks": list(REQUIRED_INVOICE_CHANNEL_CHECKS),
        }
    checks = assessment.get("checks")
    if not isinstance(checks, Mapping):
        checks = {}
    normalized_checks = {
        key: str(checks.get(key, "not_tested")).strip().lower()
        for key in REQUIRED_INVOICE_CHANNEL_CHECKS
    }
    missing = [key for key, value in normalized_checks.items() if value != "passed"]
    return {
        "status": "ready" if not missing else "blocked",
        "ready": not missing,
        "checked_on": assessment.get("checked_on"),
        "source_path": assessment.get("source_path"),
        "checks": normalized_checks,
        "missing_checks": missing,
        "notes": assessment.get("notes", ""),
    }


def _normalize_operational_acceptance(
    acceptance: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if acceptance is None:
        return {
            "status": "not_checked",
            "ready": False,
            "required": True,
            "supplier_expenses": [],
            "non_invoice_expenses": [],
            "operational_expenses": [],
            "excluded_xolo_derived_count": 0,
            "missing_proofs": ["posted_independent_expense"],
        }
    normalized = dict(acceptance)
    normalized["ready"] = bool(acceptance.get("ready"))
    normalized["required"] = True
    normalized["status"] = "ready" if normalized["ready"] else str(
        acceptance.get("status") or "in_progress"
    )
    normalized.setdefault("supplier_expenses", [])
    normalized.setdefault("non_invoice_expenses", [])
    normalized.setdefault(
        "operational_expenses",
        [
            *normalized["supplier_expenses"],
            *normalized["non_invoice_expenses"],
        ],
    )
    normalized.setdefault("excluded_xolo_derived_count", 0)
    normalized.setdefault("missing_proofs", [])
    return normalized


def _normalize_required_tax_settlements(
    settlements: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if settlements is None:
        return {
            "status": "not_required",
            "ready": True,
            "required": False,
            "requirements": [],
            "missing_settlements": [],
        }
    normalized = dict(settlements)
    normalized["required"] = bool(settlements.get("required"))
    normalized["ready"] = (
        bool(settlements.get("ready")) if normalized["required"] else True
    )
    normalized["status"] = (
        "not_required"
        if not normalized["required"]
        else "ready"
        if normalized["ready"]
        else str(settlements.get("status") or "blocked")
    )
    normalized.setdefault("requirements", [])
    normalized.setdefault("missing_settlements", [])
    return normalized


def _is_xolo_derived(row: Mapping[str, Any]) -> bool:
    lineage = " | ".join(
        str(row.get(key) or "")
        for key in (
            "source_path",
            "import_source",
            "import_batch_key",
            "treatment_notes",
        )
    ).lower()
    return any(
        marker in lineage
        for marker in (
            "xolo evidence archive",
            "xolo export",
            "xolo-source-books",
            "xolo_source_book",
            "source_book_line_id=",
        )
    )


def _proof_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "transaction_id": row["transaction_id"],
        "transaction_date": row["transaction_date"],
        "document_type": row["document_type"],
        "document_number": row["document_number"],
        "lifecycle_status": row["lifecycle_status"],
    }


def _obligation_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "form": row.get("obligation_code"),
        "determination": row.get("determination"),
        "filing_status": row.get("filing_status"),
        "internal_due_on": row.get("internal_due_on"),
        "direct_debit_cutoff_on": row.get("direct_debit_cutoff_on"),
        "statutory_due_on": row.get("due_on"),
    }


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        key = (str(item.get("kind", "")), str(item.get("reference", "")))
        unique[key] = item
    return [unique[key] for key in sorted(unique)]


def _next_actions(
    *,
    accounting_items: list[dict[str, Any]],
    workflow_items: list[dict[str, Any]],
    payment_ready: bool,
    archive_ready: bool,
    operational_acceptance: Mapping[str, Any],
    required_tax_settlements: Mapping[str, Any],
    invoice_channel: Mapping[str, Any],
    due_unfiled: list[dict[str, Any]],
    period_ended: bool,
) -> list[str]:
    actions: list[str] = []
    if accounting_items:
        actions.append("Resolve the listed accounting and AEAT mapping blockers.")
    if workflow_items:
        actions.append("Post approved forecast rows only after their evidence and review are complete.")
    if not payment_ready:
        actions.append("Optional: use ZenMoney or bank data to corroborate selected ledger payments.")
    if not archive_ready:
        actions.append("Build and verify the complete Xolo offboarding evidence manifest.")
    if not operational_acceptance.get("ready"):
        missing = set(operational_acceptance.get("missing_proofs") or [])
        if "posted_independent_expense" in missing:
            actions.append(
                "Post one independently reviewed expense whose lineage does not come from Xolo."
            )
    for missing in required_tax_settlements.get("missing_settlements") or []:
        selector = missing.get("selector", "unknown")
        reason = missing.get("reason", "unresolved")
        actions.append(
            f"Resolve required tax settlement {selector}: {_settlement_reason(reason)}."
        )
    if not invoice_channel.get("ready"):
        actions.append(
            "Before 2027-07-01, migrate manual invoice issuance to a reviewed RRSIF-compliant SIF."
        )
    if due_unfiled:
        forms = ", ".join(str(row["form"]) for row in due_unfiled)
        if period_ended:
            actions.append(f"Prepare and submit due forms after final review: {forms}.")
        else:
            actions.append(f"Keep forecast calculations current for expected forms: {forms}.")
    return actions


def _settlement_reason(reason: Any) -> str:
    return {
        "obligation_not_found": "the obligation is missing from the ledger",
        "obligation_not_due": "the obligation is not classified as due",
        "obligation_not_filed": "the obligation is not recorded as filed",
        "payment_missing": "no linked payment has been recorded",
        "payment_evidence_missing": "the linked payment lacks verified hash-archived evidence",
        "filed_amount_conflict": "filed snapshots disagree on the payable amount",
        "filed_amount_missing": "the filed snapshot does not contain a usable payable amount",
        "payment_amount_mismatch": "the evidenced payment total does not match the filed payable amount",
    }.get(str(reason), str(reason))


def _render_markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    gates = report["gates"]
    lines = [
        f"# {report['period']} shadow-close readiness",
        "",
        f"As of: {report['as_of']}.",
        "",
        "| Outcome | Ready | Cutover role |",
        "| --- | --- | --- |",
        f"| Accounting data | {_yes_no(summary['accounting_data_ready'])} | required |",
        f"| Filing | {_yes_no(summary['filing_ready'])} | informational |",
        f"| Xolo archive | {_yes_no(summary['offboarding_archive_ready'])} | required |",
        (
            f"| Live operating proof | {_yes_no(summary['operational_acceptance_ready'])} | "
            f"{_cutover_role(summary['operational_acceptance_required_for_cutover'])} |"
        ),
        (
            "| Required tax settlements | "
            f"{_required_gate_label(gates['required_tax_settlements'])} | "
            f"{_cutover_role(summary['required_tax_settlements_required_for_cutover'])} |"
        ),
        (
            f"| Replacement invoice channel | {_yes_no(summary['invoice_channel_ready'])} | "
            f"{_cutover_role(summary['invoice_channel_required_for_cutover'])} |"
        ),
        f"| Cutover from Xolo | {_yes_no(summary['cutover_ready'])} | result |",
        "",
        "## Gate status",
        "",
        "| Gate | Status | Items |",
        "| --- | --- | ---: |",
        f"| Accounting data | {gates['accounting_data']['status']} | {len(gates['accounting_data']['items'])} |",
        f"| Posting | {gates['posting']['status']} | {len(gates['posting']['items'])} |",
        f"| Obligations | {gates['obligations']['status']} | {len(gates['obligations']['due_unfiled']) + len(gates['obligations']['unknown'])} |",
        f"| AEAT books | {gates['aeat_books']['status']} | {gates['aeat_books']['counts'].get('blockers', 0)} |",
        f"| Payments (optional) | {gates['payments']['status']} | {len(gates['payments']['unmatched_payment_ids'])} |",
        f"| Xolo archive | {gates['offboarding_archive']['status']} | 0 |",
        f"| Live operating proof | {gates['operational_acceptance']['status']} | {len(gates['operational_acceptance']['missing_proofs'])} |",
        f"| Required tax settlements | {gates['required_tax_settlements']['status']} | {len(gates['required_tax_settlements']['missing_settlements'])} |",
        f"| Invoice channel | {gates['invoice_channel']['status']} | {len(gates['invoice_channel']['missing_checks'])} |",
        "",
        "## Accounting blockers",
        "",
    ]
    if gates["accounting_data"]["items"]:
        for item in gates["accounting_data"]["items"]:
            detail = f": {item.get('detail')}" if item.get("detail") else ""
            lines.append(f"- {item.get('kind')} {item.get('reference')}{detail}")
    else:
        lines.append("None.")
    lines.extend(["", "## Expected filing work", ""])
    due = gates["obligations"]["due_unfiled"]
    if due:
        for row in due:
            lines.append(
                f"- Modelo {row['form']}: internal {row.get('internal_due_on') or ''}, "
                f"direct debit {row.get('direct_debit_cutoff_on') or ''}, "
                f"statutory {row.get('statutory_due_on') or ''}."
            )
    else:
        lines.append("None.")
    lines.extend(["", "## Next actions", ""])
    lines.extend(f"- {action}" for action in report["next_actions"])
    lines.append("")
    return "\n".join(lines)


def _yes_no(value: Any) -> str:
    return "yes" if value else "no"


def _cutover_role(required: Any) -> str:
    return "required" if required else "informational"


def _required_gate_label(gate: Mapping[str, Any]) -> str:
    return "not required" if not gate.get("required") else _yes_no(gate.get("ready"))
