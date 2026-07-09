from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys

try:
    import yaml
except Exception:  # pragma: no cover - dependency guard for clearer CLI errors.
    yaml = None

from .annual import compare_annual_to_quarterly, write_annual_comparison_csv, write_annual_comparison_markdown
from .annual_categories import (
    build_annual_category_reconciliation,
    write_annual_category_reconciliation_csv,
    write_annual_category_reconciliation_markdown,
)
from .annual_constrained_assets import (
    build_annual_constrained_asset_reconciliation,
    write_annual_constrained_asset_reconciliation_csv,
    write_annual_constrained_asset_reconciliation_markdown,
)
from .asset_audit import (
    build_asset_audit,
    build_asset_scenarios,
    build_candidate_quarter_reconciliation,
    write_asset_audit_csvs,
    write_asset_audit_markdown,
    write_asset_scenarios_csv,
    write_candidate_quarter_reconciliation_csv,
)
from .closure_request import build_xolo_closure_request, write_xolo_closure_request
from .evidence_inventory import (
    build_evidence_inventory,
    write_evidence_inventory_csv,
    write_evidence_inventory_markdown,
)
from .history import run_history_audit, write_history_audit_csv, write_history_audit_markdown
from .hypothesis_ledger import (
    build_hypothesis_ledger,
    write_hypothesis_markdown,
    write_hypothesis_summary_csv,
)
from .ledger_projection import (
    build_ledger_projection,
    write_ledger_projection_csv,
    write_ledger_projection_markdown,
)
from .local_archive_audit import (
    build_local_archive_audit,
    write_local_archive_audit_csv,
    write_local_archive_audit_markdown,
)
from .local_attention_bridge import (
    build_local_attention_bridge,
    write_local_attention_bridge_csv,
    write_local_attention_bridge_markdown,
)
from .material_gap_drilldown import (
    build_material_gap_drilldown,
    write_material_gap_drilldown_csv,
    write_material_gap_drilldown_markdown,
)
from .material_gap_context import (
    build_material_gap_context,
    write_material_gap_context_csv,
    write_material_gap_context_markdown,
)
from .p1_near_fit_context import (
    build_p1_near_fit_context,
    write_p1_near_fit_context_csv,
    write_p1_near_fit_context_markdown,
)
from .p2_near_target_context import (
    build_p2_near_target_context,
    write_p2_near_target_context_csv,
    write_p2_near_target_context_markdown,
)
from .modelo130 import (
    calculate_modelo130,
    extract_modelo130_values,
    previous_positive_payments_with_warnings,
)
from .modelo303 import (
    build_modelo303_vat_crosscheck,
    write_modelo303_vat_crosscheck_csv,
    write_modelo303_vat_crosscheck_markdown,
)
from .money import cents, format_es, parse_amount, parse_rate
from .parsers import (
    LedgerEntry,
    apply_fx,
    derive_single_currency_rate,
    in_ytd,
    scan_expense_dir,
    scan_income_dir,
)
from .quarter_evidence import (
    build_quarter_evidence,
    write_quarter_evidence_csv,
    write_quarter_evidence_markdown,
)
from .quarter_closure import (
    build_quarter_closure_checklist,
    write_quarter_closure_csv,
    write_quarter_closure_markdown,
)
from .quarter_balance_bridge import (
    build_quarter_balance_bridge,
    write_quarter_balance_bridge_csv,
    write_quarter_balance_bridge_markdown,
)
from .quarter_acceptance import (
    build_quarter_acceptance_matrix,
    write_quarter_acceptance_csv,
    write_quarter_acceptance_markdown,
)
from .quarter_packets import build_quarter_packets, write_quarter_packets
from .register_review import (
    build_register_review_template,
    write_register_review_csv,
    write_register_review_markdown,
)
from .register_reconcile import (
    build_register_reconciliation,
    write_register_reconciliation_csv,
    write_register_reconciliation_markdown,
)
from .reports import write_compare, write_ledger, write_manifest, write_markdown_report
from .row_audit import build_row_audit, write_row_audit_csv, write_row_audit_markdown
from .row_decision_report import (
    build_row_decision_report,
    write_row_decision_report_csv,
    write_row_decision_report_markdown,
)
from .root_cause_narrowing import (
    build_root_cause_narrowing,
    write_root_cause_narrowing_csv,
    write_root_cause_narrowing_markdown,
)
from .sequential_summary import build_sequential_summary, write_sequential_summary
from .timing_audit import build_timing_audit, write_timing_audit_csv, write_timing_audit_markdown
from .xolo_ledger import (
    import_xolo_expense_csv,
    load_xolo_expense_ledger,
    reconcile_xolo_expenses,
    reviewed_expense_entries,
    write_xolo_expense_ledger_csv,
    write_xolo_reconciliation,
    xolo_ledger_total,
)
from .xolo_questions import build_xolo_questions, write_questions_csv, write_questions_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autonomo-tax")
    parser.add_argument("--config", type=Path, help="Optional YAML config file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    modelo = subparsers.add_parser("modelo130", help="Build Modelo 130 ledger/report")
    _add_common_args(modelo)
    modelo.add_argument("--out-dir", type=Path, help="Output directory. Defaults to runs/YYYY-QN")
    modelo.add_argument("--fx-rate", action="append", default=[], help="Currency rate, e.g. USD=0.85679")
    modelo.add_argument("--asset-review-threshold-eur", help="Expense amount threshold for asset/amortization review")
    modelo.add_argument(
        "--manual-ledger",
        type=Path,
        help="Optional reviewed CSV ledger with scanned/manual entries",
    )
    modelo.add_argument(
        "--xolo-expense-ledger",
        type=Path,
        help="Optional reviewed Xolo expense ledger CSV. Replaces parsed expense PDFs for Modelo 130 expense totals.",
    )
    modelo.add_argument(
        "--difficult-expenses-policy",
        choices=["include", "exclude"],
        default="include",
        help="Whether to add Modelo 130 difficult-to-justify expenses on top of deductible expenses.",
    )
    modelo.add_argument("--derive-target-fx", action="store_true", help="Derive missing income FX from the target Xolo report for historical reconciliation only")

    extract_income = subparsers.add_parser("extract-invoices", help="Extract income invoice ledger")
    _add_common_args(extract_income)
    extract_income.add_argument("--out", type=Path, required=True)

    extract_expenses = subparsers.add_parser("extract-expenses", help="Extract expense ledger")
    _add_common_args(extract_expenses)
    extract_expenses.add_argument("--out", type=Path, required=True)

    compare = subparsers.add_parser("compare-xolo", help="Read target Modelo 130 casillas")
    compare.add_argument("--target-report", type=Path, required=True)
    compare.add_argument("--year", type=int, required=True)
    compare.add_argument("--quarter", type=int, required=True, choices=[1, 2, 3, 4])

    xolo_ledger = subparsers.add_parser("xolo-ledger", help="Validate and reconcile reviewed Xolo expense ledgers")
    xolo_subparsers = xolo_ledger.add_subparsers(dest="xolo_ledger_command", required=True)
    xolo_import = xolo_subparsers.add_parser("import", help="Validate a Xolo expense ledger CSV and write a local mirror")
    xolo_import.add_argument("--input", type=Path, required=True)
    xolo_import.add_argument("--out", type=Path, required=True)
    xolo_import.add_argument("--fx-rate", action="append", default=[], help="Currency rate, e.g. USD=0.85679")
    xolo_reconcile = xolo_subparsers.add_parser("reconcile", help="Compare a Xolo expense ledger to local EXPENSE files")
    _add_common_args(xolo_reconcile)
    xolo_reconcile.add_argument("--xolo-expense-ledger", type=Path, required=True)
    xolo_reconcile.add_argument("--out", type=Path, required=True)
    xolo_reconcile.add_argument("--fx-rate", action="append", default=[], help="Currency rate, e.g. USD=0.85679")
    xolo_reconcile.add_argument("--asset-review-threshold-eur", help="Expense amount threshold for asset/amortization review")

    audit_history = subparsers.add_parser("audit-history", help="Audit every available Modelo 130 quarter in sequence")
    audit_history.add_argument("--xolo-root", type=Path, required=True)
    audit_history.add_argument("--xolo-raw-expenses", type=Path, help="Optional raw Xolo expense CSV from scripts/fetch_xolo_expenses.py")
    audit_history.add_argument("--out-csv", type=Path, required=True)
    audit_history.add_argument("--out-md", type=Path, required=True)

    audit_evidence_inventory = subparsers.add_parser(
        "audit-evidence-inventory",
        help="Inventory local Xolo archive evidence for submitted registers and asset schedules",
    )
    audit_evidence_inventory.add_argument("--xolo-root", type=Path, required=True)
    audit_evidence_inventory.add_argument("--out-csv", type=Path, required=True)
    audit_evidence_inventory.add_argument("--out-md", type=Path, required=True)

    audit_annual = subparsers.add_parser("audit-annual", help="Compare annual Modelo 100 summaries to Q4 Modelo 130")
    audit_annual.add_argument("--modelo100-summary", type=Path, required=True)
    audit_annual.add_argument("--history-audit", type=Path, required=True)
    audit_annual.add_argument("--xolo-raw-expenses", type=Path, required=True)
    audit_annual.add_argument("--out-csv", type=Path, required=True)
    audit_annual.add_argument("--out-md", type=Path, required=True)

    audit_annual_categories = subparsers.add_parser(
        "audit-annual-categories",
        help="Compare Modelo 100 annual expense categories with raw Xolo expense categories",
    )
    audit_annual_categories.add_argument("--modelo100-summary", type=Path, required=True)
    audit_annual_categories.add_argument("--history-audit", type=Path, required=True)
    audit_annual_categories.add_argument("--xolo-raw-expenses", type=Path, required=True)
    audit_annual_categories.add_argument("--out-csv", type=Path, required=True)
    audit_annual_categories.add_argument("--out-md", type=Path, required=True)

    audit_modelo303 = subparsers.add_parser(
        "audit-modelo303-vat",
        help="Cross-check Modelo 303 VAT totals against raw Xolo VAT-bearing expense rows",
    )
    audit_modelo303.add_argument("--tax-report-dir", type=Path, required=True)
    audit_modelo303.add_argument("--xolo-raw-expenses", type=Path, required=True)
    audit_modelo303.add_argument("--out-csv", type=Path, required=True)
    audit_modelo303.add_argument("--out-md", type=Path, required=True)

    audit_assets = subparsers.add_parser("audit-assets", help="Build an explicit asset/amortization audit table")
    audit_assets.add_argument("--history-audit", type=Path, required=True)
    audit_assets.add_argument("--xolo-raw-expenses", type=Path, required=True)
    audit_assets.add_argument("--modelo100-summary", type=Path, help="Optional annual Modelo 100 summary for scenario comparison")
    audit_assets.add_argument("--out-assets-csv", type=Path, required=True)
    audit_assets.add_argument("--out-quarter-csv", type=Path, required=True)
    audit_assets.add_argument("--out-scenarios-csv", type=Path, help="Optional output CSV for annual amortization scenarios")
    audit_assets.add_argument("--out-quarter-reconciliation-csv", type=Path, help="Optional output CSV for candidate quarterly reconciliation")
    audit_assets.add_argument("--out-md", type=Path, required=True)

    audit_questions = subparsers.add_parser("audit-questions", help="Generate prioritized Xolo questions from audit outputs")
    audit_questions.add_argument("--candidate-quarter-reconciliation", type=Path, required=True)
    audit_questions.add_argument("--out-csv", type=Path, required=True)
    audit_questions.add_argument("--out-md", type=Path, required=True)

    audit_annual_constrained_assets = subparsers.add_parser(
        "audit-annual-constrained-assets",
        help="Constrain candidate quarterly asset amortization to annual Modelo 100 line 0208",
    )
    audit_annual_constrained_assets.add_argument("--candidate-quarter-reconciliation", type=Path, required=True)
    audit_annual_constrained_assets.add_argument("--modelo100-summary", type=Path, required=True)
    audit_annual_constrained_assets.add_argument("--out-csv", type=Path, required=True)
    audit_annual_constrained_assets.add_argument("--out-md", type=Path, required=True)

    audit_quarter_evidence = subparsers.add_parser(
        "audit-quarter-evidence",
        help="Package every Modelo 130 quarter with raw rows, candidate amortization, and Xolo questions",
    )
    audit_quarter_evidence.add_argument("--history-audit", type=Path, required=True)
    audit_quarter_evidence.add_argument("--candidate-quarter-reconciliation", type=Path, required=True)
    audit_quarter_evidence.add_argument("--xolo-raw-expenses", type=Path, required=True)
    audit_quarter_evidence.add_argument("--xolo-questions", type=Path)
    audit_quarter_evidence.add_argument("--out-csv", type=Path, required=True)
    audit_quarter_evidence.add_argument("--out-md", type=Path, required=True)

    audit_row_evidence = subparsers.add_parser(
        "audit-row-evidence",
        help="Classify raw Xolo expense rows by Modelo 130 quarter and reconciliation role",
    )
    audit_row_evidence.add_argument("--history-audit", type=Path, required=True)
    audit_row_evidence.add_argument("--candidate-quarter-reconciliation", type=Path, required=True)
    audit_row_evidence.add_argument("--xolo-raw-expenses", type=Path, required=True)
    audit_row_evidence.add_argument("--out-csv", type=Path, required=True)
    audit_row_evidence.add_argument("--out-md", type=Path, required=True)

    audit_register_template = subparsers.add_parser(
        "audit-register-template",
        help="Create a fillable submitted-register review template from row audit evidence",
    )
    audit_register_template.add_argument("--row-audit", type=Path, required=True)
    audit_register_template.add_argument("--out-csv", type=Path, required=True)
    audit_register_template.add_argument("--out-md", type=Path, required=True)

    audit_register_reconcile = subparsers.add_parser(
        "audit-register-reconcile",
        help="Compare a filled submitted-register review template against Modelo 130 target deltas",
    )
    audit_register_reconcile.add_argument("--history-audit", type=Path, required=True)
    audit_register_reconcile.add_argument("--register-review", type=Path, required=True)
    audit_register_reconcile.add_argument("--out-csv", type=Path, required=True)
    audit_register_reconcile.add_argument("--out-md", type=Path, required=True)

    audit_ledger_projection = subparsers.add_parser(
        "audit-ledger-projection",
        help="Compare a curated Xolo expense ledger against historical Modelo 130 casilla 02 targets",
    )
    audit_ledger_projection.add_argument("--history-audit", type=Path, required=True)
    audit_ledger_projection.add_argument("--xolo-expense-ledger", type=Path, required=True)
    audit_ledger_projection.add_argument("--out-csv", type=Path, required=True)
    audit_ledger_projection.add_argument("--out-md", type=Path, required=True)

    audit_hypothesis_ledger = subparsers.add_parser(
        "audit-hypothesis-ledger",
        help="Build an unconfirmed ledger scenario from row audit evidence and candidate amortization",
    )
    audit_hypothesis_ledger.add_argument("--row-audit", type=Path, required=True)
    audit_hypothesis_ledger.add_argument("--candidate-quarter-reconciliation", type=Path, required=True)
    audit_hypothesis_ledger.add_argument("--out-ledger-csv", type=Path, required=True)
    audit_hypothesis_ledger.add_argument("--out-summary-csv", type=Path, required=True)
    audit_hypothesis_ledger.add_argument("--out-md", type=Path, required=True)

    audit_quarter_closure = subparsers.add_parser(
        "audit-quarter-closure",
        help="Build a quarter-by-quarter closure checklist from hypothesis ledger evidence",
    )
    audit_quarter_closure.add_argument("--hypothesis-summary", type=Path, required=True)
    audit_quarter_closure.add_argument("--row-audit", type=Path, required=True)
    audit_quarter_closure.add_argument("--out-csv", type=Path, required=True)
    audit_quarter_closure.add_argument("--out-md", type=Path, required=True)

    audit_quarter_balance_bridge = subparsers.add_parser(
        "audit-quarter-balance-bridge",
        help="Rewrite each Modelo 130 quarter as raw expenses plus amortization minus exclusions plus balance",
    )
    audit_quarter_balance_bridge.add_argument("--candidate-quarter-reconciliation", type=Path, required=True)
    audit_quarter_balance_bridge.add_argument("--quarter-closure", type=Path, required=True)
    audit_quarter_balance_bridge.add_argument("--annual-constrained-assets", type=Path)
    audit_quarter_balance_bridge.add_argument("--out-csv", type=Path, required=True)
    audit_quarter_balance_bridge.add_argument("--out-md", type=Path, required=True)

    audit_material_gaps = subparsers.add_parser(
        "audit-material-gaps",
        help="Drill into material Modelo 130 balances with explicit local hypotheses",
    )
    audit_material_gaps.add_argument("--quarter-balance-bridge", type=Path, required=True)
    audit_material_gaps.add_argument("--hypotheses", type=Path, required=True)
    audit_material_gaps.add_argument("--out-csv", type=Path, required=True)
    audit_material_gaps.add_argument("--out-md", type=Path, required=True)

    audit_quarter_acceptance = subparsers.add_parser(
        "audit-quarter-acceptance",
        help="Build the quarter acceptance gate from closure, balance bridge, and material-gap evidence",
    )
    audit_quarter_acceptance.add_argument("--quarter-closure", type=Path, required=True)
    audit_quarter_acceptance.add_argument("--quarter-balance-bridge", type=Path, required=True)
    audit_quarter_acceptance.add_argument("--material-gap-drilldown", type=Path)
    audit_quarter_acceptance.add_argument("--packets-dir", type=Path)
    audit_quarter_acceptance.add_argument("--out-csv", type=Path, required=True)
    audit_quarter_acceptance.add_argument("--out-md", type=Path, required=True)

    audit_material_gap_context = subparsers.add_parser(
        "audit-material-gap-context",
        help="Add raw Xolo ledger context to P0 material-gap quarters",
    )
    audit_material_gap_context.add_argument("--quarter-acceptance", type=Path, required=True)
    audit_material_gap_context.add_argument("--quarter-balance-bridge", type=Path, required=True)
    audit_material_gap_context.add_argument("--xolo-expense-ledger", type=Path, required=True)
    audit_material_gap_context.add_argument("--material-gap-drilldown", type=Path)
    audit_material_gap_context.add_argument("--out-csv", type=Path, required=True)
    audit_material_gap_context.add_argument("--out-md", type=Path, required=True)

    audit_p1_near_fit_context = subparsers.add_parser(
        "audit-p1-near-fit-context",
        help="Summarize strongest local arithmetic forks for P1 near-fit quarters",
    )
    audit_p1_near_fit_context.add_argument("--quarter-acceptance", type=Path, required=True)
    audit_p1_near_fit_context.add_argument("--quarter-balance-bridge", type=Path, required=True)
    audit_p1_near_fit_context.add_argument("--hypotheses", type=Path, required=True)
    audit_p1_near_fit_context.add_argument("--out-csv", type=Path, required=True)
    audit_p1_near_fit_context.add_argument("--out-md", type=Path, required=True)

    audit_p2_near_target_context = subparsers.add_parser(
        "audit-p2-near-target-context",
        help="Summarize near-target forks for P2 quarters from source findings",
    )
    audit_p2_near_target_context.add_argument("--quarter-acceptance", type=Path, required=True)
    audit_p2_near_target_context.add_argument("--quarter-balance-bridge", type=Path, required=True)
    audit_p2_near_target_context.add_argument("--source-findings", type=Path, required=True)
    audit_p2_near_target_context.add_argument("--out-csv", type=Path, required=True)
    audit_p2_near_target_context.add_argument("--out-md", type=Path, required=True)

    audit_closure_request = subparsers.add_parser(
        "audit-closure-request",
        help="Build a draft request to Xolo from the quarter closure checklist",
    )
    audit_closure_request.add_argument("--quarter-closure", type=Path, required=True)
    audit_closure_request.add_argument("--source-findings", type=Path)
    audit_closure_request.add_argument("--row-decisions", type=Path)
    audit_closure_request.add_argument("--root-cause-narrowing", type=Path)
    audit_closure_request.add_argument("--evidence-inventory", type=Path)
    audit_closure_request.add_argument("--local-attention-bridge", type=Path)
    audit_closure_request.add_argument("--material-gap-drilldown", type=Path)
    audit_closure_request.add_argument("--material-gap-context", type=Path)
    audit_closure_request.add_argument("--p1-near-fit-context", type=Path)
    audit_closure_request.add_argument("--p2-near-target-context", type=Path)
    audit_closure_request.add_argument("--out-md", type=Path, required=True)

    audit_root_cause_narrowing = subparsers.add_parser(
        "audit-root-cause-narrowing",
        help="Combine closure, annual amortization, and Modelo 303 gates into remaining root-cause categories",
    )
    audit_root_cause_narrowing.add_argument("--quarter-closure", type=Path, required=True)
    audit_root_cause_narrowing.add_argument("--annual-constrained-assets", type=Path, required=True)
    audit_root_cause_narrowing.add_argument("--annual-categories", type=Path)
    audit_root_cause_narrowing.add_argument("--modelo303-vat-crosscheck", type=Path, required=True)
    audit_root_cause_narrowing.add_argument("--out-csv", type=Path, required=True)
    audit_root_cause_narrowing.add_argument("--out-md", type=Path, required=True)

    audit_row_decisions = subparsers.add_parser(
        "audit-row-decisions",
        help="Build concrete row-level questions needed to close Modelo 130 quarter residuals",
    )
    audit_row_decisions.add_argument("--row-audit", type=Path, required=True)
    audit_row_decisions.add_argument("--root-cause-narrowing", type=Path, required=True)
    audit_row_decisions.add_argument("--out-csv", type=Path, required=True)
    audit_row_decisions.add_argument("--out-md", type=Path, required=True)

    audit_quarter_packets = subparsers.add_parser(
        "audit-quarter-packets",
        help="Write one verification packet per Modelo 130 quarter",
    )
    audit_quarter_packets.add_argument("--history-audit", type=Path, required=True)
    audit_quarter_packets.add_argument("--quarter-closure", type=Path, required=True)
    audit_quarter_packets.add_argument("--row-audit", type=Path, required=True)
    audit_quarter_packets.add_argument("--source-findings", type=Path)
    audit_quarter_packets.add_argument("--root-cause-narrowing", type=Path)
    audit_quarter_packets.add_argument("--row-decisions", type=Path)
    audit_quarter_packets.add_argument("--quarter-balance-bridge", type=Path)
    audit_quarter_packets.add_argument("--out-dir", type=Path, required=True)

    audit_sequential_summary = subparsers.add_parser(
        "audit-sequential-summary",
        help="Build a chronological root-cause summary from quarter closure and source findings",
    )
    audit_sequential_summary.add_argument("--quarter-closure", type=Path, required=True)
    audit_sequential_summary.add_argument("--source-findings", type=Path, required=True)
    audit_sequential_summary.add_argument("--modelo303-vat-crosscheck", type=Path)
    audit_sequential_summary.add_argument("--annual-categories", type=Path)
    audit_sequential_summary.add_argument("--material-gap-drilldown", type=Path)
    audit_sequential_summary.add_argument("--material-gap-context", type=Path)
    audit_sequential_summary.add_argument("--p1-near-fit-context", type=Path)
    audit_sequential_summary.add_argument("--p2-near-target-context", type=Path)
    audit_sequential_summary.add_argument("--out-md", type=Path, required=True)

    audit_timing = subparsers.add_parser(
        "audit-timing",
        help="Diagnose timing, carry-forward, and netting patterns across Modelo 130 quarter balances",
    )
    audit_timing.add_argument("--quarter-closure", type=Path, required=True)
    audit_timing.add_argument("--source-findings", type=Path)
    audit_timing.add_argument("--out-csv", type=Path, required=True)
    audit_timing.add_argument("--out-md", type=Path, required=True)

    audit_local_archive = subparsers.add_parser(
        "audit-local-archive",
        help="Compare local EXPENSE archive documents against raw Xolo expense API rows",
    )
    audit_local_archive.add_argument("--xolo-root", type=Path, required=True)
    audit_local_archive.add_argument("--xolo-raw-expenses", type=Path, required=True)
    audit_local_archive.add_argument("--out-csv", type=Path, required=True)
    audit_local_archive.add_argument("--out-md", type=Path, required=True)

    audit_local_attention = subparsers.add_parser(
        "audit-local-attention",
        help="Tie local archive attention candidates to current Modelo 130 quarter residuals",
    )
    audit_local_attention.add_argument("--local-archive-audit", type=Path, required=True)
    audit_local_attention.add_argument("--quarter-closure", type=Path, required=True)
    audit_local_attention.add_argument("--out-csv", type=Path, required=True)
    audit_local_attention.add_argument("--out-md", type=Path, required=True)

    args = parser.parse_args(argv)
    config = _load_config(args.config)
    _merge_config(args, config)

    if args.command == "modelo130":
        return _cmd_modelo130(args)
    if args.command == "extract-invoices":
        entries, manual = scan_income_dir(Path(args.xolo_root) / "INVOICE")
        write_ledger(args.out, entries + manual)
        print(f"Wrote {args.out}")
        return 0
    if args.command == "extract-expenses":
        entries, manual = scan_expense_dir(Path(args.xolo_root) / "EXPENSE")
        write_ledger(args.out, entries + manual)
        print(f"Wrote {args.out}")
        return 0
    if args.command == "compare-xolo":
        values = extract_modelo130_values(args.target_report, args.year, args.quarter)
        for key, value in values.items():
            print(f"{key}: {format_es(value)}")
        return 0
    if args.command == "xolo-ledger":
        if args.xolo_ledger_command == "import":
            rows = import_xolo_expense_csv(args.input, _parse_fx_rates(args.fx_rate))
            write_xolo_expense_ledger_csv(args.out, rows)
            print(f"Validated {len(rows)} Xolo expense rows and wrote {args.out}")
            return 0
        if args.xolo_ledger_command == "reconcile":
            return _cmd_xolo_reconcile(args)
    if args.command == "audit-history":
        rows = run_history_audit(Path(args.xolo_root), Path(args.xolo_raw_expenses) if args.xolo_raw_expenses else None)
        write_history_audit_csv(args.out_csv, rows)
        write_history_audit_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} historical audit rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-evidence-inventory":
        rows = build_evidence_inventory(args.xolo_root)
        write_evidence_inventory_csv(args.out_csv, rows)
        write_evidence_inventory_markdown(args.out_md, rows, args.xolo_root)
        print(f"Wrote {len(rows)} evidence inventory rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-annual":
        rows = compare_annual_to_quarterly(args.modelo100_summary, args.history_audit, args.xolo_raw_expenses)
        write_annual_comparison_csv(args.out_csv, rows)
        write_annual_comparison_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} annual comparison rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-annual-categories":
        rows = build_annual_category_reconciliation(
            args.modelo100_summary,
            args.history_audit,
            args.xolo_raw_expenses,
        )
        write_annual_category_reconciliation_csv(args.out_csv, rows)
        write_annual_category_reconciliation_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} annual category reconciliation rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-modelo303-vat":
        rows = build_modelo303_vat_crosscheck(args.tax_report_dir, args.xolo_raw_expenses)
        write_modelo303_vat_crosscheck_csv(args.out_csv, rows)
        write_modelo303_vat_crosscheck_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} Modelo 303 VAT cross-check rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-assets":
        if args.out_scenarios_csv and not args.modelo100_summary:
            raise SystemExit("--out-scenarios-csv requires --modelo100-summary")
        assets, quarters = build_asset_audit(args.history_audit, args.xolo_raw_expenses)
        scenarios = build_asset_scenarios(assets, args.modelo100_summary) if args.modelo100_summary else []
        candidate_quarters = build_candidate_quarter_reconciliation(args.history_audit, args.xolo_raw_expenses)
        write_asset_audit_csvs(args.out_assets_csv, args.out_quarter_csv, assets, quarters)
        if args.out_scenarios_csv:
            write_asset_scenarios_csv(args.out_scenarios_csv, scenarios)
        if args.out_quarter_reconciliation_csv:
            write_candidate_quarter_reconciliation_csv(args.out_quarter_reconciliation_csv, candidate_quarters)
        write_asset_audit_markdown(args.out_md, assets, quarters, scenarios, candidate_quarters)
        print(
            f"Wrote {len(assets)} asset rows and {len(quarters)} quarter rows "
            f"to {args.out_assets_csv}, {args.out_quarter_csv}, and {args.out_md}"
        )
        if scenarios:
            print(f"Wrote {len(scenarios)} annual scenario rows")
        if args.out_quarter_reconciliation_csv:
            print(f"Wrote {len(candidate_quarters)} candidate quarterly reconciliation rows")
        return 0
    if args.command == "audit-questions":
        questions = build_xolo_questions(args.candidate_quarter_reconciliation)
        write_questions_csv(args.out_csv, questions)
        write_questions_markdown(args.out_md, questions)
        print(f"Wrote {len(questions)} Xolo questions to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-annual-constrained-assets":
        rows = build_annual_constrained_asset_reconciliation(
            args.candidate_quarter_reconciliation,
            args.modelo100_summary,
        )
        write_annual_constrained_asset_reconciliation_csv(args.out_csv, rows)
        write_annual_constrained_asset_reconciliation_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} annual-constrained asset rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-quarter-evidence":
        rows = build_quarter_evidence(
            args.history_audit,
            args.candidate_quarter_reconciliation,
            args.xolo_raw_expenses,
            args.xolo_questions,
        )
        write_quarter_evidence_csv(args.out_csv, rows)
        write_quarter_evidence_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} quarter evidence rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-row-evidence":
        rows = build_row_audit(args.history_audit, args.candidate_quarter_reconciliation, args.xolo_raw_expenses)
        write_row_audit_csv(args.out_csv, rows)
        write_row_audit_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} row audit rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-register-template":
        rows = build_register_review_template(args.row_audit)
        write_register_review_csv(args.out_csv, rows)
        write_register_review_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} register review rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-register-reconcile":
        rows = build_register_reconciliation(args.history_audit, args.register_review)
        write_register_reconciliation_csv(args.out_csv, rows)
        write_register_reconciliation_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} register reconciliation rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-ledger-projection":
        rows = build_ledger_projection(args.history_audit, args.xolo_expense_ledger)
        write_ledger_projection_csv(args.out_csv, rows)
        write_ledger_projection_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} ledger projection rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-hypothesis-ledger":
        result = build_hypothesis_ledger(args.row_audit, args.candidate_quarter_reconciliation)
        write_xolo_expense_ledger_csv(args.out_ledger_csv, result.ledger_rows)
        write_hypothesis_summary_csv(args.out_summary_csv, result.summary_rows)
        write_hypothesis_markdown(args.out_md, result)
        print(
            f"Wrote {len(result.ledger_rows)} hypothesis ledger rows and "
            f"{len(result.summary_rows)} summary rows"
        )
        return 0
    if args.command == "audit-quarter-closure":
        rows = build_quarter_closure_checklist(args.hypothesis_summary, args.row_audit)
        write_quarter_closure_csv(args.out_csv, rows)
        write_quarter_closure_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} quarter closure rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-quarter-balance-bridge":
        rows = build_quarter_balance_bridge(
            args.candidate_quarter_reconciliation,
            args.quarter_closure,
            args.annual_constrained_assets,
        )
        write_quarter_balance_bridge_csv(args.out_csv, rows)
        write_quarter_balance_bridge_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} quarter balance bridge rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-material-gaps":
        rows = build_material_gap_drilldown(args.quarter_balance_bridge, args.hypotheses)
        write_material_gap_drilldown_csv(args.out_csv, rows)
        write_material_gap_drilldown_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} material gap drilldown rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-quarter-acceptance":
        rows = build_quarter_acceptance_matrix(
            args.quarter_closure,
            args.quarter_balance_bridge,
            args.material_gap_drilldown,
            args.packets_dir,
        )
        write_quarter_acceptance_csv(args.out_csv, rows)
        write_quarter_acceptance_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} quarter acceptance rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-material-gap-context":
        rows = build_material_gap_context(
            args.quarter_acceptance,
            args.quarter_balance_bridge,
            args.xolo_expense_ledger,
            args.material_gap_drilldown,
        )
        write_material_gap_context_csv(args.out_csv, rows)
        write_material_gap_context_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} material gap context rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-p1-near-fit-context":
        rows = build_p1_near_fit_context(
            args.quarter_acceptance,
            args.quarter_balance_bridge,
            args.hypotheses,
        )
        write_p1_near_fit_context_csv(args.out_csv, rows)
        write_p1_near_fit_context_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} P1 near-fit context rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-p2-near-target-context":
        rows = build_p2_near_target_context(
            args.quarter_acceptance,
            args.quarter_balance_bridge,
            args.source_findings,
        )
        write_p2_near_target_context_csv(args.out_csv, rows)
        write_p2_near_target_context_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} P2 near-target context rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-closure-request":
        markdown = build_xolo_closure_request(
            args.quarter_closure,
            args.source_findings,
            args.row_decisions,
            args.root_cause_narrowing,
            args.evidence_inventory,
            args.local_attention_bridge,
            args.material_gap_drilldown,
            args.material_gap_context,
            args.p1_near_fit_context,
            args.p2_near_target_context,
        )
        write_xolo_closure_request(args.out_md, markdown)
        print(f"Wrote Xolo closure request to {args.out_md}")
        return 0
    if args.command == "audit-root-cause-narrowing":
        rows = build_root_cause_narrowing(
            args.quarter_closure,
            args.annual_constrained_assets,
            args.modelo303_vat_crosscheck,
            args.annual_categories,
        )
        write_root_cause_narrowing_csv(args.out_csv, rows)
        write_root_cause_narrowing_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} root-cause narrowing rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-row-decisions":
        rows = build_row_decision_report(args.row_audit, args.root_cause_narrowing)
        write_row_decision_report_csv(args.out_csv, rows)
        write_row_decision_report_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} row decision rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-quarter-packets":
        packets = build_quarter_packets(
            args.history_audit,
            args.quarter_closure,
            args.row_audit,
            args.source_findings,
            args.root_cause_narrowing,
            args.row_decisions,
            args.quarter_balance_bridge,
        )
        write_quarter_packets(args.out_dir, packets)
        print(f"Wrote {len(packets)} quarter packets to {args.out_dir}")
        return 0
    if args.command == "audit-sequential-summary":
        markdown = build_sequential_summary(
            args.quarter_closure,
            args.source_findings,
            args.modelo303_vat_crosscheck,
            args.annual_categories,
            args.material_gap_drilldown,
            args.material_gap_context,
            args.p1_near_fit_context,
            args.p2_near_target_context,
        )
        write_sequential_summary(args.out_md, markdown)
        print(f"Wrote sequential audit summary to {args.out_md}")
        return 0
    if args.command == "audit-timing":
        rows = build_timing_audit(args.quarter_closure, args.source_findings)
        write_timing_audit_csv(args.out_csv, rows)
        write_timing_audit_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} timing audit rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-local-archive":
        rows = build_local_archive_audit(args.xolo_root, args.xolo_raw_expenses)
        write_local_archive_audit_csv(args.out_csv, rows)
        write_local_archive_audit_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} local archive audit rows to {args.out_csv} and {args.out_md}")
        return 0
    if args.command == "audit-local-attention":
        rows = build_local_attention_bridge(args.local_archive_audit, args.quarter_closure)
        write_local_attention_bridge_csv(args.out_csv, rows)
        write_local_attention_bridge_markdown(args.out_md, rows)
        print(f"Wrote {len(rows)} local attention bridge rows to {args.out_csv} and {args.out_md}")
        return 0
    raise AssertionError(args.command)


