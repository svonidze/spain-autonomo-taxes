from __future__ import annotations

from collections import Counter
import csv
from dataclasses import dataclass
from pathlib import Path

from .goal_status import build_goal_status, write_goal_status_csv, write_goal_status_markdown
from .quarter_acceptance import (
    build_quarter_acceptance_matrix,
    write_quarter_acceptance_csv,
    write_quarter_acceptance_markdown,
)
from .source_book_content_check import (
    build_source_book_content_check,
    write_source_book_content_check_csv,
    write_source_book_content_check_markdown,
)
from .source_book_import import (
    build_source_book_import,
    write_source_book_import_csv,
    write_source_book_import_markdown,
)
from .source_book_reconcile import (
    build_source_book_reconciliation,
    write_source_book_reconciliation_csv,
    write_source_book_reconciliation_markdown,
)
from .source_book_response_check import (
    build_source_book_response_check,
    write_source_book_response_check_csv,
    write_source_book_response_check_markdown,
)


SOURCE_BOOK_REFRESH_FIELDS = [
    "step",
    "status",
    "rows",
    "csv",
    "markdown",
    "evidence",
    "next_action",
]


@dataclass(frozen=True)
class _RefreshPaths:
    response_root: Path
    runs_root: Path
    quarter_acceptance_csv: Path
    history_audit_csv: Path
    quarter_closure_csv: Path
    quarter_balance_bridge_csv: Path
    material_gap_drilldown_csv: Path
    packets_dir: Path
    tax_report_sequence_csv: Path
    target_values_coverage_csv: Path
    first_gate_answer_check_csv: Path
    source_book_response_check_csv: Path
    source_book_response_check_md: Path
    source_book_content_check_csv: Path
    source_book_content_check_md: Path
    source_book_rows_csv: Path
    source_book_rows_md: Path
    source_book_reconciliation_csv: Path
    source_book_reconciliation_md: Path
    quarter_acceptance_md: Path
    goal_status_csv: Path
    goal_status_md: Path


