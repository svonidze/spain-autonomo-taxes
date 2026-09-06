from __future__ import annotations

from .counterparty_names import CounterpartyMatchError, find_name_candidates

import argparse
import csv
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Callable, Iterable, Mapping

from .fx_policy import ALLOWED_PRODUCTION_SOURCES, FXRateDeviationError, check_manual_rate
from .fx_reference import fetch_eur_rate
from .intake import (
    ExpenseInboxCleanupCandidate,
    InboxCleanupError,
    archive_evidence,
    cleanup_expense_inbox_source,
    evidence_archive_path,
    inspect_document,
    validate_expense_inbox_cleanup,
)
from .intake_bundle import (
    InvoiceAmounts,
    extract_invoice_amounts,
    extract_invoice_number,
    review_requirements,
)
from .invoice_series import collect_invoice_number_observations, invoice_series_status
from .ledger_db import (
    LedgerDB,
    LedgerDbError,
    StaleRowVersionError,
    initialize,
    open as open_ledger_db,
)
from .money import cents
from .non_invoice_expenses import (
    IDENTITY_AEAT_TYPES,
    PRESETS as NON_INVOICE_EXPENSE_PRESETS,
    NonInvoiceExpenseInput,
    quarter_key as non_invoice_quarter_key,
    record_non_invoice_expense,
)
from .obligations import ActivityFact, CounterpartyFact, detect_obligations
from .parsers import LedgerEntry, parse_any_date, parse_expense, parse_income_invoice
from .posting import (
    build_expense_inbox_cleanup_candidate,
    build_posting_preview,
    evaluate_transaction_posting,
    expense_inbox_cleanup_row,
    prevalidate_expense_inbox_cleanup,
)
from .private_paths import default_private_root
from .revolut import (
    RevolutMatchCandidate,
    RevolutPayment,
    load_revolut_payments_csv,
    match_revolut_payments,
)
from .review_packet import apply_review_packet, packet_json, prepare_review_packet
from .sheet_intake import (
    INTAKE_TABS,
    IntakeSheetError,
    IntakeSheetRow,
    load_intake_csv,
    resolve_evidence_path,
    write_intake_writeback_csv,
)
from .sheet_sync import SheetRow, diff_sheet_rows
from .storage_migration import (
    StorageMigrationError,
    adopt_google_drive_originals,
    migrate_legacy_storage,
)
from .storage_cleanup_preflight import check_cleanup_preflight
from .storage_reconcile import StorageReconcileError, reconcile_backend
from .storage_service import register_local_source_replica
from .tax_engine import (
    CalculationBlocked,
    CalculationResult,
    TaxRow,
    calculate_modelo100_business_support,
    calculate_modelo130_rows,
    calculate_modelo303_rows,
    calculate_modelo347_rows,
    calculate_modelo349_rows,
    calculate_modelo390,
    calculate_retention_rows,
)
from .tax_calendar import entry_as_record, load_tax_calendar
from .tax_row_loader import load_tax_rows
from .tax_rules import (
    ALL_FORM_CODES,
    ANNUAL_FORM_CODES,
    QUARTERLY_FORM_CODES,
    difficult_expense_rule_for_year,
)
from .zenmoney import ZenMoneyPayment, inspect_zenmoney_csv, load_zenmoney_payments_csv


DEFAULT_DB = default_private_root() / "autonomo.sqlite"
Handler = Callable[[argparse.Namespace], int]
PAYMENT_SHEET_FIELDS = [
    "uuid",
    "row_version",
    "status",
    "transaction_id",
    "obligation_id",
    "paid_on",
    "amount_minor",
    "currency",
    "amount_eur_minor",
    "original_reference",
    "fee_minor",
    "fee_currency",
    "source_system",
    "external_id",
    "account_name",
    "counterparty_name",
    "category",
    "comment",
]
TAX_TREATMENT_SHEET_FIELDS = [
    "uuid",
    "row_version",
    "status",
    "period_key",
    "transaction_id",
    "transaction_lifecycle",
    "transaction_date",
    "entry_type",
    "description",
    "counterparty",
    "document_number",
    "gross_original",
    "original_currency",
    "amount_eur",
    "treatment_type",
    "jurisdiction",
    "tax_code",
    "aeat_invoice_type",
    "aeat_operation_key",
    "aeat_operation_qualification",
    "aeat_exemption_code",
    "aeat_reverse_charge",
    "aeat_expense_concept",
    "rate_basis_points",
    "deductible_ratio",
    "taxable_base_eur",
    "vat_eur",
    "deductible_irpf_eur",
    "deductible_vat_eur",
    "withholding_eur",
    "include_modelo130",
    "include_modelo303",
    "include_modelo347",
    "rule_version_id",
    "notes",
]
TAX_CALENDAR_SHEET_FIELDS = [
    "uuid",
    "row_version",
    "status",
    "calendar_year",
    "period_key",
    "form_code",
    "filing_opens_on",
    "internal_due_on",
    "direct_debit_cutoff_on",
    "statutory_due_on",
    "source_url",
    "source_checked_on",
    "notes",
]