def _cmd_modelo130(args: argparse.Namespace) -> int:
    xolo_root = Path(args.xolo_root)
    year = int(args.year)
    quarter = int(args.quarter)
    out_dir = args.out_dir or Path.cwd() / "runs" / f"{year}-Q{quarter}"
    out_dir.mkdir(parents=True, exist_ok=True)

    target = None
    if args.target_report:
        target = extract_modelo130_values(Path(args.target_report), year, quarter)

    income_entries, income_manual = scan_income_dir(xolo_root / "INVOICE")
    asset_threshold = parse_amount(args.asset_review_threshold_eur or "600.00")
    expense_entries, expense_manual = scan_expense_dir(xolo_root / "EXPENSE", asset_threshold)
    manual = income_manual + expense_manual
    reviewed: list[LedgerEntry] = []
    if args.manual_ledger:
        reviewed = _load_manual_ledger(Path(args.manual_ledger))
        manual.extend(e for e in reviewed if e.review_required)

    fx_rates = _parse_fx_rates(args.fx_rate)
    fx_notes: list[str] = []
    for currency, rate in sorted(fx_rates.items()):
        fx_notes.append(f"Configured FX rate: {currency}={rate}")
    if target and "USD" not in fx_rates and args.derive_target_fx:
        derived = derive_single_currency_rate(income_entries, target["01"], "USD", year, quarter)
        if derived is not None:
            fx_rates["USD"] = derived
            fx_notes.append(
                "WARNING: Derived USD rate from Xolo target casilla 01 for historical reconciliation only: "
                f"USD={derived}"
            )

    entries_for_fx = income_entries + expense_entries + [e for e in reviewed if not e.review_required]
    warnings = apply_fx(entries_for_fx, fx_rates)
    newly_manual = [e for e in entries_for_fx if e.review_required]
    manual.extend(newly_manual)
    entries = _final_entries(args, income_entries, expense_entries, reviewed, warnings, year, quarter)
    expense_source_summary = None
    xolo_reconciliation = None
    if args.xolo_expense_ledger:
        xolo_rows = load_xolo_expense_ledger(Path(args.xolo_expense_ledger))
        xolo_entries, xolo_warnings = reviewed_expense_entries(xolo_rows, year, quarter)
        warnings.extend(xolo_warnings)
        if xolo_warnings:
            raise SystemExit("; ".join(xolo_warnings))
        manual_expense_rows = [e for e in reviewed if not e.review_required and e.kind == "expense"]
        if manual_expense_rows:
            warnings.append(
                "Ignoring reviewed expense rows from --manual-ledger because --xolo-expense-ledger is the authoritative expense source"
            )
        entries = [e for e in entries if e.kind != "expense"] + xolo_entries
        local_expense_ytd = cents(
            sum(
                (e.deductible_eur or Decimal("0.00"))
                for e in expense_entries
                if not e.review_required and in_ytd(e.date, year, quarter)
            )
        )
        xolo_total = xolo_ledger_total(xolo_rows, year, quarter)
        xolo_reconciliation = reconcile_xolo_expenses(xolo_rows, expense_entries + expense_manual, year, quarter)
        write_xolo_reconciliation(out_dir / "xolo_expense_reconcile.csv", xolo_reconciliation)
        expense_source_summary = {
            "source": "reviewed Xolo expense ledger",
            "xolo_expense_ledger": str(args.xolo_expense_ledger),
            "local_parsed_deductible_before_5": local_expense_ytd,
            "xolo_ledger_deductible_before_5": xolo_total,
            "reconciliation_csv": str(out_dir / "xolo_expense_reconcile.csv"),
        }
    if args.xolo_expense_ledger:
        ytd_entries = [e for e in entries if e.kind == "expense"] + [
            e for e in entries if e.kind != "expense" and in_ytd(e.date, year, quarter)
        ]
    else:
        ytd_entries = [e for e in entries if in_ytd(e.date, year, quarter)]
    ytd_manual = [e for e in manual if _manual_relevant_to_run(e, year, quarter)]

    income_ytd = cents(sum((e.amount_eur or Decimal("0.00")) for e in ytd_entries if e.kind == "income"))
    deductible_before_difficult = cents(
        sum((e.deductible_eur or Decimal("0.00")) for e in ytd_entries if e.kind == "expense" and not e.review_required)
    )
    previous, previous_warnings = previous_positive_payments_with_warnings(xolo_root / "TAX_REPORT", year, quarter)
    warnings.extend(previous_warnings)
    result = calculate_modelo130(
        income_ytd,
        deductible_before_difficult,
        previous,
        minoracion=target.get("13", Decimal("0.00")) if target else Decimal("0.00"),
        include_difficult_expenses=args.difficult_expenses_policy == "include",
        difficult_expenses_rate=_difficult_expenses_rate_for_year(year),
    )
    manifest = _build_manifest(args, xolo_root, out_dir, target is not None)

    write_ledger(out_dir / "ledger.csv", ytd_entries)
    write_ledger(out_dir / "manual_review.csv", ytd_manual)
    write_ledger(out_dir / "manual_review_all.csv", manual)
    write_compare(out_dir / "xolo_compare.json", result, target)
    write_manifest(out_dir / "run_manifest.json", manifest)
    write_markdown_report(
        out_dir / "modelo130_report.md",
        year,
        quarter,
        result,
        target,
        ytd_entries,
        ytd_manual,
        warnings,
        fx_notes,
        expense_source_summary=expense_source_summary,
        xolo_reconciliation=xolo_reconciliation,
    )

    print(f"Wrote run artifacts to {out_dir}")
    print(f"casilla 01: {format_es(result.casilla_01)}")
    print(f"casilla 02: {format_es(result.casilla_02)}")
    print(f"casilla 19: {format_es(result.casilla_19)}")
    if target:
        print(f"xolo casilla 19: {format_es(target.get('19'))}")
        print(f"diff casilla 19: {format_es(result.casilla_19 - target['19'])}")
    if warnings:
        print(f"warnings: {len(warnings)}")
    if ytd_manual:
        print(f"manual review items: {len(ytd_manual)}")
    return 0


