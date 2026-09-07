from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Iterable

from .ledger_db import LedgerDB
from .money import cents
from .tax_engine import (
    CalculationBlocked,
    INTRACOMMUNITY_ACQUISITION_CODES,
    OTHER_REVERSE_CHARGE_CODES,
    calculate_modelo303_rows,
)
from .tax_row_loader import load_tax_rows
from .tax_rules import ANNUAL_FORM_CODES


_CLOSED_PERIOD_STATUSES = {"closed", "amended"}
_MONEY_TOLERANCE = Decimal("0.02")
_MODELO303_SETTLEMENT_KEYS = ("110", "78", "87", "71", "72", "73")
_DATA_VALIDATION_KEYS = (
    "blocking_issues",
    "review_documents",
    "review_transactions",
    "target_derived_fx",
    "xolo_recorded_fx_after_cutover",
    "invalid_amount_transactions",
    "out_of_period_transactions",
)


def assess_annual_readiness(
    database: LedgerDB,
    *,
    year: int,
    form_code: str | None = None,
    allow_authoritative_history: bool = False,
) -> dict[str, Any]:
    if form_code is not None and form_code not in ANNUAL_FORM_CODES:
        raise ValueError(f"Unsupported annual form: {form_code}")

    activities = [
        row for row in database.list_business_activities() if _overlaps_year(row, year)
    ]
    expected_quarters = _expected_quarters(activities, year)
    period_rows = {row["period_key"]: row for row in database.list_periods()}
    blockers: list[dict[str, Any]] = []
    quarter_status: list[dict[str, Any]] = []

    if not activities:
        blockers.append({"kind": "business_activity_inventory_missing", "year": year})

    for period_key in expected_quarters:
        period = period_rows.get(period_key)
        if period is None:
            quarter_status.append({"period": period_key, "status": "missing"})
            blockers.append({"kind": "quarter_missing", "period": period_key})
            continue
        status = str(period["status"])
        quarter_status.append({"period": period_key, "status": status})
        if status not in _CLOSED_PERIOD_STATUSES:
            blockers.append(
                {"kind": "quarter_not_closed", "period": period_key, "status": status}
            )
        validation = database.validate_period(
            period_key,
            allow_authoritative_history=allow_authoritative_history,
        )
        categories = _validation_categories(validation, include_obligations=True)
        if categories:
            blockers.append(
                {
                    "kind": "quarter_accounting_unresolved",
                    "period": period_key,
                    "categories": categories,
                }
            )

    annual_key = str(year)
    annual_period = period_rows.get(annual_key)
    if annual_period is None:
        blockers.append({"kind": "annual_period_missing", "period": annual_key})
        annual_status = "missing"
        annual_obligations: list[dict[str, Any]] = []
    else:
        annual_status = str(annual_period["status"])
        annual_validation = database.validate_period(
            annual_key,
            allow_authoritative_history=allow_authoritative_history,
        )
        categories = _validation_categories(annual_validation, include_obligations=False)
        if categories:
            blockers.append(
                {
                    "kind": "annual_accounting_unresolved",
                    "period": annual_key,
                    "categories": categories,
                }
            )
        annual_obligations = database.list_obligations_with_deadlines(
            period_key=annual_key
        )

    asset_validation = database.validate_asset_year(year)
    asset_summary = {
        "ready": bool(asset_validation["ready"]),
        "asset_count": len(asset_validation["assets"]),
        "issue_count": len(asset_validation["issues"]),
        "issue_codes": sorted(
            {str(row["issue_code"]) for row in asset_validation["issues"]}
        ),
    }
    asset_schedule_required = form_code is None or form_code == "100"
    asset_summary["required_for_requested_scope"] = asset_schedule_required
    if asset_schedule_required and not asset_summary["ready"]:
        blockers.append(
            {
                "kind": "annual_asset_schedule_unresolved",
                "year": year,
                "issue_codes": asset_summary["issue_codes"],
            }
        )

    obligations_by_code = {
        str(row["obligation_code"]): row for row in annual_obligations
    }
    requested_forms: Iterable[str] = (
        (form_code,) if form_code is not None else ANNUAL_FORM_CODES
    )
    form_status: list[dict[str, Any]] = []
    for code in requested_forms:
        obligation = obligations_by_code.get(code)
        if obligation is None:
            form_status.append(
                {
                    "form": code,
                    "determination": "missing",
                    "filing_status": "missing",
                }
            )
            blockers.append({"kind": "annual_obligation_missing", "form": code})
            continue
        determination = str(obligation["determination"])
        filing_status = str(obligation["filing_status"])
        form_status.append(
            {
                "form": code,
                "determination": determination,
                "filing_status": filing_status,
                "due_on": obligation.get("due_on"),
            }
        )
        if determination == "unknown":
            blockers.append({"kind": "annual_obligation_unknown", "form": code})

    form_specific_gates: dict[str, Any] = {}
    modelo390_obligation = obligations_by_code.get("390")
    modelo390_requested = form_code == "390" or (
        form_code is None and modelo390_obligation is not None
    )
    if modelo390_requested:
        determination = (
            str(modelo390_obligation["determination"])
            if modelo390_obligation is not None
            else "missing"
        )
        if determination == "not_due":
            modelo390_gates = _modelo390_not_required()
        elif determination != "due":
            modelo390_gates = _modelo390_unresolved_obligation(determination)
        else:
            modelo390_gates = assess_modelo390_category_gates(
                database,
                year=year,
                expected_quarters=expected_quarters,
                allow_authoritative_history=allow_authoritative_history,
            )
        form_specific_gates["390"] = modelo390_gates
        for gate in modelo390_gates["categories"]:
            if gate["status"] == "blocked":
                blockers.append(
                    {
                        "kind": "modelo390_category_unresolved",
                        "form": "390",
                        "category": gate["category"],
                        "reasons": list(gate.get("reasons") or ()),
                    }
                )

    return {
        "schema_version": 2,
        "report_type": "annual_calculation_readiness",
        "year": year,
        "requested_form": form_code,
        "ready": not blockers,
        "expected_quarters": list(expected_quarters),
        "quarters": quarter_status,
        "annual_period": {"period": annual_key, "status": annual_status},
        "asset_year": asset_summary,
        "forms": form_status,
        "form_specific_gates": form_specific_gates,
        "blockers": blockers,
        "calculation_stage": "complete_year" if not blockers else "not_ready",
    }


