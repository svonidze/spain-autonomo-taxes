"""Shared accounting operations; no HTTP, CLI dispatch or UI dependency."""

from __future__ import annotations
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Iterable
from ..ledger_db import LedgerDB, open as open_ledger_db
from ..tax_engine import CalculationBlocked, TaxRow
from ..tax_row_loader import load_tax_rows
from ..tax_rules import difficult_expense_rule_for_year


def dashboard(
    *,
    database: Path,
    period: str,
    as_of: date,
    out_dir: Path,
    xolo_modelo130_calculations: Path | None = None,
):
    from ..current_quarter import (
        add_xolo_comparison,
        build_current_quarter_dashboard,
        load_xolo_modelo130_forecast,
        write_current_quarter_dashboard,
    )

    try:
        year_text, quarter_text = period.split("-Q", 1)
        year = int(year_text)
        quarter = int(quarter_text)
    except (ValueError, TypeError) as exc:
        raise ValueError("Dashboard period must use YYYY-QN format") from exc
    if quarter not in {1, 2, 3, 4} or period != f"{year}-Q{quarter}":
        raise ValueError("Dashboard period must use YYYY-QN format")
    with open_ledger_db(database, read_only=True) as db:
        actual_rows = _tax_rows_from_db(
            db, year, mode="production", allow_authoritative_history=True
        )
        projected_rows = _tax_rows_from_db(
            db,
            year,
            mode="production",
            allow_authoritative_history=True,
            include_approved_periods={period},
        )
        rule = difficult_expense_rule_for_year(year)
        dashboard = build_current_quarter_dashboard(
            actual_rows=actual_rows,
            projected_rows=projected_rows,
            period_key=period,
            as_of=as_of,
            difficult_expenses_rate=rule.rate,
            previous_positive_casilla_07=_previous_filed_positive(db, year, quarter),
            previous_negative_carry=_previous_filed_negative_carry(db, year, quarter),
            previous_vat_compensation=_previous_filed_vat_compensation(
                db, year, quarter
            ),
            approved_current_rows=_approved_forecast_rows(db, period),
            obligations=db.list_obligations_with_deadlines(period_key=period),
            period_validation=db.validate_period(
                period, allow_authoritative_history=True
            ),
        )
    if xolo_modelo130_calculations:
        add_xolo_comparison(
            dashboard,
            load_xolo_modelo130_forecast(xolo_modelo130_calculations, period=period),
        )
    outputs = write_current_quarter_dashboard(dashboard, out_dir)
    return (
        {
            "period": period,
            "filing_assessment_performed": False,
            "filing_ready": False,
            "submission_ready": False,
            "outputs": {key: str(path.resolve()) for key, path in outputs.items()},
            "blocking_item_count": len(dashboard["blocking_items"]),
            "expected_item_count": len(dashboard["expected_items"]),
        },
        0,
    )


def _tax_rows_from_db(
    db: LedgerDB,
    year: int,
    *,
    mode: str = "production",
    allow_authoritative_history: bool = False,
    include_approved_periods: Iterable[str] = (),
) -> list[TaxRow]:
    return load_tax_rows(
        db,
        year,
        mode=mode,
        allow_authoritative_history=allow_authoritative_history,
        include_approved_periods=include_approved_periods,
    )


def _approved_forecast_rows(db: LedgerDB, period_key: str) -> list[dict[str, Any]]:
    year = int(period_key[:4])
    grouped: dict[str, dict[str, Any]] = {}
    for raw in db.list_tax_rows(year=year):
        if raw["period_key"] != period_key or raw["lifecycle_status"] != "approved":
            continue
        bucket = grouped.setdefault(
            raw["transaction_id"],
            {
                "transaction_id": raw["transaction_id"],
                "transaction_date": raw["transaction_date"],
                "description": raw["description"],
                "counterparty_name": raw.get("counterparty_name") or "",
                "amount_eur_minor": raw.get("amount_eur_minor"),
                "document_id": raw.get("document_id") or "",
                "document_type": "",
                "document_source_path": "",
                "tax_code": raw.get("tax_code") or "unknown",
                "deductible_irpf_minor": raw.get("deductible_irpf_minor"),
                "deductible_vat_minor": raw.get("deductible_vat_minor"),
                "asset_id": raw.get("asset_id") or "",
            },
        )
        for key in (
            "tax_code",
            "deductible_irpf_minor",
            "deductible_vat_minor",
            "asset_id",
        ):
            value = raw.get(key)
            if value not in {None, "", "unknown"}:
                bucket[key] = value
    for bucket in grouped.values():
        if not bucket["document_id"]:
            continue
        document = db.connection.execute(
            "SELECT document_type, source_path FROM documents WHERE document_id = ?",
            (bucket["document_id"],),
        ).fetchone()
        if document is not None:
            bucket["document_type"] = document["document_type"]
            bucket["document_source_path"] = document["source_path"] or ""
    return sorted(
        grouped.values(),
        key=lambda row: (
            row["transaction_date"],
            row["description"],
            row["transaction_id"],
        ),
    )