def _cmd_xolo_reconcile(args: argparse.Namespace) -> int:
    xolo_root = Path(args.xolo_root)
    year = int(args.year)
    quarter = int(args.quarter)
    rows = load_xolo_expense_ledger(Path(args.xolo_expense_ledger))
    asset_threshold = parse_amount(args.asset_review_threshold_eur or "600.00")
    expense_entries, expense_manual = scan_expense_dir(xolo_root / "EXPENSE", asset_threshold)
    apply_fx(expense_entries + expense_manual, _parse_fx_rates(args.fx_rate))
    reconciliation = reconcile_xolo_expenses(rows, expense_entries + expense_manual, year, quarter)
    write_xolo_reconciliation(args.out, reconciliation)
    print(f"Wrote {len(reconciliation)} reconciliation rows to {args.out}")
    return 0


def _final_entries(
    args: argparse.Namespace,
    income_entries: list[LedgerEntry],
    expense_entries: list[LedgerEntry],
    reviewed: list[LedgerEntry],
    warnings: list[str],
    year: int,
    quarter: int,
) -> list[LedgerEntry]:
    non_reviewed = [e for e in reviewed if not e.review_required]
    if args.xolo_expense_ledger:
        ignored_local = cents(
            sum(
                (e.deductible_eur or Decimal("0.00"))
                for e in expense_entries
                if not e.review_required and in_ytd(e.date, year, quarter)
            )
        )
        warnings.append(
            "Parsed local expense PDFs are used only for reconciliation because --xolo-expense-ledger is set; "
            f"ignored parsed expense deductible before 5%: {format_es(ignored_local)} EUR"
        )
        return [e for e in income_entries + non_reviewed if not e.review_required and e.kind != "expense"]
    return [e for e in income_entries + expense_entries + non_reviewed if not e.review_required]


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--xolo-root", type=Path, required=False)
    parser.add_argument("--year", type=int, required=False)
    parser.add_argument("--quarter", type=int, choices=[1, 2, 3, 4], required=False)
    parser.add_argument("--target-report", type=Path, required=False)