def annual_readiness_error(report: dict[str, Any]) -> str:
    labels = []
    for blocker in report["blockers"]:
        reference = (
            blocker.get("period")
            or blocker.get("category")
            or blocker.get("form")
            or blocker.get("year")
        )
        labels.append(
            f"{blocker['kind']}:{reference}" if reference is not None else blocker["kind"]
        )
    return "; ".join(labels)


def assess_modelo390_category_gates(
    database: LedgerDB,
    *,
    year: int,
    expected_quarters: Iterable[str],
    allow_authoritative_history: bool = False,
) -> dict[str, Any]:
    periods, inventory_reasons = _modelo303_inventory(
        database,
        expected_quarters=expected_quarters,
    )
    evidence = {
        period: _best_modelo303_evidence(database, period)
        for period in periods
    }
    missing_evidence = [period for period, item in evidence.items() if item is None]
    evidence_reasons = list(inventory_reasons)
    evidence_reasons.extend(
        f"{period} has no final Modelo 303 evidence with usable values"
        for period in missing_evidence
    )
    quarterly_gate = _assess_quarterly_evidence_gate(
        database,
        year=year,
        periods=periods,
        evidence=evidence,
        prerequisite_reasons=evidence_reasons,
        allow_authoritative_history=allow_authoritative_history,
    )

    reverse_charge = _assess_reverse_charge_gate(
        database,
        year=year,
        periods=periods,
        evidence=evidence,
        prerequisite_reasons=evidence_reasons,
        quarterly_totals_ready=quarterly_gate["status"] != "blocked",
        allow_authoritative_history=allow_authoritative_history,
    )
    compensation = _assess_compensation_gate(
        database,
        year=year,
        periods=periods,
        evidence=evidence,
        prerequisite_reasons=evidence_reasons,
    )
    final_settlement = _assess_final_settlement_gate(
        year=year,
        periods=periods,
        evidence=evidence,
        prerequisite_reasons=evidence_reasons,
    )
    categories = [quarterly_gate, reverse_charge, compensation, final_settlement]
    return {
        "ready": all(gate["status"] != "blocked" for gate in categories),
        "periods": list(periods),
        "categories": categories,
    }


def _modelo390_not_required() -> dict[str, Any]:
    categories = [
        _category_gate(category, status="not_required")
        for category in (
            "quarterly_303_evidence",
            "reverse_charge",
            "compensation_carryforward",
            "final_refund_or_compensation",
        )
    ]
    categories[-1]["choice"] = "compensate"
    return {"ready": True, "periods": [], "categories": categories}


