from __future__ import annotations

import csv
from collections import Counter
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from uuid import UUID, uuid5

from .ledger_db import LedgerDB
from .money import parse_amount
from .tax_rules import (
    ANNUAL_FORM_CODES,
    FORM_RULES,
    QUARTERLY_FORM_CODES,
    recognize_tax_form_filename,
)


MIGRATION_NAMESPACE = UUID("adca0a24-7f65-4bde-9251-558c3d820889")
QUARTERLY_BOOK_TYPES = {
    "ingresos_book": "income",
    "gastos_book": "expense",
    "compras_gastos_book": "expense",
}
ANNUAL_ASSET_BOOK_TYPES = {"bienes_inversion_book", "bienes_inversion_or_asset_schedule"}
UNRESOLVED_RECONCILIATION_STATUSES = {
    "amount_mismatch",
    "pending_confirmation",
    "deductibility_pending_confirmation",
    "missing_locally",
}
QUARTER_KEY_RE = re.compile(r"^20\d{2}-Q[1-4]$", re.IGNORECASE)
DATE_IN_FILE_RE = re.compile(r"20\d{2}-Q[1-4]", re.IGNORECASE)


def migrate_xolo_history(
    db: LedgerDB,
    source_book_csv: str | Path,
    reconciliation_csv: str | Path | None = None,
    xolo_calculations_csv: str | Path | None = None,
    modelo130_reconciliation_csv: str | Path | None = None,
    document_inventory_csv: str | Path | None = None,
) -> dict[str, Any]:
    source_path = Path(source_book_csv)
    source_rows = _load_rows(source_path)
    source_batch = db.add_import_batch(
        source_name="xolo_source_book_rows",
        source_hash=_file_hash(source_path),
        batch_key=source_path.name,
        notes=f"Deterministic history migration from {source_path}",
    )

    counts: Counter[str] = Counter()
    counts["import_batches"] = 1
    issues: list[dict[str, str]] = []

    for row in source_rows:
        status = (row.get("import_status") or "imported").strip().lower()
        if status != "imported":
            counts["skipped_source_rows"] += 1
            continue

        source_type = row.get("source_book_type", "").strip()
        if source_type in QUARTERLY_BOOK_TYPES:
            _import_quarterly_row(db, row=row, import_batch_id=source_batch["import_batch_id"])
            counts["quarterly_rows"] += 1
            counts["documents"] += 1
            counts["transactions"] += 1
            counts["tax_treatments"] += 1
            continue

        if source_type in ANNUAL_ASSET_BOOK_TYPES and _is_annual_asset_row(row):
            schedule_count = _import_annual_asset_row(
                db,
                row=row,
                import_batch_id=source_batch["import_batch_id"],
            )
            counts["annual_asset_rows"] += 1
            counts["documents"] += 1
            counts["assets"] += 1
            counts["amortization_entries"] += 1
            counts["derived_quarter_schedule_rows"] += schedule_count
            continue

        counts["ignored_source_rows"] += 1

    if reconciliation_csv is not None:
        reconciliation_path = Path(reconciliation_csv)
        reconciliation_rows = _load_rows(reconciliation_path)
        reconciliation_batch = db.add_import_batch(
            source_name="xolo_expense_reconcile",
            source_hash=_file_hash(reconciliation_path),
            batch_key=reconciliation_path.name,
            notes=f"Deterministic reconciliation issue import from {reconciliation_path}",
        )
        counts["import_batches"] += 1
        period_key = _reconciliation_period_key(reconciliation_path, reconciliation_rows)
        active_subject_ids: set[str] = set()
        for row in reconciliation_rows:
            issue = _import_reconciliation_issue(
                db,
                row=row,
                period_key=period_key,
                import_batch_id=reconciliation_batch["import_batch_id"],
            )
            if issue is None:
                counts["ignored_reconciliation_rows"] += 1
                continue
            active_subject_ids.add(issue["subject_id"])
            counts["validation_issues"] += 1
            issues.append(issue)
        resolved_count = _resolve_stale_reconciliation_issues(
            db,
            period_key=period_key,
            active_subject_ids=active_subject_ids,
            import_batch_id=reconciliation_batch["import_batch_id"],
        )
        if resolved_count:
            counts["resolved_reconciliation_issues"] += resolved_count

    if xolo_calculations_csv is not None:
        calculations_path = Path(xolo_calculations_csv)
        calculation_rows = _load_rows(calculations_path)
        calculation_batch = db.add_import_batch(
            source_name="xolo_modelo130_calculations",
            source_hash=_file_hash(calculations_path),
            batch_key=calculations_path.name,
            notes=f"Immutable Xolo filed baseline import from {calculations_path}",
        )
        counts["import_batches"] += 1
        for row in calculation_rows:
            if (row.get("xolo_status") or "").strip().lower() != "submitted":
                counts["ignored_calculation_rows"] += 1
                continue
            if _import_filed_baseline(db, row=row, import_batch_id=calculation_batch["import_batch_id"]):
                counts["filing_snapshots"] += 1

    if modelo130_reconciliation_csv is not None:
        adjustment_path = Path(modelo130_reconciliation_csv)
        adjustment_rows = _load_rows(adjustment_path)
        adjustment_batch = db.add_import_batch(
            source_name="xolo_modelo130_reconciliation",
            source_hash=_file_hash(adjustment_path),
            batch_key=adjustment_path.name,
            notes=f"Documented annual-close adjustments from {adjustment_path}",
        )
        counts["import_batches"] += 1
        for row in adjustment_rows:
            if _import_historical_adjustment(
                db,
                row=row,
                import_batch_id=adjustment_batch["import_batch_id"],
            ):
                counts["historical_adjustments"] += 1

    if document_inventory_csv is not None:
        inventory_path = Path(document_inventory_csv)
        inventory_rows = _load_rows(inventory_path)
        inventory_batch = db.add_import_batch(
            source_name="xolo_document_inventory",
            source_hash=_file_hash(inventory_path),
            batch_key=inventory_path.name,
            notes=f"Filing evidence inventory from {inventory_path}",
        )
        counts["import_batches"] += 1
        for row in inventory_rows:
            if _import_filing_inventory_row(
                db,
                row=row,
                import_batch_id=inventory_batch["import_batch_id"],
            ):
                counts["filed_obligations_from_inventory"] += 1

    counts["seeded_obligations"] += _seed_missing_obligations(db, source_rows)

    return {"counts": dict(counts), "issues": issues}