def run_source_book_refresh(
    *,
    response_root: Path,
    runs_root: Path,
    quarter_acceptance_csv: Path | None = None,
    history_audit_csv: Path | None = None,
    quarter_closure_csv: Path | None = None,
    quarter_balance_bridge_csv: Path | None = None,
    material_gap_drilldown_csv: Path | None = None,
    packets_dir: Path | None = None,
    tax_report_sequence_csv: Path | None = None,
    target_values_coverage_csv: Path | None = None,
    first_gate_answer_check_csv: Path | None = None,
) -> list[dict[str, str]]:
    paths = _refresh_paths(
        response_root=response_root,
        runs_root=runs_root,
        quarter_acceptance_csv=quarter_acceptance_csv,
        history_audit_csv=history_audit_csv,
        quarter_closure_csv=quarter_closure_csv,
        quarter_balance_bridge_csv=quarter_balance_bridge_csv,
        material_gap_drilldown_csv=material_gap_drilldown_csv,
        packets_dir=packets_dir,
        tax_report_sequence_csv=tax_report_sequence_csv,
        target_values_coverage_csv=target_values_coverage_csv,
        first_gate_answer_check_csv=first_gate_answer_check_csv,
    )
    paths.runs_root.mkdir(parents=True, exist_ok=True)

    summary: list[dict[str, str]] = []

    response_rows = build_source_book_response_check(
        response_root=paths.response_root,
        quarter_acceptance_csv=paths.quarter_acceptance_csv,
    )
    write_source_book_response_check_csv(paths.source_book_response_check_csv, response_rows)
    write_source_book_response_check_markdown(paths.source_book_response_check_md, response_rows)
    summary.append(
        _verdict_summary(
            step="source_book_response_check",
            rows=response_rows,
            verdict_key="package_verdict",
            verdict_field="check",
            csv_path=paths.source_book_response_check_csv,
            markdown_path=paths.source_book_response_check_md,
        )
    )

    content_rows = build_source_book_content_check(
        response_root=paths.response_root,
        source_book_response_check_csv=paths.source_book_response_check_csv,
    )
    write_source_book_content_check_csv(paths.source_book_content_check_csv, content_rows)
    write_source_book_content_check_markdown(paths.source_book_content_check_md, content_rows)
    summary.append(
        _verdict_summary(
            step="source_book_content_check",
            rows=content_rows,
            verdict_key="content_verdict",
            verdict_field="check",
            csv_path=paths.source_book_content_check_csv,
            markdown_path=paths.source_book_content_check_md,
        )
    )

    imported_rows = build_source_book_import(
        response_root=paths.response_root,
        source_book_content_check_csv=paths.source_book_content_check_csv,
    )
    write_source_book_import_csv(paths.source_book_rows_csv, imported_rows)
    write_source_book_import_markdown(paths.source_book_rows_md, imported_rows)
    summary.append(_import_summary(imported_rows, paths.source_book_rows_csv, paths.source_book_rows_md))

    reconciliation_rows = build_source_book_reconciliation(paths.history_audit_csv, paths.source_book_rows_csv)
    write_source_book_reconciliation_csv(paths.source_book_reconciliation_csv, reconciliation_rows)
    write_source_book_reconciliation_markdown(paths.source_book_reconciliation_md, reconciliation_rows)
    summary.append(
        _counted_summary(
            step="source_book_reconciliation",
            rows=reconciliation_rows,
            status_field="status",
            complete_status={"rows_and_tieout_match_target", "rows_match_target_no_tieout"},
            complete_label="complete",
            incomplete_label="not_reconciled",
            csv_path=paths.source_book_reconciliation_csv,
            markdown_path=paths.source_book_reconciliation_md,
            next_action="Resolve official-register import/reconciliation rows until every quarter matches Xolo's filed casilla 02.",
        )
    )

    acceptance_rows = build_quarter_acceptance_matrix(
        paths.quarter_closure_csv,
        paths.quarter_balance_bridge_csv,
        paths.material_gap_drilldown_csv,
        paths.packets_dir,
        paths.source_book_reconciliation_csv,
    )
    write_quarter_acceptance_csv(paths.quarter_acceptance_csv, acceptance_rows)
    write_quarter_acceptance_markdown(paths.quarter_acceptance_md, acceptance_rows)
    summary.append(
        _counted_summary(
            step="quarter_acceptance",
            rows=acceptance_rows,
            status_field="acceptance_status",
            complete_status="accepted_from_source_books",
            complete_label="complete",
            incomplete_label="not_closed",
            csv_path=paths.quarter_acceptance_csv,
            markdown_path=paths.quarter_acceptance_md,
            next_action="Keep asking Xolo for official registers until every quarter is accepted.",
        )
    )

    goal_rows = build_goal_status(
        tax_report_sequence_csv=paths.tax_report_sequence_csv,
        target_values_coverage_csv=paths.target_values_coverage_csv,
        quarter_acceptance_csv=paths.quarter_acceptance_csv,
        first_gate_answer_check_csv=paths.first_gate_answer_check_csv,
        source_book_response_check_csv=paths.source_book_response_check_csv,
        source_book_content_check_csv=paths.source_book_content_check_csv,
        source_book_reconciliation_csv=paths.source_book_reconciliation_csv,
    )
    write_goal_status_csv(paths.goal_status_csv, goal_rows)
    write_goal_status_markdown(paths.goal_status_md, goal_rows)
    summary.append(
        _verdict_summary(
            step="goal_status",
            rows=goal_rows,
            verdict_key="goal_verdict",
            verdict_field="gate",
            csv_path=paths.goal_status_csv,
            markdown_path=paths.goal_status_md,
        )
    )

    return summary