def register_operational_commands(subparsers: argparse._SubParsersAction[Any]) -> None:
    db = subparsers.add_parser("db", help="Initialize or inspect the canonical SQLite ledger")
    db_sub = db.add_subparsers(dest="db_command", required=True)
    db_init = db_sub.add_parser("init", help="Initialize the ledger schema")
    _db_arg(db_init)
    db_init.set_defaults(_operational_handler=_cmd_db_init)
    db_status = db_sub.add_parser("status", help="Show schema, counts, periods, and blockers")
    _db_arg(db_status)
    db_status.set_defaults(_operational_handler=_cmd_db_status)

    profile = subparsers.add_parser(
        "profile",
        help="Maintain the taxpayer and reviewed AEAT business activities",
    )
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_set = profile_sub.add_parser("set", help="Create or update the taxpayer profile")
    _db_arg(profile_set)
    profile_set.add_argument("--input", type=Path, required=True)
    profile_set.set_defaults(_operational_handler=_cmd_profile_set)
    profile_show = profile_sub.add_parser("show", help="Show taxpayer profiles and activities")
    _db_arg(profile_show)
    profile_show.set_defaults(_operational_handler=_cmd_profile_show)
    activity_upsert = profile_sub.add_parser(
        "activity-upsert",
        help="Create or update a source-backed business activity",
    )
    _db_arg(activity_upsert)
    activity_upsert.add_argument("--input", type=Path, required=True)
    activity_upsert.set_defaults(_operational_handler=_cmd_activity_upsert)

    counterparties = subparsers.add_parser(
        "counterparties",
        help="Maintain source-backed supplier and customer identities",
    )
    counterparty_sub = counterparties.add_subparsers(
        dest="counterparty_command",
        required=True,
    )
    identity_upsert = counterparty_sub.add_parser(
        "identity-upsert",
        help="Create or update an AEAT identity backed by an evidence document",
    )
    _db_arg(identity_upsert)
    identity_upsert.add_argument("--input", type=Path, required=True)
    identity_upsert.set_defaults(_operational_handler=_cmd_counterparty_identity_upsert)
    identity_list = counterparty_sub.add_parser(
        "identity-list",
        help="List reviewed AEAT identities",
    )
    _db_arg(identity_list)
    identity_list.add_argument("--counterparty-id")
    identity_list.set_defaults(_operational_handler=_cmd_counterparty_identity_list)

    ingest = subparsers.add_parser("ingest", help="Extract a document into review without posting it")
    _db_arg(ingest)
    ingest.add_argument("path", type=Path)
    ingest.add_argument(
        "--kind",
        required=True,
        choices=["income_invoice", "expense_invoice", "bank_statement", "tax_report", "other"],
    )
    ingest.add_argument("--period")
    ingest.add_argument("--issued-on")
    ingest.add_argument("--document-number")
    ingest.add_argument("--counterparty-id")
    ingest.add_argument("--counterparty-name")
    ingest.add_argument("--defer-counterparty", action="store_true", help="Leave expense supplier selection to the reviewed UI workflow")
    ingest.add_argument("--drive-file-id")
    ingest.add_argument("--archive-root", type=Path)
    ingest.add_argument("--tesseract-command", default="tesseract")
    ingest.add_argument("--gross", type=Decimal)
    ingest.add_argument("--taxable-base", type=Decimal)
    ingest.add_argument("--vat", type=Decimal)
    ingest.add_argument("--currency")
    ingest.add_argument(
        "--document-only",
        action="store_true",
        help="Archive and review the document without creating an invoice transaction draft",
    )
    ingest.set_defaults(_operational_handler=_cmd_ingest)

    intake = subparsers.add_parser(
        "intake",
        help="Accept expense or income rows from the Google Sheet intake tabs",
    )
    intake_sub = intake.add_subparsers(dest="intake_command", required=True)
    intake_apply = intake_sub.add_parser(
        "apply",
        help="Validate a tab CSV, ingest its evidence, and emit system_id write-back rows",
    )
    _db_arg(intake_apply)
    intake_apply.add_argument("--tab", required=True, choices=INTAKE_TABS)
    intake_apply.add_argument("--remote-csv", type=Path, required=True)
    intake_apply.add_argument("--out-csv", type=Path, required=True)
    intake_apply.add_argument("--inbox-root", type=Path)
    intake_apply.add_argument("--archive-root", type=Path)
    intake_apply.add_argument("--tesseract-command", default="tesseract")
    intake_apply.add_argument(
        "--show-paths",
        action="store_true",
        help="Include absolute private filesystem paths in JSON output",
    )
    intake_apply.set_defaults(_operational_handler=_cmd_intake_apply)

    inbox = subparsers.add_parser(
        "inbox",
        help="Process quarter Inbox files and clean verified posted expense sources",
    )
    inbox_sub = inbox.add_subparsers(dest="inbox_command", required=True)
    inbox_process = inbox_sub.add_parser(
        "process",
        help="Ingest supported files and report per-file review or data errors",
    )
    _db_arg(inbox_process)
    inbox_process.add_argument("--period", required=True)
    inbox_process.add_argument("--inbox-root", type=Path)
    inbox_process.add_argument("--archive-root", type=Path)
    inbox_process.add_argument("--tesseract-command", default="tesseract")
    inbox_process.add_argument(
        "--show-paths",
        action="store_true",
        help="Include absolute private filesystem paths in JSON output",
    )
    inbox_process.set_defaults(_operational_handler=_cmd_inbox_process)
    inbox_cleanup = inbox_sub.add_parser(
        "cleanup-posted",
        help="Delete one verified expense_intake source after its transaction was posted",
    )
    _db_arg(inbox_cleanup)
    inbox_cleanup.add_argument("review_id")
    inbox_cleanup.add_argument("--inbox-root", type=Path)
    inbox_cleanup.add_argument("--archive-root", type=Path)
    inbox_cleanup.add_argument(
        "--show-paths",
        action="store_true",
        help="Include absolute private filesystem paths in JSON output",
    )
    inbox_cleanup.set_defaults(_operational_handler=_cmd_inbox_cleanup_posted)

    expense = subparsers.add_parser(
        "expense",
        help="Record reviewed expenses supported by evidence other than a supplier invoice",
    )
    expense_sub = expense.add_subparsers(dest="expense_command", required=True)
    expense_record = expense_sub.add_parser(
        "record",
        help="Archive and classify one TGSS contribution or bank fee without posting it",
    )
    _db_arg(expense_record)
    expense_record.add_argument(
        "--kind",
        required=True,
        choices=sorted(NON_INVOICE_EXPENSE_PRESETS),
    )
    expense_record.add_argument("--evidence", type=Path, required=True)
    expense_record.add_argument("--date", type=date.fromisoformat, required=True)
    expense_record.add_argument("--amount-eur", type=Decimal, required=True)
    expense_record.add_argument("--deductible-eur", type=Decimal, required=True)
    expense_record.add_argument("--reference", required=True)
    expense_record.add_argument("--description")
    expense_record.add_argument("--business-purpose")
    expense_record.add_argument("--note")
    expense_record.add_argument("--period")
    expense_record.add_argument("--business-activity-id")
    expense_record.add_argument("--counterparty-id")
    expense_record.add_argument("--counterparty-name")
    expense_record.add_argument("--counterparty-country")
    expense_record.add_argument("--counterparty-tax-id")
    expense_record.add_argument(
        "--counterparty-identity-kind",
        choices=sorted(IDENTITY_AEAT_TYPES),
    )
    expense_record.add_argument("--counterparty-identifier")
    expense_record.add_argument("--archive-root", type=Path)
    expense_record.add_argument("--tesseract-command", default="tesseract")
    expense_record.add_argument("--dry-run", action="store_true")
    expense_record.add_argument(
        "--show-paths",
        action="store_true",
        help="Include absolute private filesystem paths in JSON output",
    )
    expense_record.set_defaults(_operational_handler=_cmd_expense_record)

    review = subparsers.add_parser(
        "review",
        help="Inspect and advance typed review items through existing lifecycle gates",
    )
    review_sub = review.add_subparsers(dest="review_command", required=True)
    review_list = review_sub.add_parser(
        "list",
        help="List pending reviews and approved transactions waiting to be posted",
    )
    _db_arg(review_list)
    review_list.add_argument("--period", required=True)
    review_list.add_argument("--inbox-root", type=Path)
    review_list.add_argument("--archive-root", type=Path)
    review_list.add_argument(
        "--show-paths",
        action="store_true",
        help="Include absolute private filesystem paths in JSON output",
    )
    review_list.add_argument(
        "--ready-to-post",
        action="store_true",
        help="Return only approved transactions that pass the current review gates",
    )
    review_list.set_defaults(_operational_handler=_cmd_review_list)
    review_prepare = review_sub.add_parser(
        "prepare",
        help="Write a deterministic private decision packet for one linked invoice",
    )
    _db_arg(review_prepare)
    review_prepare.add_argument("review_id")
    review_prepare.add_argument("--out", type=Path, required=True)
    review_prepare.set_defaults(_operational_handler=_cmd_review_prepare)
    review_apply = review_sub.add_parser(
        "apply",
        help="Atomically validate and apply a reviewed decision packet without posting",
    )
    _db_arg(review_apply)
    review_apply.add_argument("--input", type=Path, required=True)
    review_apply.add_argument("--dry-run", action="store_true")
    review_apply.set_defaults(_operational_handler=_cmd_review_apply)
    review_apply_fx = review_sub.add_parser(
        "apply-fx",
        help="Atomically record a sourced FX rate and apply it to a review transaction",
        description=(
            "Atomically record a sourced FX rate and apply it to a review transaction. "
            "The payload rate is EUR per one unit of the transaction currency, not the "
            "units-per-EUR figure published by the ECB and public quote pages: a 1.2500 "
            "units-per-EUR quote must be entered as 0.8000. Rates labeled ecb or "
            "banco_de_espana are checked against the ECB reference for rate_date."
        ),
    )
    _db_arg(review_apply_fx)
    review_apply_fx.add_argument(
        "--input",
        type=Path,
        required=True,
        help=(
            "JSON object with review_id, expected_row_version, rate_date, rate "
            "(EUR per unit of the foreign currency), rate_source and source_reference"
        ),
    )
    review_apply_fx.add_argument(
        "--allow-unverified-rate",
        action="store_true",
        help=(
            "Record an official-source rate that deviates more than 5%% from the ECB "
            "reference; the stored source_reference notes that it was not verified"
        ),
    )
    review_apply_fx.set_defaults(_operational_handler=_cmd_review_apply_fx)
    review_confirm = review_sub.add_parser(
        "confirm",
        help="Approve one reviewed document or transaction without posting it",
    )
    _db_arg(review_confirm)
    review_confirm.add_argument("review_id")
    review_confirm.add_argument("--expected-row-version", type=int, required=True)
    review_confirm.set_defaults(_operational_handler=_cmd_review_confirm)
    review_post = review_sub.add_parser(
        "post",
        help="Post one approved transaction through the canonical posting gate",
    )
    _db_arg(review_post)
    review_post.add_argument("review_id")
    review_post.add_argument("--expected-row-version", type=int, required=True)
    review_post.add_argument("--inbox-root", type=Path)
    review_post.add_argument("--archive-root", type=Path)
    review_post.add_argument(
        "--show-paths",
        action="store_true",
        help="Include absolute private filesystem paths in JSON output",
    )
    review_post.set_defaults(_operational_handler=_cmd_review_post)
    review_post_batch = review_sub.add_parser(
        "post-batch",
        help="Post review list transaction items from a JSON object on stdin without batch rollback",
    )
    _db_arg(review_post_batch)
    review_post_batch.add_argument("--period", required=True)
    review_post_batch.add_argument("--inbox-root", type=Path)
    review_post_batch.add_argument("--archive-root", type=Path)
    review_post_batch.add_argument(
        "--show-paths",
        action="store_true",
        help="Include absolute private filesystem paths in JSON output",
    )
    review_post_batch.set_defaults(_operational_handler=_cmd_review_post_batch)

    documents = subparsers.add_parser("documents", help="Review document lifecycle decisions")
    document_sub = documents.add_subparsers(dest="document_command", required=True)
    document_transition = document_sub.add_parser("transition", help="Advance a document lifecycle")
    _db_arg(document_transition)
    document_transition.add_argument("document_id")
    document_transition.add_argument(
        "--to-status",
        required=True,
        choices=["extracted", "needs_review", "approved", "posted", "duplicate", "rejected", "void"],
    )
    document_transition.add_argument("--expected-row-version", type=int, required=True)
    document_transition.set_defaults(_operational_handler=_cmd_document_transition)

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
    transaction_counterparty = transaction_sub.add_parser(
        "set-counterparty",
        help="Correct a reviewed transaction counterparty with optimistic concurrency",
    )
    _db_arg(transaction_counterparty)
    transaction_counterparty.add_argument("transaction_id")
    transaction_counterparty.add_argument("--counterparty-id", required=True)
    transaction_counterparty.add_argument("--expected-row-version", type=int, required=True)
    transaction_counterparty.set_defaults(_operational_handler=_cmd_transaction_set_counterparty)
    transaction_activity = transaction_sub.add_parser(
        "set-activity",
        help="Assign a reviewed AEAT business activity to a transaction",
    )
    _db_arg(transaction_activity)
    transaction_activity.add_argument("transaction_id")
    transaction_activity.add_argument("--business-activity-id", required=True)
    transaction_activity.add_argument("--expected-row-version", type=int, required=True)
    transaction_activity.set_defaults(_operational_handler=_cmd_transaction_set_activity)
    transaction_fx = transaction_sub.add_parser(
        "apply-fx",
        help="Apply a stored FX rate to an unposted transaction",
        description=(
            "Apply a stored FX rate to an unposted transaction. The stored rate is EUR "
            "per one unit of the transaction currency, not the units-per-EUR figure "
            "published by the ECB and public quote pages: a 1.2500 units-per-EUR quote "
            "must be stored as 0.8000. Rates labeled ecb or banco_de_espana are checked "
            "against the ECB reference for their rate_date before they are applied."
        ),
    )
    _db_arg(transaction_fx)
    transaction_fx.add_argument("transaction_id")
    transaction_fx.add_argument("--fx-rate-id", required=True)
    transaction_fx.add_argument("--expected-row-version", type=int, required=True)
    transaction_fx.add_argument(
        "--allow-unverified-rate",
        action="store_true",
        help=(
            "Apply an official-source rate that deviates more than 5%% from the ECB "
            "reference; the stored source_reference notes that it was not verified"
        ),
    )
    transaction_fx.set_defaults(_operational_handler=_cmd_transaction_apply_fx)

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

    invoice = subparsers.add_parser(
        "invoice",
        help="Prepare outgoing invoice drafts and link final issued evidence",
    )
    invoice_sub = invoice.add_subparsers(dest="invoice_command", required=True)
    invoice_template = invoice_sub.add_parser(
        "template-upsert",
        help="Create or version a reviewed recurring invoice template",
    )
    _db_arg(invoice_template)
    invoice_template.add_argument("--input", type=Path, required=True)
    invoice_template.set_defaults(_operational_handler=_cmd_invoice_template_upsert)
    invoice_templates = invoice_sub.add_parser(
        "templates",
        help="List recurring invoice templates",
    )
    _db_arg(invoice_templates)
    invoice_templates.add_argument("--include-inactive", action="store_true")
    invoice_templates.set_defaults(_operational_handler=_cmd_invoice_templates)
    invoice_draft = invoice_sub.add_parser(
        "draft",
        help="Create a non-issued outgoing invoice draft from a reviewed template",
    )
    _db_arg(invoice_draft)
    invoice_draft.add_argument("--input", type=Path, required=True)
    invoice_draft.set_defaults(_operational_handler=_cmd_invoice_draft)
    invoice_review = invoice_sub.add_parser(
        "review",
        help="Mark a complete outgoing invoice draft ready for an external channel",
    )
    _db_arg(invoice_review)
    invoice_review.add_argument("outgoing_invoice_draft_id")
    invoice_review.add_argument("--expected-row-version", type=int, required=True)
    invoice_review.set_defaults(_operational_handler=_cmd_invoice_review)
    invoice_finalize = invoice_sub.add_parser(
        "finalize",
        help="Link a reviewed draft to the issued PDF and income transaction",
    )
    _db_arg(invoice_finalize)
    invoice_finalize.add_argument("outgoing_invoice_draft_id")
    invoice_finalize.add_argument("--document-id", required=True)
    invoice_finalize.add_argument("--transaction-id", required=True)
    invoice_finalize.add_argument("--external-number", required=True)
    invoice_finalize.add_argument("--external-series")
    invoice_finalize.add_argument("--expected-row-version", type=int, required=True)
    invoice_finalize.set_defaults(_operational_handler=_cmd_invoice_finalize)
    invoice_void = invoice_sub.add_parser(
        "void",
        help="Void an unissued outgoing invoice draft with an audit reason",
    )
    _db_arg(invoice_void)
    invoice_void.add_argument("outgoing_invoice_draft_id")
    invoice_void.add_argument("--reason", required=True)
    invoice_void.add_argument("--expected-row-version", type=int, required=True)
    invoice_void.set_defaults(_operational_handler=_cmd_invoice_void)
    invoice_show = invoice_sub.add_parser("show", help="Show a draft and all invoice lines")
    _db_arg(invoice_show)
    invoice_show.add_argument("outgoing_invoice_draft_id")
    invoice_show.add_argument("--out", type=Path)
    invoice_show.set_defaults(_operational_handler=_cmd_invoice_show)
    invoice_list = invoice_sub.add_parser("list", help="List outgoing invoice drafts")
    _db_arg(invoice_list)
    invoice_list.add_argument("--period")
    invoice_list.set_defaults(_operational_handler=_cmd_invoice_list)
    invoice_series = invoice_sub.add_parser(
        "series-status",
        help="Report issued-number continuity and a non-reserved next-number suggestion",
    )
    _db_arg(invoice_series)
    invoice_series.add_argument("--year", type=int, required=True)
    invoice_series.add_argument("--series")
    invoice_series.add_argument(
        "--bare-belongs-to-series",
        action="store_true",
        help="Assert that bare historical numbers belong to the target series",
    )
    invoice_series.set_defaults(_operational_handler=_cmd_invoice_series_status)

    assets = subparsers.add_parser(
        "assets",
        help="Review asset identity, tax basis, and AEAT book metadata",
    )
    assets_sub = assets.add_subparsers(dest="assets_command", required=True)
    assets_list = assets_sub.add_parser("list", help="List assets and book-profile fields")
    _db_arg(assets_list)
    assets_list.set_defaults(_operational_handler=_cmd_assets_list)
    asset_profile = assets_sub.add_parser(
        "book-profile",
        help="Apply a source-backed AEAT asset book profile",
    )
    _db_arg(asset_profile)
    asset_profile.add_argument("--input", type=Path, required=True)
    asset_profile.set_defaults(_operational_handler=_cmd_asset_book_profile)

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
    obligation_mark.add_argument("--due-on")
    obligation_mark.add_argument("--filed-at")
    obligation_mark.add_argument("--expected-row-version", type=int)
    obligation_mark.set_defaults(_operational_handler=_cmd_obligation_mark)
    obligation_evidence = obligation_sub.add_parser(
        "record-evidence",
        help="Attach hashed evidence to a reviewed obligation decision",
    )
    _db_arg(obligation_evidence)
    obligation_evidence.add_argument("--period", required=True)
    obligation_evidence.add_argument("--form", required=True)
    obligation_evidence.add_argument(
        "--kind",
        choices=["aeat_account_check", "independent_calculation"],
        required=True,
    )
    obligation_evidence.add_argument("--evidence", type=Path, required=True)
    obligation_evidence.add_argument("--source-reference")
    obligation_evidence.add_argument("--notes")
    obligation_evidence.set_defaults(
        _operational_handler=_cmd_obligation_record_evidence
    )

    tax_calendar = subparsers.add_parser(
        "calendar",
        help="Import and inspect source-backed filing deadlines",
    )
    tax_calendar_sub = tax_calendar.add_subparsers(dest="calendar_command", required=True)
    calendar_import = tax_calendar_sub.add_parser("import")
    _db_arg(calendar_import)
    calendar_import.add_argument("--input", type=Path, required=True)
    calendar_import.set_defaults(_operational_handler=_cmd_calendar_import)
    calendar_list = tax_calendar_sub.add_parser("list")
    _db_arg(calendar_list)
    calendar_list.add_argument("--year", type=int)
    calendar_list.add_argument("--period")
    calendar_list.add_argument("--out", type=Path)
    calendar_list.set_defaults(_operational_handler=_cmd_calendar_list)

    agenda = subparsers.add_parser(
        "agenda",
        help="Show the forward tax, payment, and cash agenda",
    )
    _db_arg(agenda)
    agenda.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=date.today(),
        help="Agenda date (default: today)",
    )
    agenda.add_argument(
        "--horizon-days",
        type=int,
        default=120,
        help="Forward schedule window in days (default: 120)",
    )
    agenda.add_argument(
        "--available-eur",
        type=Decimal,
        help="Cash currently available for tax payments",
    )
    agenda.add_argument(
        "--cash-buffer-eur",
        type=Decimal,
        help="Override the default operating reserve buffer",
    )
    agenda.add_argument("--out", type=Path, help="Write JSON instead of stdout")
    agenda.set_defaults(_operational_handler=_cmd_agenda)

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
    period_dashboard = period_sub.add_parser(
        "dashboard",
        help="Build a read-only current-quarter actual and approved forecast dashboard",
    )
    _db_arg(period_dashboard)
    period_dashboard.add_argument("period")
    period_dashboard.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    period_dashboard.add_argument("--out-dir", type=Path, required=True)
    period_dashboard.add_argument("--xolo-modelo130-calculations", type=Path)
    period_dashboard.set_defaults(_operational_handler=_cmd_period_dashboard)
    period_prepare = period_sub.add_parser(
        "prepare",
        help="Build calculations, cash check, calendar, books, and a manual AEAT filing checklist",
    )
    _db_arg(period_prepare)
    period_prepare.add_argument("period")
    period_prepare.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    period_prepare.add_argument("--out-dir", type=Path, required=True)
    period_prepare.add_argument("--available-eur", type=Decimal)
    period_prepare.add_argument("--cash-buffer-eur", type=Decimal)
    period_prepare.set_defaults(_operational_handler=_cmd_period_prepare)
    period_shadow_close = period_sub.add_parser(
        "shadow-close",
        help="Aggregate accounting, filing, archive, payment, and cutover readiness",
    )
    _db_arg(period_shadow_close)
    period_shadow_close.add_argument("period")
    period_shadow_close.add_argument(
        "--as-of", type=date.fromisoformat, default=date.today()
    )
    period_shadow_close.add_argument("--out-dir", type=Path, required=True)
    period_shadow_close.add_argument("--offboarding-manifest", type=Path)
    period_shadow_close.add_argument("--invoice-channel-assessment", type=Path)
    period_shadow_close.add_argument(
        "--operational-proof-since",
        type=date.fromisoformat,
        help="Require at least one independently reviewed and posted expense since this date",
    )
    period_shadow_close.add_argument(
        "--required-settled-obligation",
        action="append",
        default=[],
        metavar="PERIOD:FORM",
        help="Require hash-archived settlement evidence for a filed due tax obligation; repeat as needed",
    )
    period_shadow_close.set_defaults(
        _operational_handler=_cmd_period_shadow_close
    )
    period_annual_status = period_sub.add_parser(
        "annual-status",
        help="Explain whether a tax year is complete enough for annual calculations",
    )
    _db_arg(period_annual_status)
    period_annual_status.add_argument("--year", type=int, required=True)
    period_annual_status.add_argument("--form", choices=ANNUAL_FORM_CODES)
    period_annual_status.add_argument(
        "--allow-authoritative-history",
        action="store_true",
        help="Accept reviewed Xolo history only where immutable filed evidence exists",
    )
    period_annual_status.add_argument("--out", type=Path)
    period_annual_status.set_defaults(
        _operational_handler=_cmd_period_annual_status
    )
    period_open = period_sub.add_parser(
        "open",
        help="Create an accounting period and seed its obligations as undecided",
    )
    _db_arg(period_open)
    period_open.add_argument("period")
    period_open.add_argument("--starts-on")
    period_open.add_argument("--ends-on")
    period_open.set_defaults(_operational_handler=_cmd_period_open)
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

    storage = subparsers.add_parser(
        "storage",
        help="Migrate and inspect provider-neutral document storage metadata",
    )
    storage_sub = storage.add_subparsers(dest="storage_command", required=True)
    storage_migrate = storage_sub.add_parser(
        "migrate",
        help="Backfill files, attachments, and physical replicas from legacy document paths",
    )
    _db_arg(storage_migrate)
    storage_migrate.add_argument("--startup", action="store_true")
    storage_migrate.add_argument("--require-complete", action="store_true")
    storage_migrate.add_argument("--batch-size", type=int, default=25)
    storage_migrate.add_argument("--local-root", type=Path)
    storage_migrate.add_argument("--drive-mount-root", type=Path)
    storage_migrate.add_argument("--snapshot-out", type=Path)
    storage_migrate.set_defaults(_operational_handler=_cmd_storage_migrate)
    storage_reconcile = storage_sub.add_parser(
        "reconcile",
        help="Create verified missing physical replicas for a configured backend",
    )
    _db_arg(storage_reconcile)
    storage_reconcile.add_argument("--backend", required=True)
    storage_reconcile.add_argument("--limit", type=int, default=0)
    storage_reconcile.set_defaults(_operational_handler=_cmd_storage_reconcile)
    storage_backend = storage_sub.add_parser(
        "backend-upsert",
        help="Register a non-secret storage backend configuration",
    )
    _db_arg(storage_backend)
    storage_backend.add_argument("--input", type=Path, required=True)
    storage_backend.set_defaults(_operational_handler=_cmd_storage_backend_upsert)
    storage_backends = storage_sub.add_parser(
        "backends-apply",
        help="Transactionally apply an authoritative list of storage backends",
    )
    _db_arg(storage_backends)
    storage_backends.add_argument("--input", type=Path, required=True)
    storage_backends.set_defaults(_operational_handler=_cmd_storage_backends_apply)
    storage_adopt_drive = storage_sub.add_parser(
        "adopt-google-archive",
        help="Verify and adopt pre-existing Google Drive originals as file replicas",
    )
    _db_arg(storage_adopt_drive)
    storage_adopt_drive.add_argument("--backend", default="google_archive_ro")
    storage_adopt_drive.add_argument(
        "--records",
        type=Path,
        required=True,
        help="JSON array of content_sha256, drive_file_id and optional archive metadata",
    )
    storage_adopt_drive.add_argument("--dry-run", action="store_true")
    storage_adopt_drive.add_argument(
        "--retire-backend",
        action="append",
        default=[],
        help="Legacy managed backend whose replicas may be retired after adoption",
    )
    storage_adopt_drive.set_defaults(_operational_handler=_cmd_storage_adopt_google_archive)
    storage_cleanup_preflight = storage_sub.add_parser(
        "cleanup-preflight",
        help="Read-only safety gate before retiring duplicate managed Drive objects",
    )
    _db_arg(storage_cleanup_preflight)
    storage_cleanup_preflight.add_argument("--archive-backend", required=True)
    storage_cleanup_preflight.add_argument("--yandex-backend", required=True)
    storage_cleanup_preflight.add_argument("--managed-backend", required=True)
    storage_cleanup_preflight.add_argument("--backup-restore-proof", type=Path, required=True)
    storage_cleanup_preflight.add_argument("--max-proof-age-hours", type=int, default=36)
    storage_cleanup_preflight.set_defaults(_operational_handler=_cmd_storage_cleanup_preflight)

    bank = subparsers.add_parser("bank", help="Import and reconcile bank payments")
    bank_sub = bank.add_subparsers(dest="bank_command", required=True)
    bank_import = bank_sub.add_parser("import-revolut")
    _db_arg(bank_import)
    bank_import.add_argument("--csv", type=Path, required=True)
    bank_import.set_defaults(_operational_handler=_cmd_bank_import_revolut)
    bank_record = bank_sub.add_parser(
        "record-payment",
        help="Attach reviewed payment evidence to a transaction or filed tax obligation",
    )
    _db_arg(bank_record)
    payment_target = bank_record.add_mutually_exclusive_group(required=True)
    payment_target.add_argument("--transaction-id")
    payment_target.add_argument("--obligation-id")
    payment_target.add_argument(
        "--tax-obligation",
        metavar="PERIOD:FORM",
        help="Select a filed tax obligation by period and form, for example 2026-Q2:130",
    )
    bank_record.add_argument("--evidence", type=Path, required=True)
    bank_record.add_argument("--paid-on", type=date.fromisoformat, required=True)
    bank_record.add_argument("--amount", type=Decimal, required=True)
    bank_record.add_argument("--currency", default="EUR")
    bank_record.add_argument("--amount-eur", type=Decimal)
    bank_record.add_argument("--source-system", required=True)
    bank_record.add_argument("--external-id", required=True)
    bank_record.add_argument("--reference")
    bank_record.add_argument("--account-name")
    bank_record.add_argument("--counterparty-name")
    bank_record.add_argument("--comment")
    bank_record.add_argument("--archive-root", type=Path)
    bank_record.set_defaults(_operational_handler=_cmd_bank_record_payment)
    zenmoney_inspect = bank_sub.add_parser(
        "inspect-zenmoney",
        help="List exact account names and export coverage without importing rows",
    )
    zenmoney_inspect.add_argument("--csv", type=Path, required=True)
    zenmoney_inspect.set_defaults(_operational_handler=_cmd_bank_inspect_zenmoney)
    zenmoney_import = bank_sub.add_parser("import-zenmoney")
    _db_arg(zenmoney_import)
    zenmoney_import.add_argument("--csv", type=Path, required=True)
    zenmoney_import.add_argument("--period", required=True)
    zenmoney_import.add_argument("--account", action="append", required=True)
    zenmoney_import.add_argument("--default-currency")
    zenmoney_import.add_argument("--archive-root", type=Path)
    zenmoney_import.set_defaults(_operational_handler=_cmd_bank_import_zenmoney)

    sheet = subparsers.add_parser("sheet", help="Export or reconcile the Google Sheet review projection")
    sheet_sub = sheet.add_subparsers(dest="sheet_command", required=True)
    sheet_export = sheet_sub.add_parser("export")
    _db_arg(sheet_export)
    sheet_export.add_argument("--out-dir", type=Path, required=True)
    sheet_export.set_defaults(_operational_handler=_cmd_sheet_export)
    sheet_reconcile = sheet_sub.add_parser("reconcile")
    _db_arg(sheet_reconcile)
    sheet_reconcile.add_argument(
        "--tab",
        choices=[
            "inbox_review",
            "counterparties",
            "transactions",
            "tax_treatments",
            "issues",
            "assets",
        ],
        required=True,
    )
    sheet_reconcile.add_argument("--remote-csv", type=Path, required=True)
    sheet_reconcile.add_argument("--out", type=Path)
    sheet_reconcile.set_defaults(_operational_handler=_cmd_sheet_reconcile)
    sheet_apply = sheet_sub.add_parser("apply", help="Apply reviewed rows with optimistic concurrency")
    _db_arg(sheet_apply)
    sheet_apply.add_argument(
        "--tab",
        choices=[
            "inbox_review",
            "counterparties",
            "transactions",
            "tax_treatments",
            "issues",
            "assets",
        ],
        required=True,
    )
    sheet_apply.add_argument("--remote-csv", type=Path, required=True)
    sheet_apply.set_defaults(_operational_handler=_cmd_sheet_apply)

    books = subparsers.add_parser("books", help="Build deterministic accounting books from SQLite")
    books_sub = books.add_subparsers(dest="books_command", required=True)
    books_build = books_sub.add_parser("build")
    _db_arg(books_build)
    books_build.add_argument("--period", required=True)
    books_build.add_argument("--out-dir", type=Path, required=True)
    books_build.set_defaults(_operational_handler=_cmd_books_build)
    books_aeat = books_sub.add_parser(
        "aeat-preview",
        help="Build a fail-closed cumulative AEAT row projection; this does not create XLSX",
    )
    _db_arg(books_aeat)
    books_aeat.add_argument("--period", required=True)
    books_aeat.add_argument("--taxpayer-tax-id")
    books_aeat.add_argument("--allow-authoritative-history", action="store_true")
    books_aeat.add_argument("--out", type=Path, required=True)
    books_aeat.set_defaults(_operational_handler=_cmd_books_aeat_preview)
    books_template = books_sub.add_parser(
        "aeat-template-check",
        help="Verify the exact reviewed AEAT 2026 XLSX template contract",
    )
    books_template.add_argument("--template", type=Path, required=True)
    books_template.add_argument("--out", type=Path, required=True)
    books_template.set_defaults(_operational_handler=_cmd_books_aeat_template_check)
    books_payload = books_sub.add_parser(
        "aeat-payload",
        help="Build the typed write plan for the official AEAT workbook",
    )
    _db_arg(books_payload)
    books_payload.add_argument("--period", required=True)
    books_payload.add_argument("--taxpayer-tax-id")
    books_payload.add_argument("--allow-authoritative-history", action="store_true")
    books_payload.add_argument("--template", type=Path, required=True)
    books_payload.add_argument("--out", type=Path, required=True)
    books_payload.set_defaults(_operational_handler=_cmd_books_aeat_payload)
    books_write = books_sub.add_parser(
        "aeat-write",
        help="Populate the reviewed Registrar sheets in the official AEAT XLSX template",
    )
    _db_arg(books_write)
    books_write.add_argument("--period", required=True)
    books_write.add_argument("--taxpayer-tax-id")
    books_write.add_argument("--allow-authoritative-history", action="store_true")
    books_write.add_argument("--template", type=Path, required=True)
    books_write.add_argument("--out", type=Path, required=True)
    books_write.add_argument(
        "--out-payload",
        type=Path,
        help="Optional JSON write plan retained beside the generated workbook",
    )
    books_write.add_argument(
        "--out-manifest",
        type=Path,
        help="Optional audit manifest with template, payload, and workbook hashes",
    )
    books_write.set_defaults(_operational_handler=_cmd_books_aeat_write)
    books_validate = books_sub.add_parser(
        "aeat-validate",
        help="Upload an XLSX to the official AEAT validator and preserve its response",
    )
    books_validate.add_argument("--xlsx", type=Path, required=True)
    books_validate.add_argument("--year", type=int, required=True)
    books_validate.add_argument("--out-receipt", type=Path, required=True)
    books_validate.add_argument(
        "--confirm-upload-to-aeat",
        action="store_true",
        help="Required acknowledgement that the workbook will be sent to AEAT",
    )
    books_validate.set_defaults(_operational_handler=_cmd_books_aeat_validate)

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
    filings_receipt = filings_sub.add_parser(
        "record-receipt",
        help="Verify an AEAT filed PDF against a prepared calculation and store it",
    )
    _db_arg(filings_receipt)
    filings_receipt.add_argument("--period", required=True)
    filings_receipt.add_argument("--form", choices=["130", "303"], required=True)
    filings_receipt.add_argument("--filed-pdf", type=Path, required=True)
    filings_receipt.add_argument("--calculation", type=Path, required=True)
    filings_receipt.add_argument("--archive-root", type=Path)
    filings_receipt.add_argument("--tolerance-eur", type=Decimal, default=Decimal("0.01"))
    filings_receipt.add_argument("--dry-run", action="store_true")
    filings_receipt.add_argument(
        "--show-paths",
        action="store_true",
        help="Include absolute private filesystem paths in JSON output",
    )
    filings_receipt.set_defaults(_operational_handler=_cmd_filing_receipt)
    filings_procedure_receipt = filings_sub.add_parser(
        "record-procedure-receipt",
        help="Archive a tax-procedure receipt without replacing the original filed return",
    )
    _db_arg(filings_procedure_receipt)
    filings_procedure_receipt.add_argument("--period", required=True)
    filings_procedure_receipt.add_argument(
        "--form", choices=sorted(ALL_FORM_CODES), required=True
    )
    filings_procedure_receipt.add_argument(
        "--procedure", choices=["rectification"], required=True
    )
    filings_procedure_receipt.add_argument(
        "--stage", choices=["submitted", "resolved", "rejected"], required=True
    )
    filings_procedure_receipt.add_argument("--evidence", type=Path, required=True)
    filings_procedure_receipt.add_argument("--occurred-on", required=True)
    filings_procedure_receipt.add_argument("--reference", required=True)
    filings_procedure_receipt.add_argument("--amount-eur", type=Decimal)
    filings_procedure_receipt.add_argument("--notes")
    filings_procedure_receipt.add_argument("--archive-root", type=Path)
    filings_procedure_receipt.add_argument("--dry-run", action="store_true")
    filings_procedure_receipt.add_argument(
        "--show-paths",
        action="store_true",
        help="Include absolute private filesystem paths in JSON output",
    )
    filings_procedure_receipt.set_defaults(
        _operational_handler=_cmd_tax_procedure_receipt
    )
    filings_refresh = filings_sub.add_parser(
        "refresh-evidence",
        help="Append newly extractable filed-return evidence without replaying accounting rows",
    )
    _db_arg(filings_refresh)
    filings_refresh.add_argument("--document-inventory", type=Path, required=True)
    filings_refresh.set_defaults(_operational_handler=_cmd_filings_refresh_evidence)

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
    calculate.add_argument(
        "--previous-vat-compensation",
        help="Modelo 303 compensation balance entering the quarter; defaults to the prior filed snapshot",
    )
    calculate.add_argument(
        "--final-vat-settlement",
        choices=["compensate", "refund"],
        help=(
            "For Q4 Modelo 303, carry a negative result forward or request its refund. "
            "Modelo 390 derives the choice from final filed Modelo 303 evidence and rejects a conflict."
        ),
    )
    calculate.add_argument("--withholding-and-payments")
    calculate.add_argument("--reduction")
    calculate.add_argument("--unsupported-annual-category", action="append", default=[])
    calculate.add_argument("--out", type=Path)
    calculate.set_defaults(_operational_handler=_cmd_calculate)

    offboarding = subparsers.add_parser("offboarding", help="Build or verify the Xolo exit manifest")
    offboarding_sub = offboarding.add_subparsers(dest="offboarding_command", required=True)
    offboarding_build = offboarding_sub.add_parser("build")
    _db_arg(offboarding_build)
    offboarding_build.add_argument("paths", type=Path, nargs="+")
    offboarding_build.add_argument(
        "--generated-on",
        type=date.fromisoformat,
        default=date.today(),
        help="Operational date represented by this manifest (default: today)",
    )
    offboarding_build.add_argument("--out", type=Path, required=True)
    offboarding_build.set_defaults(_operational_handler=_cmd_offboarding_build)
    offboarding_verify = offboarding_sub.add_parser("verify")
    offboarding_verify.add_argument("--manifest", type=Path, required=True)
    offboarding_verify.add_argument(
        "--required-generated-on",
        type=date.fromisoformat,
        help="Also require the manifest to have been generated on this date",
    )
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
    parser.add_argument("--db", type=Path)


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


def _cmd_profile_set(args: argparse.Namespace) -> int:
    payload = _load_json_object(args.input)
    with open_ledger_db(args.db) as db:
        row = db.upsert_taxpayer_profile(**payload)
    _emit(row)
    return 0


def _cmd_profile_show(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db, read_only=True) as db:
        profiles = [
            dict(row)
            for row in db.connection.execute(
                "SELECT * FROM taxpayer_profile ORDER BY taxpayer_profile_id"
            ).fetchall()
        ]
        activities = db.list_business_activities()
    _emit({"profiles": profiles, "activities": activities})
    return 0


def _cmd_activity_upsert(args: argparse.Namespace) -> int:
    payload = _load_json_object(args.input)
    with open_ledger_db(args.db) as db:
        row = db.upsert_business_activity(**payload)
    _emit(row)
    return 0


def _cmd_counterparty_identity_upsert(args: argparse.Namespace) -> int:
    payload = _load_json_object(args.input)
    with open_ledger_db(args.db) as db:
        row = db.upsert_counterparty_identity(**payload)
    _emit(row)
    return 0


def _cmd_counterparty_identity_list(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db, read_only=True) as db:
        rows = db.list_counterparty_identities(
            counterparty_id=args.counterparty_id,
        )
    _emit(rows)
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    _emit(_ingest_document(args))
    return 0


def _cmd_intake_apply(args: argparse.Namespace) -> int:
    if args.inbox_root is None:
        raise ValueError("--inbox-root is required (or set inbox_root in the private config)")
    if args.archive_root is None:
        raise ValueError("--archive-root is required (or set archive_root in the private config)")
    with open_ledger_db(args.db):
        pass
    intake = load_intake_csv(args.remote_csv, args.tab)
    system_ids: dict[int, str] = {}
    items: list[dict[str, Any]] = [
        {
            "row_number": error.row_number,
            "status": "failed",
            "message": error.message,
        }
        for error in intake.errors
    ]
    counts = {
        "accepted": 0,
        "already_accepted": 0,
        "recovered": 0,
        "failed": len(intake.errors),
        "blank": len(intake.raw_rows) - len(intake.rows) - len(intake.errors),
    }

    for row in intake.rows:
        try:
            existing_status = _existing_intake_receipt(args.db, row)
            if existing_status is not None:
                receipt, status = existing_status
                receipt_id = receipt["intake_receipt_id"]
                _ensure_intake_follow_up_issues(
                    args.db,
                    row,
                    transaction_id=receipt["transaction_id"],
                )
                system_ids[row.row_number] = receipt_id
                counts[status] += 1
                items.append(
                    {
                        "row_number": row.row_number,
                        "status": status,
                        "system_id": receipt_id,
                        "document_id": receipt["document_id"],
                        "transaction_id": receipt["transaction_id"],
                    }
                )
                continue

            evidence_path = resolve_evidence_path(row, args.inbox_root)
            evidence_sha256 = _file_sha256(evidence_path)
            with open_ledger_db(args.db, read_only=True) as db:
                evidence_receipt = db.find_intake_receipt_by_evidence(evidence_sha256)
            if evidence_receipt is not None:
                raise IntakeSheetError(
                    f"row {row.row_number}: this evidence file was already accepted with "
                    "different row values"
                )

            result = _ingest_document(
                argparse.Namespace(
                    db=args.db,
                    path=evidence_path,
                    kind=row.document_kind,
                    period=row.period_key,
                    issued_on=row.tax_date.isoformat(),
                    document_number=row.document_number,
                    counterparty_id=None,
                    counterparty_name=row.counterparty_name,
                    drive_file_id=None,
                    archive_root=args.archive_root,
                    tesseract_command=args.tesseract_command,
                    gross=row.gross_amount,
                    taxable_base=None,
                    vat=None,
                    currency=row.currency,
                    document_only=False,
                )
            )
            if result["sha256"] != evidence_sha256:
                raise IntakeSheetError(
                    f"row {row.row_number}: evidence file changed while it was being imported"
                )
            transaction = result.get("transaction") or {}
            transaction_id = transaction.get("transaction_id")
            treatment_id = transaction.get("tax_treatment_id")
            if not transaction_id or not treatment_id:
                raise IntakeSheetError(
                    f"row {row.row_number}: invoice did not produce a review transaction"
                )
            with open_ledger_db(args.db) as db:
                live_transaction = db.connection.execute(
                    "SELECT * FROM transactions WHERE transaction_id = ?",
                    (transaction_id,),
                ).fetchone()
                if live_transaction["lifecycle_status"] in {"received", "extracted"}:
                    live_transaction = db.transition_transaction(
                        transaction_id,
                        lifecycle_status="needs_review",
                        expected_row_version=live_transaction["row_version"],
                    )
                if live_transaction["lifecycle_status"] != "needs_review":
                    raise IntakeSheetError(
                        f"row {row.row_number}: existing transaction is "
                        f"{live_transaction['lifecycle_status']!r}, not needs_review"
                    )
                receipt = db.add_intake_receipt(
                    intake_tab=row.tab,
                    source_row_number=row.row_number,
                    row_fingerprint=row.row_fingerprint,
                    evidence_sha256=evidence_sha256,
                    document_id=result["document_id"],
                    transaction_id=transaction_id,
                    treatment_id=treatment_id,
                    input_payload=row.values,
                )
            _ensure_intake_follow_up_issues(args.db, row, transaction_id=transaction_id)
            receipt_id = receipt["intake_receipt_id"]
            system_ids[row.row_number] = receipt_id
            counts["accepted"] += 1
            item = {
                "row_number": row.row_number,
                "status": "accepted",
                "system_id": receipt_id,
                "document_id": result["document_id"],
                "transaction_id": transaction_id,
                "needs_review": True,
            }
            if args.show_paths:
                item["evidence_path"] = str(evidence_path)
            else:
                item["file_name"] = row.values["file_name"]
            items.append(item)
        except (
            IntakeSheetError,
            LedgerDbError,
            KeyError,
            OSError,
            ValueError,
            sqlite3.Error,
        ) as exc:
            counts["failed"] += 1
            items.append(
                {
                    "row_number": row.row_number,
                    "status": "failed",
                    "message": str(exc),
                }
            )

    write_intake_writeback_csv(args.out_csv, intake, system_ids)
    _emit(
        {
            "tab": args.tab,
            "counts": counts,
            "items": sorted(items, key=lambda item: item["row_number"]),
            "writeback_csv": str(args.out_csv),
            "meaning_of_system_id": "accepted_for_review_not_posted",
        }
    )
    return 2 if counts["failed"] else 0


