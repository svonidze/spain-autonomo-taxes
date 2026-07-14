from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterable

from .fx_policy import ALLOWED_PRODUCTION_SOURCES
from .intake import inspect_document
from .ledger_db import LedgerDB, initialize, open as open_ledger_db
from .obligations import ActivityFact, CounterpartyFact, detect_obligations
from .revolut import RevolutMatchCandidate, load_revolut_payments_csv, match_revolut_payments
from .sheet_sync import SheetRow, diff_sheet_rows
from .tax_engine import (
    CalculationBlocked,
    CalculationResult,
    TaxRow,
    WITHHOLDING_TYPE_BY_TAX_CODE,
    calculate_modelo100_business_support,
    calculate_modelo130_rows,
    calculate_modelo303_rows,
    calculate_modelo347_rows,
    calculate_modelo349_rows,
    calculate_modelo390,
    calculate_retention_rows,
)
from .tax_rules import ANNUAL_FORM_CODES, QUARTERLY_FORM_CODES, difficult_expense_rule_for_year


DEFAULT_DB = Path(".local") / "autonomo.sqlite"
Handler = Callable[[argparse.Namespace], int]


def register_operational_commands(subparsers: argparse._SubParsersAction[Any]) -> None:
    db = subparsers.add_parser("db", help="Initialize or inspect the canonical SQLite ledger")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_init = db_sub.add_parser("init", help="Initialize the ledger schema")
    _db_arg(db_init)
    db_init.set_defaults(_operational_handler=_cmd_db_init)
    db_status = db_sub.add_parser("status", help="Show schema, counts, periods, and blockers")
    _db_arg(db_status)
    db_status.set_defaults(_operational_handler=_cmd_db_status)

    ingest = subparsers.add_parser("ingest", help="Extract a document into review without posting it")
    _db_arg(ingest)
    ingest.add_argument("path", type=Path)
    ingest.add_argument(
        "--kind",
        required=True,
        choices=["income_invoice", "expense_invoice", "bank_statement", "tax_report", "other"],
    )
    ingest.add_argument("--period", required=True)
    ingest.add_argument("--issued-on", required=True)
    ingest.add_argument("--document-number")
    ingest.add_argument("--counterparty-id")
    ingest.add_argument("--drive-file-id")
    ingest.add_argument("--tesseract-command", default="tesseract")
    ingest.set_defaults(_operational_handler=_cmd_ingest)

    transactions = subparsers.add_parser("transactions", help="Create and post reviewed ledger transactions")
    transaction_sub = transactions.add_subparsers(dest="transaction_command", required=True)
    transaction_add = transaction_sub.add_parser("add", help="Add a transaction from a typed JSON object")
    _db_arg(transaction_add)
    transaction_add.add_argument("--input", type=Path, required=True)
    transaction_add.set_defaults(_operational_handler=_cmd_transaction_add)
    transaction_transition = transaction_sub.add_parser("transition", help="Advance a transaction lifecycle")
    _db_arg(transaction_transition)
    transaction_transition.add_argument("transaction_id")
    transaction_transition.add_argument(
        "--to-status",
        required=True,
        choices=["extracted", "needs_review", "approved", "posted", "duplicate", "rejected", "void"],
    )
    transaction_transition.add_argument("--expected-row-version", type=int, required=True)
    transaction_transition.set_defaults(_operational_handler=_cmd_transaction_transition)

    treatment = subparsers.add_parser("tax-treatment", help="Classify a reviewed transaction for IRPF and IVA")
    treatment_sub = treatment.add_subparsers(dest="tax_treatment_command", required=True)
    treatment_add = treatment_sub.add_parser("add", help="Add or update a detailed treatment from JSON")
    _db_arg(treatment_add)
    treatment_add.add_argument("--input", type=Path, required=True)
    treatment_add.set_defaults(_operational_handler=_cmd_tax_treatment_add)

    fx = subparsers.add_parser("fx", help="Persist an explicit, sourced FX rate")
    fx_sub = fx.add_subparsers(dest="fx_command", required=True)
    fx_add = fx_sub.add_parser("add", help="Add an FX rate from JSON")
    _db_arg(fx_add)
    fx_add.add_argument("--input", type=Path, required=True)
    fx_add.set_defaults(_operational_handler=_cmd_fx_add)

    migration = subparsers.add_parser("migrate-history", help="Import Xolo source-book history into SQLite")
    _db_arg(migration)
    migration.add_argument("--source-book", type=Path, required=True)
    migration.add_argument("--reconciliation", type=Path)
    migration.add_argument("--xolo-calculations", type=Path)
    migration.add_argument("--modelo130-reconciliation", type=Path)
    migration.add_argument("--document-inventory", type=Path)
    migration.set_defaults(_operational_handler=_cmd_migrate_history)

    obligations = subparsers.add_parser("obligations", help="Explain and persist form obligations")
    obligation_sub = obligations.add_subparsers(dest="obligation_command", required=True)
    explain = obligation_sub.add_parser("explain", help="Evaluate due/not_due/unknown from a facts JSON file")
    _db_arg(explain)
    explain.add_argument("--facts", type=Path, required=True)
    explain.add_argument("--year", type=int, required=True)
    explain.add_argument("--quarter", type=int, choices=[1, 2, 3, 4])
    explain.add_argument("--filed-form", action="append", default=[])
    explain.add_argument("--persist", action="store_true")
    explain.add_argument("--out", type=Path)
    explain.set_defaults(_operational_handler=_cmd_obligations_explain)
    obligation_mark = obligation_sub.add_parser("mark", help="Persist a reviewed obligation decision")
    _db_arg(obligation_mark)
    obligation_mark.add_argument("--period", required=True)
    obligation_mark.add_argument("--form", required=True)
    obligation_mark.add_argument("--determination", choices=["due", "not_due", "unknown"], required=True)
    obligation_mark.add_argument("--filing-status", choices=["unknown", "due", "filed", "waived"], required=True)
    obligation_mark.add_argument("--explanation", required=True)
    obligation_mark.add_argument("--source-citation")
    obligation_mark.add_argument("--filed-at")
    obligation_mark.add_argument("--expected-row-version", type=int)
    obligation_mark.set_defaults(_operational_handler=_cmd_obligation_mark)

    issues = subparsers.add_parser("issues", help="List or explicitly waive validation issues")
    issues_sub = issues.add_subparsers(dest="issues_command", required=True)
    issues_list = issues_sub.add_parser("list")
    _db_arg(issues_list)
    issues_list.add_argument("--period")
    issues_list.set_defaults(_operational_handler=_cmd_issues_list)
    issues_waive = issues_sub.add_parser("waive")
    _db_arg(issues_waive)
    issues_waive.add_argument("issue_id")
    issues_waive.add_argument("--reason", required=True)
    issues_waive.add_argument("--expected-row-version", type=int, required=True)
    issues_waive.set_defaults(_operational_handler=_cmd_issues_waive)
    issues_resolve = issues_sub.add_parser("resolve")
    _db_arg(issues_resolve)
    issues_resolve.add_argument("issue_id")
    issues_resolve.add_argument("--reason", required=True)
    issues_resolve.add_argument("--expected-row-version", type=int, required=True)
    issues_resolve.set_defaults(_operational_handler=_cmd_issues_resolve)

    period = subparsers.add_parser("period", help="Validate, close, or amend an accounting period")
    period_sub = period.add_subparsers(dest="period_command", required=True)
    period_validate = period_sub.add_parser("validate")
    _db_arg(period_validate)
    period_validate.add_argument("period")
    period_validate.add_argument(
        "--allow-authoritative-history",
        action="store_true",
        help="Accept approved Xolo source-book rows only when immutable lineage and filed-period evidence exist",
    )
    period_validate.set_defaults(_operational_handler=_cmd_period_validate)
    period_close = period_sub.add_parser("close")
    _db_arg(period_close)
    period_close.add_argument("period")
    period_close.add_argument("--expected-row-version", type=int)
    period_close.add_argument(
        "--allow-authoritative-history",
        action="store_true",
        help="Accept approved Xolo source-book rows only when immutable lineage and filed-period evidence exist",
    )
    period_close.set_defaults(_operational_handler=_cmd_period_close)
    period_amend = period_sub.add_parser("amend")
    _db_arg(period_amend)
    period_amend.add_argument("period")
    period_amend.add_argument("--in-period", required=True)
    period_amend.add_argument("--reason", required=True)
    period_amend.add_argument("--expected-row-version", type=int)
    period_amend.set_defaults(_operational_handler=_cmd_period_amend)

    backup = subparsers.add_parser("backup", help="Create or restore a consistent SQLite snapshot")
    backup_sub = backup.add_subparsers(dest="backup_command", required=True)
    backup_create = backup_sub.add_parser("create")
    _db_arg(backup_create)
    backup_create.add_argument("--out", type=Path, required=True)
    backup_create.set_defaults(_operational_handler=_cmd_backup_create)
    backup_restore = backup_sub.add_parser("restore")
    _db_arg(backup_restore)
    backup_restore.add_argument("--from", dest="source", type=Path, required=True)
    backup_restore.set_defaults(_operational_handler=_cmd_backup_restore)

    bank = subparsers.add_parser("bank", help="Import and reconcile bank payments")
    bank_sub = bank.add_subparsers(dest="bank_command", required=True)
    bank_import = bank_sub.add_parser("import-revolut")
    _db_arg(bank_import)
    bank_import.add_argument("--csv", type=Path, required=True)
    bank_import.set_defaults(_operational_handler=_cmd_bank_import_revolut)

    sheet = subparsers.add_parser("sheet", help="Export or reconcile the Google Sheet review projection")
    sheet_sub = sheet.add_subparsers(dest="sheet_command", required=True)
    sheet_export = sheet_sub.add_parser("export")
    _db_arg(sheet_export)
    sheet_export.add_argument("--out-dir", type=Path, required=True)
    sheet_export.set_defaults(_operational_handler=_cmd_sheet_export)
    sheet_reconcile = sheet_sub.add_parser("reconcile")
    _db_arg(sheet_reconcile)
    sheet_reconcile.add_argument("--tab", choices=["transactions", "issues", "assets"], required=True)
    sheet_reconcile.add_argument("--remote-csv", type=Path, required=True)
    sheet_reconcile.add_argument("--out", type=Path)
    sheet_reconcile.set_defaults(_operational_handler=_cmd_sheet_reconcile)
    sheet_apply = sheet_sub.add_parser("apply", help="Apply reviewed rows with optimistic concurrency")
    _db_arg(sheet_apply)
    sheet_apply.add_argument("--tab", choices=["transactions", "issues", "assets"], required=True)
    sheet_apply.add_argument("--remote-csv", type=Path, required=True)
    sheet_apply.set_defaults(_operational_handler=_cmd_sheet_apply)

    books = subparsers.add_parser("books", help="Build deterministic accounting books from SQLite")
    books_sub = books.add_subparsers(dest="books_command", required=True)
    books_build = books_sub.add_parser("build")
    _db_arg(books_build)
    books_build.add_argument("--period", required=True)
    books_build.add_argument("--out-dir", type=Path, required=True)
    books_build.set_defaults(_operational_handler=_cmd_books_build)

    filing_package = subparsers.add_parser(
        "filing-package",
        help="Create or verify an export-only filing package; this never submits to AEAT",
    )
    filing_sub = filing_package.add_subparsers(dest="filing_package_command", required=True)
    filing_build = filing_sub.add_parser("build")
    _db_arg(filing_build)
    filing_build.add_argument("--period", required=True)
    filing_build.add_argument("--out-dir", type=Path, required=True)
    filing_build.set_defaults(_operational_handler=_cmd_filing_package_build)
    filing_verify = filing_sub.add_parser("verify")
    _db_arg(filing_verify)
    filing_verify.add_argument("--manifest", type=Path, required=True)
    filing_verify.set_defaults(_operational_handler=_cmd_filing_package_verify)

    filings = subparsers.add_parser("filings", help="Persist immutable calculation or filing snapshots")
    filings_sub = filings.add_subparsers(dest="filings_command", required=True)
    filings_snapshot = filings_sub.add_parser("snapshot")
    _db_arg(filings_snapshot)
    filings_snapshot.add_argument("--period", required=True)
    filings_snapshot.add_argument("--status", choices=["draft", "baseline", "filed", "submitted", "final"], required=True)
    filings_snapshot.add_argument("--payload", type=Path, required=True)
    filings_snapshot.add_argument("--filed-on")
    filings_snapshot.set_defaults(_operational_handler=_cmd_filing_snapshot)

    calculate = subparsers.add_parser("calculate", help="Calculate a form from reviewed SQLite rows")
    _db_arg(calculate)
    calculate.add_argument(
        "--form",
        required=True,
        choices=["130", "303", "349", "390", "347", "111", "190", "115", "180", "216", "296", "100"],
    )
    calculate.add_argument("--year", type=int, required=True)
    calculate.add_argument("--quarter", type=int, choices=[1, 2, 3, 4])
    calculate.add_argument("--mode", choices=["production", "verify_history"], default="production")
    calculate.add_argument(
        "--allow-authoritative-history",
        action="store_true",
        help="Use approved Xolo source-book rows with immutable lineage and filed-period evidence in production",
    )
    calculate.add_argument(
        "--difficult-expenses-policy",
        choices=["calculate", "source_book_total", "exclude_by_documented_decision"],
        default="calculate",
    )
    calculate.add_argument("--decision-ref")
    calculate.add_argument("--previous-positive-07")
    calculate.add_argument("--previous-negative-carry")
    calculate.add_argument("--withholding-and-payments")
    calculate.add_argument("--reduction")
    calculate.add_argument("--unsupported-annual-category", action="append", default=[])
    calculate.add_argument("--out", type=Path)
    calculate.set_defaults(_operational_handler=_cmd_calculate)

    offboarding = subparsers.add_parser("offboarding", help="Build or verify the Xolo exit manifest")
    offboarding_sub = offboarding.add_subparsers(dest="offboarding_command", required=True)
    offboarding_build = offboarding_sub.add_parser("build")
    offboarding_build.add_argument("paths", type=Path, nargs="+")
    offboarding_build.add_argument("--out", type=Path, required=True)
    offboarding_build.set_defaults(_operational_handler=_cmd_offboarding_build)
    offboarding_verify = offboarding_sub.add_parser("verify")
    offboarding_verify.add_argument("--manifest", type=Path, required=True)
    offboarding_verify.set_defaults(_operational_handler=_cmd_offboarding_verify)

    verify_history = subparsers.add_parser("verify-history", help="Rebuild legacy history without production FX shortcuts")
    _db_arg(verify_history)
    verify_history.add_argument("--source-book", type=Path, required=True)
    verify_history.add_argument("--reconciliation", type=Path)
    verify_history.add_argument("--xolo-calculations", type=Path)
    verify_history.add_argument("--modelo130-reconciliation", type=Path)
    verify_history.add_argument("--document-inventory", type=Path)
    verify_history.add_argument("--out", type=Path)
    verify_history.set_defaults(_operational_handler=_cmd_verify_history)