def _modelo390_unresolved_obligation(determination: str) -> dict[str, Any]:
    reason = f"Modelo 390 obligation is {determination}; category applicability is unresolved"
    categories = [
        _category_gate(category, status="blocked", reasons=[reason])
        for category in (
            "quarterly_303_evidence",
            "reverse_charge",
            "compensation_carryforward",
            "final_refund_or_compensation",
        )
    ]
    categories[-1]["choice"] = None
    return {"ready": False, "periods": [], "categories": categories}


def _modelo303_inventory(
    database: LedgerDB,
    *,
    expected_quarters: Iterable[str],
) -> tuple[tuple[str, ...], list[str]]:
    periods: list[str] = []
    reasons: list[str] = []
    for period_key in expected_quarters:
        obligations = database.list_obligations_with_deadlines(period_key=period_key)
        obligation = next(
            (
                row
                for row in obligations
                if str(row["obligation_code"]) == "303"
            ),
            None,
        )
        if obligation is None:
            reasons.append(f"{period_key} has no Modelo 303 obligation decision")
            continue
        determination = str(obligation["determination"])
        filing_status = str(obligation["filing_status"])
        if determination == "unknown":
            reasons.append(f"{period_key} Modelo 303 obligation is unknown")
            continue
        if determination == "due" or filing_status == "filed":
            periods.append(period_key)
    if not periods:
        reasons.append("No due or filed Modelo 303 periods were found for Modelo 390")
    return tuple(periods), reasons