def _ingest_document(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "defer_counterparty", False) and args.kind != "expense_invoice":
        raise ValueError("Deferred supplier selection is only supported for expenses")
    if args.document_only and any(
        value is not None for value in (args.gross, args.taxable_base, args.vat, args.currency)
    ):
        raise ValueError("Invoice amount overrides cannot be combined with --document-only")
    result = inspect_document(args.path, args.kind, tesseract_command=args.tesseract_command)
    suggestion = _document_suggestion(args.path, args.kind, result.extracted_text)
    amounts = extract_invoice_amounts(
        result.extracted_text,
        suggestion,
        gross_override=args.gross,
        taxable_base_override=args.taxable_base,
        vat_override=args.vat,
        currency_override=args.currency,
    )
    requirements = review_requirements(args.kind, suggestion, amounts)
    parsed_date = (
        suggestion.date
        if suggestion is not None and suggestion.date is not None
        else parse_any_date(result.extracted_text)
    )
    issued_on = args.issued_on or (parsed_date.isoformat() if parsed_date is not None else None)
    if issued_on is None:
        raise ValueError("--issued-on is required when the document parser cannot identify a date")
    period_key = _quarter_key(date.fromisoformat(issued_on))
    if args.period is not None and args.period != period_key:
        raise ValueError(f"Document belongs to {period_key}, not requested {args.period}")
    archived_path = (
        archive_evidence(
            args.path,
            args.archive_root,
            period_key=period_key,
            evidence_kind=args.kind,
            digest=result.sha256,
        )
        if args.archive_root is not None
        else Path(result.source_path)
    )
    with open_ledger_db(args.db) as db:
        batch = db.add_import_batch(
            source_name=str(args.path.resolve()),
            source_hash=result.sha256,
            batch_key=f"document:{result.sha256}",
            notes=f"Extraction method: {result.extraction_method}",
        )
        counterparty_id = args.counterparty_id
        if counterparty_id is None and not getattr(args, "defer_counterparty", False):
            counterparty_id = _upsert_intake_counterparty(
                db, suggestion, preferred_name=getattr(args, "counterparty_name", None),
            )
        document_number = args.document_number or extract_invoice_number(result.extracted_text)
        if document_number is None and suggestion is not None:
            parsed_description = suggestion.description or ""
            if args.kind == "income_invoice" or parsed_description != args.path.name:
                document_number = parsed_description or None
        existing_document = db.connection.execute(
            """
            SELECT d.*, p.period_key
            FROM documents d
            LEFT JOIN periods p ON p.period_id = d.period_id
            WHERE d.source_hash = ?
            """,
            (result.sha256,),
        ).fetchone()
        is_new_document = existing_document is None
        if existing_document is None:
            document = db.upsert_document(
                external_key=f"sha256:{result.sha256}",
                counterparty_id=counterparty_id,
                import_batch_id=batch["import_batch_id"],
                document_type=args.kind,
                document_number=document_number,
                issued_on=issued_on,
                period_key=period_key,
                currency=amounts.currency or (
                    suggestion.currency if suggestion is not None else ""
                ),
                total_minor=(
                    int(amounts.gross * 100) if amounts.gross is not None else None
                ),
                lifecycle_status=result.status,
                source_hash=result.sha256,
            )
        else:
            document = _backfill_existing_intake_document(
                db,
                existing_document,
                import_batch_id=batch["import_batch_id"],
                counterparty_id=counterparty_id,
                document_kind=args.kind,
                period_key=period_key,
                issued_on=issued_on,
                document_number=document_number,
                amounts=amounts,
            )
        document = db.set_document_storage(
            document["document_id"],
            source_path=str(archived_path),
            drive_file_id=args.drive_file_id,
            mime_type=result.mime_type,
            expected_row_version=document["row_version"],
        )
        # Keep the legacy path during the rollout, while registering the same
        # immutable bytes in the provider-neutral storage catalogue.
        register_local_source_replica(
            db,
            document_id=str(document["document_id"]),
            source_path=archived_path,
            media_type=result.mime_type,
            storage_root=args.archive_root or archived_path.parent,
        )
        if is_new_document and result.needs_review:
            db.add_validation_issue(
                period_key=period_key,
                issue_code="document_structural_review",
                severity="error",
                message="; ".join(result.structural_errors),
                subject_table="documents",
                subject_id=document["document_id"],
                blocking=True,
                source_hash=result.sha256,
            )
        if is_new_document and suggestion is not None and suggestion.review_required:
            db.add_validation_issue(
                period_key=period_key,
                issue_code="document_classification_review",
                severity="warning",
                message=suggestion.notes or "Document requires classification review",
                subject_table="documents",
                subject_id=document["document_id"],
                blocking=True,
                source_hash=result.sha256,
            )
        if is_new_document and counterparty_id is not None:
            counterparty = db.connection.execute(
                "SELECT country_code FROM counterparties WHERE counterparty_id = ?",
                (counterparty_id,),
            ).fetchone()
            if counterparty["country_code"] == "ZZ":
                db.add_validation_issue(
                    period_key=period_key,
                    issue_code="counterparty_tax_profile_review",
                    severity="warning",
                    message="Review country and tax identity before posting this counterparty.",
                    subject_table="counterparties",
                    subject_id=counterparty_id,
                    blocking=True,
                    source_hash=result.sha256,
                )
        transaction = _intake_transaction_draft(
            db,
            document=document,
            document_kind=args.kind,
            period_key=period_key,
            issued_on=issued_on,
            document_number=document_number,
            counterparty_id=counterparty_id,
            suggestion=suggestion,
            amounts=amounts,
            requirements=requirements,
            source_hash=result.sha256,
            structural_review=result.needs_review,
            document_only=args.document_only,
            create_missing_issue=is_new_document,
        )
    return {
        **asdict(result),
        "archived_path": str(archived_path),
        "period": period_key,
        "issued_on": issued_on,
        "suggested_entry": suggestion.as_row() if suggestion is not None else None,
        "invoice_amounts": amounts.as_dict(),
        "review_requirements": list(requirements),
        "document_id": document["document_id"],
        "document_created": is_new_document,
        "document_lifecycle_status": document["lifecycle_status"],
        "row_version": document["row_version"],
        "transaction": transaction,
    }