def run_operational_handler(args: argparse.Namespace) -> int | None:
    handler: Handler | None = getattr(args, "_operational_handler", None)
    return None if handler is None else handler(args)


def _db_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)


def _cmd_db_init(args: argparse.Namespace) -> int:
    with initialize(args.db) as db:
        _emit({"database": str(args.db.resolve()), "schema_version": _schema_version(db), "counts": db.table_counts()})
    return 0


def _cmd_db_status(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        periods = db.list_periods()
        payload = {
            "database": str(args.db.resolve()),
            "schema_version": _schema_version(db),
            "counts": db.table_counts(),
            "periods": [
                {
                    "period": row["period_key"],
                    "status": row["status"],
                    "row_version": row["row_version"],
                }
                for row in periods
            ],
            "open_blocking_issues": len(db.list_issues()),
        }
        _emit(payload)
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    result = inspect_document(args.path, args.kind, tesseract_command=args.tesseract_command)
    with open_ledger_db(args.db) as db:
        batch = db.add_import_batch(
            source_name=str(args.path.resolve()),
            source_hash=result.sha256,
            batch_key=f"document:{result.sha256}",
            notes=f"Extraction method: {result.extraction_method}",
        )
        document = db.upsert_document(
            external_key=f"sha256:{result.sha256}",
            counterparty_id=args.counterparty_id,
            import_batch_id=batch["import_batch_id"],
            document_type=args.kind,
            document_number=args.document_number,
            issued_on=args.issued_on,
            period_key=args.period,
            lifecycle_status=result.status,
            source_hash=result.sha256,
        )
        document = db.set_document_storage(
            document["document_id"],
            source_path=result.source_path,
            drive_file_id=args.drive_file_id,
            mime_type=result.mime_type,
            expected_row_version=document["row_version"],
        )
        if result.needs_review:
            db.add_validation_issue(
                period_key=args.period,
                issue_code="document_structural_review",
                severity="error",
                message="; ".join(result.structural_errors),
                subject_table="documents",
                subject_id=document["document_id"],
                blocking=True,
                source_hash=result.sha256,
            )
    _emit({**asdict(result), "document_id": document["document_id"], "row_version": document["row_version"]})
    return 0


def _cmd_transaction_add(args: argparse.Namespace) -> int:
    payload = _load_json_object(args.input)
    payload.setdefault("lifecycle_status", "received")
    if payload["lifecycle_status"] != "received":
        raise ValueError("New production transactions must start at lifecycle_status=received")
    if payload.get("transaction_id") or payload.get("expected_row_version") is not None:
        raise ValueError("Use transactions transition for lifecycle updates")
    with open_ledger_db(args.db) as db:
        row = db.add_transaction(**payload)
    _emit(row)
    return 0


def _cmd_transaction_transition(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        row = db.transition_transaction(
            args.transaction_id,
            lifecycle_status=args.to_status,
            expected_row_version=args.expected_row_version,
        )
    _emit(row)
    return 0


def _cmd_tax_treatment_add(args: argparse.Namespace) -> int:
    payload = _load_json_object(args.input)
    with open_ledger_db(args.db) as db:
        existing = db.connection.execute(
            """
            SELECT row_version FROM tax_treatments
            WHERE transaction_id = ? AND treatment_type = ? AND jurisdiction = ?
            """,
            (
                payload.get("transaction_id"),
                payload.get("treatment_type"),
                payload.get("jurisdiction", "ES"),
            ),
        ).fetchone()
        if existing is not None and payload.get("expected_row_version") is None:
            raise ValueError("Updating a tax treatment requires expected_row_version")
        row = db.add_detailed_tax_treatment(**payload)
    _emit(row)
    return 0


def _cmd_fx_add(args: argparse.Namespace) -> int:
    payload = _load_json_object(args.input)
    payload.setdefault("source_hash", _json_payload_hash(payload))
    with open_ledger_db(args.db) as db:
        row = db.add_fx_rate(**payload)
    _emit(row)
    return 0


def _cmd_migrate_history(args: argparse.Namespace) -> int:
    from .history_migration import migrate_xolo_history

    with open_ledger_db(args.db) as db:
        result = migrate_xolo_history(
            db,
            args.source_book,
            args.reconciliation,
            args.xolo_calculations,
            args.modelo130_reconciliation,
            args.document_inventory,
        )
    _emit(_jsonable(result))
    return 0


def _cmd_obligations_explain(args: argparse.Namespace) -> int:
    facts = json.loads(args.facts.read_text(encoding="utf-8"))
    activity_payload = dict(facts.get("activity", facts))
    activity_payload.pop("counterparties", None)
    activity_payload["tax_year"] = args.year
    for key in _decimal_fact_fields(ActivityFact):
        if activity_payload.get(key) is not None:
            activity_payload[key] = Decimal(str(activity_payload[key]))
    activity = ActivityFact(**activity_payload)
    counterparties: list[CounterpartyFact] = []
    for raw in facts.get("counterparties", []):
        row = dict(raw)
        if row.get("annual_total_eur") is not None:
            row["annual_total_eur"] = Decimal(str(row["annual_total_eur"]))
        counterparties.append(CounterpartyFact(**row))
    results = detect_obligations(activity, counterparties, args.filed_form)

    if args.persist:
        with open_ledger_db(args.db) as db:
            for code, obligation in results.items():
                period_key = _obligation_period(code, args.year, args.quarter)
                filing_status = (
                    "filed" if obligation.filed else "due" if obligation.status == "due" else "waived"
                    if obligation.status == "not_due"
                    else "unknown"
                )
                db.add_obligation(
                    period_key=period_key,
                    obligation_code=code,
                    filing_status=filing_status,
                    determination=obligation.status,
                    explanation=obligation.explanation,
                    source_citation=obligation.source_citation,
                    blocking=obligation.blocking,
                    filed_at=date.today().isoformat() if obligation.filed else None,
                )
    payload = {code: asdict(value) for code, value in results.items()}
    _write_or_emit(payload, args.out)
    return 0


def _cmd_obligation_mark(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        existing = db.connection.execute(
            """
            SELECT o.row_version
            FROM obligations o JOIN periods p ON p.period_id = o.period_id
            WHERE p.period_key = ? AND o.obligation_code = ?
            """,
            (args.period, args.form),
        ).fetchone()
        if existing is not None and args.expected_row_version is None:
            raise ValueError("Updating an obligation requires --expected-row-version")
        row = db.add_obligation(
            period_key=args.period,
            obligation_code=args.form,
            determination=args.determination,
            filing_status=args.filing_status,
            explanation=args.explanation,
            source_citation=args.source_citation,
            filed_at=args.filed_at,
            expected_row_version=args.expected_row_version,
        )
    _emit(row)
    return 0


def _cmd_issues_list(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        _emit(db.list_issues(period_key=args.period))
    return 0


def _cmd_issues_waive(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        _emit(
            db.waive_issue(
                args.issue_id,
                reason=args.reason,
                expected_row_version=args.expected_row_version,
            )
        )
    return 0


def _cmd_issues_resolve(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        _emit(
            db.resolve_issue(
                args.issue_id,
                reason=args.reason,
                expected_row_version=args.expected_row_version,
            )
        )
    return 0


def _cmd_period_validate(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        validation = db.validate_period(
            args.period,
            allow_authoritative_history=args.allow_authoritative_history,
        )
        if len(args.period) == 4 and args.period.isdigit():
            validation["asset_year"] = db.validate_asset_year(int(args.period))
            validation["ready"] = validation["ready"] and validation["asset_year"]["ready"]
    _emit(validation)
    return 0 if validation["ready"] else 2


def _cmd_period_close(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        _emit(
            db.close_period(
                args.period,
                expected_row_version=args.expected_row_version,
                allow_authoritative_history=args.allow_authoritative_history,
            )
        )
    return 0


def _cmd_period_amend(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        _emit(
            db.amend_period(
                args.period,
                amendment_period_key=args.in_period,
                reason=args.reason,
                expected_row_version=args.expected_row_version,
            )
        )
    return 0


def _cmd_backup_create(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        target = db.backup_to(args.out)
    _emit({"backup": str(target.resolve()), "sha256": _sha256(target)})
    return 0


def _cmd_backup_restore(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        safety_backup = args.db.with_name(
            f"{args.db.stem}.pre-restore-{timestamp}{args.db.suffix or '.sqlite'}"
        )
        db.backup_to(safety_backup)
        db.restore_from(args.source)
        _emit(
            {
                "database": str(args.db.resolve()),
                "restored_from": str(args.source.resolve()),
                "safety_backup": str(safety_backup.resolve()),
                "safety_backup_sha256": _sha256(safety_backup),
                "counts": db.table_counts(),
            }
        )
    return 0


def _cmd_bank_import_revolut(args: argparse.Namespace) -> int:
    payments = load_revolut_payments_csv(args.csv)
    source_digest = _sha256(args.csv)
    with open_ledger_db(args.db) as db:
        candidates = []
        transactions = db.list_transactions()
        for row in transactions:
            if row["lifecycle_status"] not in {"approved", "posted", "included_in_snapshot"}:
                continue
            if row["entry_type"] not in {"income", "expense"}:
                continue
            signed_minor = row["amount_original_minor"] or row["amount_minor"]
            if str(row["entry_type"]).startswith("expense"):
                signed_minor = -abs(signed_minor)
            candidates.append(
                RevolutMatchCandidate(
                    candidate_id=row["transaction_id"],
                    recognition_date=date.fromisoformat(row["transaction_date"]),
                    payment_date=None,
                    reference=row["external_key"] or row["description"],
                    amount_original=Decimal(signed_minor) / 100,
                    currency=row["original_currency"] or row["currency"],
                    amount_eur=(
                        (Decimal(-abs(row["amount_eur_minor"])) if str(row["entry_type"]).startswith("expense") else Decimal(row["amount_eur_minor"])) / 100
                        if row["amount_eur_minor"] is not None
                        else None
                    ),
                )
            )
        matches = {match.payment_id: match for match in match_revolut_payments(payments, candidates)}
        rollback = sqlite3.connect(":memory:")
        db.connection.backup(rollback)
        try:
            imported = []
            for payment in payments:
                match = matches[payment.payment_id]
                transaction_id = match.matched_candidate_ids[0] if match.outcome == "exact" else None
                row = db.add_payment(
                    transaction_id=transaction_id,
                    paid_on=payment.payment_date.isoformat(),
                    amount_minor=int(payment.amount_original * 100),
                    currency=payment.currency,
                    original_reference=payment.reference,
                    fee_minor=int(payment.fee_original * 100) if payment.fee_original is not None else None,
                    fee_currency=payment.currency if payment.fee_original is not None else None,
                    match_status=match.outcome,
                    source_hash=hashlib.sha256(f"{source_digest}:{payment.payment_id}".encode()).hexdigest(),
                )
                imported.append({"payment_id": row["payment_id"], "match": asdict(match)})
        except Exception:
            db.connection.rollback()
            rollback.backup(db.connection)
            raise
        finally:
            rollback.close()
    _emit({"source_sha256": source_digest, "payments": imported})
    return 0


def _cmd_sheet_export(args: argparse.Namespace) -> int:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with open_ledger_db(args.db) as db:
        tables = {
            "inbox_review": db.connection.execute(
                """SELECT d.document_id AS uuid, d.row_version, d.lifecycle_status AS status,
                          p.period_key, d.document_type, d.document_number, d.issued_on,
                          c.display_name AS counterparty, d.currency, d.total_minor,
                          d.source_path, d.drive_file_id, d.mime_type
                   FROM documents d
                   LEFT JOIN periods p ON p.period_id=d.period_id
                   LEFT JOIN counterparties c ON c.counterparty_id=d.counterparty_id
                   ORDER BY d.issued_on, d.document_id"""
            ).fetchall(),
            "counterparties": db.connection.execute(
                """SELECT counterparty_id AS uuid, row_version, 'open' AS status,
                          display_name, tax_id, country_code, vat_id, roi_status,
                          professional_supplier, retention_expected, email, phone
                   FROM counterparties ORDER BY display_name, counterparty_id"""
            ).fetchall(),
            "transactions": db.connection.execute(
                """SELECT t.transaction_id AS uuid, t.row_version,
                          CASE WHEN p.status IN ('closed', 'amended') THEN 'closed'
                               ELSE 'open' END AS status,
                          p.period_key, t.lifecycle_status, t.transaction_date, t.entry_type, t.description,
                          t.amount_original_minor, t.original_currency, t.amount_eur_minor,
                          tt.tax_code, tt.taxable_base_minor, tt.vat_minor,
                          tt.deductible_irpf_minor, tt.deductible_vat_minor,
                          tt.include_modelo130, tt.include_modelo303, tt.include_modelo347
                   FROM transactions t JOIN periods p ON p.period_id=t.period_id
                   LEFT JOIN tax_treatments tt ON tt.transaction_id=t.transaction_id
                   ORDER BY t.transaction_date, t.transaction_id"""
            ).fetchall(),
            "issues": db.connection.execute(
                """SELECT vi.validation_issue_id AS uuid, vi.row_version,
                          CASE WHEN p.status IN ('closed', 'amended') THEN 'closed'
                               ELSE vi.issue_status END AS status,
                          vi.issue_code, vi.severity, vi.message, vi.blocking, vi.waiver_reason,
                          vi.resolution_reason, vi.resolved_at
                   FROM validation_issues vi
                   LEFT JOIN periods p ON p.period_id=vi.period_id
                   ORDER BY vi.created_at, vi.validation_issue_id"""
            ).fetchall(),
            "assets": db.connection.execute(
                """SELECT a.asset_id AS uuid, a.row_version,
                          CASE WHEN COALESCE(tp.status, dp.status) IN ('closed', 'amended')
                                    OR COALESCE(ap.has_closed_period, 0) = 1 THEN 'closed'
                               ELSE 'open' END AS status,
                          a.asset_code, a.placed_in_service_on, a.cost_minor, a.currency,
                          a.amortizable_base_minor, a.iva_treatment, a.business_use_ratio,
                          a.annual_rate_basis_points, a.advisor_decision, a.advisor_decision_on
                   FROM assets a
                   LEFT JOIN transactions t ON t.transaction_id=a.acquisition_transaction_id
                   LEFT JOIN periods tp ON tp.period_id=t.period_id
                   LEFT JOIN documents d ON d.document_id=a.document_id
                   LEFT JOIN periods dp ON dp.period_id=d.period_id
                   LEFT JOIN (
                       SELECT ae.asset_id,
                              MAX(CASE WHEN p.status IN ('closed', 'amended') THEN 1 ELSE 0 END)
                                  AS has_closed_period
                       FROM amortization_entries ae
                       JOIN periods p ON p.period_id=ae.period_id
                       GROUP BY ae.asset_id
                   ) ap ON ap.asset_id=a.asset_id
                   ORDER BY a.asset_code"""
            ).fetchall(),
            "payments": db.connection.execute(
                """SELECT payment_id AS uuid, row_version, match_status AS status,
                          transaction_id, obligation_id, paid_on, amount_minor, currency,
                          original_reference, fee_minor, fee_currency
                   FROM payments ORDER BY paid_on, payment_id"""
            ).fetchall(),
            "filings": db.connection.execute(
                """SELECT fs.filing_snapshot_id AS uuid, fs.row_version, fs.status,
                          p.period_key, fs.snapshot_hash, fs.filed_on, fs.manifest_path
                   FROM filing_snapshots fs JOIN periods p ON p.period_id=fs.period_id
                   ORDER BY p.starts_on, fs.filed_on"""
            ).fetchall(),
            "obligations": db.connection.execute(
                """SELECT o.obligation_id AS uuid, o.row_version, o.filing_status AS status,
                          p.period_key, o.obligation_code, o.determination, o.explanation,
                          o.source_citation, o.blocking
                   FROM obligations o JOIN periods p ON p.period_id=o.period_id
                   ORDER BY p.starts_on, o.obligation_code"""
            ).fetchall(),
            "rules_sources": db.connection.execute(
                """SELECT rule_version_id AS uuid, row_version, 'historical' AS status,
                          rule_name, version, activated_at, source_hash
                   FROM rule_versions ORDER BY rule_name, version"""
            ).fetchall(),
        }
        outputs = {name: str(_write_rows_csv(args.out_dir / f"{name}.csv", rows)) for name, rows in tables.items()}
    _emit({"outputs": outputs})
    return 0


def _cmd_sheet_reconcile(args: argparse.Namespace) -> int:
    remote = _read_sheet_rows(args.remote_csv, tab=args.tab)
    with open_ledger_db(args.db) as db:
        local = _sheet_rows_from_db(db, args.tab)
    plan = diff_sheet_rows(local, remote)
    payload = {
        "tab": args.tab,
        "push_create": [row.uuid for row in plan.push_create],
        "push_update": [update.row.uuid for update in plan.push_update],
        "pull_create": [row.uuid for row in plan.pull_create],
        "pull_update": [row.uuid for row in plan.pull_update],
        "conflicts": [asdict(row) for row in plan.conflicts],
        "unchanged": list(plan.unchanged),
        "ok": not plan.conflicts,
    }
    _write_or_emit(payload, args.out)
    return 0 if payload["ok"] else 2


def _cmd_sheet_apply(args: argparse.Namespace) -> int:
    remote = _read_sheet_rows(args.remote_csv, tab=args.tab, editable_only=True)
    with open_ledger_db(args.db) as db:
        local = [
            SheetRow(
                uuid=row.uuid,
                row_version=row.row_version,
                status=row.status,
                values=_editable_sheet_values(args.tab, row.values),
            )
            for row in _sheet_rows_from_db(db, args.tab)
        ]
        local_by_uuid = {row.uuid: row for row in local}
        plan = diff_sheet_rows(local, remote)
        blockers = [asdict(row) for row in plan.conflicts]
        blockers.extend(
            {"uuid": row.uuid, "reason": "remote_create_not_allowed"}
            for row in plan.pull_create
        )
        blockers.extend(
            {"uuid": row.uuid, "reason": "sheet_missing_local_row"}
            for row in plan.push_create
        )
        blockers.extend(
            {"uuid": update.row.uuid, "reason": "sheet_is_stale"}
            for update in plan.push_update
        )
        for row in plan.pull_update:
            local_row = local_by_uuid[row.uuid]
            if row.row_version != local_row.row_version + 1:
                blockers.append(
                    {
                        "uuid": row.uuid,
                        "reason": "non_sequential_row_version",
                        "local_row_version": local_row.row_version,
                        "remote_row_version": row.row_version,
                    }
                )
        if blockers:
            _emit({"ok": False, "tab": args.tab, "applied": [], "conflicts": blockers})
            return 2

        rollback = sqlite3.connect(":memory:")
        db.connection.backup(rollback)
        try:
            applied: list[dict[str, Any]] = []
            for row in plan.pull_update:
                local_row = local_by_uuid[row.uuid]
                applied.append(_apply_reviewed_sheet_row(db, args.tab, local_row, row))
        except Exception:
            db.connection.rollback()
            rollback.backup(db.connection)
            raise
        finally:
            rollback.close()
    _emit({"ok": True, "tab": args.tab, "applied": applied, "conflicts": []})
    return 0


def _cmd_books_build(args: argparse.Namespace) -> int:
    from .books import write_accounting_books

    with open_ledger_db(args.db, read_only=True) as db:
        result = write_accounting_books(db, args.out_dir, period_key=args.period)
    _emit(_jsonable(result))
    return 0


def _cmd_filing_package_build(args: argparse.Namespace) -> int:
    from .filing_package import write_filing_package

    with open_ledger_db(args.db, read_only=True) as db:
        manifest = write_filing_package(db, args.out_dir, period_key=args.period)
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    _emit(
        {
            "manifest": str(manifest.resolve()),
            "submission": "manual_aeat_only",
            "package_stage": manifest_payload["package_stage"],
            "submission_ready": manifest_payload["submission_ready"],
            "readiness_blockers": manifest_payload["readiness_blockers"],
        }
    )
    return 0


def _cmd_filing_package_verify(args: argparse.Namespace) -> int:
    from .filing_package import verify_filing_package

    with open_ledger_db(args.db, read_only=True) as db:
        result = verify_filing_package(db, args.manifest)
    _emit(result)
    return 0 if result["ok"] else 2


def _cmd_filing_snapshot(args: argparse.Namespace) -> int:
    payload = _load_json_object(args.payload)
    with open_ledger_db(args.db) as db:
        row = db.create_filing_snapshot(
            args.period,
            status=args.status,
            filed_on=args.filed_on,
            payload=payload,
        )
    _emit(row)
    return 0


def _cmd_calculate(args: argparse.Namespace) -> int:
    if args.form in QUARTERLY_FORM_CODES and args.quarter is None:
        raise ValueError(f"Modelo {args.form} requires --quarter")
    if args.difficult_expenses_policy == "source_book_total" and args.mode != "verify_history":
        raise CalculationBlocked("source_book_total is restricted to verify_history")
    if args.difficult_expenses_policy == "exclude_by_documented_decision" and not args.decision_ref:
        raise CalculationBlocked("A documented decision reference is required to exclude difficult expenses")
    with open_ledger_db(args.db, read_only=True) as db:
        modelo303_periods: tuple[str, ...] = ()
        periods = [f"{args.year}-Q{args.quarter}"] if args.quarter else []
        if args.form == "390":
            modelo303_periods = _modelo303_inventory_periods(db, args.year)
            periods = list(modelo303_periods)
        if args.mode == "production":
            period_rows = {row["period_key"]: row for row in db.list_periods()}
            current_period = f"{args.year}-Q{args.quarter}" if args.quarter else None
            if args.form == "130" and current_period is not None:
                available_quarters = [
                    quarter
                    for quarter in range(1, args.quarter + 1)
                    if f"{args.year}-Q{quarter}" in period_rows
                ]
                if not available_quarters:
                    raise CalculationBlocked(f"{current_period} is missing from the production ledger")
                first_quarter = min(available_quarters)
                periods = [f"{args.year}-Q{quarter}" for quarter in range(first_quarter, args.quarter + 1)]
                missing = [period_key for period_key in periods if period_key not in period_rows]
                if missing:
                    raise CalculationBlocked(
                        f"Production YTD ledger has missing periods: {', '.join(missing)}"
                    )
            for period_key in periods:
                if period_key not in period_rows:
                    raise CalculationBlocked(f"{period_key} is missing from the production ledger")
                validation = db.validate_period(
                    period_key,
                    allow_authoritative_history=args.allow_authoritative_history,
                )
                is_current = period_key == current_period
                obligation_blockers = [
                    row
                    for row in validation["unresolved_obligations"]
                    if not is_current or row["determination"] == "unknown"
                ]
                data_blockers = (
                    validation["blocking_issues"],
                    obligation_blockers,
                    validation["review_documents"],
                    validation["review_transactions"],
                    validation["target_derived_fx"],
                )
                if any(data_blockers):
                    raise CalculationBlocked(f"{period_key} has unresolved production accounting data")
                if not is_current and period_rows[period_key]["status"] not in {"closed", "amended"}:
                    raise CalculationBlocked(
                        f"Prior YTD period {period_key} must be closed before production calculation"
                    )
        rows = _tax_rows_from_db(
            db,
            args.year,
            mode=args.mode,
            allow_authoritative_history=args.allow_authoritative_history,
        )
        if args.form == "130" and args.previous_positive_07 is None:
            args.previous_positive_07 = str(_previous_filed_positive(db, args.year, args.quarter))
        if args.form == "130" and args.previous_negative_carry is None:
            args.previous_negative_carry = str(_previous_filed_negative_carry(db, args.year, args.quarter))
        baseline_period = f"{args.year}-Q{args.quarter}" if args.quarter else str(args.year)
        baseline = _filed_baseline(db, baseline_period, args.form)
        if args.form == "130" and args.reduction is None:
            if args.mode == "verify_history" and baseline is not None:
                args.reduction = str(baseline["filed_values"].get("13", "0"))
            else:
                args.reduction = "0"
        result = _calculate(args, rows, modelo303_periods=modelo303_periods)
        payload = _calculation_payload(result)
        baseline = _filed_baseline(db, result.period, args.form)
        if baseline is not None:
            payload["filed_baseline"] = baseline
            payload["diff_from_filed"] = _calculation_diff(payload["values"], baseline["filed_values"])
    payload["calculation_mode"] = args.mode
    payload["authoritative_history_mode"] = bool(args.allow_authoritative_history)
    payload["difficult_expenses_policy"] = args.difficult_expenses_policy
    if args.decision_ref:
        payload["decision_ref"] = args.decision_ref
    _write_or_emit(payload, args.out)
    return 0


def _cmd_offboarding_build(args: argparse.Namespace) -> int:
    from .offboarding import build_offboarding_manifest

    rows = build_offboarding_manifest(args.paths)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    _emit({"manifest": str(args.out.resolve()), "rows": len(rows)})
    return 0


def _cmd_offboarding_verify(args: argparse.Namespace) -> int:
    from .offboarding import verify_offboarding_manifest

    result = verify_offboarding_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
    _emit(result)
    return 0 if result["ok"] else 2


def _cmd_verify_history(args: argparse.Namespace) -> int:
    from .history_migration import migrate_xolo_history

    with open_ledger_db(args.db) as db:
        migration = migrate_xolo_history(
            db,
            args.source_book,
            args.reconciliation,
            args.xolo_calculations,
            args.modelo130_reconciliation,
            args.document_inventory,
        )
        payload = {
            "mode": "verify_history",
            "migration": _jsonable(migration),
            "counts": db.table_counts(),
            "blocking_issues": db.list_issues(),
            "production_fx_sources": sorted(ALLOWED_PRODUCTION_SOURCES),
            "note": "Target-derived FX remains excluded from production period close.",
        }
    _write_or_emit(payload, args.out)
    return 0


def _calculate(
    args: argparse.Namespace,
    rows: list[TaxRow],
    *,
    modelo303_periods: tuple[str, ...] = (),
) -> CalculationResult:
    rule = difficult_expense_rule_for_year(args.year)
    if args.form == "130":
        _, report = calculate_modelo130_rows(
            rows,
            year=args.year,
            quarter=args.quarter,
            difficult_expenses_rate=rule.rate,
            previous_positive_casilla_07=Decimal(args.previous_positive_07),
            previous_negative_carry=Decimal(args.previous_negative_carry),
            withholding_and_payments=(
                Decimal(args.withholding_and_payments)
                if args.withholding_and_payments is not None
                else None
            ),
            reduction=Decimal(args.reduction),
            include_difficult_expenses=args.difficult_expenses_policy == "calculate",
        )
        warning = {
            "calculate": "Difficult expenses were recomputed from the year-specific AEAT rule.",
            "source_book_total": "Historical replay: source-book casilla 02 total was used without adding another difficult-expense amount.",
            "exclude_by_documented_decision": f"Difficult expenses excluded under documented decision {args.decision_ref}.",
        }[args.difficult_expenses_policy]
        return CalculationResult(report.form, report.period, report.values, report.lineage, (warning,))
    if args.form == "303":
        return calculate_modelo303_rows(rows, year=args.year, quarter=args.quarter)
    if args.form == "349":
        return calculate_modelo349_rows(rows, year=args.year, quarter=args.quarter)
    if args.form == "390":
        return calculate_modelo390(
            [
                calculate_modelo303_rows(rows, year=args.year, quarter=int(period[-1]))
                for period in modelo303_periods
            ],
            year=args.year,
            expected_periods=modelo303_periods,
        )
    if args.form == "347":
        return calculate_modelo347_rows(rows, year=args.year)
    if args.form in {"111", "190"}:
        return calculate_retention_rows(
            rows,
            year=args.year,
            quarter=args.quarter if args.form == "111" else None,
            withholding_type="professional",
        )
    if args.form in {"115", "180"}:
        return calculate_retention_rows(
            rows,
            year=args.year,
            quarter=args.quarter if args.form == "115" else None,
            withholding_type="rent",
        )
    if args.form in {"216", "296"}:
        return calculate_retention_rows(
            rows,
            year=args.year,
            quarter=args.quarter if args.form == "216" else None,
            withholding_type="nonresident",
        )
    return calculate_modelo100_business_support(
        rows,
        year=args.year,
        difficult_expenses_rate=rule.rate,
        difficult_expenses_cap=rule.annual_cap_eur,
        unsupported_categories=args.unsupported_annual_category,
    )


def _modelo303_inventory_periods(db: LedgerDB, year: int) -> tuple[str, ...]:
    rows = db.connection.execute(
        """
        SELECT p.period_key, o.determination, o.filing_status
        FROM obligations o
        JOIN periods p ON p.period_id = o.period_id
        WHERE o.obligation_code = '303'
          AND p.period_key LIKE ?
        ORDER BY p.starts_on, p.period_key
        """,
        (f"{year}-Q%",),
    ).fetchall()
    unresolved = [row["period_key"] for row in rows if row["determination"] == "unknown"]
    if unresolved:
        raise CalculationBlocked(
            "Modelo 390 has unresolved Modelo 303 obligation periods: " + ", ".join(unresolved)
        )
    periods = tuple(
        row["period_key"]
        for row in rows
        if row["determination"] == "due" or row["filing_status"] == "filed"
    )
    if not periods:
        raise CalculationBlocked(
            f"Modelo 390 has no due or filed Modelo 303 periods in the obligation inventory for {year}"
        )
    return periods


def _tax_rows_from_db(
    db: LedgerDB,
    year: int,
    *,
    mode: str = "production",
    allow_authoritative_history: bool = False,
) -> list[TaxRow]:
    authoritative_ids = {
        row["transaction_id"]
        for row in db.list_authoritative_history_transactions(year=year)
    } if mode == "production" and allow_authoritative_history else set()
    grouped: dict[str, dict[str, Any]] = {}
    for raw in db.list_tax_rows(year=year):
        if raw["entry_type"] == "verify_history_adjustment" and mode != "verify_history":
            continue
        if mode == "production" and raw["lifecycle_status"] not in {"posted", "included_in_snapshot"}:
            if not (
                raw["lifecycle_status"] == "approved"
                and raw["transaction_id"] in authoritative_ids
            ):
                continue
        if int(raw.get("asset_count") or 0) > 1:
            raise CalculationBlocked(
                f"Transaction {raw['transaction_id']} is linked to multiple assets and must be split before tax calculation"
            )
        bucket = grouped.get(raw["transaction_id"])
        if bucket is None:
            grouped[raw["transaction_id"]] = dict(raw)
            continue
        if raw.get("tax_code") and raw["tax_code"] != "unknown":
            current = bucket.get("tax_code")
            if current not in {None, "", "unknown", raw["tax_code"]}:
                raise CalculationBlocked(f"Conflicting tax codes for {raw['transaction_id']}")
            bucket["tax_code"] = raw["tax_code"]
        for key in (
            "taxable_base_minor",
            "vat_minor",
            "deductible_irpf_minor",
            "deductible_vat_minor",
            "withholding_minor",
        ):
            value = raw.get(key)
            if value is None:
                continue
            current = bucket.get(key)
            if current is not None and int(current) != int(value):
                raise CalculationBlocked(
                    f"Conflicting {key} values for transaction {raw['transaction_id']}"
                )
            bucket[key] = int(value)
        for key in ("include_modelo130", "include_modelo303", "include_modelo347"):
            bucket[key] = int(bool(bucket.get(key)) or bool(raw.get(key)))

    output: list[TaxRow] = []
    for row in grouped.values():
        entry_type = str(row["entry_type"])
        kind = "income" if entry_type.startswith("income") else "expense" if entry_type.startswith("expense") else "adjustment"
        amount_minor = row["amount_eur_minor"] if row["amount_eur_minor"] is not None else row["amount_minor"]
        output.append(
            TaxRow(
                transaction_id=row["transaction_id"],
                tax_date=date.fromisoformat(row["transaction_date"]),
                kind=kind,
                amount_eur=Decimal(amount_minor) / 100,
                taxable_base_eur=Decimal(row.get("taxable_base_minor") or 0) / 100,
                vat_eur=Decimal(row.get("vat_minor") or 0) / 100,
                deductible_irpf_eur=Decimal(row.get("deductible_irpf_minor") or 0) / 100,
                deductible_vat_eur=Decimal(row.get("deductible_vat_minor") or 0) / 100,
                withholding_eur=Decimal(row.get("withholding_minor") or 0) / 100,
                tax_code=row.get("tax_code") or "unknown",
                counterparty_id=row.get("counterparty_id") or "",
                counterparty_name=row.get("counterparty_name") or "",
                country_code=row.get("country_code") or "",
                vat_id=row.get("vat_id") or "",
                include_modelo130=bool(row.get("include_modelo130")),
                include_modelo303=bool(row.get("include_modelo303")),
                include_modelo347=bool(row.get("include_modelo347")),
                withholding_type=WITHHOLDING_TYPE_BY_TAX_CODE.get(
                    row.get("tax_code") or "",
                    "professional" if row.get("withholding_minor") else "",
                ),
                asset_id=row.get("asset_id") or "",
            )
        )
    return output


def _sheet_rows_from_db(db: LedgerDB, tab: str) -> list[SheetRow]:
    query = {
        "transactions": """SELECT t.transaction_id AS uuid, t.row_version,
                            CASE WHEN p.status IN ('closed', 'amended') THEN 'closed'
                                 ELSE 'open' END AS status,
                            t.lifecycle_status, t.description, t.amount_eur_minor
                         FROM transactions t JOIN periods p ON p.period_id=t.period_id""",
        "issues": """SELECT vi.validation_issue_id AS uuid, vi.row_version,
                            CASE WHEN p.status IN ('closed', 'amended') THEN 'closed'
                                 ELSE vi.issue_status END AS status,
                            vi.issue_code, vi.message, vi.blocking,
                            COALESCE(vi.resolution_reason, '') AS resolution_reason,
                            COALESCE(vi.waiver_reason, '') AS waiver_reason
                     FROM validation_issues vi
                     LEFT JOIN periods p ON p.period_id=vi.period_id""",
        "assets": """SELECT a.asset_id AS uuid, a.row_version,
                         CASE WHEN COALESCE(tp.status, dp.status) IN ('closed', 'amended')
                                   OR COALESCE(ap.has_closed_period, 0) = 1 THEN 'closed'
                              ELSE 'open' END AS status,
                         a.asset_code, a.advisor_decision, a.advisor_decision_on
                      FROM assets a
                      LEFT JOIN transactions t ON t.transaction_id=a.acquisition_transaction_id
                      LEFT JOIN periods tp ON tp.period_id=t.period_id
                      LEFT JOIN documents d ON d.document_id=a.document_id
                      LEFT JOIN periods dp ON dp.period_id=d.period_id
                      LEFT JOIN (
                          SELECT ae.asset_id,
                                 MAX(CASE WHEN p.status IN ('closed', 'amended') THEN 1 ELSE 0 END)
                                     AS has_closed_period
                          FROM amortization_entries ae
                          JOIN periods p ON p.period_id=ae.period_id
                          GROUP BY ae.asset_id
                      ) ap ON ap.asset_id=a.asset_id""",
    }[tab]
    rows = db.connection.execute(query).fetchall()
    return [
        SheetRow(
            uuid=row["uuid"],
            row_version=row["row_version"],
            status=row["status"],
            values={
                key: _sheet_value(row[key])
                for key in row.keys()
                if key not in {"uuid", "row_version", "status"}
            },
        )
        for row in rows
    ]


def _read_sheet_rows(
    path: Path,
    *,
    tab: str,
    editable_only: bool = False,
) -> list[SheetRow]:
    fields = {
        "transactions": {"lifecycle_status"},
        "issues": {"resolution_reason", "waiver_reason"},
        "assets": {"advisor_decision", "advisor_decision_on"},
    }
    if not editable_only:
        fields = {
            "transactions": fields["transactions"] | {"description", "amount_eur_minor"},
            "issues": fields["issues"] | {"issue_code", "message", "blocking"},
            "assets": fields["assets"] | {"asset_code"},
        }
    selected_fields = fields[tab]
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    normalized: list[SheetRow] = []
    for raw in rows:
        row = dict(raw)
        normalized.append(
            SheetRow(
                uuid=str(row.pop("uuid")),
                row_version=int(row.pop("row_version")),
                status=str(row.pop("status")),
                values={key: value for key, value in row.items() if key in selected_fields},
            )
        )
    return normalized


def _editable_sheet_values(tab: str, values: dict[str, Any]) -> dict[str, Any]:
    editable_fields = {
        "transactions": {"lifecycle_status"},
        "issues": {"resolution_reason", "waiver_reason"},
        "assets": {"advisor_decision", "advisor_decision_on"},
    }[tab]
    return {key: value for key, value in values.items() if key in editable_fields}


def _apply_reviewed_sheet_row(
    db: LedgerDB,
    tab: str,
    local: SheetRow,
    remote: SheetRow,
) -> dict[str, Any]:
    if tab == "transactions":
        target = str(remote.values.get("lifecycle_status", "")).strip()
        if not target:
            raise ValueError(f"Reviewed transaction {remote.uuid} is missing lifecycle_status")
        return db.transition_transaction(
            remote.uuid,
            lifecycle_status=target,
            expected_row_version=local.row_version,
        )
    if tab == "issues":
        if remote.status == "resolved":
            return db.resolve_issue(
                remote.uuid,
                reason=str(remote.values.get("resolution_reason", "")).strip(),
                expected_row_version=local.row_version,
            )
        if remote.status == "ignored":
            return db.waive_issue(
                remote.uuid,
                reason=str(remote.values.get("waiver_reason", "")).strip(),
                expected_row_version=local.row_version,
            )
        raise ValueError(f"Reviewed issue {remote.uuid} must transition to resolved or ignored")
    return db.update_asset_decision(
        remote.uuid,
        advisor_decision=str(remote.values.get("advisor_decision", "")).strip() or None,
        advisor_decision_on=str(remote.values.get("advisor_decision_on", "")).strip() or None,
        expected_row_version=local.row_version,
    )


def _write_rows_csv(path: Path, rows: Iterable[sqlite3.Row]) -> Path:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(materialized[0].keys()) if materialized else ["uuid", "row_version", "status"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(dict(row) for row in materialized)
    return path


def _obligation_period(code: str, year: int, quarter: int | None) -> str:
    if code in ANNUAL_FORM_CODES:
        return str(year)
    if quarter is None:
        raise ValueError("Quarterly obligation persistence requires --quarter")
    return f"{year}-Q{quarter}"


def _decimal_fact_fields(cls: type[Any]) -> set[str]:
    return {
        "professional_income_withholding_ratio",
        "net_assets_eur",
        "foreign_accounts_value_eur",
        "foreign_securities_value_eur",
        "foreign_real_estate_value_eur",
        "foreign_assets_increase_since_last_report_eur",
        "foreign_crypto_value_eur",
        "foreign_crypto_increase_since_last_report_eur",
    }


def _schema_version(db: LedgerDB) -> int:
    return int(db.connection.execute("PRAGMA user_version").fetchone()[0])


def _calculation_payload(result: CalculationResult) -> dict[str, Any]:
    return {
        "form": result.form,
        "period": result.period,
        "values": _jsonable(result.values),
        "lineage": {key: list(value) for key, value in result.lineage.items()},
        "warnings": list(result.warnings),
    }


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
    for row in rows:
        payload = json.loads(row["payload_json"])
        payload_form = str(payload.get("form", "")).lower().replace("modelo", "")
        if payload_form == str(form).lower().replace("modelo", ""):
            filed_values = payload.get("filed_values") or payload.get("values") or {}
            return {
                "filed_on": row["filed_on"],
                "snapshot_hash": row["snapshot_hash"],
                "source": payload.get("baseline_kind", "historical"),
                "status": row["status"],
                "filed_values": filed_values,
                "filename": payload.get("filename", ""),
            }
    return None


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


def _calculation_diff(recomputed: dict[str, Any], filed: dict[str, Any]) -> dict[str, str]:
    differences: dict[str, str] = {}
    for key in sorted(set(recomputed) & set(filed)):
        try:
            differences[key] = f"{(Decimal(str(recomputed[key])) - Decimal(str(filed[key]))):.2f}"
        except Exception:
            continue
    return differences


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload


def _json_payload_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _write_or_emit(payload: Any, path: Path | None) -> None:
    normalized = _jsonable(payload)
    if path is None:
        _emit(normalized)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
    _emit({"written": str(path.resolve())})


def _emit(payload: Any) -> None:
    print(json.dumps(_jsonable(payload), indent=2, ensure_ascii=False))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, Path)):
        return str(value)
    if isinstance(value, sqlite3.Row):
        return {key: _jsonable(value[key]) for key in value.keys()}
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sheet_value(value: Any) -> str:
    return "" if value is None else str(value)