def _best_modelo303_evidence(
    database: LedgerDB,
    period_key: str,
) -> dict[str, Any] | None:
    rows = database.connection.execute(
        """
        SELECT fs.snapshot_hash, fs.status, fs.filed_on, fs.created_at,
               fs.rowid AS snapshot_sequence,
               fs.form_code, fs.payload_json
        FROM filing_snapshots fs
        JOIN periods p ON p.period_id = fs.period_id
        WHERE p.period_key = ?
          AND fs.status IN ('baseline', 'filed', 'submitted', 'final')
        ORDER BY
            CASE fs.status
                WHEN 'final' THEN 4
                WHEN 'filed' THEN 3
                WHEN 'submitted' THEN 2
                ELSE 1
            END DESC,
            COALESCE(fs.filed_on, '') DESC,
            fs.created_at DESC,
            fs.rowid DESC
        """,
        (period_key,),
    ).fetchall()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        payload = json.loads(str(row["payload_json"] or "{}"))
        payload_form = str(payload.get("form") or row["form_code"] or "")
        if payload_form.lower().replace("modelo", "").strip() != "303":
            continue
        receipt = payload.get("receipt_verification") or {}
        values = payload.get("values")
        schema = str(payload.get("value_extraction_schema") or "")
        filed_values = payload.get("filed_values")
        if receipt.get("status") == "matched" and isinstance(values, dict):
            rank = 4
            source_type = "matched_filing_receipt"
            selected_values = dict(values)
            explicit_keys = set(selected_values)
        elif schema == "modelo303_v4" and isinstance(filed_values, dict):
            rank = 3
            source_type = "modelo303_v4_extraction"
            selected_values = dict(filed_values)
            explicit_keys = set(selected_values)
            for key in payload.get("blank_casillas") or ():
                explicit_keys.add(str(key))
                selected_values.setdefault(str(key), "0.00")
        elif isinstance(values, dict):
            rank = 2
            source_type = "calculation_snapshot"
            selected_values = dict(values)
            explicit_keys = set(selected_values)
        elif isinstance(filed_values, dict):
            rank = 1
            source_type = "legacy_filed_values"
            selected_values = dict(filed_values)
            explicit_keys = set(selected_values)
        else:
            continue
        candidates.append(
            (
                rank,
                {
                    "snapshot_hash": str(row["snapshot_hash"]),
                    "status": str(row["status"]),
                    "source_type": source_type,
                    "value_extraction_schema": schema,
                    "structural_extraction_status": str(
                        payload.get("structural_extraction_status") or ""
                    ),
                    "settlement_extraction_status": str(
                        payload.get("settlement_extraction_status") or ""
                    ),
                    "values": selected_values,
                    "explicit_keys": explicit_keys,
                },
            )
        )
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _assess_reverse_charge_gate(
    database: LedgerDB,
    *,
    year: int,
    periods: tuple[str, ...],
    evidence: dict[str, dict[str, Any] | None],
    prerequisite_reasons: list[str],
    quarterly_totals_ready: bool,
    allow_authoritative_history: bool,
) -> dict[str, Any]:
    reasons = list(prerequisite_reasons)
    fallbacks: list[str] = []
    annual_service_base = Decimal("0.00")
    reverse_charge_required = False
    try:
        tax_rows = load_tax_rows(
            database,
            year,
            mode="production",
            allow_authoritative_history=allow_authoritative_history,
        )
        for period_key in periods:
            quarter = int(period_key[-1])
            period_rows = [
                row
                for row in tax_rows
                if row.tax_date.year == year
                and ((row.tax_date.month - 1) // 3) + 1 == quarter
                and row.include_modelo303
            ]
            item = evidence.get(period_key)
            if item is None:
                continue

            other_rows = [
                row for row in period_rows if row.tax_code in OTHER_REVERSE_CHARGE_CODES
            ]
            intra_rows = [
                row
                for row in period_rows
                if row.tax_code in INTRACOMMUNITY_ACQUISITION_CODES
            ]
            annual_service_base += sum(
                (
                    row.taxable_base_eur
                    for row in period_rows
                    if row.tax_code in {"eu_service_expense", "non_eu_service_expense"}
                ),
                Decimal("0.00"),
            )
            for rows_for_pair, base_key, vat_key, label in (
                (other_rows, "12", "13", "other reverse charge"),
                (intra_rows, "10", "11", "intra-Community acquisition"),
            ):
                expected_base = cents(
                    sum((row.taxable_base_eur for row in rows_for_pair), Decimal("0.00"))
                )
                expected_vat = cents(
                    sum((row.vat_eur for row in rows_for_pair), Decimal("0.00"))
                )
                actual_base = _evidence_money(item, base_key)
                actual_vat = _evidence_money(item, vat_key)
                pair_required = expected_base != 0 or expected_vat != 0
                reverse_charge_required = reverse_charge_required or pair_required
                if actual_base is not None or actual_vat is not None:
                    _compare_evidence_value(
                        item,
                        base_key,
                        expected_base,
                        period_key=period_key,
                        reasons=reasons,
                    )
                    _compare_evidence_value(
                        item,
                        vat_key,
                        expected_vat,
                        period_key=period_key,
                        reasons=reasons,
                    )
                elif pair_required:
                    if quarterly_totals_ready:
                        fallbacks.append(
                            f"{period_key} {label} uses filed totals 27/45 plus reviewed ledger "
                            f"because direct casillas {base_key}/{vat_key} are not extractable"
                        )
                    else:
                        reasons.append(
                            f"{period_key} lacks direct {label} casillas {base_key}/{vat_key} "
                            "and filed totals do not prove the reviewed ledger values"
                        )
    except (CalculationBlocked, ValueError, InvalidOperation) as exc:
        reasons.append(f"Modelo 303 ledger reconstruction failed: {exc}")

    status = "blocked" if reasons else ("ready" if reverse_charge_required else "not_required")
    return _category_gate(
        "reverse_charge",
        status=status,
        reasons=reasons,
        details={
            "expected_annual_service_base_eur": f"{cents(annual_service_base):.2f}",
            "direct_pair_fallbacks": fallbacks,
        },
    )


def _assess_quarterly_evidence_gate(
    database: LedgerDB,
    *,
    year: int,
    periods: tuple[str, ...],
    evidence: dict[str, dict[str, Any] | None],
    prerequisite_reasons: list[str],
    allow_authoritative_history: bool,
) -> dict[str, Any]:
    reasons = list(prerequisite_reasons)
    try:
        tax_rows = load_tax_rows(
            database,
            year,
            mode="production",
            allow_authoritative_history=allow_authoritative_history,
        )
        for period_key in periods:
            item = evidence.get(period_key)
            if item is None:
                continue
            report = calculate_modelo303_rows(
                tax_rows,
                year=year,
                quarter=int(period_key[-1]),
            )
            for key in ("27", "45"):
                _compare_evidence_value(
                    item,
                    key,
                    Decimal(str(report.values[key])),
                    period_key=period_key,
                    reasons=reasons,
                )
    except (CalculationBlocked, ValueError, InvalidOperation) as exc:
        reasons.append(f"Modelo 303 ledger reconstruction failed: {exc}")
    return _category_gate(
        "quarterly_303_evidence",
        status="blocked" if reasons else "ready",
        reasons=reasons,
        details={
            "periods": list(periods),
            "evidence": [
                _public_evidence_summary(period, item)
                for period, item in evidence.items()
            ],
        },
    )


def _assess_compensation_gate(
    database: LedgerDB,
    *,
    year: int,
    periods: tuple[str, ...],
    evidence: dict[str, dict[str, Any] | None],
    prerequisite_reasons: list[str],
) -> dict[str, Any]:
    reasons = list(prerequisite_reasons)
    chain: list[dict[str, str]] = []
    previous_carry: Decimal | None = None
    if periods and periods[0].endswith("Q1"):
        prior = _best_modelo303_evidence(database, f"{year - 1}-Q4")
        previous_carry = _evidence_carry(prior)

    for index, period_key in enumerate(periods):
        item = evidence.get(period_key)
        if item is None:
            continue
        missing = [key for key in _MODELO303_SETTLEMENT_KEYS if key not in item["explicit_keys"]]
        if missing:
            reasons.append(
                f"{period_key} lacks explicit settlement casillas: {', '.join(missing)}"
            )
            continue
        values = {key: _evidence_money(item, key) for key in _MODELO303_SETTLEMENT_KEYS}
        if any(value is None for value in values.values()):
            reasons.append(f"{period_key} has non-numeric settlement evidence")
            continue
        opening = values["110"]
        applied = values["78"]
        pending = values["87"]
        result = values["71"]
        generated = values["72"]
        refund = values["73"]
        assert opening is not None and applied is not None and pending is not None
        assert result is not None and generated is not None and refund is not None
        if any(value < 0 for value in (opening, applied, pending, generated, refund)):
            reasons.append(f"{period_key} has a negative non-result settlement casilla")
        if previous_carry is not None and abs(opening - previous_carry) > _MONEY_TOLERANCE:
            reasons.append(
                f"{period_key} opening compensation {opening:.2f} does not match "
                f"prior carry {previous_carry:.2f}"
            )
        elif previous_carry is None and index == 0 and opening > _MONEY_TOLERANCE:
            reasons.append(
                f"{period_key} opens with {opening:.2f} compensation but prior Q4 evidence is missing"
            )
        if applied - opening > _MONEY_TOLERANCE:
            reasons.append(f"{period_key} applies more compensation than its opening balance")
        expected_pending = cents(max(opening - applied, Decimal("0.00")))
        if abs(pending - expected_pending) > _MONEY_TOLERANCE:
            reasons.append(
                f"{period_key} casilla 87 is {pending:.2f}; expected {expected_pending:.2f}"
            )
        if generated > 0 and refund > 0:
            reasons.append(f"{period_key} records both compensation and refund")
        if refund > 0 and period_key != f"{year}-Q4":
            reasons.append(f"{period_key} records a refund outside Q4")
        negative_result = cents(max(-result, Decimal("0.00")))
        if result < 0:
            if abs((generated + refund) - negative_result) > _MONEY_TOLERANCE:
                reasons.append(
                    f"{period_key} negative result {negative_result:.2f} is not assigned "
                    "to casilla 72 or 73"
                )
        elif generated > _MONEY_TOLERANCE or refund > _MONEY_TOLERANCE:
            reasons.append(f"{period_key} assigns a non-negative result to compensation/refund")
        previous_carry = cents(pending + generated)
        explicit_carry = _evidence_money(item, "compensation_carryforward")
        if explicit_carry is not None and abs(explicit_carry - previous_carry) > _MONEY_TOLERANCE:
            reasons.append(
                f"{period_key} explicit carry {explicit_carry:.2f} does not match "
                f"casillas 87+72 ({previous_carry:.2f})"
            )
        chain.append(
            {
                "period": period_key,
                "opening": f"{opening:.2f}",
                "applied": f"{applied:.2f}",
                "pending": f"{pending:.2f}",
                "generated": f"{generated:.2f}",
                "refund": f"{refund:.2f}",
                "closing_carry": f"{previous_carry:.2f}",
            }
        )
    return _category_gate(
        "compensation_carryforward",
        status="blocked" if reasons else "ready",
        reasons=reasons,
        details={
            "chain": chain,
            "closing_carry_eur": f"{(previous_carry or Decimal('0.00')):.2f}",
        },
    )


def _assess_final_settlement_gate(
    *,
    year: int,
    periods: tuple[str, ...],
    evidence: dict[str, dict[str, Any] | None],
    prerequisite_reasons: list[str],
) -> dict[str, Any]:
    reasons = list(prerequisite_reasons)
    choice: str | None = None
    required = False
    final_period = periods[-1] if periods else None
    item = evidence.get(final_period) if final_period else None
    if item is not None:
        for key in ("71", "72", "73"):
            if key not in item["explicit_keys"]:
                reasons.append(f"{final_period} lacks explicit casilla {key}")
        result = _evidence_money(item, "71")
        compensation = _evidence_money(item, "72")
        refund = _evidence_money(item, "73")
        if result is not None and compensation is not None and refund is not None:
            required = result < 0
            if result < 0:
                if compensation > 0 and refund == 0:
                    choice = "compensate"
                elif refund > 0 and compensation == 0 and final_period == f"{year}-Q4":
                    choice = "refund"
                else:
                    reasons.append(
                        f"{final_period} does not provide one unambiguous refund/compensation choice"
                    )
            else:
                choice = "compensate"
                if compensation > 0 or refund > 0:
                    reasons.append(
                        f"{final_period} has a non-negative result but non-zero casilla 72/73"
                    )
    status = "blocked" if reasons else ("ready" if required else "not_required")
    gate = _category_gate(
        "final_refund_or_compensation",
        status=status,
        reasons=reasons,
        details={"final_period": final_period, "required": required},
    )
    gate["choice"] = choice
    return gate


def _category_gate(
    category: str,
    *,
    status: str,
    reasons: Iterable[str] = (),
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in {"ready", "not_required", "blocked"}:
        raise ValueError(f"Unsupported category gate status: {status}")
    return {
        "category": category,
        "status": status,
        "reasons": list(dict.fromkeys(reasons)),
        **(details or {}),
    }


def _public_evidence_summary(
    period_key: str,
    evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    if evidence is None:
        return {"period": period_key, "status": "missing"}
    return {
        "period": period_key,
        "status": "available",
        "snapshot_hash": evidence["snapshot_hash"],
        "source_type": evidence["source_type"],
        "value_extraction_schema": evidence["value_extraction_schema"],
    }


def _evidence_money(
    evidence: dict[str, Any] | None,
    key: str,
) -> Decimal | None:
    if evidence is None or key not in evidence["explicit_keys"]:
        return None
    try:
        return cents(Decimal(str(evidence["values"].get(key, "0"))))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _evidence_carry(evidence: dict[str, Any] | None) -> Decimal | None:
    explicit = _evidence_money(evidence, "compensation_carryforward")
    if explicit is not None:
        return explicit
    pending = _evidence_money(evidence, "87")
    generated = _evidence_money(evidence, "72")
    if pending is None or generated is None:
        return None
    return cents(pending + generated)


def _compare_evidence_value(
    evidence: dict[str, Any],
    key: str,
    expected: Decimal,
    *,
    period_key: str,
    reasons: list[str],
) -> None:
    actual = _evidence_money(evidence, key)
    if actual is None:
        reasons.append(f"{period_key} lacks explicit numeric casilla {key}")
        return
    expected = cents(expected)
    if abs(actual - expected) > _MONEY_TOLERANCE:
        reasons.append(
            f"{period_key} casilla {key} is {actual:.2f}; reviewed ledger expects {expected:.2f}"
        )


def _expected_quarters(
    activities: Iterable[dict[str, Any]],
    year: int,
) -> tuple[str, ...]:
    periods = []
    for quarter in range(1, 5):
        quarter_start, quarter_end = _quarter_bounds(year, quarter)
        if any(_overlaps(row, quarter_start, quarter_end) for row in activities):
            periods.append(f"{year}-Q{quarter}")
    return tuple(periods)


def _overlaps_year(activity: dict[str, Any], year: int) -> bool:
    return _overlaps(activity, date(year, 1, 1), date(year, 12, 31))


def _overlaps(activity: dict[str, Any], starts_on: date, ends_on: date) -> bool:
    activity_start = date.fromisoformat(str(activity["starts_on"]))
    activity_end = (
        date.fromisoformat(str(activity["ends_on"]))
        if activity.get("ends_on")
        else date.max
    )
    return activity_start <= ends_on and activity_end >= starts_on


def _quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    start_month = (quarter - 1) * 3 + 1
    end_month = quarter * 3
    end_day = 31 if end_month in {3, 12} else 30
    return date(year, start_month, 1), date(year, end_month, end_day)


def _validation_categories(
    validation: dict[str, Any],
    *,
    include_obligations: bool,
) -> list[str]:
    categories = [key for key in _DATA_VALIDATION_KEYS if validation.get(key)]
    if include_obligations and validation.get("unresolved_obligations"):
        categories.append("unresolved_obligations")
    return categories