def _load_config(path: Path | None) -> dict:
    if path is None:
        return {}
    if yaml is None:
        raise SystemExit("PyYAML is required to use --config")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _merge_config(args: argparse.Namespace, config: dict) -> None:
    review = config.get("review") or {}
    if hasattr(args, "asset_review_threshold_eur") and getattr(args, "asset_review_threshold_eur", None) in (None, ""):
        threshold = review.get("asset_review_threshold_eur")
        if threshold not in (None, ""):
            setattr(args, "asset_review_threshold_eur", str(threshold))
    for key in (
        "xolo_root",
        "year",
        "quarter",
        "target_report",
        "manual_ledger",
        "xolo_expense_ledger",
        "difficult_expenses_policy",
    ):
        if hasattr(args, key) and getattr(args, key, None) in (None, "") and key in config:
            setattr(args, key, config[key])
    if hasattr(args, "fx_rate") and not args.fx_rate:
        fx = config.get("fx_rates") or {}
        args.fx_rate = [f"{currency}={rate}" for currency, rate in fx.items() if rate not in (None, "")]
    required = []
    if getattr(args, "command", None) in {"modelo130", "extract-invoices", "extract-expenses"}:
        required = ["xolo_root"]
    if getattr(args, "command", None) == "modelo130":
        required += ["year", "quarter"]
    if getattr(args, "command", None) == "xolo-ledger" and getattr(args, "xolo_ledger_command", None) == "reconcile":
        required = ["xolo_root", "year", "quarter"]
    missing = [key for key in required if getattr(args, key, None) in (None, "")]
    if missing:
        raise SystemExit(f"Missing required option(s): {', '.join('--' + m.replace('_', '-') for m in missing)}")