def _import_filed_baseline(db: LedgerDB, *, row: dict[str, str], import_batch_id: str) -> bool:
    period_key = (row.get("period") or "").strip().upper()
    _require_quarter_period(period_key)
    db.ensure_period(period_key)
    source_hash = _stable_payload_hash({"kind": "xolo_filed_baseline", "row": row})
    existing = db.connection.execute(
        "SELECT filing_snapshot_id FROM filing_snapshots WHERE source_hash = ?",
        (source_hash,),
    ).fetchone()
    if existing is not None:
        return True

    previous = parse_amount(row.get("previous_quarters_compensation") or "0")
    withholding = parse_amount(row.get("withholding_taxes") or "0")
    twenty_percent = parse_amount(row.get("net_results_20_percent") or "0")
    reduction = parse_amount(row.get("article_110_3_reduction") or "0")
    filed_values = {
        "01": _amount_text(row.get("total_compounded_sales_ytd")),
        "02": _amount_text(row.get("total_compounded_deductible_expenses_ytd")),
        "03": _amount_text(row.get("net_results_ytd")),
        "04": _amount_text(row.get("net_results_20_percent")),
        "05": f"{previous:.2f}",
        "06": f"{withholding:.2f}",
        "07": f"{(twenty_percent - previous - withholding):.2f}",
        "13": f"{reduction:.2f}",
        "19": _amount_text(row.get("payable_irpf_for_quarter") or row.get("amount_due")),
    }
    db.create_filing_snapshot(
        period_key,
        status="baseline",
        filed_on=_normalize_submitted_date(row.get("submitted_date", "")),
        payload={
            "form": "130",
            "baseline_kind": "xolo_submitted",
            "filed_values": filed_values,
            "report_id": (row.get("report_id") or "").strip(),
            "file_id": (row.get("file_id") or "").strip(),
            "filename": (row.get("filename") or "").strip(),
            "import_batch_id": import_batch_id,
        },
        snapshot_hash=source_hash,
        source_hash=source_hash,
    )
    return True


