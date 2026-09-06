from __future__ import annotations

from .counterparty_names import (
    CounterpartyMatchError, find_name_candidates, identity_conflicts,
    normalize_counterparty_name, usable_tax_id,
)

import csv
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid5

from .filing_evidence import extract_filing_evidence
from .ledger_db import LedgerDB
from .money import parse_amount
from .tax_rules import (
    ANNUAL_FORM_CODES,
    FORM_RULES,
    QUARTERLY_FORM_CODES,
    recognize_tax_form_filename,
)
from .tax_engine import EU_349_CODES


MIGRATION_NAMESPACE = UUID("adca0a24-7f65-4bde-9251-558c3d820889")
EU_COUNTRY_CODES = {
    "AT",
    "BE",
    "BG",
    "CY",
    "CZ",
    "DE",
    "DK",
    "EE",
    "ES",
    "FI",
    "FR",
    "GR",
    "HR",
    "HU",
    "IE",
    "IT",
    "LT",
    "LU",
    "LV",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SE",
    "SI",
    "SK",
}
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
HISTORY_MIGRATION_RULE_VERSION = "official-source-book-v6"


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

    counts.update(_prune_superseded_source_book_records(db))

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
        source_book_evidence = _source_book_reconciliation_index(source_rows)
        active_subject_ids: set[str] = set()
        for row in reconciliation_rows:
            resolution_reason = _source_book_resolution_reason(row, source_book_evidence)
            if resolution_reason is not None:
                counts["reconciliation_rows_superseded_by_source_book"] += 1
                counts["resolved_reconciliation_issues"] += _resolve_reconciliation_subject(
                    db,
                    period_key=period_key,
                    subject_id=_reconciliation_row_key(row, period_key),
                    reason=resolution_reason,
                )
                continue
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
        inventory_result = refresh_filing_inventory(db, document_inventory_csv)
        counts["import_batches"] += 1
        counts["filed_obligations_from_inventory"] += inventory_result["recognized_rows"]

    counts["classified_source_book_obligations"] += _classify_source_book_obligations(
        db,
        source_rows,
    )
    counts["seeded_obligations"] += _seed_missing_obligations(db, source_rows)

    return {"counts": dict(counts), "issues": issues}