def _parse_fx_rates(values: list[str]) -> dict[str, Decimal]:
    rates: dict[str, Decimal] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"Invalid --fx-rate {value!r}; expected CURRENCY=RATE")
        currency, raw_rate = value.split("=", 1)
        rates[currency.upper()] = parse_rate(raw_rate)
    return rates


def _load_manual_ledger(path: Path) -> list[LedgerEntry]:
    if not path.exists():
        raise SystemExit(f"Manual ledger not found: {path}")
    entries: list[LedgerEntry] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            entry_date = date.fromisoformat(row["date"]) if row.get("date") else None
            amount_original = _optional_amount(row.get("amount_original"))
            amount_eur = _optional_amount(row.get("amount_eur"))
            deductible_eur = _optional_amount(row.get("deductible_eur"))
            currency = (row.get("currency") or "EUR").upper()
            if amount_eur is None and currency == "EUR":
                amount_eur = amount_original
            if deductible_eur is None and (row.get("kind") or "expense") == "expense":
                deductible_eur = amount_eur
            reviewed = (row.get("review_required") or "no").lower() in {"yes", "true", "1"}
            if not reviewed and (amount_eur is None or amount_eur == 0):
                raise SystemExit(f"Manual ledger row must have non-zero amount_eur when review_required=no: {row}")
            if not reviewed and (row.get("kind") or "expense") == "expense" and (deductible_eur is None or deductible_eur == 0):
                raise SystemExit(f"Manual ledger expense row must have non-zero deductible_eur when review_required=no: {row}")
            entries.append(
                LedgerEntry(
                    kind=row.get("kind") or "expense",
                    date=entry_date,
                    document=row.get("document") or str(path),
                    counterparty=row.get("counterparty") or "Manual",
                    description=row.get("description") or "",
                    amount_original=amount_original,
                    currency=currency,
                    amount_eur=amount_eur,
                    deductible_eur=deductible_eur,
                    category=row.get("category") or "manual",
                    confidence=row.get("confidence") or "manual",
                    review_required=reviewed,
                    notes=row.get("notes") or f"Manual ledger: {path}",
                )
            )
    return entries