def _import_historical_adjustment(
    db: LedgerDB,
    *,
    row: dict[str, str],
    import_batch_id: str,
) -> bool:
    status = (row.get("status") or "").strip().lower()
    if status != "rows_match_target_after_annual_adjustment_no_tieout":
        return False
    period_key = _require_quarter_period(row.get("period", ""))
    amount_minor = _minor_from_text(row.get("annual_adjustment_delta", "0"))
    if amount_minor == 0:
        return False

    source_hash = _stable_payload_hash(
        {"kind": "verify_history_annual_close_adjustment", "row": row}
    )
    external_key = _external_key("verify-history-adjustment", period_key, source_hash)
    existing = db.connection.execute(
        """
        SELECT transaction_id FROM transactions
        WHERE external_key = ? AND source_hash = ?
        """,
        (external_key, source_hash),
    ).fetchone()
    if existing is not None:
        return True

    period_end = _quarter_end(period_key)
    transaction = db.add_transaction(
        transaction_id=_uuid_for("verify-history-adjustment", period_key, source_hash),
        external_key=external_key,
        period_key=period_key,
        transaction_date=period_end,
        booking_date=period_end,
        entry_type="verify_history_adjustment",
        description=(
            f"{period_key} annual-close bridge documented by Modelo 100 comparison; "
            f"import_batch_id={import_batch_id}"
        ),
        amount_minor=-amount_minor,
        amount_eur_minor=-amount_minor,
        currency="EUR",
        direction="debit",
        lifecycle_status="approved",
        source_hash=source_hash,
    )
    db.add_detailed_tax_treatment(
        transaction_id=transaction["transaction_id"],
        treatment_type="adjustment",
        tax_code="verify_history_annual_close_adjustment",
        taxable_base_minor=0,
        vat_minor=0,
        deductible_irpf_minor=-amount_minor,
        deductible_vat_minor=0,
        withholding_minor=0,
        include_modelo130=True,
        include_modelo303=False,
        include_modelo347=False,
        notes=(
            f"Verification-only annual/Q4 bridge. {row.get('notes', '').strip()} "
            "It is excluded from production calculations."
        ).strip(),
        source_hash=_stable_payload_hash(
            {"kind": "verify_history_adjustment_treatment", "row": row}
        ),
    )
    return True


def _import_filing_inventory_row(
    db: LedgerDB,
    *,
    row: dict[str, str],
    import_batch_id: str,
) -> bool:
    if (row.get("category") or "").strip().lower() != "tax_report":
        return False
    filename = (row.get("original_name") or row.get("drive_relative_path") or "").strip()
    if not filename or "draft" in filename.casefold():
        return False
    recognized = recognize_tax_form_filename(filename)
    if recognized is None:
        return False
    existing = db.connection.execute(
        """
        SELECT * FROM obligations
        WHERE period_id = (SELECT period_id FROM periods WHERE period_key = ?)
          AND obligation_code = ?
        """,
        (recognized.period, recognized.code),
    ).fetchone()
    if existing is not None and existing["filing_status"] == "filed":
        return True

    year = int(recognized.period[:4])
    rule_version_id = _obligation_rule_version(db, recognized.code, year)
    source_reference = (
        row.get("drive_relative_path")
        or row.get("local_source_path")
        or filename
    ).strip()
    db.add_obligation(
        period_key=recognized.period,
        obligation_code=recognized.code,
        filing_status="filed",
        determination="due",
        explanation=(
            f"Filed Modelo {recognized.code} evidence was found in the Xolo archive inventory. "
            f"Inventory batch: {import_batch_id}."
        ),
        source_citation=f"{recognized.source_citation} Evidence: {source_reference}",
        blocking=False,
        rule_version_id=rule_version_id,
        source_hash=(row.get("sha256") or _stable_payload_hash(row)).strip(),
        expected_row_version=(existing["row_version"] if existing is not None else None),
    )
    return True


def _seed_missing_obligations(db: LedgerDB, source_rows: list[dict[str, str]]) -> int:
    quarters = sorted(
        {
            period["period_key"]
            for period in db.list_periods()
            if QUARTER_KEY_RE.fullmatch(period["period_key"])
        }
    )
    years = sorted(
        {
            int(value)
            for row in source_rows
            for value in ((row.get("source_scope") or "").strip(),)
            if re.fullmatch(r"20\d{2}", value)
        }
    )
    baseline_periods = _modelo130_baseline_periods(db)
    planned = 0

    for period_key in quarters:
        year = int(period_key[:4])
        for code in QUARTERLY_FORM_CODES:
            planned += 1
            if _obligation_exists(db, period_key, code):
                continue
            filed = code == "130" and period_key in baseline_periods
            db.add_obligation(
                period_key=period_key,
                obligation_code=code,
                filing_status="filed" if filed else "unknown",
                determination="due" if filed else "unknown",
                explanation=(
                    "Submitted Xolo Modelo 130 baseline was imported."
                    if filed
                    else "The migrated records do not contain enough reviewed facts or filing evidence to determine this obligation."
                ),
                source_citation=FORM_RULES[code].source_citation,
                blocking=not filed,
                rule_version_id=_obligation_rule_version(db, code, year),
                source_hash=_stable_payload_hash(
                    {
                        "kind": "seeded_obligation",
                        "period_key": period_key,
                        "code": code,
                        "filed": filed,
                    }
                ),
            )

    for year in years:
        period_key = str(year)
        for code in ANNUAL_FORM_CODES:
            rule = FORM_RULES[code]
            if rule.introduced_year is not None and year < rule.introduced_year:
                continue
            planned += 1
            if _obligation_exists(db, period_key, code):
                continue
            db.add_obligation(
                period_key=period_key,
                obligation_code=code,
                filing_status="unknown",
                determination="unknown",
                explanation=(
                    "The migrated records do not contain enough reviewed household facts or filing evidence "
                    "to determine this annual obligation."
                ),
                source_citation=rule.source_citation,
                blocking=True,
                rule_version_id=_obligation_rule_version(db, code, year),
                source_hash=_stable_payload_hash(
                    {"kind": "seeded_obligation", "period_key": period_key, "code": code}
                ),
            )
    return planned