def write_source_book_refresh_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_BOOK_REFRESH_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_source_book_refresh_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    verdict = rows[-1] if rows else {}
    lines = [
        "# Xolo Source-Book Refresh",
        "",
        "This report is the one-command intake trail after Xolo provides source-book exports.",
        "It treats Xolo official registers and investment-goods amortization rows as evidence; it does not infer accounting treatment from target fitting.",
        "",
        "## Verdict",
        "",
        f"- Status: `{verdict.get('status', 'unknown')}`.",
        f"- Next action: {verdict.get('next_action', '')}",
        "",
        "## Steps",
        "",
        "| Step | Status | Rows | Evidence | CSV | Markdown | Next action |",
        "|---|---|---:|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["step"]),
                    _cell(row["status"]),
                    _cell(row["rows"]),
                    _cell(row["evidence"]),
                    _cell(row["csv"]),
                    _cell(row["markdown"]),
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _refresh_paths(
    *,
    response_root: Path,
    runs_root: Path,
    quarter_acceptance_csv: Path | None,
    history_audit_csv: Path | None,
    quarter_closure_csv: Path | None,
    quarter_balance_bridge_csv: Path | None,
    material_gap_drilldown_csv: Path | None,
    packets_dir: Path | None,
    tax_report_sequence_csv: Path | None,
    target_values_coverage_csv: Path | None,
    first_gate_answer_check_csv: Path | None,
) -> _RefreshPaths:
    return _RefreshPaths(
        response_root=response_root,
        runs_root=runs_root,
        quarter_acceptance_csv=quarter_acceptance_csv or runs_root / "modelo130_quarter_acceptance.csv",
        history_audit_csv=history_audit_csv or runs_root / "modelo130_history_audit.csv",
        quarter_closure_csv=quarter_closure_csv or runs_root / "modelo130_quarter_closure.csv",
        quarter_balance_bridge_csv=quarter_balance_bridge_csv or runs_root / "modelo130_quarter_balance_bridge.csv",
        material_gap_drilldown_csv=material_gap_drilldown_csv or runs_root / "modelo130_material_gap_drilldown.csv",
        packets_dir=packets_dir or runs_root / "modelo130_quarter_packets",
        tax_report_sequence_csv=tax_report_sequence_csv or runs_root / "modelo130_tax_report_sequence.csv",
        target_values_coverage_csv=target_values_coverage_csv or runs_root / "modelo130_target_values_coverage.csv",
        first_gate_answer_check_csv=first_gate_answer_check_csv or runs_root / "modelo130_first_gate_answer_check.csv",
        source_book_response_check_csv=runs_root / "xolo_source_book_response_check.csv",
        source_book_response_check_md=runs_root / "xolo_source_book_response_check.md",
        source_book_content_check_csv=runs_root / "xolo_source_book_content_check.csv",
        source_book_content_check_md=runs_root / "xolo_source_book_content_check.md",
        source_book_rows_csv=runs_root / "xolo_source_book_rows.csv",
        source_book_rows_md=runs_root / "xolo_source_book_rows.md",
        source_book_reconciliation_csv=runs_root / "xolo_source_book_reconciliation.csv",
        source_book_reconciliation_md=runs_root / "xolo_source_book_reconciliation.md",
        quarter_acceptance_md=runs_root / "modelo130_quarter_acceptance.md",
        goal_status_csv=runs_root / "modelo130_goal_status.csv",
        goal_status_md=runs_root / "modelo130_goal_status.md",
    )


def _verdict_summary(
    *,
    step: str,
    rows: list[dict[str, str]],
    verdict_key: str,
    verdict_field: str,
    csv_path: Path,
    markdown_path: Path,
) -> dict[str, str]:
    verdict = next((row for row in rows if row.get(verdict_field) == verdict_key), {})
    return {
        "step": step,
        "status": verdict.get("status", "missing"),
        "rows": str(sum(1 for row in rows if row.get(verdict_field) != verdict_key)),
        "csv": str(csv_path),
        "markdown": str(markdown_path),
        "evidence": _verdict_evidence(verdict),
        "next_action": verdict.get("next_action", ""),
    }


def _import_summary(rows: list[dict[str, str]], csv_path: Path, markdown_path: Path) -> dict[str, str]:
    counts = Counter(row.get("import_status", "") for row in rows)
    clean = bool(rows) and set(counts) == {"imported"}
    status = "imported" if clean else "no_rows_imported" if not rows else "import_attention"
    return {
        "step": "source_book_import",
        "status": status,
        "rows": str(len(rows)),
        "csv": str(csv_path),
        "markdown": str(markdown_path),
        "evidence": _counts_evidence(counts),
        "next_action": "Reconcile imported source-book rows against filed Modelo 130 targets.",
    }


def _counted_summary(
    *,
    step: str,
    rows: list[dict[str, str]],
    status_field: str,
    complete_status: str | set[str],
    complete_label: str,
    incomplete_label: str,
    csv_path: Path,
    markdown_path: Path,
    next_action: str,
) -> dict[str, str]:
    counts = Counter(row.get(status_field, "") for row in rows)
    complete_statuses = {complete_status} if isinstance(complete_status, str) else complete_status
    complete = bool(rows) and sum(counts.get(status, 0) for status in complete_statuses) == len(rows)
    return {
        "step": step,
        "status": complete_label if complete else incomplete_label,
        "rows": str(len(rows)),
        "csv": str(csv_path),
        "markdown": str(markdown_path),
        "evidence": _counts_evidence(counts),
        "next_action": "Continue to the next gate." if complete else next_action,
    }


def _verdict_evidence(verdict: dict[str, str]) -> str:
    parts = []
    for key in ("scope", "matched_count", "content_ready_count", "actual", "evidence"):
        value = verdict.get(key, "")
        if value:
            parts.append(f"{key}={value}")
    return "; ".join(parts)


def _counts_evidence(counts: Counter[str]) -> str:
    return "; ".join(f"{key}={value}" for key, value in sorted(counts.items()) if key)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