def refresh_filing_inventory(
    db: LedgerDB,
    document_inventory_csv: str | Path,
) -> dict[str, int]:
    inventory_path = Path(document_inventory_csv)
    inventory_rows = _load_rows(inventory_path)
    inventory_batch = db.add_import_batch(
        source_name="xolo_document_inventory",
        source_hash=_file_hash(inventory_path),
        batch_key=inventory_path.name,
        notes=f"Filing evidence inventory from {inventory_path}",
    )
    snapshots_before = db.connection.execute(
        "SELECT COUNT(*) FROM filing_snapshots"
    ).fetchone()[0]
    recognized_rows = sum(
        int(
            _import_filing_inventory_row(
                db,
                row=row,
                import_batch_id=inventory_batch["import_batch_id"],
            )
        )
        for row in inventory_rows
    )
    snapshots_after = db.connection.execute(
        "SELECT COUNT(*) FROM filing_snapshots"
    ).fetchone()[0]
    return {
        "inventory_rows": len(inventory_rows),
        "recognized_rows": recognized_rows,
        "new_snapshots": snapshots_after - snapshots_before,
    }


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
        form_code="130",
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

    transaction_source_hash = _stable_payload_hash(
        {"kind": "verify_history_annual_close_adjustment", "row": row}
    )
    treatment_source_hash = _stable_payload_hash(
        {"kind": "verify_history_adjustment_treatment", "row": row}
    )
    existing_rows = db.connection.execute(
        """
        SELECT t.transaction_id, t.external_key, t.source_hash,
               tt.source_hash AS treatment_source_hash
        FROM transactions t
        JOIN periods p ON p.period_id = t.period_id
        LEFT JOIN tax_treatments tt
          ON tt.transaction_id = t.transaction_id
         AND tt.treatment_type = 'adjustment'
         AND tt.jurisdiction = 'ES'
        WHERE p.period_key = ?
          AND t.entry_type = 'verify_history_adjustment'
        """,
        (period_key,),
    ).fetchall()
    if len(existing_rows) > 1:
        raise ValueError(f"Multiple verify-history adjustments exist for {period_key}")
    existing = existing_rows[0] if existing_rows else None
    if amount_minor == 0 and existing is None:
        return False
    if (
        existing is not None
        and existing["source_hash"] == transaction_source_hash
        and existing["treatment_source_hash"] == treatment_source_hash
    ):
        return True

    period_end = _quarter_end(period_key)
    external_key = _external_key("verify-history-adjustment", period_key)
    transaction = db.add_transaction(
        transaction_id=(
            existing["transaction_id"]
            if existing is not None
            else _uuid_for("verify-history-adjustment", period_key)
        ),
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
        source_hash=transaction_source_hash,
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
        source_hash=treatment_source_hash,
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
    year = int(recognized.period[:4])
    rule_version_id = _obligation_rule_version(db, recognized.code, year)
    source_reference = (
        row.get("drive_relative_path")
        or row.get("local_source_path")
        or filename
    ).strip()
    if existing is None or existing["filing_status"] != "filed":
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
    local_source = Path((row.get("local_source_path") or "").strip())
    if local_source.is_file():
        evidence = extract_filing_evidence(local_source)
        source_hash = (row.get("sha256") or evidence.source_sha256).strip().upper()
        payload = {
            **evidence.payload,
            "inventory_import_batch_id": import_batch_id,
            "drive_relative_path": (row.get("drive_relative_path") or "").strip(),
            "drive_url": (row.get("drive_url") or "").strip(),
        }
        existing_snapshots = db.connection.execute(
            "SELECT snapshot_hash, payload_json FROM filing_snapshots WHERE source_hash = ?",
            (source_hash,),
        ).fetchall()
        extraction_schema = str(payload.get("value_extraction_schema") or "")
        schema_already_imported = any(
            str(json.loads(snapshot["payload_json"]).get("value_extraction_schema") or "")
            == extraction_schema
            for snapshot in existing_snapshots
        )
        should_import = not existing_snapshots or (
            bool(extraction_schema) and not schema_already_imported
        )
        if should_import:
            filed_on = evidence.filed_on or (row.get("received_at") or "").strip()
            if not filed_on:
                filed_on = db.connection.execute(
                    "SELECT ends_on FROM periods WHERE period_key = ?",
                    (recognized.period,),
                ).fetchone()["ends_on"]
            snapshot_hash = (
                source_hash
                if not existing_snapshots
                else _stable_payload_hash(
                    {
                        "source_hash": source_hash,
                        "value_extraction_schema": extraction_schema,
                        "payload": payload,
                    }
                )
            )
            db.create_filing_snapshot(
                recognized.period,
                status="baseline",
                filed_on=filed_on,
                payload=payload,
                snapshot_hash=snapshot_hash,
                source_hash=source_hash,
                form_code=evidence.form_code,
                submission_reference=evidence.submission_reference,
                justificante_number=evidence.justificante_number,
                verification_code=evidence.verification_code,
                source_reference=(row.get("drive_relative_path") or str(local_source)).strip(),
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


def _classify_source_book_obligations(
    db: LedgerDB,
    source_rows: list[dict[str, str]],
) -> int:
    periods = sorted(
        {
            (row.get("period") or "").strip().upper()
            for row in source_rows
            if (row.get("import_status") or "imported").strip().lower() == "imported"
            and (row.get("source_book_type") or "").strip() in QUARTERLY_BOOK_TYPES
            and QUARTER_KEY_RE.fullmatch((row.get("period") or "").strip().upper())
        }
    )
    classified = 0
    for period_key in periods:
        existing = db.connection.execute(
            """
            SELECT * FROM obligations
            WHERE period_id = (SELECT period_id FROM periods WHERE period_key = ?)
              AND obligation_code = '349'
            """,
            (period_key,),
        ).fetchone()
        if existing is not None and existing["filing_status"] == "filed":
            continue
        tax_codes = {
            row["tax_code"]
            for row in db.connection.execute(
                """
                SELECT DISTINCT tt.tax_code
                FROM tax_treatments tt
                JOIN transactions t ON t.transaction_id = tt.transaction_id
                JOIN periods p ON p.period_id = t.period_id
                WHERE p.period_key = ?
                """,
                (period_key,),
            ).fetchall()
        }
        reportable_codes = sorted(tax_codes & EU_349_CODES)
        due = bool(reportable_codes)
        year = int(period_key[:4])
        db.add_obligation(
            period_key=period_key,
            obligation_code="349",
            filing_status="due" if due else "waived",
            determination="due" if due else "not_due",
            explanation=(
                "The official source books contain Modelo 349 operation codes: "
                + ", ".join(reportable_codes)
                if due
                else "The official source books contain no taxable intra-Community goods or services for this period."
            ),
            source_citation=(
                "AEAT Modelo 349: intra-Community supplies and acquisitions of goods and services are reportable; "
                "periods without such operations are not filed. "
                "https://sede.agenciatributaria.gob.es/Sede/iva/iva-operaciones-comercio-exterior/"
                "identificacion-realizar-operaciones-otros-empresarios-ue/modelo-349.html"
            ),
            blocking=due,
            rule_version_id=_obligation_rule_version(db, "349", year),
            source_hash=_stable_payload_hash(
                {
                    "kind": "source_book_obligation",
                    "period_key": period_key,
                    "form": "349",
                    "reportable_codes": reportable_codes,
                }
            ),
            expected_row_version=(existing["row_version"] if existing is not None else None),
        )
        classified += 1
    return classified


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
    kind = QUARTERLY_BOOK_TYPES[row["source_book_type"].strip()]
    # A scoped rule token refreshes affected old rows without invalidating all
    # history or colliding with independently versioned classification rules.
    infer_clave09 = _should_infer_clave09_output(row, kind)
    transaction_external_key = _external_key("transaction", line_key)
    transaction_source_hash = _stable_payload_hash(
        {
            "kind": "transaction",
            **({"clave09_output_rule": 1} if infer_clave09 else {}),
            "rule_version": HISTORY_MIGRATION_RULE_VERSION,
            "row": row,
        }
    )
    treatment_source_hash = _stable_payload_hash(
        {
            "kind": "tax_treatment",
            **({"clave09_output_rule": 1} if infer_clave09 else {}),
            "rule_version": HISTORY_MIGRATION_RULE_VERSION,
            "row": row,
        }
    )
    complete_existing = db.connection.execute(
        """
        SELECT t.transaction_id, t.source_hash, t.lifecycle_status, t.period_id,
               t.counterparty_id AS previous_counterparty_id,
               tt.source_hash AS treatment_source_hash,
               tt.aeat_invoice_type, tt.aeat_operation_key, tt.aeat_reverse_charge
        FROM transactions t
        JOIN documents d ON d.document_id = t.document_id
        JOIN document_sources ds ON ds.document_id = d.document_id
        JOIN tax_treatments tt
          ON tt.transaction_id = t.transaction_id
         AND tt.treatment_type = ?
         AND tt.jurisdiction = 'ES'
        WHERE t.external_key = ? AND ds.source_book_line_id = ?
        """,
        (kind, transaction_external_key, _source_book_line_id(row)),
    ).fetchone()
    if (
        complete_existing is not None
        and (
            complete_existing["source_hash"] == transaction_source_hash
            or (
                complete_existing["lifecycle_status"] in {"duplicate", "rejected", "void"}
                and complete_existing["treatment_source_hash"] == treatment_source_hash
            )
        )
        and complete_existing["aeat_invoice_type"] is not None
        and complete_existing["aeat_operation_key"] is not None
        and (kind == "income" or complete_existing["aeat_reverse_charge"] is not None)
    ):
        return
    if complete_existing is not None:
        # A changed rule or source row must not mutate counterparties/documents
        # before discovering that the existing financial period is immutable.
        db._assert_period_mutable(complete_existing["period_id"])
    period_key = _require_quarter_period(row.get("period", ""))
    amount_minor = _minor_from_text(row.get("gross_eur", "0"))
    taxable_base_minor = _optional_minor(row.get("taxable_base_eur"))
    if taxable_base_minor is None:
        taxable_base_minor = _optional_minor(row.get("deductible_base_eur"))
    deductible_irpf_minor = _optional_minor(row.get("irpf_deductible_eur"))
    if kind == "income":
        if (row.get("vat_treatment") or "").strip() and taxable_base_minor is None:
            raise ValueError(
                "VAT-marked income requires an explicit taxable base; "
                "gross cannot be assumed VAT-free"
            )
        taxable_base_minor = taxable_base_minor if taxable_base_minor is not None else amount_minor
        deductible_irpf_minor = 0
    else:
        taxable_base_minor = taxable_base_minor if taxable_base_minor is not None else amount_minor
        deductible_irpf_minor = deductible_irpf_minor if deductible_irpf_minor is not None else amount_minor

    explicit_vat_minor = _optional_minor(row.get("vat_eur"))
    deductible_vat_minor = _optional_minor(row.get("deductible_vat_eur")) or 0
    reverse_charge = _is_yes(row.get("reverse_charge")) or (
        # Xolo source books mark intra-EU service reverse charge via
        # operation key 09 and leave the explicit reverse-charge column blank.
        kind == "expense"
        and (row.get("operation_key") or "").strip() == "09"
    )
    vat_minor = explicit_vat_minor if explicit_vat_minor is not None else amount_minor - taxable_base_minor
    if infer_clave09:
        # The input deduction may be partial or zero. It cannot establish the
        # output VAT due on an intra-Community acquisition.
        vat_minor = _clave09_output_vat(row)
    elif reverse_charge and vat_minor == 0 and deductible_vat_minor:
        vat_minor = deductible_vat_minor
    terminal_existing = (
        complete_existing is not None
        and complete_existing["lifecycle_status"] in {"duplicate", "rejected", "void"}
    )
    if terminal_existing:
        transaction = db.connection.execute(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (complete_existing["transaction_id"],),
        ).fetchone()
    else:
        previous_link = db.connection.execute(
            "SELECT counterparty_id FROM transactions WHERE external_key = ?",
            (transaction_external_key,),
        ).fetchone()
        counterparty_id = _upsert_counterparty(
            db, row, previous_counterparty_id=(
                previous_link["counterparty_id"] if previous_link else None
            ),
        )
        document = _upsert_source_document(
            db,
            row=row,
            counterparty_id=counterparty_id,
            import_batch_id=import_batch_id,
            document_type=row["source_book_type"].strip(),
            period_key=period_key,
            total_minor=_document_total_minor(row, amount_minor),
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
            lifecycle_status=(
                complete_existing["lifecycle_status"]
                if complete_existing is not None
                and complete_existing["lifecycle_status"]
                in {"posted", "included_in_snapshot"}
                else "approved"
            ),
            document_id=document["document_id"],
            counterparty_id=counterparty_id,
            source_hash=transaction_source_hash,
        )
        previous_counterparty_id = (
            complete_existing["previous_counterparty_id"]
            if complete_existing is not None
            else None
        )
        if previous_counterparty_id != counterparty_id:
            _prune_unreferenced_migration_counterparty(db, previous_counterparty_id)

    tax_code = _tax_code_for_row(row, kind)
    operation_key = _aeat_operation_key_for_row(row, tax_code)
    db.add_detailed_tax_treatment(
        transaction_id=transaction["transaction_id"],
        treatment_type=kind,
        tax_code=tax_code,
        aeat_invoice_type=_aeat_invoice_type_for_row(row, kind),
        aeat_operation_key=operation_key,
        aeat_operation_qualification=_aeat_code_prefix(
            row.get("operation_qualification"), {"S1", "S2", "N1", "N2"}
        ),
        aeat_exemption_code=_aeat_code_prefix(
            row.get("exempt_operation"), {"E1", "E2", "E3", "E4", "E5", "E6"}
        ),
        aeat_reverse_charge=_aeat_reverse_charge_for_row(row, tax_code, kind),
        aeat_expense_concept=_aeat_expense_concept_for_row(row, kind),
        rate_basis_points=_reviewed_vat_rate_basis_points(
            row,
            tax_code=tax_code,
            taxable_base_minor=taxable_base_minor,
            vat_minor=vat_minor,
        ),
        taxable_base_minor=taxable_base_minor,
        vat_minor=vat_minor,
        deductible_irpf_minor=deductible_irpf_minor,
        deductible_vat_minor=deductible_vat_minor,
        withholding_minor=_optional_minor(row.get("withholding_eur")) or 0,
        include_modelo130=True,
        include_modelo303=_include_modelo303(row, kind),
        include_modelo347=False,
        notes=_treatment_notes(row, normalized_operation_key=operation_key),
        source_hash=treatment_source_hash,
    )


def _import_annual_asset_row(db: LedgerDB, *, row: dict[str, str], import_batch_id: str) -> int:
    line_key = _row_line_key(row)
    year = _asset_year(row)
    annual_period_key = year
    stable_asset_code = _external_key(
        "asset",
        row.get("asset_id", ""),
        row.get("date", ""),
    )
    identity_rows = db.connection.execute(
        """
        SELECT DISTINCT a.asset_id, a.asset_code
        FROM assets a
        JOIN amortization_entries ae ON ae.asset_id = a.asset_id
        WHERE ae.entry_kind = 'annual_evidence'
          AND ae.tax_year = ?
          AND ae.source_book_line_id = ?
        """,
        (int(year), _source_book_line_id(row)),
    ).fetchall()
    if len(identity_rows) > 1:
        raise ValueError(
            f"Multiple assets map to annual source row {_source_book_line_id(row)!r} for {year}"
        )
    # Preserve the code of a database imported before stable asset identities existed.
    asset_code = identity_rows[0]["asset_code"] if identity_rows else stable_asset_code
    entry_source_hash = _stable_payload_hash(
        {"kind": "amortization_entry", "row": row, "period": annual_period_key}
    )
    previous_asset_party = db.connection.execute(
        """SELECT d.counterparty_id FROM assets a
           JOIN documents d ON d.document_id = a.document_id WHERE a.asset_code = ?""",
        (asset_code,),
    ).fetchone()
    counterparty_id = _upsert_counterparty(
        db, row, previous_counterparty_id=(
            previous_asset_party["counterparty_id"] if previous_asset_party else None
        ),
    )
    annual_amount_minor = _minor_from_text(row.get("amortization_amount_eur", "0"))
    amortizable_base_minor = _optional_minor(row.get("amortizable_base_eur"))

    acquisition = _find_acquisition_transaction(
        db,
        counterparty_id=counterparty_id,
        issued_on=_normalize_date(row.get("date", "")),
    )
    business_use_ratio = None
    stored_amortizable_base_minor = amortizable_base_minor
    stored_cost_minor = (
        amortizable_base_minor if amortizable_base_minor is not None else annual_amount_minor
    )
    if acquisition is not None and amortizable_base_minor is not None:
        acquisition_base = int(acquisition["taxable_base_minor"] or 0)
        if acquisition_base > 0 and amortizable_base_minor <= acquisition_base:
            business_use_ratio = amortizable_base_minor / acquisition_base
            stored_amortizable_base_minor = acquisition_base
            stored_cost_minor = int(
                acquisition["amount_eur_minor"]
                or acquisition["amount_minor"]
                or acquisition_base
            )

    placed_in_service_on = _normalize_date(row.get("date", ""))
    annual_rate_basis_points = _basis_points_from_text(row.get("rate_or_life", ""))
    iva_treatment = (row.get("vat_treatment") or "unknown").strip() or "unknown"
    asset_source_hash = _stable_payload_hash(
        {
            "kind": "asset",
            "asset_code": asset_code,
            "placed_in_service_on": placed_in_service_on,
        }
    )
    complete_existing = db.connection.execute(
        """
        SELECT a.*
        FROM assets a
        JOIN amortization_entries ae ON ae.asset_id = a.asset_id
        WHERE a.asset_code = ? AND ae.source_hash = ?
        """,
        (asset_code, entry_source_hash),
    ).fetchone()
    acquisition_transaction_id = (
        acquisition["transaction_id"] if acquisition is not None else None
    )
    if complete_existing is not None and _asset_import_matches(
        complete_existing,
        cost_minor=stored_cost_minor,
        acquisition_transaction_id=acquisition_transaction_id,
        placed_in_service_on=placed_in_service_on,
        source_hash=asset_source_hash,
        amortizable_base_minor=stored_amortizable_base_minor,
        iva_treatment=iva_treatment,
        business_use_ratio=business_use_ratio,
        annual_rate_basis_points=annual_rate_basis_points,
    ):
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
    document = _upsert_source_document(
        db,
        row=row,
        counterparty_id=counterparty_id,
        import_batch_id=import_batch_id,
        document_type="xolo_annual_asset_evidence",
        period_key=None,
        number_override=(row.get("asset_id") or "").strip() or None,
        total_minor=amortizable_base_minor or annual_amount_minor,
    )
    existing_asset = complete_existing or db.connection.execute(
        "SELECT * FROM assets WHERE asset_code = ?",
        (asset_code,),
    ).fetchone()

    asset = db.add_asset(
        asset_code=asset_code,
        cost_minor=stored_cost_minor,
        currency="EUR",
        depreciation_method="xolo_annual_evidence_active_day_schedule",
        source_hash=asset_source_hash,
        document_id=document["document_id"],
        acquisition_transaction_id=acquisition_transaction_id,
        placed_in_service_on=placed_in_service_on,
        amortizable_base_minor=stored_amortizable_base_minor,
        iva_treatment=iva_treatment,
        business_use_ratio=business_use_ratio,
        annual_rate_basis_points=annual_rate_basis_points,
        advisor_decision=(existing_asset["advisor_decision"] if existing_asset else None),
        advisor_decision_on=(existing_asset["advisor_decision_on"] if existing_asset else None),
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


def _asset_import_matches(
    asset: Any,
    *,
    cost_minor: int,
    acquisition_transaction_id: str | None,
    placed_in_service_on: str,
    source_hash: str,
    amortizable_base_minor: int | None,
    iva_treatment: str,
    business_use_ratio: float | None,
    annual_rate_basis_points: int | None,
) -> bool:
    existing_ratio = asset["business_use_ratio"]
    ratio_matches = (
        existing_ratio is None and business_use_ratio is None
    ) or (
        existing_ratio is not None
        and business_use_ratio is not None
        and abs(float(existing_ratio) - business_use_ratio) < 1e-12
    )
    return (
        int(asset["cost_minor"]) == cost_minor
        and asset["currency"] == "EUR"
        and asset["depreciation_method"] == "xolo_annual_evidence_active_day_schedule"
        and asset["acquisition_transaction_id"] == acquisition_transaction_id
        and asset["placed_in_service_on"] == placed_in_service_on
        and asset["source_hash"] == source_hash
        and asset["amortizable_base_minor"] == amortizable_base_minor
        and asset["iva_treatment"] == iva_treatment
        and ratio_matches
        and asset["annual_rate_basis_points"] == annual_rate_basis_points
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
    desired_periods = {period_key for period_key, _ in allocations}
    derived_prefix = f"{_source_book_line_id(row)}:derived:"
    existing_derived = db.connection.execute(
        """
        SELECT ae.amortization_entry_id, ae.source_book_line_id, p.period_key
        FROM amortization_entries ae
        JOIN periods p ON p.period_id = ae.period_id
        WHERE ae.asset_id = ?
          AND ae.entry_kind = 'quarter_schedule'
          AND ae.tax_year = ?
        """,
        (asset_id, year),
    ).fetchall()
    for existing in existing_derived:
        source_line = existing["source_book_line_id"] or ""
        if (
            source_line.startswith(derived_prefix)
            and existing["period_key"] not in desired_periods
        ):
            db.delete_amortization_entry(existing["amortization_entry_id"])

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
    rows = db.connection.execute(
        """
        SELECT t.transaction_id, t.amount_minor, t.amount_eur_minor,
               MAX(tt.taxable_base_minor) AS taxable_base_minor,
               MAX(CASE WHEN tt.tax_code <> 'historical_g03' THEN 1 ELSE 0 END)
                   AS is_direct_acquisition
        FROM transactions t
        JOIN documents d ON d.document_id = t.document_id
        JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
        WHERE t.counterparty_id = ?
          AND d.issued_on = ?
          AND t.entry_type = 'expense'
          AND t.lifecycle_status NOT IN ('duplicate', 'rejected', 'void')
          AND COALESCE(tt.taxable_base_minor, 0) > 0
        GROUP BY t.transaction_id, t.amount_minor, t.amount_eur_minor
        ORDER BY t.transaction_date, t.transaction_id
        """,
        (counterparty_id, issued_on),
    ).fetchall()
    direct = [row for row in rows if row["is_direct_acquisition"]]
    if len(direct) == 1:
        return dict(direct[0])
    if not direct and len(rows) == 1:
        return dict(rows[0])
    return None


def _prune_superseded_source_book_records(db: LedgerDB) -> Counter[str]:
    counts: Counter[str] = Counter()
    duplicate_lines = db.connection.execute(
        """
        SELECT d.document_type, ds.source_book_line_id
        FROM document_sources ds
        JOIN documents d ON d.document_id = ds.document_id
        WHERE ds.source_book_line_id <> ''
        GROUP BY d.document_type, ds.source_book_line_id
        HAVING COUNT(DISTINCT ds.document_id) > 1
        """
    ).fetchall()
    for duplicate_line in duplicate_lines:
        line_id = duplicate_line["source_book_line_id"]
        document_type = duplicate_line["document_type"]
        candidates = db.connection.execute(
            """
            SELECT ds.document_source_id, ds.document_id, ds.created_at,
                   d.counterparty_id, p.status AS period_status,
                   EXISTS(SELECT 1 FROM transactions t WHERE t.document_id = ds.document_id) AS has_transaction,
                   EXISTS(SELECT 1 FROM assets a WHERE a.document_id = ds.document_id) AS has_asset
            FROM document_sources ds
            JOIN documents d ON d.document_id = ds.document_id
            LEFT JOIN periods p ON p.period_id = d.period_id
            WHERE ds.source_book_line_id = ? AND d.document_type = ?
            ORDER BY has_transaction DESC, has_asset DESC, ds.created_at DESC, ds.document_source_id DESC
            """,
            (line_id, document_type),
        ).fetchall()
        referenced = [row for row in candidates if row["has_transaction"] or row["has_asset"]]
        referenced_document_ids = {row["document_id"] for row in referenced}
        if len(referenced_document_ids) > 1:
            raise ValueError(
                f"Source-book line {line_id!r} maps to multiple live documents and cannot be pruned"
            )
        canonical_document_id = (
            referenced[0]["document_id"] if referenced else candidates[0]["document_id"]
        )
        superseded = [row for row in candidates if row["document_id"] != canonical_document_id]
        if any(row["period_status"] in {"closed", "amended"} for row in superseded):
            counts["prune_skipped_immutable"] += 1
            continue
        with db.connection:
            for row in superseded:
                db.connection.execute(
                    "DELETE FROM document_sources WHERE document_source_id = ?",
                    (row["document_source_id"],),
                )
                counts["pruned_document_sources"] += 1
                document_references = db.connection.execute(
                    """
                    SELECT
                        EXISTS(SELECT 1 FROM document_sources WHERE document_id = ?) OR
                        EXISTS(SELECT 1 FROM transactions WHERE document_id = ?) OR
                        EXISTS(SELECT 1 FROM assets WHERE document_id = ?) OR
                        EXISTS(SELECT 1 FROM documents WHERE rectifies_document_id = ?) AS is_referenced
                    """,
                    (row["document_id"],) * 4,
                ).fetchone()["is_referenced"]
                if document_references:
                    continue
                db.connection.execute(
                    "DELETE FROM documents WHERE document_id = ?",
                    (row["document_id"],),
                )
                counts["pruned_documents"] += 1
                if not row["counterparty_id"]:
                    continue
                if _prune_unreferenced_migration_counterparty(db, row["counterparty_id"]):
                    counts["pruned_counterparties"] += 1
    return counts


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

    row_key = _reconciliation_row_key(row, period_key)
    issue_source_hash = _stable_payload_hash(
        {"kind": "validation_issue", "period_key": period_key, "row": row}
    )
    dedupe_key = f"xolo-reconcile:{row_key}"
    existing = db.connection.execute(
        """
        SELECT * FROM validation_issues
        WHERE period_id = (SELECT period_id FROM periods WHERE period_key = ?)
          AND dedupe_key = ?
        """,
        (period_key, dedupe_key),
    ).fetchone()
    issue_status = (
        existing["issue_status"]
        if existing is not None
        and existing["issue_status"] in {"resolved", "ignored"}
        and existing["source_hash"] == issue_source_hash
        else "open"
    )
    issue = db.add_validation_issue(
        period_key=period_key,
        issue_code=status,
        severity="error",
        message=_reconciliation_issue_message(row, period_key, import_batch_id),
        blocking=True,
        subject_table="xolo_expense_reconcile",
        subject_id=row_key,
        issue_status=issue_status,
        source_hash=issue_source_hash,
        dedupe_key=dedupe_key,
    )
    return {
        "period_key": period_key,
        "issue_code": issue["issue_code"],
        "subject_id": issue["subject_id"] or "",
        "message": issue["message"],
    }


def _source_book_reconciliation_index(
    source_rows: list[dict[str, str]],
) -> dict[tuple[str, str, str], list[dict[str, str]]]:
    index: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in source_rows:
        if (row.get("import_status") or "imported").strip().lower() != "imported":
            continue
        if (row.get("source_book_type") or "").strip() not in QUARTERLY_BOOK_TYPES:
            continue
        if not (row.get("irpf_deductible_eur") or "").strip():
            continue
        key = _source_book_evidence_key(
            date_text=row.get("date", ""),
            party=row.get("supplier", ""),
            number=row.get("document_number", ""),
        )
        index.setdefault(key, []).append(row)
    return index


def _source_book_resolution_reason(
    reconciliation_row: dict[str, str],
    source_book_evidence: dict[tuple[str, str, str], list[dict[str, str]]],
) -> str | None:
    status = (reconciliation_row.get("status") or "").strip().lower()
    if status not in {
        "amount_mismatch",
        "pending_confirmation",
        "deductibility_pending_confirmation",
    }:
        return None
    key = _source_book_evidence_key(
        date_text=reconciliation_row.get("date", ""),
        party=reconciliation_row.get("recipient", ""),
        number=reconciliation_row.get("number", ""),
    )
    matches = source_book_evidence.get(key, [])
    if len(matches) != 1:
        return None
    evidence = matches[0]
    deductible = _amount_text(evidence.get("irpf_deductible_eur"))
    source_line = _source_book_line_id(evidence) or _row_line_key(evidence)
    return (
        f"Official Xolo source-book row {source_line} records IRPF deductible EUR "
        f"{deductible}; it supersedes the earlier {status} reconciliation."
    )


def _source_book_evidence_key(
    *,
    date_text: str,
    party: str,
    number: str,
) -> tuple[str, str, str]:
    return (
        _normalize_date(date_text),
        "".join(character for character in party.casefold() if character.isalnum()),
        "".join(character for character in number.casefold() if character.isalnum()),
    )


def _reconciliation_row_key(row: dict[str, str], period_key: str) -> str:
    return _external_key(
        "reconcile-row",
        period_key,
        row.get("xolo_id", ""),
        row.get("number", ""),
        row.get("date", ""),
    )


def _resolve_reconciliation_subject(
    db: LedgerDB,
    *,
    period_key: str,
    subject_id: str,
    reason: str,
) -> int:
    open_rows = db.connection.execute(
        """
        SELECT vi.validation_issue_id, vi.row_version
        FROM validation_issues vi
        JOIN periods p ON p.period_id = vi.period_id
        WHERE p.period_key = ?
          AND vi.subject_table = 'xolo_expense_reconcile'
          AND vi.subject_id = ?
          AND vi.issue_status = 'open'
        """,
        (period_key, subject_id),
    ).fetchall()
    for row in open_rows:
        db.resolve_issue(
            row["validation_issue_id"],
            reason=reason,
            expected_row_version=row["row_version"],
        )
    return len(open_rows)


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


def _row_reference(row: dict[str, str], supplier: str) -> str:
    line_id = (row.get("source_book_line_id") or "").strip() or "?"
    return f"row {line_id}, supplier '{supplier}'"


def _record_counterparty_divergence(
    db: LedgerDB,
    *,
    row: dict[str, str],
    counterparty: Mapping[str, Any],
    country_code: str,
) -> None:
    period_key = (row.get("period") or "").strip().upper()
    if not period_key:
        return
    line_id = (row.get("source_book_line_id") or "").strip() or "?"
    known_country = str(counterparty["country_code"] or "").strip().upper() or "ZZ"
    db.add_validation_issue(
        period_key=period_key,
        issue_code="counterparty_country_divergence",
        severity="warning",
        message=(
            f"Source row {line_id} lists {counterparty['display_name']} in {country_code} "
            f"with placeholder identifiers only, while the ledger identity is {known_country}; "
            "the existing counterparty was kept."
        ),
        blocking=False,
        subject_table="counterparties",
        subject_id=str(counterparty["counterparty_id"]),
        dedupe_key=f"counterparty-country-divergence:{counterparty['counterparty_id']}:{country_code}",
        source_hash=_stable_payload_hash(
            {
                "kind": "counterparty_country_divergence",
                "counterparty_id": str(counterparty["counterparty_id"]),
                "country_code": country_code,
                "source_book_line_id": line_id,
            }
        ),
    )


def _upsert_counterparty(
    db: LedgerDB, row: dict[str, str], *, previous_counterparty_id: str | None = None,
) -> str:
    desired_counterparty_id, supplier = _counterparty_identity(row)
    country_code = (row.get("counterparty_country_code") or "ZZ").strip().upper() or "ZZ"
    raw_tax_id = (row.get("counterparty_tax_id") or "").strip()
    tax_id = raw_tax_id if _usable_tax_id(raw_tax_id) else None
    raw_vat_id = (row.get("counterparty_vat_id") or "").strip().upper()
    vat_id = raw_vat_id if _usable_tax_id(raw_vat_id) else None
    external_key = _counterparty_external_key(row, supplier)
    exact_matches = db.connection.execute(
        """
        SELECT * FROM counterparties
        WHERE external_key = ?
           OR (? IS NOT NULL AND tax_id = ?)
           OR (? IS NOT NULL AND vat_id = ?)
        ORDER BY CASE WHEN external_key = ? THEN 0 ELSE 1 END, created_at
        """,
        (external_key, tax_id, tax_id, vat_id, vat_id, external_key),
    ).fetchall()
    if len(exact_matches) > 1:
        raise CounterpartyMatchError("Counterparty identifiers are ambiguous")
    existing = exact_matches[0] if exact_matches else None
    # An explicit reviewed identity may supersede old source facts while keeping
    # the historical source key. It is not a new name-only match.
    def trusted_source_binding(candidate):
        return candidate["external_key"] == external_key and db.connection.execute(
            "SELECT 1 FROM counterparty_identities WHERE counterparty_id = ? AND is_primary = 1",
            (candidate["counterparty_id"],),
        ).fetchone() is not None

    def conflicts(candidate):
        return identity_conflicts(
            candidate, country_code=country_code, tax_id=tax_id, vat_id=vat_id, connection=db.connection,
        )

    placeholder_divergence = False
    previous = None
    if previous_counterparty_id:
        previous = db.connection.execute(
            "SELECT * FROM counterparties WHERE counterparty_id = ?", (previous_counterparty_id,)
        ).fetchone()
    if previous is not None and conflicts(previous) and not trusted_source_binding(previous):
        raise CounterpartyMatchError(
            f"Historical counterparty conflict for source row {row.get('source_book_line_id', '')}; review explicitly"
        )
    if existing is not None and conflicts(existing) and not trusted_source_binding(existing):
        if tax_id is not None or vat_id is not None:
            raise CounterpartyMatchError(
                f"Counterparty source key conflicts with known identity ({_row_reference(row, supplier)})"
            )
        # Provider exports record the same supplier with placeholder identifiers
        # and inconsistent country codes across months. A row without any usable
        # identifier cannot contradict a known identity, so keep the counterparty
        # and surface the divergence for review instead of aborting the import.
        _record_counterparty_divergence(db, row=row, counterparty=existing, country_code=country_code)
        placeholder_divergence = True
    all_name_matches = find_name_candidates(db.connection, supplier)
    name_matches = [
        candidate for candidate in all_name_matches
        if not conflicts(candidate)
    ]
    if existing is None and not name_matches and any(
        candidate["name_is_manual"] for candidate in all_name_matches
    ):
        raise CounterpartyMatchError(
            f"Imported alias conflicts with reviewed counterparty identity ({_row_reference(row, supplier)})"
        )
    if existing is None and len(name_matches) == 1:
        existing = name_matches[0]
    if tax_id is None and vat_id is None:
        identity_matches = [
            candidate for candidate in name_matches if _counterparty_has_usable_identity(candidate)
        ]
        if len(identity_matches) == 1 and (
            existing is None or not _counterparty_has_usable_identity(existing)
        ):
            # Some official Xolo rows contain a placeholder NIF while another
            # row for the exact same supplier carries the reviewed identity.
            existing = identity_matches[0]
    if existing is None and len(name_matches) > 1:
        raise CounterpartyMatchError("Counterparty name is ambiguous; review the source identity")
    primary_identity = None
    if existing is not None:
        primary_identity = db.connection.execute(
            """
            SELECT counterparty_identity_id
            FROM counterparty_identities
            WHERE counterparty_id = ? AND is_primary = 1
            """,
            (existing["counterparty_id"],),
        ).fetchone()
    counterparty_id = existing["counterparty_id"] if existing is not None else desired_counterparty_id
    effective_external_key = external_key
    if (
        existing is not None
        and external_key.startswith("counterparty-name:")
        and str(existing["external_key"] or "").startswith(
            ("counterparty-vat:", "counterparty-tax:")
        )
        and _counterparty_identity_key_is_usable(str(existing["external_key"]))
    ):
        effective_external_key = existing["external_key"]
    reusing_reviewed_identity = (
        existing is not None
        and tax_id is None
        and vat_id is None
        and _counterparty_has_usable_identity(existing)
    )
    effective_display_name = supplier
    effective_country_code = country_code
    effective_tax_id = tax_id
    if existing is not None and (
        existing["name_is_manual"]
        or reusing_reviewed_identity
        or primary_identity is not None
        or _source_matches_existing_identity(existing, tax_id=tax_id, vat_id=vat_id)
    ):
        effective_display_name = existing["display_name"]
    if reusing_reviewed_identity or primary_identity is not None or placeholder_divergence:
        effective_country_code = existing["country_code"]
    if (
        existing is not None
        and primary_identity is not None
        and existing["tax_id"]
        and tax_id != existing["tax_id"]
    ):
        # A source-backed primary identity is an explicit review decision.
        # Historical source-book refreshes may update rows, but cannot replace
        # the reviewed master identifier with an older imported value.
        effective_tax_id = existing["tax_id"]
    counterparty = db.upsert_counterparty(
        counterparty_id=counterparty_id,
        external_key=effective_external_key,
        tax_id=effective_tax_id,
        display_name=effective_display_name,
        country_code=effective_country_code,
        source_hash=_stable_payload_hash({"kind": "counterparty", "supplier": supplier}),
    )
    if vat_id and counterparty.get("vat_id") != vat_id:
        counterparty = db.set_counterparty_tax_profile(
            counterparty["counterparty_id"],
            vat_id=vat_id,
            roi_status=counterparty.get("roi_status") or "unknown",
            professional_supplier=(
                bool(counterparty["professional_supplier"])
                if counterparty.get("professional_supplier") is not None
                else None
            ),
            retention_expected=(
                bool(counterparty["retention_expected"])
                if counterparty.get("retention_expected") is not None
                else None
            ),
            expected_row_version=counterparty["row_version"],
            source_hash=counterparty["source_hash"],
        )
    elif counterparty.get("vat_id") and not _usable_tax_id(str(counterparty["vat_id"])):
        counterparty = db.set_counterparty_tax_profile(
            counterparty["counterparty_id"],
            vat_id=None,
            roi_status=counterparty.get("roi_status") or "unknown",
            professional_supplier=(
                bool(counterparty["professional_supplier"])
                if counterparty.get("professional_supplier") is not None
                else None
            ),
            retention_expected=(
                bool(counterparty["retention_expected"])
                if counterparty.get("retention_expected") is not None
                else None
            ),
            expected_row_version=counterparty["row_version"],
            source_hash=counterparty["source_hash"],
        )
    return counterparty["counterparty_id"]


def _counterparty_has_usable_identity(counterparty: Any) -> bool:
    def value(field: str) -> Any:
        try:
            return counterparty[field]
        except (IndexError, KeyError, TypeError):
            return None

    return any(
        _usable_tax_id(str(value(field) or ""))
        for field in ("tax_id", "vat_id")
    ) or _counterparty_identity_key_is_usable(str(value("external_key") or ""))


def _source_matches_existing_identity(
    counterparty: Any,
    *,
    tax_id: str | None,
    vat_id: str | None,
) -> bool:
    existing_ids = {
        str(counterparty[field] or "").strip().upper()
        for field in ("tax_id", "vat_id")
        if counterparty[field] and _usable_tax_id(str(counterparty[field]))
    }
    source_ids = {
        value.strip().upper()
        for value in (tax_id, vat_id)
        if value and _usable_tax_id(value)
    }
    return bool(existing_ids & source_ids)


def _prune_unreferenced_migration_counterparty(
    db: LedgerDB,
    counterparty_id: str | None,
) -> bool:
    if not counterparty_id:
        return False
    with db.transaction():
        if _migration_counterparty_is_referenced(db, counterparty_id):
            return False
        return db.connection.execute(
            "DELETE FROM counterparties WHERE counterparty_id = ?", (counterparty_id,),
        ).rowcount > 0


def _migration_counterparty_is_referenced(db: LedgerDB, counterparty_id: str) -> bool:
    referenced = db.connection.execute(
        """
        SELECT
            EXISTS(SELECT 1 FROM documents WHERE counterparty_id = ?) OR
            EXISTS(SELECT 1 FROM transactions WHERE counterparty_id = ?) OR
            EXISTS(SELECT 1 FROM counterparty_identities WHERE counterparty_id = ?) OR
            EXISTS(SELECT 1 FROM invoice_templates WHERE counterparty_id = ?) OR
            EXISTS(SELECT 1 FROM outgoing_invoice_drafts WHERE counterparty_id = ?) OR
            EXISTS(SELECT 1 FROM counterparty_name_changes WHERE counterparty_id = ?) OR
            EXISTS(SELECT 1 FROM counterparties WHERE counterparty_id = ? AND name_is_manual = 1) OR
            EXISTS(
                SELECT 1 FROM validation_issues
                WHERE subject_table = 'counterparties' AND subject_id = ?
                  AND issue_status = 'open'
            ) AS is_referenced
        """,
        (counterparty_id,) * 8,
    ).fetchone()["is_referenced"]
    return bool(referenced)


def _normalized_counterparty_name(value: str) -> str:
    return normalize_counterparty_name(value or "")


def _counterparty_identity(row: dict[str, str]) -> tuple[str, str]:
    supplier = (row.get("supplier") or row.get("recipient") or "Unknown counterparty").strip()
    identity = _counterparty_external_key(row, supplier)
    return _uuid_for("counterparty", identity), supplier


def _counterparty_external_key(row: dict[str, str], supplier: str) -> str:
    vat_id = (row.get("counterparty_vat_id") or "").strip().upper()
    tax_id = (row.get("counterparty_tax_id") or "").strip().upper()
    country_code = (row.get("counterparty_country_code") or "").strip().upper()
    if _usable_tax_id(vat_id):
        return _external_key("counterparty-vat", vat_id)
    if _usable_tax_id(tax_id):
        return _external_key("counterparty-tax", country_code, tax_id)
    normalized_name = _normalized_counterparty_name(supplier)
    return _external_key("counterparty-name", normalized_name)


def _usable_tax_id(value: str) -> bool:
    return usable_tax_id(value)


def _counterparty_identity_key_is_usable(value: str) -> bool:
    prefix, separator, identity = (value or "").partition(":")
    if not separator or prefix not in {"counterparty-vat", "counterparty-tax"}:
        return False
    return _usable_tax_id(identity.rsplit(":", 1)[-1])


def _upsert_source_document(
    db: LedgerDB,
    *,
    row: dict[str, str],
    counterparty_id: str,
    import_batch_id: str,
    document_type: str,
    period_key: str | None,
    number_override: str | None = None,
    total_minor: int | None = None,
) -> dict[str, Any]:
    line_key = _row_line_key(row)
    issued_on = _normalize_date(row.get("date", ""))
    document_number = number_override or (row.get("document_number") or "").strip() or None
    source_book_line_id = _source_book_line_id(row)
    existing_row = None
    if source_book_line_id:
        existing_row = db.connection.execute(
            """
            SELECT d.*
            FROM documents d
            JOIN document_sources ds ON ds.document_id = d.document_id
            WHERE ds.source_book_line_id = ? AND d.document_type = ?
            ORDER BY d.created_at, d.document_id
            LIMIT 1
            """,
            (source_book_line_id, document_type),
        ).fetchone()
    existing = dict(existing_row) if existing_row is not None else None
    if existing is None and document_number:
        existing_row = db.connection.execute(
            """
            SELECT * FROM documents
            WHERE counterparty_id = ? AND document_type = ?
              AND document_number = ? AND issued_on = ?
            """,
            (counterparty_id, document_type, document_number, issued_on),
        ).fetchone()
        existing = dict(existing_row) if existing_row is not None else None

    canonical_key = "|".join(
        [counterparty_id, document_type, document_number or line_key, issued_on]
    )
    external_key = _external_key("document", canonical_key)
    if existing is None:
        existing_row = db.connection.execute(
            "SELECT * FROM documents WHERE external_key = ?",
            (external_key,),
        ).fetchone()
        existing = dict(existing_row) if existing_row is not None else None
    document = db.upsert_document(
        document_id=(existing["document_id"] if existing else _uuid_for("document", canonical_key)),
        external_key=external_key,
        counterparty_id=counterparty_id,
        import_batch_id=import_batch_id,
        document_type=document_type,
        document_number=document_number,
        issued_on=issued_on,
        period_key=period_key,
        currency="EUR",
        total_minor=total_minor,
        lifecycle_status=(existing["lifecycle_status"] if existing else "approved"),
        source_hash=_stable_payload_hash({"kind": "canonical_document", "key": canonical_key}),
        expected_row_version=(existing["row_version"] if existing else None),
    )

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


def _treatment_notes(
    row: dict[str, str], *, normalized_operation_key: str | None = None
) -> str:
    notes = [
        f"source_book_line_id={_row_line_key(row)}",
        f"reason_code={row.get('reason_code', '').strip() or 'unknown'}",
    ]
    if row.get("source_file_format"):
        notes.append(f"source_file_format={row['source_file_format'].strip()}")
    source_operation_key = (row.get("operation_key") or "").strip()
    if source_operation_key:
        notes.append(f"source_operation_key={source_operation_key}")
        if normalized_operation_key and normalized_operation_key != source_operation_key:
            notes.append(f"normalized_operation_key={normalized_operation_key}")
    if row.get("notes"):
        notes.append(f"notes={row['notes'].strip()}")
    if _minor_from_text(row.get("gross_eur", "0")) == 0:
        notes.append("zero_amount_source_book_row=true")
    period_key = (row.get("period") or "").strip().upper()
    if period_key and row.get("date"):
        document_period = _quarter_from_iso_date(_normalize_date(row["date"]))
        if document_period != period_key:
            notes.append(f"document_period={document_period}")
            notes.append(f"tax_period={period_key}")
            if row.get("booking_date"):
                notes.append(f"booking_date={_normalize_date(row['booking_date'])}")
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
    country_code = (row.get("counterparty_country_code") or "").strip().upper()
    operation_key = (row.get("operation_key") or "").strip()
    qualification = (row.get("operation_qualification") or "").strip().upper()
    reverse_charge = _is_yes(row.get("reverse_charge"))
    taxable_base_minor = _optional_minor(row.get("taxable_base_eur")) or 0
    deductible_vat_minor = _optional_minor(row.get("deductible_vat_eur")) or 0
    vat_minor = _optional_minor(row.get("vat_eur")) or 0
    has_vat_evidence = any(
        (row.get(field) or "").strip()
        for field in (
            "operation_key",
            "operation_qualification",
            "reverse_charge",
            "taxable_base_eur",
            "vat_eur",
            "deductible_vat_eur",
        )
    )
    if kind == "income" and qualification.startswith("N2"):
        return "outside_scope"
    if kind == "income" and has_vat_evidence and (taxable_base_minor or vat_minor):
        return "domestic_output" if vat_minor else "domestic_output_zero"
    if kind == "expense" and operation_key == "09" and (taxable_base_minor or deductible_vat_minor):
        return "eu_service_expense"
    if kind == "expense" and reverse_charge:
        if country_code == "ES":
            return "domestic_reverse_charge_expense"
        if country_code in EU_COUNTRY_CODES:
            return "eu_service_expense"
        return "non_eu_service_expense"
    if kind == "expense" and has_vat_evidence and deductible_vat_minor:
        return "domestic_input"
    reason_code = (row.get("reason_code") or "").strip().lower()
    if kind == "income":
        return "historical_income"
    if reason_code:
        return f"historical_{reason_code}"
    return "historical_expense"


def _include_modelo303(row: dict[str, str], kind: str) -> bool:
    return _tax_code_for_row(row, kind) in {
        "outside_scope",
        "domestic_output",
        "domestic_output_zero",
        "domestic_reverse_charge_expense",
        "non_eu_service_expense",
        "eu_service_expense",
        "domestic_input",
    }


def _aeat_invoice_type_for_row(row: dict[str, str], kind: str) -> str:
    source = _aeat_code_prefix(
        row.get("invoice_type"),
        {
            "F1", "F2", "F3", "F4", "F5", "F6",
            "R1", "R2", "R3", "R4", "R5",
            "SF", "DV", "AJ", "LC",
        },
    )
    if source:
        return source
    if kind == "expense" and (row.get("reason_code") or "").strip().upper() == "G45":
        return "F6"
    return "F1"


def _aeat_operation_key_for_row(row: dict[str, str], tax_code: str) -> str:
    if tax_code in {"non_eu_service_expense", "domestic_reverse_charge_expense"}:
        return "01"
    if tax_code == "eu_service_expense":
        return "09"
    source = (row.get("operation_key") or "").strip()
    return source if re.fullmatch(r"\d{2}", source) else "01"


def _aeat_reverse_charge_for_row(
    row: dict[str, str], tax_code: str, kind: str
) -> bool | None:
    if kind == "income":
        return None
    if tax_code in {"non_eu_service_expense", "domestic_reverse_charge_expense"}:
        return True
    if tax_code == "eu_service_expense":
        return False
    return _is_yes(row.get("reverse_charge"))


def _aeat_expense_concept_for_row(row: dict[str, str], kind: str) -> str | None:
    if kind != "expense":
        return None
    reason_code = (row.get("reason_code") or "").strip().upper()
    return reason_code if re.fullmatch(r"G(?:Y)?\d{1,2}", reason_code) else None


def _aeat_code_prefix(value: str | None, allowed: set[str]) -> str | None:
    text = (value or "").strip().upper()
    for code in sorted(allowed, key=len, reverse=True):
        if text == code or text.startswith(f"{code} ") or text.startswith(f"{code}-"):
            return code
    return None


def _should_infer_clave09_output(row: dict[str, str], kind: str) -> bool:
    if kind != "expense" or (row.get("operation_key") or "").strip() != "09":
        return False
    explicit_vat = _optional_minor(row.get("vat_eur"))
    if explicit_vat is not None:
        return explicit_vat == 0
    amount = _minor_from_text(row.get("gross_eur", "0"))
    base = _optional_minor(row.get("taxable_base_eur"))
    if base is None:
        base = _optional_minor(row.get("deductible_base_eur"))
    return base is None or amount == base


def _clave09_output_vat(row: dict[str, str]) -> int:
    base = _optional_minor(row.get("taxable_base_eur"))
    if base == 0:
        return 0
    rate_text = (row.get("vat_rate_percent") or "").strip()
    if base is None or re.fullmatch(r"\d+(?:[.,]\d{1,2})?\s*%?", rate_text) is None:
        raise ValueError(
            f"Source row {_row_line_key(row)}: clave 09 requires an explicit taxable "
            "base and reviewed VAT rate to infer output VAT; the deductible amount is insufficient"
        )
    rate = Decimal(rate_text.removesuffix("%").strip().replace(",", "."))
    if not Decimal("0") < rate <= Decimal("100"):
        raise ValueError(
            f"Source row {_row_line_key(row)}: review the positive VAT rate for clave 09 "
            "before inferring output VAT; a supplier's zero rate does not establish the Spanish rate"
        )
    return int((Decimal(base) * rate / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _reviewed_vat_rate_basis_points(
    row: dict[str, str],
    *,
    tax_code: str,
    taxable_base_minor: int,
    vat_minor: int,
) -> int | None:
    parsed = _vat_rate_basis_points(row.get("vat_rate_percent"))
    if tax_code not in {
        "non_eu_service_expense",
        "domestic_reverse_charge_expense",
        "eu_service_expense",
    }:
        return parsed
    if taxable_base_minor > 0 and vat_minor > 0:
        expected = int(
            (Decimal(taxable_base_minor) * Decimal("0.21")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        if abs(vat_minor - expected) <= 1:
            return 2100
    return parsed


def _document_total_minor(row: dict[str, str], fallback: int) -> int:
    return _optional_minor(row.get("invoice_total_eur")) or fallback


def _vat_rate_basis_points(value: str | None) -> int | None:
    text = (value or "").strip().replace("%", "").replace(",", ".")
    if not text or text.casefold() == "sin iva":
        return None
    match = re.search(r"\d+(?:\.\d+)?", text)
    if match is None:
        return None
    return int(round(float(match.group(0)) * 100))


def _is_yes(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"s", "si", "sí", "y", "yes", "true", "1"}


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
    booking_text = (row.get("booking_date") or "").strip()
    if booking_text:
        booking_date = _normalize_date(booking_text)
        if _quarter_from_iso_date(booking_date) == period_key:
            return booking_date
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