def _obligation_rule_version(db: LedgerDB, code: str, year: int) -> str:
    rule = FORM_RULES[code]
    row = db.add_rule_version(
        rule_name=f"modelo_{code}_obligation",
        version=f"{year}-inventory-gate-v1",
        activated_at=f"{year}-01-01",
        source_hash=_stable_payload_hash(
            {"form": code, "year": year, "source_citation": rule.source_citation}
        ),
    )
    return row["rule_version_id"]


def _modelo130_baseline_periods(db: LedgerDB) -> set[str]:
    periods: set[str] = set()
    rows = db.connection.execute(
        """
        SELECT p.period_key, fs.payload_json
        FROM filing_snapshots fs
        JOIN periods p ON p.period_id = fs.period_id
        WHERE fs.status = 'baseline'
        """
    ).fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"])
        if payload.get("form") == "130":
            periods.add(row["period_key"])
    return periods


def _obligation_exists(db: LedgerDB, period_key: str, code: str) -> bool:
    return (
        db.connection.execute(
            """
            SELECT 1 FROM obligations
            WHERE period_id = (SELECT period_id FROM periods WHERE period_key = ?)
              AND obligation_code = ?
            """,
            (period_key, code),
        ).fetchone()
        is not None
    )


def _import_quarterly_row(db: LedgerDB, *, row: dict[str, str], import_batch_id: str) -> None:
    line_key = _row_line_key(row)
    transaction_external_key = _external_key("transaction", line_key)
    transaction_source_hash = _stable_payload_hash({"kind": "transaction", "row": row})
    complete_existing = db.connection.execute(
        """
        SELECT t.transaction_id
        FROM transactions t
        JOIN documents d ON d.document_id = t.document_id
        JOIN document_sources ds ON ds.document_id = d.document_id
        JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
        WHERE t.external_key = ? AND t.source_hash = ? AND ds.source_book_line_id = ?
        """,
        (transaction_external_key, transaction_source_hash, _source_book_line_id(row)),
    ).fetchone()
    if complete_existing is not None:
        return
    period_key = _require_quarter_period(row.get("period", ""))
    kind = QUARTERLY_BOOK_TYPES[row["source_book_type"].strip()]
    counterparty_id = _upsert_counterparty(db, row)
    amount_minor = _minor_from_text(row.get("gross_eur", "0"))
    document = _upsert_source_document(
        db,
        row=row,
        counterparty_id=counterparty_id,
        import_batch_id=import_batch_id,
        document_type=row["source_book_type"].strip(),
        period_key=period_key,
    )

    transaction = db.add_transaction(
        transaction_id=_uuid_for("transaction", line_key),
        external_key=transaction_external_key,
        period_key=period_key,
        transaction_date=_recognition_date(row, period_key),
        booking_date=_normalize_date(row.get("booking_date") or row.get("date", "")),
        entry_type=kind,
        description=_transaction_description(row),
        amount_minor=amount_minor,
        currency="EUR",
        amount_original_minor=_optional_minor(row.get("original_amount")),
        original_currency=_optional_original_currency(row),
        amount_eur_minor=amount_minor,
        direction="credit" if kind == "income" else "debit",
        lifecycle_status="approved",
        document_id=document["document_id"],
        counterparty_id=counterparty_id,
        source_hash=transaction_source_hash,
    )

    taxable_base_minor = _optional_minor(row.get("deductible_base_eur"))
    deductible_irpf_minor = _optional_minor(row.get("irpf_deductible_eur"))
    if kind == "income":
        taxable_base_minor = taxable_base_minor if taxable_base_minor is not None else amount_minor
        deductible_irpf_minor = 0
    else:
        taxable_base_minor = taxable_base_minor if taxable_base_minor is not None else amount_minor
        deductible_irpf_minor = deductible_irpf_minor if deductible_irpf_minor is not None else amount_minor

    vat_minor = None
    if taxable_base_minor is not None:
        vat_minor = amount_minor - taxable_base_minor

    db.add_detailed_tax_treatment(
        transaction_id=transaction["transaction_id"],
        treatment_type=kind,
        tax_code=_tax_code_for_row(row, kind),
        taxable_base_minor=taxable_base_minor,
        vat_minor=vat_minor,
        deductible_irpf_minor=deductible_irpf_minor,
        deductible_vat_minor=0,
        withholding_minor=0,
        include_modelo130=True,
        include_modelo303=False,
        include_modelo347=False,
        notes=_treatment_notes(row),
        source_hash=_stable_payload_hash({"kind": "tax_treatment", "row": row}),
    )