def _optional_amount(raw: str | None) -> Decimal | None:
    if raw is None or raw == "":
        return None
    return parse_amount(raw)


def _build_manifest(args: argparse.Namespace, xolo_root: Path, out_dir: Path, target_present: bool) -> dict[str, object]:
    files: list[dict[str, object]] = []
    for subdir in ("INVOICE", "EXPENSE", "TAX_REPORT"):
        root = xolo_root / subdir
        if not root.exists():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            files.append(
                {
                    "path": str(path),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    covered = {item["path"] for item in files}
    aux_inputs: list[dict[str, object]] = []
    for value in (args.target_report, args.manual_ledger, getattr(args, "xolo_expense_ledger", None)):
        if not value:
            continue
        path = Path(value)
        if path.exists() and str(path) not in covered:
            aux_inputs.append({"path": str(path), "size": path.stat().st_size, "sha256": _sha256(path)})
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tool_git_head_at_run": _git_commit(),
        "tool_git_dirty": _git_dirty(),
        "year": args.year,
        "quarter": args.quarter,
        "xolo_root": str(xolo_root),
        "target_report": str(args.target_report) if args.target_report else None,
        "target_present": target_present,
        "manual_ledger": str(args.manual_ledger) if args.manual_ledger else None,
        "xolo_expense_ledger": str(args.xolo_expense_ledger) if getattr(args, "xolo_expense_ledger", None) else None,
        "fx_rate_args": list(getattr(args, "fx_rate", []) or []),
        "derive_target_fx": bool(getattr(args, "derive_target_fx", False)),
        "asset_review_threshold_eur": str(getattr(args, "asset_review_threshold_eur", "") or ""),
        "difficult_expenses_policy": str(getattr(args, "difficult_expenses_policy", "") or ""),
        "out_dir": str(out_dir),
        "input_files": files,
        "aux_input_files": aux_inputs,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            text=True,
            capture_output=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def _git_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            text=True,
            capture_output=True,
        )
        return bool(result.stdout.strip())
    except Exception:
        return None


def _manual_relevant_to_run(entry: LedgerEntry, year: int, quarter: int) -> bool:
    if in_ytd(entry.date, year, quarter):
        return True
    name = Path(entry.document).name
    return str(year) in name


def _difficult_expenses_rate_for_year(year: int) -> Decimal:
    return Decimal("0.07") if year == 2023 else Decimal("0.05")


if __name__ == "__main__":
    sys.exit(main())