def _filed_baseline(db: LedgerDB, period_key: str, form: str) -> dict[str, Any] | None:
    rows = db.connection.execute(
        """
        SELECT fs.payload_json, fs.filed_on, fs.snapshot_hash, fs.status
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
            fs.created_at DESC
        """,
        (period_key,),
    ).fetchall()
    fallback: dict[str, Any] | None = None
    for row in rows:
        payload = json.loads(row["payload_json"])
        payload_form = str(payload.get("form", "")).lower().replace("modelo", "")
        if payload_form == str(form).lower().replace("modelo", ""):
            filed_values = payload.get("filed_values") or payload.get("values") or {}
            candidate = {
                "filed_on": row["filed_on"],
                "snapshot_hash": row["snapshot_hash"],
                "source": payload.get("baseline_kind", "historical"),
                "status": row["status"],
                "filed_values": filed_values,
                "filename": payload.get("filename", ""),
                "value_extraction_schema": payload.get("value_extraction_schema", ""),
            }
            if filed_values:
                return candidate
            if fallback is None:
                fallback = candidate
    return fallback


def _previous_filed_positive(db: LedgerDB, year: int, quarter: int) -> Decimal:
    total = Decimal("0.00")
    for previous_quarter in range(1, quarter):
        baseline = _filed_baseline(db, f"{year}-Q{previous_quarter}", "130")
        if baseline is None:
            continue
        value = Decimal(str(baseline["filed_values"].get("07", "0")))
        total += max(value, Decimal("0.00"))
    return total.quantize(Decimal("0.01"))


def _previous_filed_negative_carry(db: LedgerDB, year: int, quarter: int) -> Decimal:
    available = Decimal("0.00")
    for previous_quarter in range(1, quarter):
        baseline = _filed_baseline(db, f"{year}-Q{previous_quarter}", "130")
        if baseline is None:
            continue
        values = baseline["filed_values"]
        result = Decimal(str(values.get("19", "0")))
        applied = max(Decimal(str(values.get("15", "0"))), Decimal("0.00"))
        available += max(-result, Decimal("0.00"))
        available = max(available - applied, Decimal("0.00"))
    return available.quantize(Decimal("0.01"))


def _previous_filed_vat_compensation(db: LedgerDB, year: int, quarter: int) -> Decimal:
    if quarter == 1:
        previous_period = f"{year - 1}-Q4"
    else:
        previous_period = f"{year}-Q{quarter - 1}"

    value_candidates = _filed_value_candidates(db, previous_period, "303")
    if not value_candidates:
        obligations = db.list_obligations_with_deadlines(period_key=previous_period)
        prior_303 = next(
            (row for row in obligations if str(row["obligation_code"]) == "303"),
            None,
        )
        if prior_303 is not None and prior_303["determination"] == "due":
            raise CalculationBlocked(
                f"Modelo 303 {previous_period} is due but has no filed snapshot; "
                "provide --previous-vat-compensation explicitly"
            )
        return Decimal("0.00")

    for values in value_candidates:
        if "compensation_carryforward" in values:
            return _nonnegative_decimal(values["compensation_carryforward"])

    for values in value_candidates:
        if "87" in values or "72" in values:
            pending_previous = _nonnegative_decimal(values.get("87", "0"))
            current_period = _nonnegative_decimal(values.get("72", "0"))
            return (pending_previous + current_period).quantize(Decimal("0.01"))

    for values in value_candidates:
        if "110" in values or "78" in values:
            opening = _nonnegative_decimal(values.get("110", "0"))
            applied = _nonnegative_decimal(values.get("78", "0"))
            liquidation = Decimal(str(values.get("71", values.get("result", "0"))))
            return (
                max(opening - applied, Decimal("0.00"))
                + max(-liquidation, Decimal("0.00"))
            ).quantize(Decimal("0.01"))

    for values in value_candidates:
        for result_key in ("71", "result", "46"):
            if result_key in values:
                result = Decimal(str(values[result_key]))
                return max(-result, Decimal("0.00")).quantize(Decimal("0.01"))

    raise CalculationBlocked(
        f"Modelo 303 {previous_period} snapshot has no compensation/result casillas; "
        "provide --previous-vat-compensation explicitly"
    )


def _nonnegative_decimal(value: Any) -> Decimal:
    return max(Decimal(str(value)), Decimal("0.00")).quantize(Decimal("0.01"))


def _filed_value_candidates(
    db: LedgerDB,
    period_key: str,
    form: str,
) -> list[dict[str, Any]]:
    rows = db.connection.execute(
        """
        SELECT fs.payload_json
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
            fs.created_at DESC
        """,
        (period_key,),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        payload_form = str(payload.get("form", "")).lower().replace("modelo", "")
        if payload_form != str(form).lower().replace("modelo", ""):
            continue
        values = payload.get("filed_values") or payload.get("values") or {}
        if isinstance(values, dict):
            candidates.append(values)
    return candidates