def _import_annual_asset_row(db: LedgerDB, *, row: dict[str, str], import_batch_id: str) -> int:
    line_key = _row_line_key(row)
    year = _asset_year(row)
    annual_period_key = year
    asset_code = _external_key(
        "asset",
        row.get("asset_id", ""),
        row.get("date", ""),
        row.get("amortizable_base_eur", ""),
    )
    entry_source_hash = _stable_payload_hash(
        {"kind": "amortization_entry", "row": row, "period": annual_period_key}
    )
    complete_existing = db.connection.execute(
        """
        SELECT a.asset_id
        FROM assets a
        JOIN amortization_entries ae ON ae.asset_id = a.asset_id
        WHERE a.asset_code = ? AND ae.source_hash = ?
        """,
        (asset_code, entry_source_hash),
    ).fetchone()
    if complete_existing is not None:
        return _ensure_derived_quarter_schedule(
            db,
            asset_id=complete_existing["asset_id"],
            row=row,
            year=int(year),
        )
    db.ensure_period(
        annual_period_key,
        starts_on=f"{year}-01-01",
        ends_on=f"{year}-12-31",
        period_type="annual",
        source_hash=_stable_payload_hash({"kind": "annual_period", "year": year}),
    )
    counterparty_id = _upsert_counterparty(db, row)
    annual_amount_minor = _minor_from_text(row.get("amortization_amount_eur", "0"))
    amortizable_base_minor = _optional_minor(row.get("amortizable_base_eur"))
    document = _upsert_source_document(
        db,
        row=row,
        counterparty_id=counterparty_id,
        import_batch_id=import_batch_id,
        document_type="xolo_annual_asset_evidence",
        period_key=None,
        number_override=(row.get("asset_id") or "").strip() or None,
    )

    acquisition = _find_acquisition_transaction(
        db,
        counterparty_id=counterparty_id,
        issued_on=_normalize_date(row.get("date", "")),
    )
    business_use_ratio = None
    if acquisition is not None and amortizable_base_minor is not None:
        acquisition_base = int(acquisition["taxable_base_minor"] or 0)
        if acquisition_base > 0 and amortizable_base_minor <= acquisition_base:
            business_use_ratio = amortizable_base_minor / acquisition_base

    asset = db.add_asset(
        asset_code=asset_code,
        cost_minor=amortizable_base_minor if amortizable_base_minor is not None else annual_amount_minor,
        currency="EUR",
        depreciation_method="xolo_annual_evidence_active_day_schedule",
        source_hash=_stable_payload_hash(
            {
                "kind": "asset",
                "asset_code": asset_code,
                "placed_in_service_on": _normalize_date(row.get("date", "")),
            }
        ),
        document_id=document["document_id"],
        acquisition_transaction_id=(
            acquisition["transaction_id"] if acquisition is not None else None
        ),
        placed_in_service_on=_normalize_date(row.get("date", "")),
        amortizable_base_minor=amortizable_base_minor,
        iva_treatment=(row.get("vat_treatment") or "unknown").strip() or "unknown",
        business_use_ratio=business_use_ratio,
        annual_rate_basis_points=_basis_points_from_text(row.get("rate_or_life", "")),
        advisor_decision=None,
    )
    db.add_amortization_entry(
        asset_id=asset["asset_id"],
        period_key=annual_period_key,
        amount_minor=annual_amount_minor,
        source_hash=entry_source_hash,
        entry_kind="annual_evidence",
        tax_year=int(year),
        source_book_line_id=(row.get("source_book_line_id") or "").strip() or None,
        include_in_books=False,
    )
    return _ensure_derived_quarter_schedule(
        db,
        asset_id=asset["asset_id"],
        row=row,
        year=int(year),
    )