def _cmd_inbox_process(args: argparse.Namespace) -> int:
    if args.inbox_root is None:
        raise ValueError("--inbox-root is required (or set inbox_root in the private config)")
    if args.archive_root is None:
        raise ValueError("--archive-root is required (or set archive_root in the private config)")
    period_root = args.inbox_root / args.period
    if not period_root.is_dir():
        raise ValueError(f"Quarter inbox not found: {period_root}")

    kind_by_folder = {
        "expense_invoice": "expense_invoice",
        "income_invoice": "income_invoice",
        "other": "other",
    }
    supported_suffixes = {
        ".bmp",
        ".csv",
        ".jpeg",
        ".jpg",
        ".pdf",
        ".png",
        ".tif",
        ".tiff",
        ".tsv",
        ".txt",
        ".webp",
    }
    ignored_metadata_names = {".ds_store", "desktop.ini", "thumbs.db"}
    counts = {
        "imported": 0,
        "already_imported": 0,
        "needs_review": 0,
        "needs_data": 0,
        "failed": 0,
    }
    items: list[dict[str, Any]] = []
    for source_path in sorted(path for path in period_root.rglob("*") if path.is_file()):
        if source_path.name.casefold() in ignored_metadata_names:
            continue
        relative = source_path.relative_to(period_root)
        output_path = str(source_path) if args.show_paths else str(relative)
        if source_path.suffix.casefold() not in supported_suffixes:
            counts["failed"] += 1
            items.append(
                {
                    "path": output_path,
                    "status": "failed",
                    "message": f"Unsupported Inbox file type: {source_path.suffix or '<none>'}",
                }
            )
            continue
        kind = kind_by_folder.get(relative.parts[0]) if relative.parts else None
        if kind is None:
            counts["failed"] += 1
            items.append(
                {
                    "path": output_path,
                    "status": "failed",
                    "message": (
                        "Supported documents must be under expense_invoice, "
                        "income_invoice, or other"
                    ),
                }
            )
            continue
        ingest_args = argparse.Namespace(
            db=args.db,
            path=source_path,
            kind=kind,
            period=args.period,
            issued_on=None,
            document_number=None,
            counterparty_id=None,
            drive_file_id=None,
            archive_root=args.archive_root,
            tesseract_command=args.tesseract_command,
            gross=None,
            taxable_base=None,
            vat=None,
            currency=None,
            document_only=(kind == "other"),
        )
        try:
            result = _ingest_document(ingest_args)
        except ValueError as exc:
            message = str(exc)
            status = (
                "needs_data"
                if "--issued-on is required" in message
                else "failed"
            )
            counts[status] += 1
            items.append(
                {"path": output_path, "kind": kind, "status": status, "message": message}
            )
            continue
        except (LedgerDbError, OSError) as exc:
            counts["failed"] += 1
            items.append(
                {
                    "path": output_path,
                    "kind": kind,
                    "status": "failed",
                    "message": str(exc),
                }
            )
            continue
        except Exception as exc:  # Keep one malformed document from aborting the batch.
            counts["failed"] += 1
            items.append(
                {
                    "path": output_path,
                    "kind": kind,
                    "status": "failed",
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        status = "imported" if result["document_created"] else "already_imported"
        counts[status] += 1
        transaction = result.get("transaction") or {}
        needs_review = bool(
            result.get("document_lifecycle_status") in {"received", "extracted", "needs_review"}
            or transaction.get("lifecycle_status") in {"received", "extracted", "needs_review"}
            or transaction.get("reason") == "invoice_amounts_incomplete"
        )
        if needs_review:
            counts["needs_review"] += 1
        items.append(
            {
                "path": output_path,
                "kind": kind,
                "status": status,
                "needs_review": needs_review,
                "document_id": result["document_id"],
                "transaction_id": transaction.get("transaction_id"),
            }
        )

    _emit(
        {
            "period": args.period,
            "inbox_root": str(args.inbox_root) if args.show_paths else args.inbox_root.name,
            "period_root": str(period_root) if args.show_paths else args.period,
            "archive_root": str(args.archive_root) if args.show_paths else args.archive_root.name,
            "counts": counts,
            "items": items,
        }
    )
    return 2 if counts["needs_data"] or counts["failed"] else 0


def _cmd_expense_record(args: argparse.Namespace) -> int:
    if args.archive_root is None:
        raise ValueError("--archive-root is required (or set archive_root in the private config)")
    preset = NON_INVOICE_EXPENSE_PRESETS[args.kind]
    inspection = inspect_document(
        args.evidence,
        "other",
        tesseract_command=args.tesseract_command,
    )
    if inspection.needs_review:
        raise ValueError(
            "Evidence could not be structurally validated: "
            + "; ".join(inspection.structural_errors)
        )
    period_key = (args.period or non_invoice_quarter_key(args.date)).upper()
    planned_archive = evidence_archive_path(
        args.evidence,
        args.archive_root,
        period_key=period_key,
        evidence_kind=preset.document_type,
        digest=inspection.sha256,
    )
    business_purpose = args.business_purpose or preset.default_business_purpose
    if not business_purpose:
        raise ValueError("--business-purpose is required for this expense kind")
    request = NonInvoiceExpenseInput(
        kind=args.kind,
        evidence_sha256=inspection.sha256,
        evidence_mime_type=inspection.mime_type,
        archived_path=planned_archive,
        transaction_date=args.date,
        gross_eur=args.amount_eur,
        deductible_eur=args.deductible_eur,
        reference=args.reference,
        business_purpose=business_purpose,
        description=args.description or preset.default_description,
        note=args.note,
        period_key=period_key,
        counterparty_id=args.counterparty_id,
        counterparty_name=args.counterparty_name,
        counterparty_country=args.counterparty_country,
        counterparty_tax_id=args.counterparty_tax_id,
        counterparty_identity_kind=args.counterparty_identity_kind,
        counterparty_identifier=args.counterparty_identifier,
        business_activity_id=args.business_activity_id,
        storage_root=args.archive_root,
    )

    with open_ledger_db(args.db) as db:
        result = record_non_invoice_expense(db, request, dry_run=True)
    if not args.dry_run:
        archived_path = archive_evidence(
            args.evidence,
            args.archive_root,
            period_key=period_key,
            evidence_kind=preset.document_type,
            digest=inspection.sha256,
        )
        request = NonInvoiceExpenseInput(
            **{
                **asdict(request),
                "archived_path": archived_path,
            }
        )
        with open_ledger_db(args.db) as db:
            result = record_non_invoice_expense(db, request)
    else:
        archived_path = planned_archive

    next_command = None
    if result["transaction_lifecycle_status"] == "approved":
        next_command = (
            "autonomo-tax review post "
            f"{result['review_id']} --expected-row-version "
            f"{result['transaction_row_version']}"
        )
    result.update(
        {
            "evidence_file": (
                str(args.evidence.resolve()) if args.show_paths else args.evidence.name
            ),
            "archive_file": str(archived_path) if args.show_paths else archived_path.name,
            "next_command": next_command,
        }
    )
    _emit(result)
    return 0


def _cmd_review_list(args: argparse.Namespace) -> int:
    pending_statuses = ("received", "extracted", "needs_review")
    transaction_statuses = (*pending_statuses, "approved")
    with open_ledger_db(args.db, read_only=True) as db:
        issues = db.list_issues(period_key=args.period)
        issues_by_subject: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for issue in issues:
            subject_table = issue.get("subject_table")
            subject_id = issue.get("subject_id")
            if subject_table and subject_id:
                issues_by_subject.setdefault((subject_table, subject_id), []).append(issue)

        rows: list[dict[str, Any]] = []
        documents = db.connection.execute(
            """
            SELECT d.*, p.period_key
            FROM documents d
            JOIN periods p ON p.period_id = d.period_id
            WHERE p.period_key = ? AND d.lifecycle_status IN (?, ?, ?)
            ORDER BY d.issued_on, d.document_id
            """,
            (args.period, *pending_statuses),
        ).fetchall()
        for raw in documents:
            document = dict(raw)
            open_issues = issues_by_subject.get(("documents", document["document_id"]), [])
            blocking_issues = [
                {
                    "issue_id": issue["validation_issue_id"],
                    "row_version": issue["row_version"],
                    "code": issue["issue_code"],
                    "message": issue["message"],
                }
                for issue in open_issues
                if issue["blocking"]
            ]
            rows.append(
                {
                    "review_id": f"document:{document['document_id']}",
                    "kind": "document",
                    "period": document["period_key"],
                    "lifecycle_status": document["lifecycle_status"],
                    "row_version": document["row_version"],
                    "expected_row_version": document["row_version"],
                    "issued_on": document["issued_on"],
                    "document_number": document["document_number"],
                    "amount_minor": document["total_minor"],
                    "currency": document["currency"],
                    "source_path": _review_source_path(
                        document.get("source_path"),
                        show_paths=args.show_paths,
                    ),
                    "blocking_issues": blocking_issues,
                    "ready_to_approve": (
                        document["lifecycle_status"] in {"extracted", "needs_review"}
                        and not blocking_issues
                    ),
                }
            )

        preview = build_posting_preview(
            db,
            period_key=args.period,
            inbox_root=args.inbox_root,
            archive_root=args.archive_root,
        )
        rows.extend(
            item
            for item in preview["items"]
            if item["lifecycle_status"] in transaction_statuses
        )
    if args.ready_to_post:
        rows = [row for row in rows if row.get("ready_to_post") is True]
    _emit(rows)
    return 0


def _cmd_review_prepare(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db, read_only=True) as db:
        packet = prepare_review_packet(db, args.review_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(packet_json(packet), encoding="utf-8")
    _emit(
        {
            "review_id": packet["review_id"],
            "snapshot_hash": packet["snapshot_hash"],
            "written": args.out.name,
            "privacy": packet["privacy"],
        }
    )
    return 0


def _cmd_review_apply(args: argparse.Namespace) -> int:
    packet = _load_json_object(args.input)
    with open_ledger_db(args.db) as db:
        result = apply_review_packet(db, packet, dry_run=args.dry_run)
    _emit(result)
    return 0


def _cmd_review_apply_fx(args: argparse.Namespace) -> int:
    payload = _load_json_object(args.input)
    expected_fields = {
        "review_id",
        "expected_row_version",
        "rate_date",
        "rate",
        "rate_source",
        "source_reference",
    }
    unexpected = sorted(set(payload) - expected_fields)
    missing = sorted(expected_fields - set(payload))
    if unexpected or missing:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        raise ValueError("Invalid FX review request (" + "; ".join(details) + ")")
    review_kind, subject_id = _parse_review_id(str(payload["review_id"]))
    with open_ledger_db(args.db) as db:
        transaction_id = subject_id
        if review_kind == "document":
            rows = db.connection.execute(
                "SELECT transaction_id FROM transactions WHERE document_id = ?",
                (subject_id,),
            ).fetchall()
            if len(rows) != 1:
                raise ValueError(
                    "Document FX review requires exactly one linked transaction; "
                    f"found {len(rows)}"
                )
            transaction_id = str(rows[0]["transaction_id"])
        source_reference = str(payload["source_reference"])
        transaction = db.connection.execute(
            "SELECT original_currency, currency FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        if transaction is not None:
            note = _check_manual_fx_rate(
                str(payload["rate"]),
                currency=str(transaction["original_currency"] or transaction["currency"]),
                rate_date=str(payload["rate_date"]),
                rate_source=str(payload["rate_source"]),
                allow_unverified=args.allow_unverified_rate,
            )
            if note:
                source_reference = f"{source_reference} ({note})"
        result = db.review_transaction_fx_rate(
            transaction_id,
            expected_row_version=int(payload["expected_row_version"]),
            rate_date=str(payload["rate_date"]),
            rate=str(payload["rate"]),
            rate_source=str(payload["rate_source"]),
            source_reference=source_reference,
        )
    _emit(result)
    return 0


def _check_manual_fx_rate(
    rate: str,
    *,
    currency: str,
    rate_date: str,
    rate_source: str,
    allow_unverified: bool,
) -> str | None:
    """Guard a manual official rate; return the note to record for an allowed override.

    The reference lookup runs before any ledger write so an unreachable ECB
    service neither holds a database lock nor blocks offline use.
    """

    normalized_currency = currency.strip().upper()
    if normalized_currency == "EUR":
        return None
    try:
        decimal_rate = Decimal(rate)
    except InvalidOperation as exc:
        raise ValueError("FX rate must be a valid Decimal") from exc
    try:
        check = check_manual_rate(
            decimal_rate,
            currency=normalized_currency,
            rate_date=date.fromisoformat(rate_date),
            rate_source=rate_source.strip(),
            reference_lookup=fetch_eur_rate,
            allow_unverified=allow_unverified,
        )
    except FXRateDeviationError as exc:
        raise FXRateDeviationError(
            f"{exc}; pass --allow-unverified-rate to record it anyway"
        ) from exc
    if check.status == "unavailable":
        print(f"warning: {check.detail}", file=sys.stderr)
        return None
    return check.detail if check.status == "unverified" else None


def _cmd_review_confirm(args: argparse.Namespace) -> int:
    review_kind, subject_id = _parse_review_id(args.review_id)
    with open_ledger_db(args.db) as db:
        if review_kind == "document":
            _assert_no_open_subject_issues(db, "documents", subject_id)
            row = db.transition_document(
                subject_id,
                lifecycle_status="approved",
                expected_row_version=args.expected_row_version,
            )
        else:
            _assert_transaction_reviewed(db, subject_id)
            row = db.transition_transaction(
                subject_id,
                lifecycle_status="approved",
                expected_row_version=args.expected_row_version,
            )
    _emit(row)
    return 0


def _cmd_review_post(args: argparse.Namespace) -> int:
    review_kind, subject_id = _parse_review_id(args.review_id)
    if review_kind != "transaction":
        raise ValueError("Only transaction:<uuid> review items can be posted")
    with open_ledger_db(args.db) as db:
        outcome, payload, _ = _post_transaction_review(
            db,
            review_id=args.review_id,
            transaction_id=subject_id,
            expected_row_version=args.expected_row_version,
            expected_period=None,
            inbox_root=args.inbox_root,
            archive_root=args.archive_root,
            show_paths=args.show_paths,
            allow_already_posted=False,
        )
    _emit(payload)
    return 0 if outcome == "posted" and payload.get("reason_code") is None else 2


def _cmd_review_post_batch(args: argparse.Namespace) -> int:
    request = _load_post_batch_request(sys.stdin, expected_period=args.period)
    requested = request["items"]
    results: list[dict[str, Any]] = []
    summary = {
        "requested": len(requested),
        "posted": 0,
        "already_posted": 0,
        "already_finalized": 0,
        "skipped": 0,
        "failed": 0,
        "interrupted": 0,
        "not_attempted": 0,
    }
    committed_any = False
    try:
        with open_ledger_db(args.db) as db:
            preflight = _preflight_post_batch_period(
                db,
                period_key=args.period,
                requested=requested,
            )
            if preflight is not None:
                _emit(preflight)
                return 2
            for index, item in enumerate(requested):
                review_id, expected_row_version = _parse_post_batch_item(item)
                review_kind, subject_id = _parse_review_id(review_id)
                if review_kind != "transaction":
                    raise ValueError("Only transaction:<uuid> review items can be posted")
                try:
                    outcome, result, committed = _post_transaction_review(
                        db,
                        review_id=review_id,
                        transaction_id=subject_id,
                        expected_row_version=expected_row_version,
                        expected_period=args.period,
                        inbox_root=args.inbox_root,
                        archive_root=args.archive_root,
                        show_paths=args.show_paths,
                        allow_already_posted=True,
                    )
                except StaleRowVersionError as exc:
                    outcome = "skipped"
                    result = _post_batch_failure_result(
                        db,
                        transaction_id=subject_id,
                        review_id=review_id,
                        expected_row_version=expected_row_version,
                        error=exc,
                    )
                    result["reason_code"] = "skipped_stale"
                    committed = False
                except (LedgerDbError, ValueError) as exc:
                    if (
                        not committed_any
                        and _post_batch_reason_code(exc) == "period_not_open"
                    ):
                        preflight = _preflight_post_batch_period(
                            db,
                            period_key=args.period,
                            requested=requested,
                        )
                        if preflight is not None:
                            _emit(preflight)
                            return 2
                    outcome = "failed"
                    result = _post_batch_failure_result(
                        db,
                        transaction_id=subject_id,
                        review_id=review_id,
                        expected_row_version=expected_row_version,
                        error=exc,
                    )
                    committed = False
                except Exception as exc:
                    if not committed_any and _is_sqlite_busy(exc):
                        _emit(
                            {
                                "status": "retry_later",
                                "period": args.period,
                                "error": "db_busy",
                                "message": str(exc),
                            }
                        )
                        return 2
                    if not committed_any:
                        _emit(
                            {
                                "status": "internal_error",
                                "error": "internal_error",
                                "period": args.period,
                                "message": str(exc),
                            }
                        )
                        return 2
                    results.append(
                        {
                            "index": index,
                            "review_id": review_id,
                            "expected_row_version": expected_row_version,
                            "outcome": "failed",
                            "result": _post_batch_failure_result(
                                db,
                                transaction_id=subject_id,
                                review_id=review_id,
                                expected_row_version=expected_row_version,
                                error=exc,
                            ),
                        }
                    )
                    summary["failed"] += 1
                    remaining = len(requested) - (index + 1)
                    for remainder in range(index + 1, len(requested)):
                        next_review_id, next_expected = _parse_post_batch_item(
                            requested[remainder]
                        )
                        results.append(
                            {
                                "index": remainder,
                                "review_id": next_review_id,
                                "expected_row_version": next_expected,
                                "outcome": "not_attempted",
                                "result": {
                                    "reason_code": "internal_error",
                                    "error_type": type(exc).__name__,
                                    "message": str(exc),
                                },
                                "transaction_id": None,
                                "previous_row_version": None,
                                "new_row_version": None,
                                "cleanup_status": None,
                            }
                        )
                    summary["not_attempted"] = remaining
                    payload = {
                        "status": "interrupted",
                        "interrupted": True,
                        "period": args.period,
                        "summary": summary,
                        "results": results,
                    }
                    _emit(payload)
                    return 0
                committed_any = committed_any or committed
                if outcome == "skipped":
                    summary["skipped"] += 1
                else:
                    summary[outcome] += 1
                if outcome == "posted" and result.get("reason_code") == "posted_cleanup_failed":
                    summary["interrupted"] += 1
                results.append(
                    _post_batch_result_row(
                        index=index,
                        review_id=review_id,
                        expected_row_version=expected_row_version,
                        outcome=outcome,
                        result=result,
                    )
                )
    except sqlite3.OperationalError as exc:
        if _is_sqlite_busy(exc):
            _emit(
                {
                    "status": "retry_later",
                    "period": args.period,
                    "error": "db_busy",
                    "message": str(exc),
                }
            )
            return 2
        _emit(
            {
                "status": "internal_error",
                "error": "internal_error",
                "period": args.period,
                "message": str(exc),
            }
        )
        return 2
    payload = {
        "status": _post_batch_status(summary),
        "interrupted": summary["not_attempted"] > 0,
        "period": args.period,
        "summary": summary,
        "results": results,
    }
    _emit(payload)
    return 0


def _post_transaction_review(
    db: LedgerDB,
    *,
    review_id: str,
    transaction_id: str,
    expected_row_version: int,
    expected_period: str | None,
    inbox_root: Path | None,
    archive_root: Path | None,
    show_paths: bool,
    allow_already_posted: bool,
) -> tuple[str, dict[str, Any], bool]:
    current = _transaction_for_cleanup_output(db, transaction_id)
    if expected_period is not None and current["period_key"] != expected_period:
        return (
            "failed",
            {
                **current,
                "error_type": "ValueError",
                "message": (
                    "Review item period does not match the requested batch period: "
                    f"{current['period_key']} != {expected_period}"
                ),
            },
            False,
        )
    if allow_already_posted and current["lifecycle_status"] == "included_in_snapshot":
        return (
            "already_finalized",
            {
                **current,
                "reason_code": "already_finalized",
                "inbox_cleanup": _inbox_cleanup_payload(
                    "not_applicable",
                    message="Transaction is already included in a snapshot.",
                    show_paths=show_paths,
                ),
            },
            False,
        )
    if (
        allow_already_posted
        and current["lifecycle_status"] == "posted"
    ):
        return _summarize_already_posted_transaction(
            current,
            prevalidate_expense_inbox_cleanup(
                db,
                transaction_id,
                inbox_root=inbox_root,
                archive_root=archive_root,
            ),
            show_paths=show_paths,
        )

    cleanup = prevalidate_expense_inbox_cleanup(
        db,
        transaction_id,
        inbox_root=inbox_root,
        archive_root=archive_root,
    )
    if cleanup.status == "failed":
        return (
            "failed",
            {
                **current,
                "reason_code": cleanup.reason_code,
                "inbox_cleanup": _inbox_cleanup_payload(
                    "failed",
                    cleanup_row=cleanup.cleanup_row,
                    candidate=cleanup.candidate,
                    message=cleanup.message,
                    show_paths=show_paths,
                ),
            },
            False,
        )
    row = db.transition_transaction(
        transaction_id,
        lifecycle_status="posted",
        expected_row_version=expected_row_version,
    )
    return _finalize_posted_transaction(
        row,
        cleanup,
        show_paths=show_paths,
    )


def _finalize_posted_transaction(
    row: Mapping[str, Any],
    cleanup: Any,
    *,
    show_paths: bool,
) -> tuple[str, dict[str, Any], bool]:
    if cleanup.candidate is None:
        return (
            "posted",
            {
                **dict(row),
                "reason_code": None,
                "inbox_cleanup": _inbox_cleanup_payload(
                    cleanup.status,
                    cleanup_row=cleanup.cleanup_row,
                    candidate=cleanup.candidate,
                    message=cleanup.message,
                    show_paths=show_paths,
                ),
            },
            True,
        )
    try:
        cleanup_status = cleanup_expense_inbox_source(cleanup.candidate)
    except Exception as exc:
        return (
            "posted",
            {
                **dict(row),
                "reason_code": "posted_cleanup_failed",
                "inbox_cleanup": _inbox_cleanup_payload(
                    "failed",
                    cleanup_row=cleanup.cleanup_row,
                    candidate=cleanup.candidate,
                    message=_inbox_cleanup_error_message(
                        exc,
                        show_paths=show_paths,
                    ),
                    show_paths=show_paths,
                ),
            },
            True,
        )
    return (
        "posted",
        {
            **dict(row),
            "reason_code": None,
            "inbox_cleanup": _inbox_cleanup_payload(
                cleanup_status,
                cleanup_row=cleanup.cleanup_row,
                candidate=cleanup.candidate,
                show_paths=show_paths,
            ),
        },
        True,
    )


def _summarize_already_posted_transaction(
    row: Mapping[str, Any],
    cleanup: Any,
    *,
    show_paths: bool,
) -> tuple[str, dict[str, Any], bool]:
    if cleanup.status == "failed":
        inbox_cleanup = _inbox_cleanup_payload(
            "failed",
            cleanup_row=cleanup.cleanup_row,
            candidate=cleanup.candidate,
            message=cleanup.message,
            show_paths=show_paths,
        )
    elif cleanup.status == "ready":
        inbox_cleanup = _inbox_cleanup_payload(
            "failed",
            cleanup_row=cleanup.cleanup_row,
            candidate=cleanup.candidate,
            message="Transaction is already posted; run inbox cleanup-posted to finish verified cleanup.",
            show_paths=show_paths,
        )
    else:
        inbox_cleanup = _inbox_cleanup_payload(
            cleanup.status,
            cleanup_row=cleanup.cleanup_row,
            candidate=cleanup.candidate,
            message=cleanup.message,
            show_paths=show_paths,
        )
    return (
        "already_posted",
        {
            **dict(row),
            "reason_code": "already_posted",
            "inbox_cleanup": inbox_cleanup,
        },
        False,
    )


def _load_post_batch_request(
    handle,
    *,
    expected_period: str,
) -> dict[str, Any]:
    payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("review post-batch expects a JSON object on stdin")
    period = str(payload.get("period") or "").strip()
    if not period:
        raise ValueError("review post-batch stdin payload requires period")
    if period != expected_period:
        raise ValueError("review post-batch stdin period must match --period")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("review post-batch stdin payload requires an items array")
    return {"period": period, "items": items}


def _parse_post_batch_item(item: Any) -> tuple[str, int]:
    if not isinstance(item, dict):
        raise ValueError("Each post-batch stdin item must be a JSON object")
    review_id = str(item.get("review_id") or "").strip()
    transaction_id = str(item.get("transaction_id") or "").strip()
    if review_id:
        review_kind, subject_id = _parse_review_id(review_id)
        if review_kind != "transaction":
            raise ValueError("Only transaction review items can be posted in batch")
        if transaction_id and transaction_id != subject_id:
            raise ValueError("Each post-batch item transaction_id must match review_id")
        transaction_id = subject_id
    elif transaction_id:
        review_id = f"transaction:{transaction_id}"
    else:
        raise ValueError("Each post-batch item requires transaction_id or review_id")
    if not review_id:
        raise ValueError("Each post-batch item requires transaction_id or review_id")
    raw_version = item.get("expected_row_version")
    if raw_version is None:
        raise ValueError("Each post-batch item requires expected_row_version")
    return review_id, int(raw_version)


def _post_batch_failure_result(
    db: LedgerDB,
    *,
    transaction_id: str,
    review_id: str,
    expected_row_version: int,
    error: Exception,
) -> dict[str, Any]:
    try:
        current = _transaction_for_cleanup_output(db, transaction_id)
    except Exception:
        current = {
            "review_id": review_id,
            "expected_row_version": expected_row_version,
        }
    return {
        **current,
        "reason_code": _post_batch_reason_code(error),
        "error_type": type(error).__name__,
        "message": str(error),
    }


def _post_batch_result_row(
    *,
    index: int,
    review_id: str,
    expected_row_version: int,
    outcome: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    current_row_version = result.get("row_version")
    if outcome == "posted" and isinstance(current_row_version, int):
        previous_row_version: int | None = expected_row_version
        new_row_version: int | None = current_row_version
    elif isinstance(current_row_version, int):
        previous_row_version = current_row_version
        new_row_version = current_row_version
    else:
        previous_row_version = None
        new_row_version = None
    return {
        "index": index,
        "review_id": review_id,
        "transaction_id": result.get("transaction_id"),
        "expected_row_version": expected_row_version,
        "previous_row_version": previous_row_version,
        "new_row_version": new_row_version,
        "outcome": outcome,
        "reason_code": result.get("reason_code"),
        "cleanup_status": (result.get("inbox_cleanup") or {}).get("status"),
        "result": result,
    }


def _post_batch_status(summary: Mapping[str, int]) -> str:
    if summary["not_attempted"]:
        return "interrupted"
    if summary["failed"] or summary["interrupted"] or summary["skipped"]:
        return "partial"
    return "completed"


def _preflight_post_batch_period(
    db: LedgerDB,
    *,
    period_key: str,
    requested: list[dict[str, Any]],
) -> dict[str, Any] | None:
    row = db.connection.execute(
        "SELECT status FROM periods WHERE period_key = ?",
        (period_key,),
    ).fetchone()
    if row is None:
        return {
            "status": "unknown_period",
            "period": period_key,
            "error": "unknown_period",
            "message": f"Unknown period: {period_key}",
        }
    if row["status"] not in {"closed", "amended"}:
        return None
    message = f"Period {period_key} is immutable after close"
    summary = {
        "requested": len(requested),
        "posted": 0,
        "already_posted": 0,
        "already_finalized": 0,
        "skipped": 0,
        "failed": 0,
        "interrupted": 0,
        "not_attempted": len(requested),
    }
    results: list[dict[str, Any]] = []
    for index, item in enumerate(requested):
        review_id, expected_row_version = _parse_post_batch_item(item)
        results.append(
            {
                "index": index,
                "review_id": review_id,
                "expected_row_version": expected_row_version,
                "outcome": "not_attempted",
                "result": {
                    "reason_code": "period_not_open",
                    "error_type": "ClosedPeriodError",
                    "message": message,
                },
            }
        )
    return {
        "status": "period_not_open",
        "interrupted": False,
        "period": period_key,
        "reason_code": "period_not_open",
        "message": message,
        "summary": summary,
        "results": results,
    }


def _post_batch_reason_code(error: Exception) -> str | None:
    message = str(error)
    if "Expected row_version" in message:
        return "stale_row_version"
    if "immutable after close" in message:
        return "period_not_open"
    if "requires an EUR amount" in message:
        return "fx_missing"
    if "requires sourced FX" in message:
        return "fx_unsourced"
    if "historical-only" in message:
        return "fx_historical_only"
    if "tax treatment is required" in message:
        return "tax_treatment_missing"
    if "Unknown tax treatment" in message:
        return "tax_treatment_unknown"
    if "must be approved before posting" in message:
        return "document_not_ready"
    if "cannot be posted before" in message:
        return "future_dated"
    if "prevent posting" in message:
        return "blocking_issue"
    if "-> posted" in message:
        return "lifecycle_not_approved"
    return None


def _is_sqlite_busy(error: Exception) -> bool:
    return isinstance(error, sqlite3.OperationalError) and "locked" in str(error).lower()


def _cmd_inbox_cleanup_posted(args: argparse.Namespace) -> int:
    review_kind, subject_id = _parse_review_id(args.review_id)
    if review_kind != "transaction":
        raise ValueError("Only transaction:<uuid> review items can be cleaned")
    with open_ledger_db(args.db, read_only=True) as db:
        current = _transaction_for_cleanup_output(db, subject_id)
        cleanup_row = expense_inbox_cleanup_row(db, subject_id)
    if current["lifecycle_status"] != "posted":
        _emit(
            {
                **current,
                "inbox_cleanup": _inbox_cleanup_payload(
                    "failed",
                    cleanup_row=cleanup_row,
                    message="Inbox cleanup requires a posted transaction.",
                    show_paths=args.show_paths,
                ),
            }
        )
        return 2
    if cleanup_row is None:
        _emit(
            {
                **current,
                "inbox_cleanup": _inbox_cleanup_payload(
                    "failed",
                    message="Transaction is not an expense_intake expense invoice.",
                    show_paths=args.show_paths,
                ),
            }
        )
        return 2

    candidate: ExpenseInboxCleanupCandidate | None = None
    try:
        candidate = build_expense_inbox_cleanup_candidate(
            cleanup_row,
            inbox_root=args.inbox_root,
            archive_root=args.archive_root,
        )
        cleanup_status = cleanup_expense_inbox_source(candidate)
    except (InboxCleanupError, OSError) as exc:
        _emit(
            {
                **current,
                "inbox_cleanup": _inbox_cleanup_payload(
                    "failed",
                    cleanup_row=cleanup_row,
                    candidate=candidate,
                    message=_inbox_cleanup_error_message(
                        exc,
                        show_paths=args.show_paths,
                    ),
                    show_paths=args.show_paths,
                ),
            }
        )
        return 2
    _emit(
        {
            **current,
            "inbox_cleanup": _inbox_cleanup_payload(
                cleanup_status,
                cleanup_row=cleanup_row,
                candidate=candidate,
                show_paths=args.show_paths,
            ),
        }
    )
    return 0


def _transaction_for_cleanup_output(db: LedgerDB, transaction_id: str) -> dict[str, Any]:
    row = db.connection.execute(
        """
        SELECT t.*, p.period_key, p.status AS period_status
        FROM transactions t
        JOIN periods p ON p.period_id = t.period_id
        WHERE t.transaction_id = ?
        """,
        (transaction_id,),
    ).fetchone()
    if row is None:
        raise LedgerDbError(f"Transaction not found: {transaction_id}")
    return dict(row)


def _inbox_cleanup_payload(
    status: str,
    *,
    cleanup_row: Mapping[str, Any] | None = None,
    candidate: ExpenseInboxCleanupCandidate | None = None,
    message: str | None = None,
    show_paths: bool,
) -> dict[str, Any]:
    source_value = (
        str(candidate.source_path)
        if candidate is not None
        else str((cleanup_row or {}).get("inbox_source_path") or "")
    )
    archive_value = (
        str(candidate.archive_path)
        if candidate is not None
        else str((cleanup_row or {}).get("archive_path") or "")
    )
    payload: dict[str, Any] = {
        "status": status,
        "message": message or _inbox_cleanup_message(status),
    }
    if source_value:
        payload["file_name"] = Path(source_value).name
    if show_paths:
        if source_value:
            payload["source_path"] = source_value
        if archive_value:
            payload["archive_path"] = archive_value
    return payload


def _inbox_cleanup_message(status: str) -> str:
    if status == "deleted":
        return "Verified expense source was removed from Inbox; Evidence archive was preserved."
    if status == "already_absent":
        return "Stored Inbox source is already absent; no other files were searched."
    if status == "not_applicable":
        return "Automatic Inbox cleanup does not apply to this transaction."
    return "Inbox cleanup failed."


def _inbox_cleanup_error_message(
    error: InboxCleanupError | OSError,
    *,
    show_paths: bool,
) -> str:
    if isinstance(error, InboxCleanupError) or show_paths:
        return str(error)
    return f"Filesystem cleanup failed ({type(error).__name__}); retry when the file is available."


def _parse_review_id(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise ValueError("Review IDs must be typed as document:<uuid> or transaction:<uuid>")
    review_kind, subject_id = value.split(":", 1)
    if review_kind not in {"document", "transaction"} or not subject_id:
        raise ValueError("Review IDs must be typed as document:<uuid> or transaction:<uuid>")
    return review_kind, subject_id


def _review_source_path(value: str | None, *, show_paths: bool) -> str | None:
    if value is None:
        return None
    return value if show_paths else Path(value).name


def _assert_no_open_subject_issues(db: LedgerDB, subject_table: str, subject_id: str) -> None:
    issue_codes = [
        row["issue_code"]
        for row in db.connection.execute(
            """
            SELECT issue_code
            FROM validation_issues
            WHERE subject_table = ? AND subject_id = ?
              AND blocking = 1 AND issue_status = 'open'
            ORDER BY issue_code
            """,
            (subject_table, subject_id),
        ).fetchall()
    ]
    if issue_codes:
        raise ValueError("Resolve blocking review issues first: " + ", ".join(issue_codes))


def _assert_transaction_reviewed(db: LedgerDB, transaction_id: str) -> None:
    transaction = db.connection.execute(
        "SELECT * FROM transactions WHERE transaction_id = ?",
        (transaction_id,),
    ).fetchone()
    if transaction is None:
        raise KeyError(f"Unknown transaction: {transaction_id}")
    treatments = db.connection.execute(
        "SELECT tax_code FROM tax_treatments WHERE transaction_id = ?",
        (transaction_id,),
    ).fetchall()
    if not treatments or any((row["tax_code"] or "unknown") == "unknown" for row in treatments):
        raise ValueError("Set a reviewed non-unknown tax treatment before approval")
    if transaction["document_id"] is not None:
        document = db.connection.execute(
            "SELECT lifecycle_status FROM documents WHERE document_id = ?",
            (transaction["document_id"],),
        ).fetchone()
        if document is None or document["lifecycle_status"] not in {
            "approved",
            "posted",
            "included_in_snapshot",
        }:
            raise ValueError("Approve the linked document before approving the transaction")
        _assert_no_open_subject_issues(db, "documents", transaction["document_id"])
    _assert_no_open_subject_issues(db, "transactions", transaction_id)
    if transaction["counterparty_id"] is not None:
        _assert_no_open_subject_issues(db, "counterparties", transaction["counterparty_id"])


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


def _cmd_document_transition(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        row = db.transition_document(
            args.document_id,
            lifecycle_status=args.to_status,
            expected_row_version=args.expected_row_version,
        )
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


def _cmd_transaction_set_counterparty(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        row = db.update_transaction_counterparty(
            args.transaction_id,
            counterparty_id=args.counterparty_id,
            expected_row_version=args.expected_row_version,
        )
    _emit(row)
    return 0


def _cmd_transaction_set_activity(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        row = db.set_transaction_business_activity(
            args.transaction_id,
            business_activity_id=args.business_activity_id,
            expected_row_version=args.expected_row_version,
        )
    _emit(row)
    return 0


def _cmd_transaction_apply_fx(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        fx_rate = db.connection.execute(
            "SELECT rate, base_currency, rate_date, rate_source FROM fx_rates WHERE fx_rate_id = ?",
            (args.fx_rate_id,),
        ).fetchone()
        note = None
        if fx_rate is not None:
            note = _check_manual_fx_rate(
                str(fx_rate["rate"]),
                currency=str(fx_rate["base_currency"]),
                rate_date=str(fx_rate["rate_date"]),
                rate_source=str(fx_rate["rate_source"]),
                allow_unverified=args.allow_unverified_rate,
            )
        row = db.apply_transaction_fx(
            args.transaction_id,
            fx_rate_id=args.fx_rate_id,
            expected_row_version=args.expected_row_version,
            source_reference_note=note,
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


def _cmd_invoice_template_upsert(args: argparse.Namespace) -> int:
    payload = _load_json_object(args.input)
    with open_ledger_db(args.db) as db:
        row = db.upsert_invoice_template(**payload)
    normalized = dict(row)
    normalized["default_lines"] = json.loads(normalized.pop("default_lines_json"))
    _emit(normalized)
    return 0


def _cmd_invoice_templates(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db, read_only=True) as db:
        rows = db.list_invoice_templates(include_inactive=args.include_inactive)
    _emit(rows)
    return 0


def _cmd_invoice_draft(args: argparse.Namespace) -> int:
    payload = _load_json_object(args.input)
    with open_ledger_db(args.db) as db:
        row = db.create_outgoing_invoice_draft(**payload)
    _emit(row)
    return 0


def _cmd_invoice_review(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        row = db.review_outgoing_invoice_draft(
            args.outgoing_invoice_draft_id,
            expected_row_version=args.expected_row_version,
        )
    _emit(row)
    return 0


def _cmd_invoice_finalize(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        row = db.finalize_outgoing_invoice_draft(
            args.outgoing_invoice_draft_id,
            document_id=args.document_id,
            transaction_id=args.transaction_id,
            external_number=args.external_number,
            external_series=args.external_series,
            expected_row_version=args.expected_row_version,
        )
    _emit(row)
    return 0


def _cmd_invoice_void(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        row = db.void_outgoing_invoice_draft(
            args.outgoing_invoice_draft_id,
            reason=args.reason,
            expected_row_version=args.expected_row_version,
        )
    _emit(row)
    return 0


def _cmd_invoice_show(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db, read_only=True) as db:
        row = db.get_outgoing_invoice_draft(args.outgoing_invoice_draft_id)
    _write_or_emit(row, args.out)
    return 0


def _cmd_invoice_list(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db, read_only=True) as db:
        rows = db.list_outgoing_invoice_drafts(period_key=args.period)
    _emit(rows)
    return 0


def _cmd_invoice_series_status(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db, read_only=True) as db:
        observations = collect_invoice_number_observations(
            db.connection,
            year=args.year,
        )
    report = invoice_series_status(
        observations,
        year=args.year,
        requested_series=args.series,
        bare_belongs_to_series=args.bare_belongs_to_series,
    )
    _emit(report)
    return 0 if report["status"] == "ok" else 2


def _cmd_assets_list(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db, read_only=True) as db:
        rows = [
            dict(row)
            for row in db.connection.execute(
                "SELECT * FROM assets ORDER BY placed_in_service_on, asset_code"
            ).fetchall()
        ]
    _emit(rows)
    return 0


def _cmd_asset_book_profile(args: argparse.Namespace) -> int:
    payload = _load_json_object(args.input)
    asset_id = str(payload.pop("asset_id", "")).strip()
    if not asset_id:
        raise ValueError("Asset book profile requires asset_id")
    with open_ledger_db(args.db) as db:
        row = db.update_asset_book_profile(asset_id, **payload)
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
            due_on=args.due_on,
            filed_at=args.filed_at,
            expected_row_version=args.expected_row_version,
        )
    _emit(row)
    return 0


def _cmd_obligation_record_evidence(args: argparse.Namespace) -> int:
    if not args.evidence.is_file():
        raise FileNotFoundError(f"Obligation evidence is missing: {args.evidence}")
    with open_ledger_db(args.db) as db:
        obligation = db.connection.execute(
            """
            SELECT o.obligation_id
            FROM obligations o
            JOIN periods p ON p.period_id = o.period_id
            WHERE p.period_key = ? AND o.obligation_code = ?
            """,
            (args.period, args.form),
        ).fetchone()
        if obligation is None:
            raise ValueError(
                f"Obligation {args.period}:{args.form} is not present in the database"
            )
        row = db.add_obligation_evidence(
            obligation_id=str(obligation["obligation_id"]),
            evidence_kind=args.kind,
            source_reference=args.source_reference or str(args.evidence.resolve()),
            source_hash=_sha256(args.evidence),
            notes=args.notes,
        )
    _emit(row)
    return 0


def _cmd_calendar_import(args: argparse.Namespace) -> int:
    loaded = load_tax_calendar(args.input)
    records = [entry_as_record(entry) for entry in loaded.entries]
    with open_ledger_db(args.db) as db:
        imported = db.import_tax_calendar(records)
    _emit(
        {
            "calendar_year": loaded.calendar_year,
            "source_file": str(args.input.resolve()),
            "source_file_hash": loaded.source_file_hash,
            "entry_count": len(imported),
            "entries": imported,
        }
    )
    return 0


def _cmd_calendar_list(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        rows = db.list_tax_calendar_entries(
            calendar_year=args.year,
            period_key=args.period,
        )
    _write_or_emit(rows, args.out)
    return 0


def _cmd_agenda(args: argparse.Namespace) -> int:
    from .agenda import build_tax_agenda, select_cash_period

    if args.horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    with open_ledger_db(args.db, read_only=True) as db:
        periods = db.list_periods()
        obligations_by_period = {
            str(period["period_key"]): db.list_obligations_with_deadlines(
                period_key=str(period["period_key"])
            )
            for period in periods
        }
        cash_period = select_cash_period(
            periods=periods,
            obligations_by_period=obligations_by_period,
            as_of=args.as_of,
        )
        cash_check = None
        if cash_period is not None:
            _, _, preparation = _build_period_preparation_context(
                db,
                period_key=cash_period,
                as_of=args.as_of,
                available_eur=args.available_eur,
                cash_buffer_eur=args.cash_buffer_eur,
            )
            cash_check = preparation["cash_check"]
        settlements = _agenda_pending_settlements(
            db,
            obligations_by_period=obligations_by_period,
            as_of=args.as_of,
            through=args.as_of + timedelta(days=args.horizon_days),
        )
        report = build_tax_agenda(
            periods=periods,
            obligations_by_period=obligations_by_period,
            as_of=args.as_of,
            horizon_days=args.horizon_days,
            cash_period=cash_period,
            cash_check=cash_check,
            settlements=settlements,
        )
    _write_or_emit(report, args.out)
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


def _cmd_period_dashboard(args: argparse.Namespace) -> int:
    from .current_quarter import (
        add_xolo_comparison,
        build_current_quarter_dashboard,
        load_xolo_modelo130_forecast,
        write_current_quarter_dashboard,
    )

    try:
        year_text, quarter_text = args.period.split("-Q", 1)
        year = int(year_text)
        quarter = int(quarter_text)
    except (ValueError, TypeError) as exc:
        raise ValueError("Dashboard period must use YYYY-QN format") from exc
    if quarter not in {1, 2, 3, 4} or args.period != f"{year}-Q{quarter}":
        raise ValueError("Dashboard period must use YYYY-QN format")

    with open_ledger_db(args.db, read_only=True) as db:
        actual_rows = _tax_rows_from_db(
            db,
            year,
            mode="production",
            allow_authoritative_history=True,
        )
        projected_rows = _tax_rows_from_db(
            db,
            year,
            mode="production",
            allow_authoritative_history=True,
            include_approved_periods={args.period},
        )
        rule = difficult_expense_rule_for_year(year)
        dashboard = build_current_quarter_dashboard(
            actual_rows=actual_rows,
            projected_rows=projected_rows,
            period_key=args.period,
            as_of=args.as_of,
            difficult_expenses_rate=rule.rate,
            previous_positive_casilla_07=_previous_filed_positive(db, year, quarter),
            previous_negative_carry=_previous_filed_negative_carry(db, year, quarter),
            previous_vat_compensation=_previous_filed_vat_compensation(db, year, quarter),
            approved_current_rows=_approved_forecast_rows(db, args.period),
            obligations=db.list_obligations_with_deadlines(period_key=args.period),
            period_validation=db.validate_period(
                args.period,
                allow_authoritative_history=True,
            ),
        )

    if args.xolo_modelo130_calculations:
        add_xolo_comparison(
            dashboard,
            load_xolo_modelo130_forecast(
                args.xolo_modelo130_calculations,
                period=args.period,
            ),
        )
    outputs = write_current_quarter_dashboard(dashboard, args.out_dir)
    _emit(
        {
            "period": args.period,
            "filing_assessment_performed": False,
            "filing_ready": False,
            "submission_ready": False,
            "outputs": {key: str(path.resolve()) for key, path in outputs.items()},
            "blocking_item_count": len(dashboard["blocking_items"]),
            "expected_item_count": len(dashboard["expected_items"]),
        }
    )
    return 0


def _cmd_period_prepare(args: argparse.Namespace) -> int:
    from .current_quarter import write_current_quarter_dashboard
    from .filing_package import write_filing_package
    from .period_prepare import write_period_preparation

    with open_ledger_db(args.db, read_only=True) as db:
        _, dashboard, report = _build_period_preparation_context(
            db,
            period_key=args.period,
            as_of=args.as_of,
            available_eur=args.available_eur,
            cash_buffer_eur=args.cash_buffer_eur,
        )
        filing_manifest = write_filing_package(
            db,
            args.out_dir / "books",
            period_key=args.period,
        )

    dashboard_outputs = write_current_quarter_dashboard(
        dashboard,
        args.out_dir / "dashboard",
    )
    outputs = write_period_preparation(report, args.out_dir)
    outputs["filing_manifest"] = filing_manifest
    outputs.update(
        {f"dashboard_{key}": value for key, value in dashboard_outputs.items()}
    )
    _emit(
        {
            "period": args.period,
            "status": report["status"],
            "preparation_ready": report["preparation_ready"],
            "filing_ready": report["filing_ready"],
            "required_tax_eur": report["cash_check"]["required_tax_eur"],
            "outputs": {key: str(path.resolve()) for key, path in outputs.items()},
        }
    )
    return 2 if report["status"] == "blocked" else 0


def _build_period_preparation_context(
    db: LedgerDB,
    *,
    period_key: str,
    as_of: date,
    available_eur: Decimal | None,
    cash_buffer_eur: Decimal | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    from .current_quarter import build_current_quarter_dashboard
    from .period_prepare import build_period_preparation

    year, quarter = _parse_quarter_period(period_key)
    period = next(
        (row for row in db.list_periods() if row["period_key"] == period_key),
        None,
    )
    if period is None:
        raise ValueError(f"Unknown period: {period_key}")
    actual_rows = _tax_rows_from_db(
        db,
        year,
        mode="production",
        allow_authoritative_history=True,
    )
    projected_rows = _tax_rows_from_db(
        db,
        year,
        mode="production",
        allow_authoritative_history=True,
        include_approved_periods={period_key},
    )
    obligations = db.list_obligations_with_deadlines(period_key=period_key)
    rule = difficult_expense_rule_for_year(year)
    dashboard = build_current_quarter_dashboard(
        actual_rows=actual_rows,
        projected_rows=projected_rows,
        period_key=period_key,
        as_of=as_of,
        difficult_expenses_rate=rule.rate,
        previous_positive_casilla_07=_previous_filed_positive(db, year, quarter),
        previous_negative_carry=_previous_filed_negative_carry(db, year, quarter),
        previous_vat_compensation=_previous_filed_vat_compensation(db, year, quarter),
        approved_current_rows=_approved_forecast_rows(db, period_key),
        obligations=obligations,
        period_validation=db.validate_period(
            period_key,
            allow_authoritative_history=True,
        ),
    )
    calculations = _period_prepare_calculations(
        dashboard=dashboard,
        rows=projected_rows,
        obligations=obligations,
        year=year,
        quarter=quarter,
    )
    report = build_period_preparation(
        period=period,
        as_of=as_of,
        dashboard=dashboard,
        calculations=calculations,
        obligations=obligations,
        available_eur=available_eur,
        cash_buffer_eur=cash_buffer_eur,
    )
    return period, dashboard, report


def _agenda_pending_settlements(
    db: LedgerDB,
    *,
    obligations_by_period: Mapping[str, list[dict[str, Any]]],
    as_of: date,
    through: date,
) -> list[dict[str, Any]]:
    from .cash_check import PAYABLE_KEYS
    from .shadow_close import summarize_required_tax_settlements

    lookback = as_of - timedelta(days=45)
    due_dates: dict[tuple[str, str], date] = {}
    for period_key, obligations in obligations_by_period.items():
        for obligation in obligations:
            if (
                obligation.get("determination") != "due"
                or obligation.get("filing_status") != "filed"
                or str(obligation.get("obligation_code")) not in PAYABLE_KEYS
            ):
                continue
            raw_due = obligation.get("due_on") or obligation.get(
                "calendar_statutory_due_on"
            )
            if not raw_due:
                continue
            due_on = date.fromisoformat(str(raw_due))
            if lookback <= due_on <= through:
                due_dates[(period_key, str(obligation["obligation_code"]))] = due_on
    if not due_dates:
        return []

    state = summarize_required_tax_settlements(db, selectors=due_dates)
    settlements: list[dict[str, Any]] = []
    for requirement in state["requirements"]:
        period_key, form = str(requirement["selector"]).split(":", 1)
        expected_minor = requirement.get("expected_amount_minor")
        if expected_minor is None or int(expected_minor) <= 0:
            continue
        settlements.append(
            {
                "period": period_key,
                "form": form,
                "due_on": due_dates[(period_key, form)].isoformat(),
                "expected_amount_eur": f"{Decimal(int(expected_minor)) / 100:.2f}",
                "status": requirement["status"],
            }
        )
    return settlements


def _cmd_period_shadow_close(args: argparse.Namespace) -> int:
    from .aeat_books import (
        AeatBookProjectionError,
        build_aeat_book_projection,
        failed_aeat_book_projection,
        write_aeat_book_projection,
    )
    from .current_quarter import (
        build_current_quarter_dashboard,
        write_current_quarter_dashboard,
    )
    from .offboarding import verify_offboarding_manifest
    from .shadow_close import (
        build_shadow_close_report,
        load_invoice_channel_assessment,
        summarize_operational_acceptance,
        summarize_payment_state,
        summarize_required_tax_settlements,
        write_shadow_close_report,
    )

    year, quarter = _parse_quarter_period(args.period)
    required_settlement_selectors = [
        _parse_tax_obligation_selector(value)
        for value in args.required_settled_obligation
    ]
    with open_ledger_db(args.db, read_only=True) as db:
        period = next(
            (row for row in db.list_periods() if row["period_key"] == args.period),
            None,
        )
        if period is None:
            raise ValueError(f"Unknown period: {args.period}")
        actual_rows = _tax_rows_from_db(
            db,
            year,
            mode="production",
            allow_authoritative_history=True,
        )
        projected_rows = _tax_rows_from_db(
            db,
            year,
            mode="production",
            allow_authoritative_history=True,
            include_approved_periods={args.period},
        )
        rule = difficult_expense_rule_for_year(year)
        dashboard = build_current_quarter_dashboard(
            actual_rows=actual_rows,
            projected_rows=projected_rows,
            period_key=args.period,
            as_of=args.as_of,
            difficult_expenses_rate=rule.rate,
            previous_positive_casilla_07=_previous_filed_positive(
                db, year, quarter
            ),
            previous_negative_carry=_previous_filed_negative_carry(
                db, year, quarter
            ),
            previous_vat_compensation=_previous_filed_vat_compensation(
                db, year, quarter
            ),
            approved_current_rows=_approved_forecast_rows(db, args.period),
            obligations=db.list_obligations_with_deadlines(
                period_key=args.period
            ),
            period_validation=db.validate_period(
                args.period,
                allow_authoritative_history=True,
            ),
        )
        try:
            aeat_projection = build_aeat_book_projection(
                db,
                period_key=args.period,
                allow_authoritative_history=True,
            )
        except AeatBookProjectionError as exc:
            aeat_projection = failed_aeat_book_projection(
                period_key=args.period,
                message=str(exc),
                allow_authoritative_history=True,
            )
        payment_state = summarize_payment_state(
            db,
            period=period,
            as_of=args.as_of,
        )
        operational_acceptance = (
            summarize_operational_acceptance(
                db,
                proof_since=args.operational_proof_since,
                as_of=args.as_of,
            )
            if args.operational_proof_since is not None
            else None
        )
        required_tax_settlements = summarize_required_tax_settlements(
            db,
            selectors=required_settlement_selectors,
        )

    offboarding_verification = None
    if args.offboarding_manifest is not None:
        manifest = json.loads(
            args.offboarding_manifest.read_text(encoding="utf-8")
        )
        offboarding_verification = verify_offboarding_manifest(
            manifest,
            required_generated_on=args.as_of,
        )
        offboarding_verification["manifest_path"] = str(
            args.offboarding_manifest.resolve()
        )
    invoice_channel_assessment = load_invoice_channel_assessment(
        args.invoice_channel_assessment
    )
    report = build_shadow_close_report(
        period=period,
        as_of=args.as_of,
        dashboard=dashboard,
        aeat_projection=aeat_projection,
        payment_state=payment_state,
        offboarding_verification=offboarding_verification,
        invoice_channel_assessment=invoice_channel_assessment,
        operational_acceptance=operational_acceptance,
        required_tax_settlements=required_tax_settlements,
    )

    dashboard_outputs = write_current_quarter_dashboard(
        dashboard, args.out_dir
    )
    aeat_path = write_aeat_book_projection(
        args.out_dir / "aeat-preview.json", aeat_projection
    )
    report_outputs = write_shadow_close_report(report, args.out_dir)
    _emit(
        {
            "period": args.period,
            "summary": report["summary"],
            "outputs": {
                **{
                    f"dashboard_{key}": str(path.resolve())
                    for key, path in dashboard_outputs.items()
                },
                "aeat_preview": str(aeat_path.resolve()),
                **{
                    f"shadow_close_{key}": str(path.resolve())
                    for key, path in report_outputs.items()
                },
            },
        }
    )
    return 0


def _cmd_period_open(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        period = db.ensure_period(args.period, starts_on=args.starts_on, ends_on=args.ends_on)
        seeded = db.seed_unreviewed_obligations(args.period)
        _emit(
            {
                "period": period,
                "seeded_obligations": [row["obligation_code"] for row in seeded],
            }
        )
    return 0


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
    args.db.parent.mkdir(parents=True, exist_ok=True)
    opener = open_ledger_db if args.db.exists() else initialize
    with opener(args.db) as db:
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


def _cmd_storage_migrate(args: argparse.Namespace) -> int:
    private_paths = getattr(args, "_private_paths", None)
    local_root = args.local_root or (
        private_paths.root if private_paths is not None else args.db.parent
    )
    try:
        result = migrate_legacy_storage(
            database=args.db,
            local_root=local_root,
            drive_mount_root=args.drive_mount_root,
            batch_size=args.batch_size,
            require_complete=args.require_complete,
            snapshot_path=args.snapshot_out,
        )
    except StorageMigrationError as exc:
        raise SystemExit(str(exc)) from exc
    _emit(result.as_dict())
    return 0


def _cmd_storage_reconcile(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db) as db:
        try:
            result = reconcile_backend(db, backend_key=args.backend, limit=args.limit)
        except StorageReconcileError as exc:
            raise SystemExit(str(exc)) from exc
    _emit(result)
    return 2 if result["failed"] else 0


def _cmd_storage_backend_upsert(args: argparse.Namespace) -> int:
    payload = _load_json_object(args.input)
    with open_ledger_db(args.db) as db:
        row = db.upsert_storage_backend(**payload)
    _emit(row)
    return 0


def _cmd_storage_backends_apply(args: argparse.Namespace) -> int:
    document = json.loads(args.input.read_text(encoding="utf-8"))
    payload = document.get("backends") if isinstance(document, dict) else document
    if not isinstance(payload, list) or not payload:
        raise ValueError("storage backends input must contain a non-empty JSON array")
    if not all(isinstance(item, dict) for item in payload):
        raise ValueError("every storage backend entry must be a JSON object")
    backend_keys = [str(item.get("backend_key") or "") for item in payload]
    if any(not key for key in backend_keys) or len(set(backend_keys)) != len(backend_keys):
        raise ValueError("storage backend keys must be non-empty and unique")
    with open_ledger_db(args.db) as db:
        db.connection.execute("BEGIN IMMEDIATE")
        try:
            rows = [db.upsert_storage_backend(**item) for item in payload]
        except Exception:
            db.connection.rollback()
            raise
        else:
            db.connection.commit()
    _emit({"applied": len(rows), "backend_keys": [row["backend_key"] for row in rows]})
    return 0


def _cmd_storage_adopt_google_archive(args: argparse.Namespace) -> int:
    records = _load_json_array(args.records)
    with open_ledger_db(args.db) as db:
        try:
            result = adopt_google_drive_originals(
                db,
                backend_key=args.backend,
                records=records,
                dry_run=args.dry_run,
                retire_backend_keys=args.retire_backend,
            )
        except StorageMigrationError as exc:
            raise SystemExit(str(exc)) from exc
    _emit(result.as_dict())
    return 0


def _cmd_storage_cleanup_preflight(args: argparse.Namespace) -> int:
    with open_ledger_db(args.db, read_only=True) as db:
        result = check_cleanup_preflight(
            db,
            archive_backend_key=args.archive_backend,
            yandex_backend_key=args.yandex_backend,
            managed_backend_key=args.managed_backend,
            backup_restore_proof=args.backup_restore_proof,
            max_proof_age_hours=args.max_proof_age_hours,
        )
    _emit(result.as_dict())
    return 0 if result.passed else 2


def _cmd_bank_import_revolut(args: argparse.Namespace) -> int:
    payments = load_revolut_payments_csv(args.csv)
    source_digest = _sha256(args.csv)
    with open_ledger_db(args.db) as db:
        rollback = sqlite3.connect(":memory:")
        db.connection.backup(rollback)
        try:
            existing_by_external_id = {
                row["external_id"]: row
                for row in db.connection.execute(
                    "SELECT * FROM payments WHERE source_system = 'revolut'"
                ).fetchall()
                if row["external_id"]
            }
            claimed_legacy = 0
            for payment in payments:
                existing = existing_by_external_id.get(payment.payment_id)
                if existing is not None:
                    _validate_existing_revolut_payment(existing, payment)
                    continue
                claimed = _claim_legacy_revolut_payment(db, payment)
                if claimed is not None:
                    existing_by_external_id[payment.payment_id] = claimed
                    claimed_legacy += 1
            new_payments = [
                payment
                for payment in payments
                if payment.payment_id not in existing_by_external_id
            ]
            candidates = _payment_match_candidates(db, exclude_settled=True)
            matches = {
                match.payment_id: match
                for match in match_revolut_payments(new_payments, candidates)
            }
            imported = []
            for payment in new_payments:
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
                    source_hash=hashlib.sha256(f"revolut:{payment.payment_id}".encode()).hexdigest(),
                    source_system="revolut",
                    external_id=payment.payment_id,
                    account_name="Revolut",
                    counterparty_name=payment.counterparty,
                    comment=payment.description,
                    amount_eur_minor=(
                        int(payment.amount_eur * 100) if payment.amount_eur is not None else None
                    ),
                    source_row_json=json.dumps(
                        payment.source_row,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                imported.append({"payment_id": row["payment_id"], "match": asdict(match)})
        except Exception:
            db.connection.rollback()
            rollback.backup(db.connection)
            raise
        finally:
            rollback.close()
    _emit(
        {
            "source_sha256": source_digest,
            "payments": imported,
            "already_imported": len(payments) - len(new_payments),
            "claimed_legacy": claimed_legacy,
        }
    )
    return 0


def _cmd_bank_record_payment(args: argparse.Namespace) -> int:
    if not args.evidence.is_file():
        raise FileNotFoundError(args.evidence)
    if args.archive_root is None:
        raise ValueError("--archive-root is required (or set archive_root in the private config)")
    amount_minor = _positive_minor(args.amount, field="amount")
    currency = str(args.currency).strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("currency must be a three-letter ISO code")
    amount_eur_minor = (
        _positive_minor(args.amount_eur, field="amount-eur")
        if args.amount_eur is not None
        else amount_minor if currency == "EUR" else None
    )
    source_system = str(args.source_system).strip()
    external_id = str(args.external_id).strip()
    if not source_system:
        raise ValueError("source-system must not be blank")
    if not external_id:
        raise ValueError("external-id must not be blank")

    evidence_digest = _sha256(args.evidence)
    payment_source_hash = hashlib.sha256(
        f"{source_system}:{external_id}".encode("utf-8")
    ).hexdigest()
    with open_ledger_db(args.db) as db:
        transaction_id = args.transaction_id
        obligation_id = args.obligation_id
        obligation_code = None
        if transaction_id is not None:
            target = db.connection.execute(
                """
                SELECT t.transaction_id, p.period_key
                FROM transactions t
                JOIN periods p ON p.period_id = t.period_id
                WHERE t.transaction_id = ?
                """,
                (transaction_id,),
            ).fetchone()
            if target is None:
                raise LedgerDbError(f"Unknown transaction_id: {transaction_id}")
            evidence_kind = "payment_evidence"
        else:
            selector_period = None
            selector_form = None
            if args.tax_obligation is not None:
                selector_period, selector_form = _parse_tax_obligation_selector(
                    args.tax_obligation
                )
                target = db.connection.execute(
                    """
                    SELECT o.obligation_id, o.obligation_code, o.determination,
                           o.filing_status, p.period_key
                    FROM obligations o
                    JOIN periods p ON p.period_id = o.period_id
                    WHERE p.period_key = ? AND o.obligation_code = ?
                    """,
                    (selector_period, selector_form),
                ).fetchone()
                target_reference = args.tax_obligation
            else:
                target = db.connection.execute(
                    """
                    SELECT o.obligation_id, o.obligation_code, o.determination,
                           o.filing_status, p.period_key
                    FROM obligations o
                    JOIN periods p ON p.period_id = o.period_id
                    WHERE o.obligation_id = ?
                    """,
                    (obligation_id,),
                ).fetchone()
                target_reference = obligation_id
            if target is None:
                raise LedgerDbError(f"Unknown tax obligation: {target_reference}")
            if target["determination"] != "due":
                raise LedgerDbError(
                    f"Tax obligation {target['period_key']}:{target['obligation_code']} is not due"
                )
            if target["filing_status"] != "filed":
                raise LedgerDbError(
                    f"Tax obligation {target['period_key']}:{target['obligation_code']} is not filed"
                )
            obligation_id = target["obligation_id"]
            obligation_code = target["obligation_code"]
            evidence_kind = "tax_payment_evidence"

        archived_path = evidence_archive_path(
            args.evidence,
            args.archive_root,
            period_key=target["period_key"],
            evidence_kind=evidence_kind,
            digest=evidence_digest,
        )
        evidence_locator = archived_path.relative_to(args.archive_root.resolve()).as_posix()
        source_payload = {
            "schema_version": 1,
            "evidence_sha256": evidence_digest,
            "evidence_locator": evidence_locator,
            "source_system": source_system,
            "external_id": external_id,
        }
        source_row_json = json.dumps(
            source_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        desired = {
            "transaction_id": transaction_id,
            "obligation_id": obligation_id,
            "paid_on": args.paid_on.isoformat(),
            "amount_minor": amount_minor,
            "currency": currency,
            "original_reference": args.reference,
            "fee_minor": None,
            "fee_currency": None,
            "match_status": "manual",
            "source_system": source_system,
            "external_id": external_id,
            "account_name": args.account_name,
            "counterparty_name": args.counterparty_name,
            "category": None,
            "comment": args.comment,
            "amount_eur_minor": amount_eur_minor,
            "source_row_json": source_row_json,
        }
        existing = db.connection.execute(
            "SELECT * FROM payments WHERE source_system = ? AND external_id = ?",
            (source_system, external_id),
        ).fetchone()
        if existing is not None:
            changed = [key for key, value in desired.items() if existing[key] != value]
            if existing["source_hash"] != payment_source_hash:
                changed.append("source_hash")
            if changed:
                raise LedgerDbError(
                    "Payment source identity already exists with different fields: "
                    + ", ".join(sorted(set(changed)))
                )
            archive_evidence(
                args.evidence,
                args.archive_root,
                period_key=target["period_key"],
                evidence_kind=evidence_kind,
                digest=evidence_digest,
            )
            row = existing
            already_imported = True
        else:
            archive_evidence(
                args.evidence,
                args.archive_root,
                period_key=target["period_key"],
                evidence_kind=evidence_kind,
                digest=evidence_digest,
            )
            row = db.add_payment(source_hash=payment_source_hash, **desired)
            already_imported = False

    _emit(
        {
            "payment_id": row["payment_id"],
            "transaction_id": row["transaction_id"],
            "obligation_id": row["obligation_id"],
            "obligation_code": obligation_code,
            "period": target["period_key"],
            "paid_on": row["paid_on"],
            "amount_minor": row["amount_minor"],
            "currency": row["currency"],
            "evidence_sha256": evidence_digest,
            "evidence_locator": evidence_locator,
            "already_imported": already_imported,
        }
    )
    return 0


def _cmd_bank_import_zenmoney(args: argparse.Namespace) -> int:
    source_digest = _sha256(args.csv)
    source_inspection = inspect_zenmoney_csv(args.csv)
    with open_ledger_db(args.db) as db:
        period = db.ensure_period(args.period)
        load_result = load_zenmoney_payments_csv(
            args.csv,
            business_accounts=set(args.account),
            starts_on=date.fromisoformat(period["starts_on"]),
            ends_on=date.fromisoformat(period["ends_on"]),
            default_currency=args.default_currency,
        )
        archived_path = (
            archive_evidence(
                args.csv,
                args.archive_root,
                period_key=args.period,
                evidence_kind="zenmoney_exports",
                digest=source_digest,
            )
            if args.archive_root is not None
            else args.csv.resolve()
        )
        existing_by_external_id = {
            row["external_id"]: row
            for row in db.connection.execute(
                "SELECT * FROM payments WHERE source_system = 'zenmoney'"
            ).fetchall()
            if row["external_id"]
        }
        for payment in load_result.payments:
            existing = existing_by_external_id.get(payment.external_id)
            if existing is not None:
                _validate_existing_zenmoney_payment(existing, payment)
        new_payments = [
            payment
            for payment in load_result.payments
            if payment.external_id not in existing_by_external_id
        ]
        candidates = _payment_match_candidates(db, exclude_settled=True)
        matches = {
            match.payment_id: match
            for match in match_revolut_payments(new_payments, candidates)  # type: ignore[arg-type]
        }
        imported: list[dict[str, Any]] = []
        rollback = sqlite3.connect(":memory:")
        db.connection.backup(rollback)
        try:
            db.add_import_batch(
                source_name=str(archived_path),
                source_hash=source_digest,
                batch_key=f"zenmoney:{source_digest}",
                notes=json.dumps(
                    {
                        "schema_version": 1,
                        "period_key": args.period,
                        "source_starts_on": (
                            source_inspection.starts_on.isoformat()
                            if source_inspection.starts_on
                            else None
                        ),
                        "source_ends_on": (
                            source_inspection.ends_on.isoformat()
                            if source_inspection.ends_on
                            else None
                        ),
                        "business_accounts": sorted(args.account),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            for payment in new_payments:
                match = matches[payment.payment_id]
                transaction_id = match.matched_candidate_ids[0] if match.outcome == "exact" else None
                source_hash = hashlib.sha256(
                    f"zenmoney:{payment.external_id}".encode("utf-8")
                ).hexdigest()
                stored = db.add_payment(
                    transaction_id=transaction_id,
                    paid_on=payment.payment_date.isoformat(),
                    amount_minor=int(payment.amount_original * 100),
                    currency=payment.currency,
                    original_reference=payment.reference,
                    match_status=match.outcome,
                    source_hash=source_hash,
                    source_system="zenmoney",
                    external_id=payment.external_id,
                    account_name=payment.account_name,
                    counterparty_name=payment.counterparty,
                    category=payment.category,
                    comment=payment.comment,
                    amount_eur_minor=(
                        int(payment.amount_eur * 100) if payment.amount_eur is not None else None
                    ),
                    source_row_json=json.dumps(
                        payment.source_row,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                imported.append({"payment_id": stored["payment_id"], "match": asdict(match)})
            missing_from_export = _sync_zenmoney_missing_issues(
                db,
                period_key=args.period,
                business_accounts=set(args.account),
                present_external_ids={payment.external_id for payment in load_result.payments},
            )
        except Exception:
            db.connection.rollback()
            rollback.backup(db.connection)
            raise
        finally:
            rollback.close()
    _emit(
        {
            "source_sha256": source_digest,
            "archived_path": str(archived_path),
            "period": args.period,
            "imported": imported,
            "already_imported": len(load_result.payments) - len(new_payments),
            "skipped": [asdict(row) for row in load_result.skipped],
            "missing_from_export": missing_from_export,
        }
    )
    return 0


def _cmd_bank_inspect_zenmoney(args: argparse.Namespace) -> int:
    inspection = asdict(inspect_zenmoney_csv(args.csv))
    inspection["source_sha256"] = _sha256(args.csv)
    _emit(inspection)
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
                           legal_form, professional_supplier, retention_expected, email, phone
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
            "tax_treatments": db.connection.execute(
                """SELECT tt.treatment_id AS uuid, tt.row_version,
                          CASE WHEN p.status = 'open'
                                      AND tt.treatment_type = 'invoice_review'
                                      AND t.lifecycle_status IN (
                                          'received', 'extracted', 'needs_review', 'approved'
                                      ) THEN 'open'
                               ELSE 'closed' END AS status,
                          p.period_key, t.transaction_id,
                          t.lifecycle_status AS transaction_lifecycle,
                          t.transaction_date, t.entry_type, t.description,
                          COALESCE(c.display_name, '') AS counterparty,
                          COALESCE(d.document_number, '') AS document_number,
                          printf('%.2f', t.amount_original_minor / 100.0) AS gross_original,
                          t.original_currency,
                          CASE WHEN t.amount_eur_minor IS NULL THEN ''
                               ELSE printf('%.2f', t.amount_eur_minor / 100.0) END AS amount_eur,
                          tt.treatment_type, tt.jurisdiction, tt.tax_code,
                          COALESCE(tt.aeat_invoice_type, '') AS aeat_invoice_type,
                          COALESCE(tt.aeat_operation_key, '') AS aeat_operation_key,
                          COALESCE(tt.aeat_operation_qualification, '')
                              AS aeat_operation_qualification,
                          COALESCE(tt.aeat_exemption_code, '') AS aeat_exemption_code,
                          CASE WHEN tt.aeat_reverse_charge IS NULL THEN ''
                               ELSE tt.aeat_reverse_charge END AS aeat_reverse_charge,
                          COALESCE(tt.aeat_expense_concept, '') AS aeat_expense_concept,
                          tt.rate_basis_points, tt.deductible_ratio,
                          CASE WHEN tt.taxable_base_minor IS NULL THEN ''
                               ELSE printf('%.2f', tt.taxable_base_minor / 100.0) END AS taxable_base_eur,
                          CASE WHEN tt.vat_minor IS NULL THEN ''
                               ELSE printf('%.2f', tt.vat_minor / 100.0) END AS vat_eur,
                          CASE WHEN tt.deductible_irpf_minor IS NULL THEN ''
                               ELSE printf('%.2f', tt.deductible_irpf_minor / 100.0) END AS deductible_irpf_eur,
                          CASE WHEN tt.deductible_vat_minor IS NULL THEN ''
                               ELSE printf('%.2f', tt.deductible_vat_minor / 100.0) END AS deductible_vat_eur,
                          CASE WHEN tt.withholding_minor IS NULL THEN ''
                               ELSE printf('%.2f', tt.withholding_minor / 100.0) END AS withholding_eur,
                          tt.include_modelo130, tt.include_modelo303,
                          tt.include_modelo347, COALESCE(tt.rule_version_id, '') AS rule_version_id,
                          COALESCE(tt.notes, '') AS notes
                   FROM tax_treatments tt
                   JOIN transactions t ON t.transaction_id=tt.transaction_id
                   JOIN periods p ON p.period_id=t.period_id
                   LEFT JOIN counterparties c ON c.counterparty_id=t.counterparty_id
                   LEFT JOIN documents d ON d.document_id=t.document_id
                   WHERE tt.treatment_type = 'invoice_review'
                   ORDER BY t.transaction_date, t.transaction_id, tt.treatment_id"""
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
                          amount_eur_minor, original_reference, fee_minor, fee_currency,
                          source_system, external_id, account_name, counterparty_name,
                          category, comment
                   FROM payments ORDER BY paid_on, payment_id"""
            ).fetchall(),
            "filings": db.connection.execute(
                """SELECT fs.filing_snapshot_id AS uuid, fs.row_version, fs.status,
                          p.period_key, fs.form_code, fs.snapshot_hash, fs.filed_on,
                          fs.submission_reference, fs.justificante_number, fs.verification_code,
                          fs.source_reference, fs.manifest_path
                   FROM filing_snapshots fs JOIN periods p ON p.period_id=fs.period_id
                   ORDER BY p.starts_on, fs.form_code, fs.filed_on"""
            ).fetchall(),
            "obligations": db.connection.execute(
                """SELECT o.obligation_id AS uuid, o.row_version, o.filing_status AS status,
                          p.period_key, o.obligation_code, o.determination, o.explanation,
                          o.source_citation, o.blocking, o.due_on
                   FROM obligations o JOIN periods p ON p.period_id=o.period_id
                   ORDER BY p.starts_on, o.obligation_code"""
            ).fetchall(),
            "tax_calendar": db.connection.execute(
                """SELECT tc.tax_calendar_entry_id AS uuid, tc.row_version,
                          tc.deadline_status AS status, tc.calendar_year, p.period_key,
                          tc.form_code, tc.filing_opens_on, tc.internal_due_on,
                          tc.direct_debit_cutoff_on, tc.statutory_due_on,
                          tc.source_url, tc.source_checked_on, tc.notes
                   FROM tax_calendar_entries tc
                   JOIN periods p ON p.period_id=tc.period_id
                   ORDER BY tc.statutory_due_on, p.period_key, tc.form_code"""
            ).fetchall(),
            "rules_sources": db.connection.execute(
                """SELECT rule_version_id AS uuid, row_version, 'historical' AS status,
                          rule_name, version, activated_at, source_hash
                   FROM rule_versions ORDER BY rule_name, version"""
            ).fetchall(),
        }
        outputs = {
            name: str(
                _write_rows_csv(
                    args.out_dir / f"{name}.csv",
                    rows,
                    fieldnames=(
                        PAYMENT_SHEET_FIELDS
                        if name == "payments"
                        else TAX_TREATMENT_SHEET_FIELDS
                        if name == "tax_treatments"
                        else TAX_CALENDAR_SHEET_FIELDS
                        if name == "tax_calendar"
                        else None
                    ),
                )
            )
            for name, rows in tables.items()
        }
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
    with open_ledger_db(args.db) as db, db.transaction():
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

        applied: list[dict[str, Any]] = []
        for row in plan.pull_update:
            local_row = local_by_uuid[row.uuid]
            applied.append(_apply_reviewed_sheet_row(db, args.tab, local_row, row))
    _emit({"ok": True, "tab": args.tab, "applied": applied, "conflicts": []})
    return 0


def _cmd_books_build(args: argparse.Namespace) -> int:
    from .books import write_accounting_books

    with open_ledger_db(args.db, read_only=True) as db:
        result = write_accounting_books(db, args.out_dir, period_key=args.period)
    _emit(_jsonable(result))
    return 0


def _cmd_books_aeat_preview(args: argparse.Namespace) -> int:
    from .aeat_books import (
        AeatBookProjectionError,
        build_aeat_book_projection,
        failed_aeat_book_projection,
        write_aeat_book_projection,
    )

    try:
        with open_ledger_db(args.db, read_only=True) as db:
            projection = build_aeat_book_projection(
                db,
                period_key=args.period,
                taxpayer_tax_id=args.taxpayer_tax_id,
                allow_authoritative_history=args.allow_authoritative_history,
            )
    except AeatBookProjectionError as exc:
        projection = failed_aeat_book_projection(
            period_key=args.period,
            message=str(exc),
            allow_authoritative_history=args.allow_authoritative_history,
        )
    target = write_aeat_book_projection(args.out, projection)
    _emit(
        {
            "projection": str(target.resolve()),
            "period": args.period,
            "data_projection_ready": projection["data_projection_ready"],
            "xlsx_generation_supported": projection["xlsx_generation_supported"],
            "counts": projection["counts"],
        }
    )
    return 0 if projection["data_projection_ready"] else 2


def _cmd_books_aeat_template_check(args: argparse.Namespace) -> int:
    from .aeat_workbook import inspect_aeat_template

    check = inspect_aeat_template(args.template)
    _write_json(args.out, check)
    _emit(
        {
            "template_check": str(args.out.resolve()),
            "valid": check["valid"],
            "writer_strategy_status": check["writer_strategy"]["status"],
            "xlsx_generation_supported": check["writer_strategy"][
                "xlsx_generation_supported"
            ],
        }
    )
    return 0 if check["valid"] else 2


def _cmd_books_aeat_payload(args: argparse.Namespace) -> int:
    from .aeat_books import (
        AeatBookProjectionError,
        build_aeat_book_projection,
        failed_aeat_book_projection,
    )
    from .aeat_workbook import (
        build_aeat_workbook_payload,
        inspect_aeat_template,
        write_aeat_workbook_payload,
    )

    try:
        with open_ledger_db(args.db, read_only=True) as db:
            projection = build_aeat_book_projection(
                db,
                period_key=args.period,
                taxpayer_tax_id=args.taxpayer_tax_id,
                allow_authoritative_history=args.allow_authoritative_history,
            )
    except AeatBookProjectionError as exc:
        projection = failed_aeat_book_projection(
            period_key=args.period,
            message=str(exc),
            allow_authoritative_history=args.allow_authoritative_history,
        )
    template_check = inspect_aeat_template(args.template)
    payload = build_aeat_workbook_payload(
        projection,
        template_check=template_check,
    )
    target = write_aeat_workbook_payload(args.out, payload)
    _emit(
        {
            "payload": str(target.resolve()),
            "period": args.period,
            "payload_ready": payload["payload_ready"],
            "xlsx_generation_supported": payload["xlsx_generation_supported"],
            "xlsx_write_ready": payload["xlsx_write_ready"],
            "blocker_count": len(payload["blockers"]),
            "xlsx_generation_blocker_count": len(
                payload["xlsx_generation_blockers"]
            ),
            "writer_strategy_status": payload["writer_strategy_status"],
        }
    )
    return 0 if payload["xlsx_write_ready"] else 2


def _cmd_books_aeat_write(args: argparse.Namespace) -> int:
    from .aeat_books import (
        AeatBookProjectionError,
        build_aeat_book_projection,
        failed_aeat_book_projection,
    )
    from .aeat_workbook import (
        AeatWorkbookWriteError,
        build_aeat_workbook_payload,
        inspect_aeat_template,
        write_aeat_workbook_payload,
        write_aeat_workbook_xlsx,
    )

    try:
        with open_ledger_db(args.db, read_only=True) as db:
            projection = build_aeat_book_projection(
                db,
                period_key=args.period,
                taxpayer_tax_id=args.taxpayer_tax_id,
                allow_authoritative_history=args.allow_authoritative_history,
            )
    except AeatBookProjectionError as exc:
        projection = failed_aeat_book_projection(
            period_key=args.period,
            message=str(exc),
            allow_authoritative_history=args.allow_authoritative_history,
        )
    template_check = inspect_aeat_template(args.template)
    payload = build_aeat_workbook_payload(
        projection,
        template_check=template_check,
    )
    payload_target = None
    if args.out_payload is not None:
        payload_target = write_aeat_workbook_payload(args.out_payload, payload)
    if not payload["xlsx_write_ready"]:
        _emit(
            {
                "ok": False,
                "workbook_written": False,
                "period": args.period,
                "payload": (
                    str(payload_target.resolve()) if payload_target is not None else None
                ),
                "blockers": payload["blockers"],
                "xlsx_generation_blockers": payload["xlsx_generation_blockers"],
                "writer_strategy_status": payload["writer_strategy_status"],
            }
        )
        return 2
    try:
        target = write_aeat_workbook_xlsx(args.template, payload, args.out)
    except AeatWorkbookWriteError as exc:
        _emit(
            {
                "ok": False,
                "workbook_written": False,
                "period": args.period,
                "error": str(exc),
            }
        )
        return 2
    workbook_sha256 = _sha256(target)
    manifest_payload = {
        "schema_version": 1,
        "artifact_type": "aeat_unified_books_xlsx_manifest",
        "period": args.period,
        "source_template": {
            "name": args.template.name,
            "sha256": payload["template"]["actual_sha256"],
        },
        "payload": {
            "name": payload_target.name if payload_target is not None else None,
            "sha256": payload["payload_sha256"],
        },
        "workbook": {
            "name": target.name,
            "sha256": workbook_sha256,
            "size_bytes": target.stat().st_size,
        },
        "counts": projection["counts"],
        "writer_strategy_status": payload["writer_strategy_status"],
        "validator_status": "not_run",
    }
    manifest_target = None
    if args.out_manifest is not None:
        manifest_target = _write_json(args.out_manifest, manifest_payload)
    _emit(
        {
            "ok": True,
            "workbook_written": True,
            "workbook": str(target.resolve()),
            "workbook_sha256": workbook_sha256,
            "workbook_size_bytes": target.stat().st_size,
            "payload": str(payload_target.resolve()) if payload_target is not None else None,
            "payload_sha256": payload["payload_sha256"],
            "manifest": (
                str(manifest_target.resolve()) if manifest_target is not None else None
            ),
            "period": args.period,
            "counts": projection["counts"],
            "writer_strategy_status": payload["writer_strategy_status"],
            "validator_status": "not_run",
        }
    )
    return 0


def _cmd_books_aeat_validate(args: argparse.Namespace) -> int:
    from .aeat_workbook import (
        AeatValidationConsentError,
        validate_aeat_workbook,
        write_aeat_validation_receipt,
    )

    try:
        receipt = validate_aeat_workbook(
            args.xlsx,
            year=args.year,
            confirm_upload_to_aeat=args.confirm_upload_to_aeat,
        )
    except (AeatValidationConsentError, ValueError) as exc:
        _emit({"ok": False, "uploaded": False, "error": str(exc)})
        return 2
    target = write_aeat_validation_receipt(args.out_receipt, receipt)
    _emit(
        {
            "ok": receipt["transport"]["ok"],
            "uploaded": True,
            "receipt": str(target.resolve()),
            "http_status": receipt["transport"]["http_status"],
        }
    )
    return 0 if receipt["transport"]["ok"] else 2


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


def _cmd_filings_refresh_evidence(args: argparse.Namespace) -> int:
    from .history_migration import refresh_filing_inventory

    with open_ledger_db(args.db) as db:
        result = refresh_filing_inventory(db, args.document_inventory)
    _emit(result)
    return 0


def _cmd_filing_receipt(args: argparse.Namespace) -> int:
    from .filing_evidence import extract_filing_evidence
    from .filing_receipts import (
        build_filing_receipt_payload,
        load_calculation_file,
        verify_filing_receipt,
    )

    if not args.filed_pdf.is_file():
        raise FileNotFoundError(args.filed_pdf)
    if not args.calculation.is_file():
        raise FileNotFoundError(args.calculation)
    if args.archive_root is None and not args.dry_run:
        raise ValueError(
            "--archive-root is required (or set archive_root in the private config)"
        )

    calculation, calculation_sha256 = load_calculation_file(args.calculation)
    evidence = extract_filing_evidence(args.filed_pdf)
    verification = verify_filing_receipt(
        evidence,
        calculation,
        expected_form=args.form,
        expected_period=args.period,
        calculation_sha256=calculation_sha256,
        tolerance_eur=args.tolerance_eur,
    )
    if verification["status"] != "matched":
        _emit({"ok": False, "recorded": False, "verification": verification})
        return 2
    if args.dry_run:
        _emit({"ok": True, "recorded": False, "dry_run": True, "verification": verification})
        return 0

    payload = build_filing_receipt_payload(evidence, calculation, verification)
    with open_ledger_db(args.db) as db:
        existing = _existing_snapshot_for_source(db, evidence.source_sha256)
        if existing is not None:
            existing_payload = json.loads(existing["payload_json"])
            receipt_verification = existing_payload.get("receipt_verification") or {}
            if (
                existing["period_key"] == args.period
                and str(existing["form_code"] or existing_payload.get("form") or "") == args.form
                and receipt_verification.get("status") == "matched"
                and receipt_verification.get("calculation_sha256")
                == verification["calculation_sha256"]
            ):
                _emit(
                    {
                        "ok": True,
                        "recorded": True,
                        "idempotent": True,
                        "filing_snapshot_id": existing["filing_snapshot_id"],
                        "form": args.form,
                        "period": args.period,
                        "verification": verification,
                    }
                )
                return 0
            raise ValueError(
                "This filed PDF already exists in the ledger with different filing metadata "
                "or calculation lineage"
            )

        period = next(
            (row for row in db.list_periods() if row["period_key"] == args.period),
            None,
        )
        if period is None:
            raise ValueError(f"Unknown period: {args.period}")
        if period["status"] not in {"closed", "amended"}:
            raise ValueError(
                f"Filed receipts require a closed or amended period: {args.period}"
            )
        conflicting = _final_filing_for_form(db, args.period, args.form)
        if conflicting is not None:
            raise ValueError(
                f"Modelo {args.form} {args.period} already has a final filing snapshot "
                f"{conflicting['filing_snapshot_id']}"
            )

        archived_path = archive_evidence(
            args.filed_pdf,
            args.archive_root,
            period_key=args.period,
            evidence_kind="filed_return_pdf",
            digest=evidence.source_sha256,
        )
        row = db.create_filing_snapshot(
            args.period,
            status="filed",
            filed_on=evidence.filed_on,
            payload=payload,
            source_hash=evidence.source_sha256,
            form_code=args.form,
            submission_reference=evidence.submission_reference,
            justificante_number=evidence.justificante_number,
            verification_code=evidence.verification_code,
            source_reference=str(archived_path),
        )

    _emit(
        {
            "ok": True,
            "recorded": True,
            "idempotent": False,
            "filing_snapshot_id": row["filing_snapshot_id"],
            "form": args.form,
            "period": args.period,
            "source_sha256": evidence.source_sha256,
            "archived_file": (
                str(archived_path) if args.show_paths else archived_path.name
            ),
            "verification": verification,
        }
    )
    return 0


def _cmd_tax_procedure_receipt(args: argparse.Namespace) -> int:
    from .filing_procedures import (
        build_tax_procedure_payload,
        tax_procedure_identity,
    )
    from .filing_receipts import sha256_file

    if not args.evidence.is_file():
        raise FileNotFoundError(args.evidence)
    if args.archive_root is None and not args.dry_run:
        raise ValueError(
            "--archive-root is required (or set archive_root in the private config)"
        )

    source_sha256 = sha256_file(args.evidence)
    with open_ledger_db(args.db) as db:
        payload, snapshot_status = build_tax_procedure_payload(
            db,
            period_key=args.period,
            form_code=args.form,
            procedure=args.procedure,
            stage=args.stage,
            occurred_on=args.occurred_on,
            procedure_reference=args.reference,
            amount_eur=args.amount_eur,
            notes=args.notes,
        )
        if args.dry_run:
            _emit(
                {
                    "ok": True,
                    "recorded": False,
                    "dry_run": True,
                    "period": args.period,
                    "form": args.form,
                    "snapshot_status": snapshot_status,
                    "source_sha256": source_sha256,
                    "payload": payload,
                }
            )
            return 0

        existing = _existing_snapshot_for_source(db, source_sha256)
        if existing is not None:
            existing_payload = json.loads(existing["payload_json"])
            procedure_reference = str(payload["procedure_reference"])
            if (
                existing["period_key"] == args.period
                and str(existing["form_code"] or existing_payload.get("form") or "")
                == args.form
                and existing["status"] == snapshot_status
                and existing["submission_reference"] == procedure_reference
                and tax_procedure_identity(existing_payload)
                == tax_procedure_identity(payload)
            ):
                _emit(
                    {
                        "ok": True,
                        "recorded": True,
                        "idempotent": True,
                        "filing_snapshot_id": existing["filing_snapshot_id"],
                        "period": args.period,
                        "form": args.form,
                        "snapshot_status": snapshot_status,
                        "source_sha256": source_sha256,
                    }
                )
                return 0
            raise ValueError(
                "This tax-procedure evidence already exists with different metadata"
            )

        archived_path = archive_evidence(
            args.evidence,
            args.archive_root,
            period_key=args.period,
            evidence_kind="tax_procedure_receipt",
            digest=source_sha256,
        )
        row = db.create_filing_snapshot(
            args.period,
            status=snapshot_status,
            filed_on=args.occurred_on,
            payload=payload,
            source_hash=source_sha256,
            form_code=args.form,
            submission_reference=str(payload["procedure_reference"]),
            source_reference=str(archived_path),
        )

    _emit(
        {
            "ok": True,
            "recorded": True,
            "idempotent": False,
            "filing_snapshot_id": row["filing_snapshot_id"],
            "period": args.period,
            "form": args.form,
            "snapshot_status": snapshot_status,
            "source_sha256": source_sha256,
            "archived_file": (
                str(archived_path) if args.show_paths else archived_path.name
            ),
        }
    )
    return 0


def _existing_snapshot_for_source(db: LedgerDB, source_sha256: str) -> dict[str, Any] | None:
    row = db.connection.execute(
        """
        SELECT fs.*, p.period_key
        FROM filing_snapshots fs
        JOIN periods p ON p.period_id = fs.period_id
        WHERE UPPER(fs.source_hash) = ?
        ORDER BY fs.created_at DESC
        LIMIT 1
        """,
        (source_sha256.upper(),),
    ).fetchone()
    return dict(row) if row is not None else None


def _final_filing_for_form(
    db: LedgerDB,
    period_key: str,
    form: str,
) -> dict[str, Any] | None:
    rows = db.connection.execute(
        """
        SELECT fs.*
        FROM filing_snapshots fs
        JOIN periods p ON p.period_id = fs.period_id
        WHERE p.period_key = ?
          AND fs.status IN ('filed', 'submitted', 'final')
        ORDER BY fs.created_at DESC
        """,
        (period_key,),
    ).fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"])
        row_form = str(row["form_code"] or payload.get("form") or "")
        if row_form.lower().replace("modelo", "").strip() == form:
            return dict(row)
    return None


def _cmd_calculate(args: argparse.Namespace) -> int:
    if args.form in QUARTERLY_FORM_CODES and args.quarter is None:
        raise ValueError(f"Modelo {args.form} requires --quarter")
    if args.difficult_expenses_policy == "source_book_total" and args.mode != "verify_history":
        raise CalculationBlocked("source_book_total is restricted to verify_history")
    if args.difficult_expenses_policy == "exclude_by_documented_decision" and not args.decision_ref:
        raise CalculationBlocked("A documented decision reference is required to exclude difficult expenses")
    with open_ledger_db(args.db, read_only=True) as db:
        annual_readiness = None
        if args.mode == "production" and args.form in ANNUAL_FORM_CODES:
            from .annual_readiness import (
                annual_readiness_error,
                assess_annual_readiness,
            )

            annual_readiness = assess_annual_readiness(
                db,
                year=args.year,
                form_code=args.form,
                allow_authoritative_history=args.allow_authoritative_history,
            )
            if not annual_readiness["ready"]:
                raise CalculationBlocked(
                    f"Modelo {args.form} annual production calculation is not ready: "
                    f"{annual_readiness_error(annual_readiness)}. "
                    "Run period annual-status for the complete report."
                )
            if args.form == "390":
                categories = annual_readiness["form_specific_gates"]["390"]["categories"]
                settlement_gate = next(
                    row
                    for row in categories
                    if row["category"] == "final_refund_or_compensation"
                )
                evidenced_choice = settlement_gate.get("choice") or "compensate"
                if (
                    args.final_vat_settlement is not None
                    and args.final_vat_settlement != evidenced_choice
                ):
                    raise CalculationBlocked(
                        "Modelo 390 final settlement choice conflicts with filed Q4 Modelo 303 "
                        f"evidence: requested {args.final_vat_settlement}, "
                        f"evidenced {evidenced_choice}"
                    )
                args.final_vat_settlement = evidenced_choice
        modelo303_periods: tuple[str, ...] = ()
        modelo303_opening_compensation = Decimal("0.00")
        periods = [f"{args.year}-Q{args.quarter}"] if args.quarter else []
        if args.form == "390":
            modelo303_periods = _modelo303_inventory_periods(db, args.year)
            periods = list(modelo303_periods)
            first_quarter = int(modelo303_periods[0][-1])
            modelo303_opening_compensation = _previous_filed_vat_compensation(
                db,
                args.year,
                first_quarter,
            )
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
                    if (
                        is_current
                        and row["determination"] == "unknown"
                    )
                    or (
                        not is_current
                        and row["obligation_code"] == args.form
                    )
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
                prior_filed_baseline = (
                    not is_current
                    and args.allow_authoritative_history
                    and _filed_baseline(db, period_key, args.form) is not None
                )
                if (
                    not is_current
                    and period_rows[period_key]["status"] not in {"closed", "amended"}
                    and not prior_filed_baseline
                ):
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
        if args.form == "303" and args.previous_vat_compensation is None:
            args.previous_vat_compensation = str(
                _previous_filed_vat_compensation(db, args.year, args.quarter)
            )
        baseline_period = f"{args.year}-Q{args.quarter}" if args.quarter else str(args.year)
        baseline = _filed_baseline(db, baseline_period, args.form)
        if args.form == "130" and args.reduction is None:
            if args.mode == "verify_history" and baseline is not None:
                args.reduction = str(baseline["filed_values"].get("13", "0"))
            else:
                args.reduction = "0"
        result = _calculate(
            args,
            rows,
            modelo303_periods=modelo303_periods,
            modelo303_opening_compensation=modelo303_opening_compensation,
        )
        payload = _calculation_payload(result)
        baseline = _filed_baseline(db, result.period, args.form)
        if baseline is not None:
            payload["filed_baseline"] = baseline
            if baseline["filed_values"]:
                payload["diff_from_filed"] = _calculation_diff(
                    payload["values"],
                    baseline["filed_values"],
                )
                payload["filed_comparison_status"] = "casillas_compared"
            else:
                payload["filed_comparison_status"] = "filed_values_unavailable"
        if annual_readiness is not None:
            payload["annual_readiness"] = annual_readiness
            payload["annual_calculation_ready"] = True
    payload["calculation_mode"] = args.mode
    payload["authoritative_history_mode"] = bool(args.allow_authoritative_history)
    payload["difficult_expenses_policy"] = args.difficult_expenses_policy
    if args.decision_ref:
        payload["decision_ref"] = args.decision_ref
    _write_or_emit(payload, args.out)
    return 0


def _cmd_period_annual_status(args: argparse.Namespace) -> int:
    from .annual_readiness import assess_annual_readiness

    with open_ledger_db(args.db, read_only=True) as db:
        report = assess_annual_readiness(
            db,
            year=args.year,
            form_code=args.form,
            allow_authoritative_history=args.allow_authoritative_history,
        )
    _write_or_emit(report, args.out)
    return 0 if report["ready"] else 2


def _cmd_offboarding_build(args: argparse.Namespace) -> int:
    from .offboarding import build_offboarding_manifest_document

    with open_ledger_db(args.db, read_only=True) as db:
        manifest = build_offboarding_manifest_document(
            args.paths,
            generated_on=args.generated_on,
            database=db,
            excluded_paths=[args.out, args.db],
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _emit(
        {
            "manifest": str(args.out.resolve()),
            "database": str(args.db.resolve()),
            "schema_version": manifest["schema_version"],
            "generated_on": manifest["generated_on"],
            "rows": len(manifest["rows"]),
            "expected_filings": len(manifest["expected_filings"]),
        }
    )
    return 0


def _cmd_offboarding_verify(args: argparse.Namespace) -> int:
    from .offboarding import verify_offboarding_manifest

    result = verify_offboarding_manifest(
        json.loads(args.manifest.read_text(encoding="utf-8")),
        required_generated_on=args.required_generated_on,
    )
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
    modelo303_opening_compensation: Decimal = Decimal("0.00"),
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
        return calculate_modelo303_rows(
            rows,
            year=args.year,
            quarter=args.quarter,
            previous_compensation=Decimal(args.previous_vat_compensation),
            final_settlement=args.final_vat_settlement or "compensate",
        )
    if args.form == "349":
        return calculate_modelo349_rows(rows, year=args.year, quarter=args.quarter)
    if args.form == "390":
        quarterly_reports: list[CalculationResult] = []
        compensation = modelo303_opening_compensation
        for period in modelo303_periods:
            report = calculate_modelo303_rows(
                rows,
                year=args.year,
                quarter=int(period[-1]),
                previous_compensation=compensation,
                final_settlement=(
                    (args.final_vat_settlement or "compensate")
                    if int(period[-1]) == 4
                    else "compensate"
                ),
            )
            quarterly_reports.append(report)
            compensation = Decimal(str(report.values["compensation_carryforward"]))
        return calculate_modelo390(
            quarterly_reports,
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


def _period_prepare_calculations(
    *,
    dashboard: dict[str, Any],
    rows: list[TaxRow],
    obligations: list[dict[str, Any]],
    year: int,
    quarter: int,
) -> dict[str, dict[str, Any]]:
    due_forms = {
        str(row["obligation_code"])
        for row in obligations
        if row["determination"] == "due"
        and row["filing_status"] not in {"filed", "waived"}
    }
    preview = dashboard["tax_arithmetic_preview"]["projected_reviewed"]
    calculations: dict[str, dict[str, Any]] = {}
    for form in sorted(due_forms):
        preview_result = preview.get(f"modelo{form}")
        if preview_result is not None:
            calculations[form] = {
                "blocked": False,
                "form": form,
                "period": f"{year}-Q{quarter}",
                **dict(preview_result),
            }
            continue
        withholding_type = {
            "111": "professional",
            "115": "rent",
            "216": "nonresident",
        }.get(form)
        if withholding_type is not None:
            result = calculate_retention_rows(
                rows,
                year=year,
                quarter=quarter,
                withholding_type=withholding_type,
            )
            calculations[form] = {
                "blocked": False,
                **_calculation_payload(result),
            }
            continue
        if form == "349":
            result = calculate_modelo349_rows(rows, year=year, quarter=quarter)
            calculations[form] = {
                "blocked": False,
                **_calculation_payload(result),
            }
            continue
        calculations[form] = {
            "blocked": True,
            "form": form,
            "period": f"{year}-Q{quarter}",
            "reason": f"Quarter preparation does not yet map Modelo {form}",
        }
    return calculations


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
    include_approved_periods: Iterable[str] = (),
) -> list[TaxRow]:
    return load_tax_rows(
        db,
        year,
        mode=mode,
        allow_authoritative_history=allow_authoritative_history,
        include_approved_periods=include_approved_periods,
    )


def _parse_quarter_period(period_key: str) -> tuple[int, int]:
    try:
        year_text, quarter_text = period_key.split("-Q", 1)
        year = int(year_text)
        quarter = int(quarter_text)
    except (ValueError, TypeError) as exc:
        raise ValueError("Period must use YYYY-QN format") from exc
    if quarter not in {1, 2, 3, 4} or period_key != f"{year}-Q{quarter}":
        raise ValueError("Period must use YYYY-QN format")
    return year, quarter


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
        for key in ("tax_code", "deductible_irpf_minor", "deductible_vat_minor", "asset_id"):
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
        key=lambda row: (row["transaction_date"], row["description"], row["transaction_id"]),
    )


def _sheet_rows_from_db(db: LedgerDB, tab: str) -> list[SheetRow]:
    query = {
        "inbox_review": """SELECT d.document_id AS uuid, d.row_version,
                            d.lifecycle_status AS status, p.period_key, d.document_type,
                            d.document_number, d.issued_on, c.display_name AS counterparty,
                            d.currency, d.total_minor, d.source_path, d.drive_file_id, d.mime_type
                         FROM documents d
                         LEFT JOIN periods p ON p.period_id=d.period_id
                         LEFT JOIN counterparties c ON c.counterparty_id=d.counterparty_id""",
        "counterparties": """SELECT counterparty_id AS uuid, row_version, 'open' AS status,
                             display_name, tax_id, country_code, vat_id, roi_status,
                             legal_form, professional_supplier, retention_expected, email, phone
                          FROM counterparties""",
        "transactions": """SELECT t.transaction_id AS uuid, t.row_version,
                            CASE WHEN p.status IN ('closed', 'amended') THEN 'closed'
                                 ELSE 'open' END AS status,
                            t.lifecycle_status, t.description, t.amount_eur_minor
                         FROM transactions t JOIN periods p ON p.period_id=t.period_id""",
        "tax_treatments": """SELECT tt.treatment_id AS uuid, tt.row_version,
                            CASE WHEN p.status = 'open'
                                      AND tt.treatment_type = 'invoice_review'
                                      AND t.lifecycle_status IN (
                                          'received', 'extracted', 'needs_review', 'approved'
                                      ) THEN 'open'
                                 ELSE 'closed' END AS status,
                            p.period_key, t.transaction_id,
                            t.lifecycle_status AS transaction_lifecycle,
                            t.transaction_date, t.entry_type, t.description,
                            COALESCE(c.display_name, '') AS counterparty,
                            COALESCE(d.document_number, '') AS document_number,
                            printf('%.2f', t.amount_original_minor / 100.0) AS gross_original,
                            t.original_currency,
                            CASE WHEN t.amount_eur_minor IS NULL THEN ''
                                 ELSE printf('%.2f', t.amount_eur_minor / 100.0) END AS amount_eur,
                            tt.treatment_type, tt.jurisdiction, tt.tax_code,
                            COALESCE(tt.aeat_invoice_type, '') AS aeat_invoice_type,
                            COALESCE(tt.aeat_operation_key, '') AS aeat_operation_key,
                            COALESCE(tt.aeat_operation_qualification, '')
                                AS aeat_operation_qualification,
                            COALESCE(tt.aeat_exemption_code, '') AS aeat_exemption_code,
                            CASE WHEN tt.aeat_reverse_charge IS NULL THEN ''
                                 ELSE tt.aeat_reverse_charge END AS aeat_reverse_charge,
                            COALESCE(tt.aeat_expense_concept, '') AS aeat_expense_concept,
                            tt.rate_basis_points, tt.deductible_ratio,
                            CASE WHEN tt.taxable_base_minor IS NULL THEN ''
                                 ELSE printf('%.2f', tt.taxable_base_minor / 100.0) END AS taxable_base_eur,
                            CASE WHEN tt.vat_minor IS NULL THEN ''
                                 ELSE printf('%.2f', tt.vat_minor / 100.0) END AS vat_eur,
                            CASE WHEN tt.deductible_irpf_minor IS NULL THEN ''
                                 ELSE printf('%.2f', tt.deductible_irpf_minor / 100.0) END AS deductible_irpf_eur,
                            CASE WHEN tt.deductible_vat_minor IS NULL THEN ''
                                 ELSE printf('%.2f', tt.deductible_vat_minor / 100.0) END AS deductible_vat_eur,
                            CASE WHEN tt.withholding_minor IS NULL THEN ''
                                 ELSE printf('%.2f', tt.withholding_minor / 100.0) END AS withholding_eur,
                            tt.include_modelo130, tt.include_modelo303,
                            tt.include_modelo347, COALESCE(tt.rule_version_id, '') AS rule_version_id,
                            COALESCE(tt.notes, '') AS notes
                         FROM tax_treatments tt
                         JOIN transactions t ON t.transaction_id=tt.transaction_id
                         JOIN periods p ON p.period_id=t.period_id
                         LEFT JOIN counterparties c ON c.counterparty_id=t.counterparty_id
                         LEFT JOIN documents d ON d.document_id=t.document_id
                         WHERE tt.treatment_type = 'invoice_review'""",
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
        "inbox_review": set(),
        "counterparties": {
            "display_name",
            "tax_id",
            "country_code",
            "vat_id",
            "roi_status",
            "legal_form",
            "professional_supplier",
            "retention_expected",
            "email",
            "phone",
        },
        "transactions": {"lifecycle_status"},
        "tax_treatments": {
            "tax_code",
            "aeat_invoice_type",
            "aeat_operation_key",
            "aeat_operation_qualification",
            "aeat_exemption_code",
            "aeat_reverse_charge",
            "aeat_expense_concept",
            "rate_basis_points",
            "deductible_ratio",
            "taxable_base_eur",
            "vat_eur",
            "deductible_irpf_eur",
            "deductible_vat_eur",
            "withholding_eur",
            "include_modelo130",
            "include_modelo303",
            "include_modelo347",
            "rule_version_id",
            "notes",
        },
        "issues": {"resolution_reason", "waiver_reason"},
        "assets": {"advisor_decision", "advisor_decision_on"},
    }
    if not editable_only:
        fields = {
            "inbox_review": {
                "period_key",
                "document_type",
                "document_number",
                "issued_on",
                "counterparty",
                "currency",
                "total_minor",
                "source_path",
                "drive_file_id",
                "mime_type",
            },
            "counterparties": fields["counterparties"],
            "transactions": fields["transactions"] | {"description", "amount_eur_minor"},
            "tax_treatments": fields["tax_treatments"]
            | {
                "period_key",
                "transaction_id",
                "transaction_lifecycle",
                "transaction_date",
                "entry_type",
                "description",
                "counterparty",
                "document_number",
                "gross_original",
                "original_currency",
                "amount_eur",
                "treatment_type",
                "jurisdiction",
            },
            "issues": fields["issues"] | {"issue_code", "message", "blocking"},
            "assets": fields["assets"] | {"asset_code"},
        }
    selected_fields = fields[tab]
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    normalized: list[SheetRow] = []
    for raw in rows:
        row = dict(raw)
        values = {key: value for key, value in row.items() if key in selected_fields}
        if tab == "counterparties":
            values = _normalize_counterparty_sheet_values(values)
        normalized.append(
            SheetRow(
                uuid=str(row.pop("uuid")),
                row_version=int(row.pop("row_version")),
                status=str(row.pop("status")),
                values=values,
            )
        )
    return normalized


def _editable_sheet_values(tab: str, values: dict[str, Any]) -> dict[str, Any]:
    editable_fields = {
        "inbox_review": set(),
        "counterparties": {
            "display_name",
            "tax_id",
            "country_code",
            "vat_id",
            "roi_status",
            "legal_form",
            "professional_supplier",
            "retention_expected",
            "email",
            "phone",
        },
        "transactions": {"lifecycle_status"},
        "tax_treatments": {
            "tax_code",
            "aeat_invoice_type",
            "aeat_operation_key",
            "aeat_operation_qualification",
            "aeat_exemption_code",
            "aeat_reverse_charge",
            "aeat_expense_concept",
            "rate_basis_points",
            "deductible_ratio",
            "taxable_base_eur",
            "vat_eur",
            "deductible_irpf_eur",
            "deductible_vat_eur",
            "withholding_eur",
            "include_modelo130",
            "include_modelo303",
            "include_modelo347",
            "rule_version_id",
            "notes",
        },
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
    if tab == "inbox_review":
        return db.transition_document(
            remote.uuid,
            lifecycle_status=remote.status,
            expected_row_version=local.row_version,
        )
    if tab == "counterparties":
        return db.update_counterparty_review(
            remote.uuid,
            display_name=str(remote.values.get("display_name", "")),
            tax_id=_optional_sheet_text(remote.values.get("tax_id")),
            country_code=str(remote.values.get("country_code", "")),
            vat_id=_optional_sheet_text(remote.values.get("vat_id")),
            roi_status=str(remote.values.get("roi_status", "unknown")) or "unknown",
            legal_form=(
                str(remote.values["legal_form"]) or "unknown"
                if "legal_form" in remote.values
                else None
            ),
            professional_supplier=_optional_sheet_bool(
                remote.values.get("professional_supplier")
            ),
            retention_expected=_optional_sheet_bool(remote.values.get("retention_expected")),
            email=_optional_sheet_text(remote.values.get("email")),
            phone=_optional_sheet_text(remote.values.get("phone")),
            expected_row_version=local.row_version,
        )
    if tab == "transactions":
        target = str(remote.values.get("lifecycle_status", "")).strip()
        if not target:
            raise ValueError(f"Reviewed transaction {remote.uuid} is missing lifecycle_status")
        return db.transition_transaction(
            remote.uuid,
            lifecycle_status=target,
            expected_row_version=local.row_version,
        )
    if tab == "tax_treatments":
        existing = db.connection.execute(
            "SELECT * FROM tax_treatments WHERE treatment_id = ?",
            (remote.uuid,),
        ).fetchone()
        if existing is None:
            raise KeyError(f"Unknown tax treatment: {remote.uuid}")
        tax_code = str(remote.values.get("tax_code", "")).strip()
        if not tax_code:
            raise ValueError(f"Reviewed tax treatment {remote.uuid} is missing tax_code")
        return db.add_detailed_tax_treatment(
            transaction_id=existing["transaction_id"],
            treatment_type=existing["treatment_type"],
            tax_code=tax_code,
            aeat_invoice_type=_optional_sheet_text(
                remote.values.get("aeat_invoice_type", existing["aeat_invoice_type"])
            ),
            aeat_operation_key=_optional_sheet_text(
                remote.values.get("aeat_operation_key", existing["aeat_operation_key"])
            ),
            aeat_operation_qualification=_optional_sheet_text(
                remote.values.get(
                    "aeat_operation_qualification",
                    existing["aeat_operation_qualification"],
                )
            ),
            aeat_exemption_code=_optional_sheet_text(
                remote.values.get("aeat_exemption_code", existing["aeat_exemption_code"])
            ),
            aeat_reverse_charge=_optional_sheet_bool(
                remote.values.get("aeat_reverse_charge", existing["aeat_reverse_charge"])
            ),
            aeat_expense_concept=_optional_sheet_text(
                remote.values.get("aeat_expense_concept", existing["aeat_expense_concept"])
            ),
            jurisdiction=existing["jurisdiction"],
            rate_basis_points=_optional_sheet_int(
                remote.values.get("rate_basis_points")
            ),
            deductible_ratio=_optional_sheet_float(
                remote.values.get("deductible_ratio")
            ),
            taxable_base_minor=_sheet_eur_minor(
                remote.values.get("taxable_base_eur")
            ),
            vat_minor=_sheet_eur_minor(remote.values.get("vat_eur")),
            deductible_irpf_minor=_sheet_eur_minor(
                remote.values.get("deductible_irpf_eur")
            ),
            deductible_vat_minor=_sheet_eur_minor(
                remote.values.get("deductible_vat_eur")
            ),
            withholding_minor=_sheet_eur_minor(
                remote.values.get("withholding_eur")
            ),
            include_modelo130=bool(
                _optional_sheet_bool(remote.values.get("include_modelo130"))
            ),
            include_modelo303=bool(
                _optional_sheet_bool(remote.values.get("include_modelo303"))
            ),
            include_modelo347=bool(
                _optional_sheet_bool(remote.values.get("include_modelo347"))
            ),
            rule_version_id=_optional_sheet_text(
                remote.values.get("rule_version_id")
            ),
            notes=_optional_sheet_text(remote.values.get("notes")),
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


def _optional_sheet_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _optional_sheet_int(value: Any) -> int | None:
    normalized = str(value or "").strip()
    return int(normalized) if normalized else None


def _optional_sheet_float(value: Any) -> float | None:
    normalized = str(value or "").strip()
    return float(normalized) if normalized else None


def _sheet_eur_minor(value: Any) -> int | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return int(cents(Decimal(normalized)) * 100)


def _optional_sheet_bool(value: Any) -> bool | None:
    normalized = "" if value is None else str(value).strip().casefold()
    if not normalized:
        return None
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"Unsupported optional boolean value: {value}")


def _normalize_counterparty_sheet_values(values: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        key: str(value or "").strip()
        for key, value in values.items()
    }
    if "country_code" in normalized:
        normalized["country_code"] = normalized["country_code"].upper()
    if "roi_status" in normalized:
        normalized["roi_status"] = normalized["roi_status"].casefold()
    if "legal_form" in normalized:
        normalized["legal_form"] = normalized["legal_form"].casefold()
    for key in ("professional_supplier", "retention_expected"):
        if key not in normalized:
            continue
        parsed = _optional_sheet_bool(normalized[key])
        normalized[key] = "" if parsed is None else str(int(parsed))
    return normalized


def _write_rows_csv(
    path: Path,
    rows: Iterable[sqlite3.Row],
    *,
    fieldnames: list[str] | None = None,
) -> Path:
    materialized = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = fieldnames or (
        list(materialized[0].keys()) if materialized else ["uuid", "row_version", "status"]
    )
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(dict(row) for row in materialized)
    return path


def _document_suggestion(path: Path, kind: str, text: str) -> LedgerEntry | None:
    if kind == "income_invoice":
        return parse_income_invoice(path, text)
    if kind == "expense_invoice":
        extraction_error = None if text.strip() else "No readable text extracted"
        suggestion = parse_expense(path, text, extraction_error=extraction_error)
        if suggestion.date is None and path.suffix.casefold() in {".csv", ".txt", ".tsv"}:
            return LedgerEntry(
                kind="expense",
                date=parse_any_date(text),
                document=str(path),
                counterparty="Unknown",
                description=path.name,
                amount_original=None,
                currency="",
                amount_eur=None,
                deductible_eur=None,
                category="unknown",
                confidence="low",
                review_required=True,
                notes="Plain-text expense candidate; no supported supplier parser matched",
            )
        return suggestion
    return None


def _intake_transaction_draft(
    db: LedgerDB,
    *,
    document: dict[str, Any],
    document_kind: str,
    period_key: str,
    issued_on: str,
    document_number: str | None,
    counterparty_id: str | None,
    suggestion: LedgerEntry | None,
    amounts: InvoiceAmounts,
    requirements: tuple[str, ...],
    source_hash: str,
    structural_review: bool,
    document_only: bool,
    create_missing_issue: bool,
) -> dict[str, Any] | None:
    if document_only or document_kind not in {"income_invoice", "expense_invoice"}:
        return None

    external_key = f"intake-document:{document['document_id']}"
    existing = db.connection.execute(
        """
        SELECT t.*, p.period_key
        FROM transactions t
        JOIN periods p ON p.period_id = t.period_id
        WHERE t.external_key = ?
        """,
        (external_key,),
    ).fetchone()
    if existing is not None:
        expected = {
            "period_key": period_key,
            "transaction_date": issued_on,
            "entry_type": "income" if document_kind == "income_invoice" else "expense",
            "document_id": document["document_id"],
        }
        changed = [key for key, value in expected.items() if existing[key] != value]
        if amounts.gross is not None and existing["amount_minor"] != int(amounts.gross * 100):
            changed.append("amount_minor")
        if amounts.currency and existing["currency"] != amounts.currency:
            changed.append("currency")
        if changed:
            raise LedgerDbError(
                "Existing intake transaction conflicts with the same source document: "
                + ", ".join(sorted(set(changed)))
            )
        treatment = db.connection.execute(
            """
            SELECT * FROM tax_treatments
            WHERE transaction_id = ? AND treatment_type = 'invoice_review'
              AND jurisdiction = 'ES'
            """,
            (existing["transaction_id"],),
        ).fetchone()
        if treatment is not None and treatment["tax_code"] == "unknown":
            treatment_changes: list[str] = []
            backfill_base = treatment["taxable_base_minor"]
            backfill_vat = treatment["vat_minor"]
            if amounts.taxable_base is not None and treatment["taxable_base_minor"] not in {
                None,
                int(amounts.taxable_base * 100),
            }:
                treatment_changes.append("taxable_base_minor")
            elif amounts.taxable_base is not None and treatment["taxable_base_minor"] is None:
                backfill_base = int(amounts.taxable_base * 100)
            if amounts.vat is not None and treatment["vat_minor"] not in {
                None,
                int(amounts.vat * 100),
            }:
                treatment_changes.append("vat_minor")
            elif amounts.vat is not None and treatment["vat_minor"] is None:
                backfill_vat = int(amounts.vat * 100)
            if treatment_changes:
                raise LedgerDbError(
                    "Existing invoice review treatment conflicts with corrected re-ingest: "
                    + ", ".join(treatment_changes)
                )
            if (
                backfill_base != treatment["taxable_base_minor"]
                or backfill_vat != treatment["vat_minor"]
            ):
                treatment = db.add_detailed_tax_treatment(
                    transaction_id=existing["transaction_id"],
                    treatment_type=treatment["treatment_type"],
                    tax_code=treatment["tax_code"],
                    jurisdiction=treatment["jurisdiction"],
                    rate_basis_points=treatment["rate_basis_points"],
                    deductible_ratio=treatment["deductible_ratio"],
                    taxable_base_minor=backfill_base,
                    vat_minor=backfill_vat,
                    deductible_irpf_minor=treatment["deductible_irpf_minor"],
                    deductible_vat_minor=treatment["deductible_vat_minor"],
                    withholding_minor=treatment["withholding_minor"],
                    include_modelo130=bool(treatment["include_modelo130"]),
                    include_modelo303=bool(treatment["include_modelo303"]),
                    include_modelo347=bool(treatment["include_modelo347"]),
                    rule_version_id=treatment["rule_version_id"],
                    notes=(treatment["notes"] or "")
                    + "; candidate amounts backfilled by corrected re-ingest",
                    expected_row_version=treatment["row_version"],
                )
                amount_summary = amounts.as_dict()
                db.add_validation_issue(
                    period_key=period_key,
                    issue_code="transaction_tax_review",
                    severity="warning",
                    message=(
                        "Review required after corrected re-ingest; "
                        f"gross={amount_summary['gross']} {amounts.currency}; "
                        f"taxable_base={amount_summary['taxable_base']}; "
                        f"vat={amount_summary['vat']}; decisions="
                        + ", ".join(requirements)
                    ),
                    subject_table="transactions",
                    subject_id=existing["transaction_id"],
                    blocking=True,
                    source_hash=source_hash,
                )
        return _intake_transaction_summary(
            dict(existing),
            created=False,
            treatment=dict(treatment) if treatment is not None else None,
        )

    if document["lifecycle_status"] in {
        "approved",
        "posted",
        "included_in_snapshot",
        "duplicate",
        "rejected",
        "void",
    }:
        return {
            "created": False,
            "reason": f"document_lifecycle:{document['lifecycle_status']}",
        }

    if not amounts.draft_ready:
        if create_missing_issue:
            db.add_validation_issue(
                period_key=period_key,
                issue_code="transaction_draft_missing_amount",
                severity="error",
                message="; ".join(amounts.errors) or "Invoice amount is incomplete",
                subject_table="documents",
                subject_id=document["document_id"],
                blocking=True,
                source_hash=source_hash,
            )
        return {"created": False, "reason": "invoice_amounts_incomplete"}

    assert amounts.gross is not None
    entry_type = "income" if document_kind == "income_invoice" else "expense"
    effective_counterparty_id = document.get("counterparty_id") or counterparty_id
    description = (
        document_number
        or (suggestion.description if suggestion is not None else None)
        or Path(document.get("source_path") or "invoice").name
    )
    transaction = db.add_transaction(
        external_key=external_key,
        period_key=period_key,
        transaction_date=issued_on,
        booking_date=issued_on,
        entry_type=entry_type,
        description=description,
        amount_minor=int(amounts.gross * 100),
        currency=amounts.currency,
        amount_original_minor=int(amounts.gross * 100),
        original_currency=amounts.currency,
        amount_eur_minor=(
            int(amounts.gross * 100) if amounts.currency == "EUR" else None
        ),
        direction="credit" if entry_type == "income" else "debit",
        lifecycle_status="received",
        document_id=document["document_id"],
        counterparty_id=effective_counterparty_id,
        source_hash=hashlib.sha256(
            f"intake-transaction:{source_hash}".encode("utf-8")
        ).hexdigest(),
    )
    transaction = db.transition_transaction(
        transaction["transaction_id"],
        lifecycle_status="extracted",
        expected_row_version=transaction["row_version"],
    )
    needs_review = bool(
        structural_review
        or amounts.errors
        or suggestion is None
        or suggestion.review_required
        or effective_counterparty_id is None
    )
    if needs_review:
        transaction = db.transition_transaction(
            transaction["transaction_id"],
            lifecycle_status="needs_review",
            expected_row_version=transaction["row_version"],
        )
    treatment = db.add_detailed_tax_treatment(
        transaction_id=transaction["transaction_id"],
        treatment_type="invoice_review",
        tax_code="unknown",
        jurisdiction="ES",
        taxable_base_minor=(
            int(amounts.taxable_base * 100)
            if amounts.taxable_base is not None
            else None
        ),
        vat_minor=int(amounts.vat * 100) if amounts.vat is not None else None,
        notes=(
            "Extraction candidate only; gross_source="
            f"{amounts.gross_source or 'unknown'}; base_source="
            f"{amounts.taxable_base_source or 'unknown'}; vat_source="
            f"{amounts.vat_source or 'unknown'}"
        ),
        source_hash=hashlib.sha256(
            f"intake-treatment:{source_hash}".encode("utf-8")
        ).hexdigest(),
    )
    amount_summary = amounts.as_dict()
    db.add_validation_issue(
        period_key=period_key,
        issue_code="transaction_tax_review",
        severity="warning",
        message=(
            "Review required before approval; "
            f"gross={amount_summary['gross']} {amounts.currency}; "
            f"taxable_base={amount_summary['taxable_base']}; "
            f"vat={amount_summary['vat']}; decisions="
            + ", ".join(requirements)
        ),
        subject_table="transactions",
        subject_id=transaction["transaction_id"],
        blocking=True,
        source_hash=source_hash,
    )
    _resolve_open_intake_issue(
        db,
        issue_code="transaction_draft_missing_amount",
        subject_table="documents",
        subject_id=document["document_id"],
        reason="Reviewed invoice amounts supplied during corrected re-ingest.",
    )
    return _intake_transaction_summary(
        transaction,
        created=True,
        treatment=treatment,
    )


def _backfill_existing_intake_document(
    db: LedgerDB,
    document: sqlite3.Row,
    *,
    import_batch_id: str,
    counterparty_id: str | None,
    document_kind: str,
    period_key: str,
    issued_on: str,
    document_number: str | None,
    amounts: InvoiceAmounts,
) -> dict[str, Any]:
    expected = {
        "document_type": document_kind,
        "period_key": period_key,
        "issued_on": issued_on,
    }
    changed = [key for key, value in expected.items() if document[key] != value]
    if document_number and document["document_number"] not in {None, "", document_number}:
        changed.append("document_number")
    if amounts.gross is not None and document["total_minor"] not in {
        None,
        int(amounts.gross * 100),
    }:
        changed.append("total_minor")
    if amounts.currency and document["currency"] not in {None, "", amounts.currency}:
        changed.append("currency")
    if changed:
        raise LedgerDbError(
            "Existing intake document conflicts with the same content hash: "
            + ", ".join(sorted(set(changed)))
        )

    active = document["lifecycle_status"] in {"received", "extracted", "needs_review"}
    desired_counterparty_id = document["counterparty_id"] or counterparty_id
    desired_import_batch_id = document["import_batch_id"] or import_batch_id
    desired_document_number = document["document_number"] or document_number
    desired_currency = document["currency"] or amounts.currency
    desired_total_minor = document["total_minor"]
    if desired_total_minor is None and amounts.gross is not None:
        desired_total_minor = int(amounts.gross * 100)
    backfill_changed = any(
        (
            desired_counterparty_id != document["counterparty_id"],
            desired_import_batch_id != document["import_batch_id"],
            desired_document_number != document["document_number"],
            desired_currency != document["currency"],
            desired_total_minor != document["total_minor"],
        )
    )
    if not active or not backfill_changed:
        return dict(document)
    return db.upsert_document(
        document_id=document["document_id"],
        external_key=document["external_key"],
        counterparty_id=desired_counterparty_id,
        import_batch_id=desired_import_batch_id,
        document_type=document["document_type"],
        document_number=desired_document_number,
        issued_on=document["issued_on"],
        period_key=document["period_key"],
        currency=desired_currency,
        total_minor=desired_total_minor,
        lifecycle_status=document["lifecycle_status"],
        source_hash=document["source_hash"],
        expected_row_version=document["row_version"],
    )


def _resolve_open_intake_issue(
    db: LedgerDB,
    *,
    issue_code: str,
    subject_table: str,
    subject_id: str,
    reason: str,
) -> None:
    issue = db.connection.execute(
        """
        SELECT * FROM validation_issues
        WHERE issue_code = ? AND subject_table = ? AND subject_id = ?
          AND issue_status = 'open'
        """,
        (issue_code, subject_table, subject_id),
    ).fetchone()
    if issue is None:
        return
    db.resolve_issue(
        issue["validation_issue_id"],
        reason=reason,
        expected_row_version=issue["row_version"],
    )


def _intake_transaction_summary(
    transaction: dict[str, Any], *, created: bool, treatment: dict[str, Any] | None = None
) -> dict[str, Any]:
    summary = {
        "created": created,
        "transaction_id": transaction["transaction_id"],
        "row_version": transaction["row_version"],
        "lifecycle_status": transaction["lifecycle_status"],
        "entry_type": transaction["entry_type"],
        "amount_minor": transaction["amount_minor"],
        "currency": transaction["currency"],
        "amount_eur_minor": transaction["amount_eur_minor"],
        "document_id": transaction["document_id"],
    }
    if treatment is not None:
        summary["tax_treatment_id"] = treatment["treatment_id"]
        summary["tax_treatment_row_version"] = treatment["row_version"]
        summary["tax_code"] = treatment["tax_code"]
    return summary


def _upsert_intake_counterparty(
    db: LedgerDB,
    suggestion: LedgerEntry | None,
    *,
    preferred_name: str | None = None,
) -> str | None:
    counterparty_name = (preferred_name or "").strip()
    if not counterparty_name and suggestion is not None:
        counterparty_name = suggestion.counterparty
    if not counterparty_name or counterparty_name == "Unknown":
        return None
    matches = find_name_candidates(db.connection, counterparty_name)
    if len(matches) == 1:
        return matches[0]["counterparty_id"]
    if len(matches) > 1:
        raise CounterpartyMatchError("Counterparty name is ambiguous; pass --counterparty-id")
    normalized = "".join(character for character in counterparty_name.casefold() if character.isalnum())
    row = db.upsert_counterparty(
        external_key=f"intake-name:{normalized}",
        display_name=counterparty_name,
        country_code="ZZ",
        source_hash=hashlib.sha256(
            f"intake-counterparty:{normalized}".encode("utf-8")
        ).hexdigest(),
    )
    return row["counterparty_id"]


def _existing_intake_receipt(
    database_path: Path,
    row: IntakeSheetRow,
) -> tuple[dict[str, Any], str] | None:
    with open_ledger_db(database_path, read_only=True) as db:
        if row.system_id is not None:
            receipt = db.get_intake_receipt(row.system_id)
            if receipt is None:
                raise IntakeSheetError(
                    f"row {row.row_number}: system_id is not known to the ledger"
                )
            if receipt["intake_tab"] != row.tab or receipt["row_fingerprint"] != row.row_fingerprint:
                raise IntakeSheetError(
                    f"row {row.row_number}: an accepted row was edited; add a new correcting row"
                )
            return receipt, "already_accepted"
        receipt = db.find_intake_receipt_by_fingerprint(row.row_fingerprint)
        if receipt is not None:
            return receipt, "recovered"
    return None


def _ensure_intake_follow_up_issues(
    database_path: Path,
    row: IntakeSheetRow,
    *,
    transaction_id: str,
) -> None:
    correction_of = row.values.get("correction_of")
    if row.tab != "income_intake" or not correction_of:
        return
    with open_ledger_db(database_path) as db:
        existing = db.connection.execute(
            """
            SELECT 1
            FROM validation_issues vi
            JOIN periods p ON p.period_id = vi.period_id
            WHERE p.period_key = ?
              AND vi.issue_code = 'invoice_correction_review'
              AND vi.subject_table = 'transactions'
              AND vi.subject_id = ?
            """,
            (row.period_key, transaction_id),
        ).fetchone()
        if existing is not None:
            return
        db.add_validation_issue(
            period_key=row.period_key,
            issue_code="invoice_correction_review",
            severity="error",
            message=(
                "Resolve the operator correction reference before approval: "
                + correction_of
            ),
            subject_table="transactions",
            subject_id=transaction_id,
            blocking=True,
            source_hash=hashlib.sha256(
                f"intake-correction:{row.row_fingerprint}".encode("utf-8")
            ).hexdigest(),
        )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quarter_key(value: date) -> str:
    return f"{value.year}-Q{((value.month - 1) // 3) + 1}"


def _payment_match_candidates(
    db: LedgerDB,
    *,
    exclude_settled: bool,
) -> list[RevolutMatchCandidate]:
    rows = db.connection.execute(
        """
        SELECT t.*, d.document_number
        FROM transactions t
        LEFT JOIN documents d ON d.document_id = t.document_id
        WHERE t.lifecycle_status IN ('approved', 'posted', 'included_in_snapshot')
          AND t.entry_type IN ('income', 'expense')
          AND (
              ? = 0 OR NOT EXISTS (
                  SELECT 1 FROM payments pay
                  WHERE pay.transaction_id = t.transaction_id
                    AND pay.match_status IN ('exact', 'manual')
              )
          )
        ORDER BY t.transaction_date, t.transaction_id
        """,
        (1 if exclude_settled else 0,),
    ).fetchall()
    candidates: list[RevolutMatchCandidate] = []
    for row in rows:
        signed_minor = (
            row["amount_original_minor"]
            if row["amount_original_minor"] is not None
            else row["amount_minor"]
        )
        if row["entry_type"] == "expense":
            signed_minor = -abs(signed_minor)
        amount_eur_minor = row["amount_eur_minor"]
        if amount_eur_minor is not None and row["entry_type"] == "expense":
            amount_eur_minor = -abs(amount_eur_minor)
        candidates.append(
            RevolutMatchCandidate(
                candidate_id=row["transaction_id"],
                recognition_date=date.fromisoformat(row["transaction_date"]),
                payment_date=None,
                reference=row["document_number"] or row["external_key"] or row["description"],
                amount_original=Decimal(signed_minor) / 100,
                currency=row["original_currency"] or row["currency"],
                amount_eur=(
                    Decimal(amount_eur_minor) / 100 if amount_eur_minor is not None else None
                ),
            )
        )
    return candidates


def _validate_existing_zenmoney_payment(
    existing: sqlite3.Row,
    payment: ZenMoneyPayment,
) -> None:
    desired = {
        "paid_on": payment.payment_date.isoformat(),
        "amount_minor": int(payment.amount_original * 100),
        "currency": payment.currency,
        "amount_eur_minor": (
            int(payment.amount_eur * 100) if payment.amount_eur is not None else None
        ),
        "account_name": payment.account_name,
    }
    changed = [key for key, value in desired.items() if existing[key] != value]
    if changed:
        raise LedgerDbError(
            "ZenMoney external_id already exists with changed accounting fields: "
            + ", ".join(sorted(changed))
        )


def _validate_existing_revolut_payment(
    existing: sqlite3.Row,
    payment: RevolutPayment,
) -> None:
    desired = {
        "paid_on": payment.payment_date.isoformat(),
        "amount_minor": int(payment.amount_original * 100),
        "currency": payment.currency,
        "amount_eur_minor": (
            int(payment.amount_eur * 100) if payment.amount_eur is not None else None
        ),
        "original_reference": payment.reference,
        "fee_minor": (
            int(payment.fee_original * 100) if payment.fee_original is not None else None
        ),
        "fee_currency": payment.currency if payment.fee_original is not None else None,
    }
    changed = [key for key, value in desired.items() if existing[key] != value]
    if changed:
        raise LedgerDbError(
            "Revolut external_id already exists with changed accounting fields: "
            + ", ".join(sorted(changed))
        )


def _claim_legacy_revolut_payment(
    db: LedgerDB,
    payment: RevolutPayment,
) -> sqlite3.Row | None:
    candidates = db.connection.execute(
        """
        SELECT * FROM payments
        WHERE source_system IS NULL
          AND paid_on = ?
          AND amount_minor = ?
          AND currency = ?
          AND COALESCE(original_reference, '') = ?
        ORDER BY created_at, payment_id
        """,
        (
            payment.payment_date.isoformat(),
            int(payment.amount_original * 100),
            payment.currency,
            payment.reference,
        ),
    ).fetchall()
    if not candidates:
        return None
    if len(candidates) > 1:
        raise LedgerDbError(
            f"Multiple legacy payments match Revolut row {payment.payment_id}; manual review required"
        )
    existing = candidates[0]
    source_row = {
        "legacy_source_hash": existing["source_hash"],
        "revolut_source_row": payment.source_row,
    }
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with db.connection:
        db.connection.execute(
            """
            UPDATE payments
            SET source_system = 'revolut', external_id = ?, account_name = 'Revolut',
                counterparty_name = ?, comment = ?, amount_eur_minor = ?,
                source_row_json = ?, fee_minor = ?, fee_currency = ?,
                row_version = row_version + 1, updated_at = ?
            WHERE payment_id = ?
            """,
            (
                payment.payment_id,
                payment.counterparty,
                payment.description,
                int(payment.amount_eur * 100) if payment.amount_eur is not None else None,
                json.dumps(source_row, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                int(payment.fee_original * 100) if payment.fee_original is not None else None,
                payment.currency if payment.fee_original is not None else None,
                timestamp,
                existing["payment_id"],
            ),
        )
    return db.connection.execute(
        "SELECT * FROM payments WHERE payment_id = ?",
        (existing["payment_id"],),
    ).fetchone()


def _sync_zenmoney_missing_issues(
    db: LedgerDB,
    *,
    period_key: str,
    business_accounts: set[str],
    present_external_ids: set[str],
) -> list[str]:
    period = db.ensure_period(period_key)
    selected_accounts = {value.casefold().strip() for value in business_accounts}
    existing = db.connection.execute(
        """
        SELECT * FROM payments
        WHERE source_system = 'zenmoney'
          AND paid_on BETWEEN ? AND ?
        ORDER BY paid_on, payment_id
        """,
        (period["starts_on"], period["ends_on"]),
    ).fetchall()
    missing = [
        row
        for row in existing
        if (row["account_name"] or "").casefold().strip() in selected_accounts
        and row["external_id"] not in present_external_ids
    ]
    missing_ids = {row["payment_id"] for row in missing}
    if period["status"] in {"closed", "amended"}:
        return sorted(missing_ids)

    for row in missing:
        db.add_validation_issue(
            period_key=period_key,
            issue_code="payment_missing_from_zenmoney_export",
            severity="error",
            message=(
                f"Previously imported ZenMoney payment is absent from the current full export: "
                f"{row['paid_on']} {row['amount_minor']} {row['currency']} {row['account_name']}."
            ),
            subject_table="payments",
            subject_id=row["payment_id"],
            blocking=True,
            source_hash=row["source_hash"],
            dedupe_key=f"zenmoney-missing:{row['payment_id']}",
        )

    stale_issues = db.connection.execute(
        """
        SELECT vi.*
        FROM validation_issues vi
        WHERE vi.period_id = ?
          AND vi.issue_code = 'payment_missing_from_zenmoney_export'
          AND vi.issue_status = 'open'
        """,
        (period["period_id"],),
    ).fetchall()
    for issue in stale_issues:
        if issue["subject_id"] in missing_ids:
            continue
        db.resolve_issue(
            issue["validation_issue_id"],
            reason="Payment is present in the latest full ZenMoney export.",
            expected_row_version=issue["row_version"],
        )
    return sorted(missing_ids)


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


def _calculation_diff(recomputed: dict[str, Any], filed: dict[str, Any]) -> dict[str, str]:
    differences: dict[str, str] = {}
    for key in sorted(set(recomputed) & set(filed)):
        try:
            differences[key] = f"{(Decimal(str(recomputed[key])) - Decimal(str(filed[key]))):.2f}"
        except Exception:
            continue
    return differences


def _load_json_object(path: Path) -> dict[str, Any]:
    if str(path) == "-":
        payload = json.loads(sys.stdin.read())
        label = "<stdin>"
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        label = str(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {label}")
    return payload


def _load_json_array(path: Path) -> list[dict[str, Any]]:
    if str(path) == "-":
        payload = json.loads(sys.stdin.read())
        label = "<stdin>"
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        label = str(path)
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError(f"Expected a JSON array of objects in {label}")
    return [dict(item) for item in payload]


def _json_payload_hash(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable(payload), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


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


def _positive_minor(value: Decimal, *, field: str) -> int:
    normalized = cents(value)
    if normalized <= 0:
        raise ValueError(f"{field} must be greater than zero")
    return int(normalized * 100)


def _parse_tax_obligation_selector(value: str) -> tuple[str, str]:
    period, separator, form = value.strip().partition(":")
    if not separator or not period or not form or ":" in form:
        raise ValueError("tax-obligation must use PERIOD:FORM, for example 2026-Q2:130")
    if form not in QUARTERLY_FORM_CODES + ANNUAL_FORM_CODES:
        raise ValueError(f"Unsupported tax obligation form: {form}")
    if form in QUARTERLY_FORM_CODES:
        year, marker, quarter = period.partition("-Q")
        if not marker or not year.isdigit() or len(year) != 4 or quarter not in {"1", "2", "3", "4"}:
            raise ValueError(f"Quarterly form {form} requires a YYYY-QN period")
    elif len(period) != 4 or not period.isdigit():
        raise ValueError(f"Annual form {form} requires a YYYY period")
    return period, form


def _sheet_value(value: Any) -> str:
    return "" if value is None else str(value)