def _ensure_derived_quarter_schedule(
    db: LedgerDB,
    *,
    asset_id: str,
    row: dict[str, str],
    year: int,
) -> int:
    annual_amount_minor = _minor_from_text(row.get("amortization_amount_eur", "0"))
    placed_on = date.fromisoformat(_normalize_date(row.get("date", "")))
    allocations = _allocate_annual_amount_by_active_days(
        annual_amount_minor,
        tax_year=year,
        placed_in_service_on=placed_on,
    )
    for period_key, amount_minor in allocations:
        source_hash = _stable_payload_hash(
            {
                "kind": "derived_quarter_schedule",
                "annual_source_book_line_id": _source_book_line_id(row),
                "period_key": period_key,
                "amount_minor": amount_minor,
            }
        )
        existing = db.connection.execute(
            """
            SELECT amortization_entry_id
            FROM amortization_entries
            WHERE asset_id = ?
              AND period_id = (SELECT period_id FROM periods WHERE period_key = ?)
              AND source_hash = ?
            """,
            (asset_id, period_key, source_hash),
        ).fetchone()
        if existing is not None:
            continue
        db.add_amortization_entry(
            asset_id=asset_id,
            period_key=period_key,
            amount_minor=amount_minor,
            source_hash=source_hash,
            entry_kind="quarter_schedule",
            tax_year=year,
            source_book_line_id=f"{_source_book_line_id(row)}:derived:{period_key}",
            include_in_books=False,
        )
    return len(allocations)


def _allocate_annual_amount_by_active_days(
    amount_minor: int,
    *,
    tax_year: int,
    placed_in_service_on: date,
) -> list[tuple[str, int]]:
    year_start = date(tax_year, 1, 1)
    year_end = date(tax_year, 12, 31)
    active_start = max(year_start, placed_in_service_on)
    if active_start > year_end:
        raise ValueError(
            f"Asset placed in service after tax year {tax_year}: {placed_in_service_on}"
        )

    day_counts: list[tuple[str, int]] = []
    for quarter in range(1, 5):
        quarter_start_month = (quarter - 1) * 3 + 1
        quarter_start = date(tax_year, quarter_start_month, 1)
        if quarter == 4:
            quarter_end = year_end
        else:
            quarter_end = date(tax_year, quarter_start_month + 3, 1) - timedelta(days=1)
        segment_start = max(active_start, quarter_start)
        if segment_start <= quarter_end:
            day_counts.append(
                (f"{tax_year}-Q{quarter}", (quarter_end - segment_start).days + 1)
            )

    total_days = sum(days for _, days in day_counts)
    allocated: list[list[int | str]] = []
    for period_key, days in day_counts:
        numerator = amount_minor * days
        allocated.append([period_key, numerator // total_days, numerator % total_days])
    remaining = amount_minor - sum(int(item[1]) for item in allocated)
    for item in sorted(allocated, key=lambda value: (-int(value[2]), str(value[0])))[:remaining]:
        item[1] = int(item[1]) + 1
    return [(str(item[0]), int(item[1])) for item in allocated]


def _find_acquisition_transaction(
    db: LedgerDB,
    *,
    counterparty_id: str,
    issued_on: str,
) -> dict[str, Any] | None:
    row = db.connection.execute(
        """
        SELECT t.transaction_id, tt.taxable_base_minor
        FROM transactions t
        JOIN documents d ON d.document_id = t.document_id
        JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
        WHERE t.counterparty_id = ?
          AND d.issued_on = ?
          AND tt.tax_code = 'historical_g03'
          AND COALESCE(tt.taxable_base_minor, 0) > 0
        ORDER BY t.transaction_date, t.transaction_id
        LIMIT 1
        """,
        (counterparty_id, issued_on),
    ).fetchone()
    return dict(row) if row is not None else None


def _import_reconciliation_issue(
    db: LedgerDB,
    *,
    row: dict[str, str],
    period_key: str,
    import_batch_id: str,
) -> dict[str, str] | None:
    status = (row.get("status") or "").strip().lower()
    if status not in UNRESOLVED_RECONCILIATION_STATUSES:
        return None

    row_key = _external_key(
        "reconcile-row",
        period_key,
        row.get("xolo_id", ""),
        row.get("number", ""),
        row.get("date", ""),
    )
    issue_source_hash = _stable_payload_hash(
        {"kind": "validation_issue", "period_key": period_key, "row": row}
    )
    existing = db.connection.execute(
        """
        SELECT * FROM validation_issues
        WHERE period_id = (SELECT period_id FROM periods WHERE period_key = ?)
          AND issue_code = ? AND subject_table = 'xolo_expense_reconcile'
          AND subject_id = ? AND source_hash = ?
        """,
        (period_key, status, row_key, issue_source_hash),
    ).fetchone()
    if existing is not None:
        issue = dict(existing)
        return {
            "period_key": period_key,
            "issue_code": issue["issue_code"],
            "subject_id": issue["subject_id"] or "",
            "message": issue["message"],
        }
    issue = db.add_validation_issue(
        period_key=period_key,
        issue_code=status,
        severity="error",
        message=_reconciliation_issue_message(row, period_key, import_batch_id),
        blocking=True,
        subject_table="xolo_expense_reconcile",
        subject_id=row_key,
        issue_status="open",
        source_hash=issue_source_hash,
    )
    return {
        "period_key": period_key,
        "issue_code": issue["issue_code"],
        "subject_id": issue["subject_id"] or "",
        "message": issue["message"],
    }


def _resolve_stale_reconciliation_issues(
    db: LedgerDB,
    *,
    period_key: str,
    active_subject_ids: set[str],
    import_batch_id: str,
) -> int:
    open_rows = db.connection.execute(
        """
        SELECT vi.validation_issue_id, vi.subject_id, vi.row_version
        FROM validation_issues vi
        JOIN periods p ON p.period_id = vi.period_id
        WHERE p.period_key = ?
          AND vi.subject_table = 'xolo_expense_reconcile'
          AND vi.issue_status = 'open'
        """,
        (period_key,),
    ).fetchall()
    resolved = 0
    for row in open_rows:
        if row["subject_id"] in active_subject_ids:
            continue
        db.resolve_issue(
            row["validation_issue_id"],
            reason=f"No longer unresolved in reconciliation import batch {import_batch_id}",
            expected_row_version=row["row_version"],
        )
        resolved += 1
    return resolved


def _upsert_counterparty(db: LedgerDB, row: dict[str, str]) -> str:
    supplier = (row.get("supplier") or row.get("recipient") or "Unknown counterparty").strip()
    counterparty = db.upsert_counterparty(
        counterparty_id=_uuid_for("counterparty", supplier.casefold()),
        external_key=_external_key("counterparty", supplier.casefold()),
        display_name=supplier,
        country_code="ZZ",
        source_hash=_stable_payload_hash({"kind": "counterparty", "supplier": supplier}),
    )
    return counterparty["counterparty_id"]


def _upsert_source_document(
    db: LedgerDB,
    *,
    row: dict[str, str],
    counterparty_id: str,
    import_batch_id: str,
    document_type: str,
    period_key: str | None,
    number_override: str | None = None,
) -> dict[str, Any]:
    line_key = _row_line_key(row)
    issued_on = _normalize_date(row.get("date", ""))
    document_number = number_override or (row.get("document_number") or "").strip() or None
    existing = None
    if document_number:
        existing_row = db.connection.execute(
            """
            SELECT * FROM documents
            WHERE counterparty_id = ? AND document_type = ?
              AND document_number = ? AND issued_on = ?
            """,
            (counterparty_id, document_type, document_number, issued_on),
        ).fetchone()
        existing = dict(existing_row) if existing_row is not None else None

    if existing is None:
        canonical_key = "|".join(
            [counterparty_id, document_type, document_number or line_key, issued_on]
        )
        document = db.upsert_document(
            document_id=_uuid_for("document", canonical_key),
            external_key=_external_key("document", canonical_key),
            counterparty_id=counterparty_id,
            import_batch_id=import_batch_id,
            document_type=document_type,
            document_number=document_number,
            issued_on=issued_on,
            period_key=period_key,
            currency="EUR",
            total_minor=None,
            lifecycle_status="approved",
            source_hash=_stable_payload_hash({"kind": "canonical_document", "key": canonical_key}),
        )
    else:
        document = existing

    db.add_document_source(
        document_id=document["document_id"],
        import_batch_id=import_batch_id,
        source_book_line_id=_source_book_line_id(row),
        source_file=(row.get("source_file") or "").strip() or None,
        source_row_number=(row.get("source_row_number") or "").strip() or None,
        source_hash=_stable_payload_hash({"kind": "document_source", "row": row}),
    )
    if row.get("source_file") and not document.get("source_path"):
        document = db.set_document_storage(document["document_id"], source_path=row["source_file"])
    return document


def _row_line_key(row: dict[str, str]) -> str:
    line_id = _source_book_line_id(row)
    if line_id:
        return "|".join(
            [
                (row.get("source_book_type") or "unknown_book").strip(),
                (row.get("source_file_format") or "unknown_sheet").strip(),
                line_id,
            ]
        )
    return "|".join(
        [
            (row.get("source_book_type") or "").strip(),
            (row.get("source_file") or "").strip(),
            (row.get("source_row_number") or "").strip(),
        ]
    )


def _source_book_line_id(row: dict[str, str]) -> str:
    return (row.get("source_book_line_id") or "").strip()


def _transaction_description(row: dict[str, str]) -> str:
    parts = [
        (row.get("supplier") or "").strip(),
        (row.get("document_number") or "").strip(),
        f"[{_row_line_key(row)}]",
    ]
    return " ".join(part for part in parts if part).strip()


def _treatment_notes(row: dict[str, str]) -> str:
    notes = [
        f"source_book_line_id={_row_line_key(row)}",
        f"reason_code={row.get('reason_code', '').strip() or 'unknown'}",
    ]
    if row.get("source_file_format"):
        notes.append(f"source_file_format={row['source_file_format'].strip()}")
    if row.get("notes"):
        notes.append(f"notes={row['notes'].strip()}")
    return "; ".join(notes)


def _reconciliation_issue_message(row: dict[str, str], period_key: str, import_batch_id: str) -> str:
    parts = [
        f"{period_key} unresolved reconciliation row",
        (row.get("status") or "").strip(),
        (row.get("recipient") or "").strip(),
        (row.get("number") or "").strip(),
        (row.get("date") or "").strip(),
        f"import_batch_id={import_batch_id}",
    ]
    if row.get("notes"):
        parts.append((row["notes"]).strip())
    return " | ".join(part for part in parts if part)


def _tax_code_for_row(row: dict[str, str], kind: str) -> str:
    reason_code = (row.get("reason_code") or "").strip().lower()
    if kind == "income":
        return "historical_income"
    if reason_code:
        return f"historical_{reason_code}"
    return "historical_expense"


def _reconciliation_period_key(path: Path, rows: list[dict[str, str]]) -> str:
    for candidate in (path.parent.name, path.stem, path.name):
        match = DATE_IN_FILE_RE.search(candidate)
        if match:
            return match.group(0).upper()
    if rows:
        return _quarter_from_iso_date(rows[-1].get("date", ""))
    raise ValueError(f"Cannot determine reconciliation period from {path}")


def _asset_year(row: dict[str, str]) -> str:
    year = (row.get("source_scope") or "").strip()
    if re.fullmatch(r"20\d{2}", year):
        return year
    raise ValueError(f"Annual asset row must have a single source_scope year: {row!r}")


def _is_annual_asset_row(row: dict[str, str]) -> bool:
    return (row.get("amortization_period") or "").strip().upper() in {"0A", "A", "ANUAL", "ANNUAL"}


def _require_quarter_period(period_key: str) -> str:
    normalized = (period_key or "").strip().upper()
    if not QUARTER_KEY_RE.fullmatch(normalized):
        raise ValueError(f"Quarterly row must carry a quarter period key, got {period_key!r}")
    return normalized


def _quarter_from_iso_date(value: str) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%d")
    quarter = ((parsed.month - 1) // 3) + 1
    return f"{parsed.year}-Q{quarter}"


def _recognition_date(row: dict[str, str], period_key: str) -> str:
    document_date = _normalize_date(row.get("date", ""))
    if _quarter_from_iso_date(document_date) == period_key:
        return document_date
    year = int(period_key[:4])
    quarter = int(period_key[-1])
    month = quarter * 3
    day = 31 if month in {3, 12} else 30
    return f"{year:04d}-{month:02d}-{day:02d}"


def _quarter_end(period_key: str) -> str:
    year = int(period_key[:4])
    quarter = int(period_key[-1])
    month = quarter * 3
    day = 31 if month in {3, 12} else 30
    return f"{year:04d}-{month:02d}-{day:02d}"


def _normalize_date(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("Date value is required for history migration")
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value!r}")


def _normalize_submitted_date(value: str) -> str:
    text = (value or "").strip()
    for fmt in ("%d %b %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unsupported submitted date: {value!r}")


def _amount_text(value: str | None) -> str:
    return f"{parse_amount(value or '0'):.2f}"


def _optional_original_currency(row: dict[str, str]) -> str | None:
    if _optional_minor(row.get("original_amount")) is None:
        return None
    currency = (row.get("original_currency") or "").strip().upper()
    return currency or None


def _minor_from_text(value: str) -> int:
    return _decimal_to_minor(parse_amount(value or "0"))


def _optional_minor(value: str | None) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    return _decimal_to_minor(parse_amount(text))


def _decimal_to_minor(value: Any) -> int:
    return int(value * 100)


def _basis_points_from_text(value: str) -> int | None:
    text = (value or "").strip().replace("%", "").replace(",", ".")
    if not text:
        return None
    return int(round(float(text) * 100))


def _external_key(prefix: str, *parts: str) -> str:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return ":".join([prefix, *cleaned])


def _uuid_for(prefix: str, *parts: str) -> str:
    return str(uuid5(MIGRATION_NAMESPACE, _external_key(prefix, *parts)))


def _stable_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))
