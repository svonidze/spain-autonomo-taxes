from __future__ import annotations

from collections import Counter
import csv
from pathlib import Path


GOAL_STATUS_FIELDS = [
    "gate",
    "status",
    "expected",
    "actual",
    "evidence",
    "next_action",
]


def build_goal_status(
    *,
    tax_report_sequence_csv: Path,
    target_values_coverage_csv: Path,
    quarter_acceptance_csv: Path,
    first_gate_answer_check_csv: Path,
    source_book_response_check_csv: Path,
    source_book_content_check_csv: Path,
) -> list[dict[str, str]]:
    sequence_rows = _load_rows(tax_report_sequence_csv)
    target_rows = _load_rows(target_values_coverage_csv)
    acceptance_rows = _load_rows(quarter_acceptance_csv)
    first_gate_rows = _load_rows(first_gate_answer_check_csv)
    response_rows = _load_rows(source_book_response_check_csv)
    content_rows = _load_rows(source_book_content_check_csv)

    rows = [
        _tax_report_sequence_gate(sequence_rows),
        _target_values_gate(target_rows),
        _quarter_acceptance_gate(acceptance_rows),
        _scope_alignment_gate(sequence_rows, target_rows, acceptance_rows, response_rows, content_rows),
        _first_gate_answer_gate(first_gate_rows),
        _source_book_response_gate(response_rows),
        _source_book_content_gate(content_rows),
    ]
    rows.append(_goal_verdict(rows))
    return rows


def write_goal_status_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=GOAL_STATUS_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_goal_status_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    verdict = next((row for row in rows if row["gate"] == "goal_verdict"), {})
    lines = [
        "# Modelo 130 Goal Status",
        "",
        "This report aggregates the hard gates for the full chronological audit goal.",
        "It does not close any quarter by itself; it shows whether current evidence proves the requested end state.",
        "",
        "## Verdict",
        "",
        f"- Status: `{verdict.get('status', 'unknown')}`.",
        f"- Next action: {verdict.get('next_action', '')}",
        "",
        "## Gates",
        "",
        "| Gate | Status | Expected | Actual | Evidence | Next action |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        if row["gate"] == "goal_verdict":
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["gate"]),
                    _cell(row["status"]),
                    _cell(row["expected"]),
                    _cell(row["actual"]),
                    _cell(row["evidence"]),
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _tax_report_sequence_gate(rows: list[dict[str, str]]) -> dict[str, str]:
    period_rows = [row for row in rows if row.get("period") != "sequence_verdict"]
    verdict = next((row for row in rows if row.get("period") == "sequence_verdict"), {})
    first = period_rows[0] if period_rows else {}
    last = period_rows[-1] if period_rows else {}
    status = "complete" if verdict.get("status") == "complete" else "attention_required"
    return _row(
        "filed_pdf_sequence",
        status,
        "one submitted Modelo 130 PDF for every quarter in scope",
        f"{sum(1 for row in period_rows if row.get('status') == 'found')}/{len(period_rows)} found",
        f"first={first.get('matched_files', '')}; last={last.get('matched_files', '')}; verdict={verdict.get('status', '')}",
        "Continue to source-book gates." if status == "complete" else verdict.get("next_action", "Fix filed PDF sequence."),
    )


def _quarter_acceptance_gate(rows: list[dict[str, str]]) -> dict[str, str]:
    status_counts = Counter(row.get("acceptance_status", "") for row in rows)
    accepted_rows = [row for row in rows if row.get("acceptance_status", "").startswith("accepted")]
    open_rows = [row for row in rows if row not in accepted_rows]
    priorities = Counter(row.get("priority", "") for row in open_rows)
    status = "complete" if rows and len(accepted_rows) == len(rows) else "not_closed"
    evidence = "; ".join(f"{key}={value}" for key, value in sorted(status_counts.items()) if key)
    priority_text = "; ".join(f"{key}={value}" for key, value in sorted(priorities.items()) if key)
    return _row(
        "quarter_acceptance",
        status,
        "all quarters accepted from source-book and asset-schedule evidence",
        f"{len(rows) - len(open_rows)}/{len(rows)} accepted",
        f"statuses: {evidence}; open priorities: {priority_text}",
        "Import Xolo source books and rerun quarter acceptance." if open_rows else "No quarter-acceptance action needed.",
    )


def _target_values_gate(rows: list[dict[str, str]]) -> dict[str, str]:
    period_rows = [row for row in rows if row.get("period") != "target_values_verdict"]
    verdict = next((row for row in rows if row.get("period") == "target_values_verdict"), {})
    matched = [row for row in period_rows if row.get("status") == "matched"]
    complete = verdict.get("status") == "complete" and bool(period_rows) and len(matched) == len(period_rows)
    status = "complete" if complete else "attention_required"
    return _row(
        "target_values_coverage",
        status,
        "all filed quarters have extracted target casillas matching Xolo calculation evidence",
        f"{len(matched)}/{len(period_rows)} matched",
        f"verdict={verdict.get('status', '')}; {verdict.get('evidence', '')}",
        "Continue to quarter acceptance gates." if status == "complete" else verdict.get("next_action", "Fix target values coverage."),
    )


def _scope_alignment_gate(
    sequence_rows: list[dict[str, str]],
    target_rows: list[dict[str, str]],
    acceptance_rows: list[dict[str, str]],
    response_rows: list[dict[str, str]],
    content_rows: list[dict[str, str]],
) -> dict[str, str]:
    sequence_periods = [row.get("period", "") for row in sequence_rows if row.get("period") != "sequence_verdict"]
    target_periods = [row.get("period", "") for row in target_rows if row.get("period") != "target_values_verdict"]
    acceptance_periods = [row.get("period", "") for row in acceptance_rows if row.get("period")]
    response_verdict = next((row for row in response_rows if row.get("check") == "package_verdict"), {})
    content_verdict = next((row for row in content_rows if row.get("check") == "content_verdict"), {})
    expected_scope = _period_scope(sequence_periods)
    target_scope = _period_scope(target_periods)
    acceptance_scope = _period_scope(acceptance_periods)
    response_scope = response_verdict.get("scope", "")
    content_scope = content_verdict.get("scope", "")
    aligned = (
        bool(sequence_periods)
        and sequence_periods == target_periods
        and sequence_periods == acceptance_periods
        and response_scope == expected_scope
        and content_scope == expected_scope
    )
    return _row(
        "scope_alignment",
        "complete" if aligned else "scope_mismatch",
        "tax-report sequence, target values, quarter acceptance, source-book response, and source-book content checks cover the same quarters",
        f"sequence={expected_scope}; targets={target_scope}; acceptance={acceptance_scope}; response={response_scope}; content={content_scope}",
        f"sequence_periods={len(sequence_periods)}; target_periods={len(target_periods)}; acceptance_periods={len(acceptance_periods)}",
        "Continue to evidence gates." if aligned else "Regenerate gate inputs for the same quarter range before relying on goal status.",
    )


def _first_gate_answer_gate(rows: list[dict[str, str]]) -> dict[str, str]:
    verdict = next((row for row in rows if row.get("check") == "closure_verdict"), {})
    status = verdict.get("status", "missing")
    return _row(
        "first_gate_answer_check",
        status,
        "ready_for_rebuild",
        status,
        verdict.get("evidence", ""),
        verdict.get("next_action", "Regenerate first-gate answer check."),
    )


def _source_book_response_gate(rows: list[dict[str, str]]) -> dict[str, str]:
    verdict = next((row for row in rows if row.get("check") == "package_verdict"), {})
    status = verdict.get("status", "missing")
    missing = [row for row in rows if row.get("status") == "missing"]
    return _row(
        "source_book_response_package",
        status,
        "ready_for_intake",
        f"{verdict.get('matched_count', '0')} matched deliverables; {len(missing)} missing",
        f"scope={verdict.get('scope', '')}; required_for={verdict.get('required_for', '')}",
        verdict.get("next_action", "Run source-book response check."),
    )


def _source_book_content_gate(rows: list[dict[str, str]]) -> dict[str, str]:
    verdict = next((row for row in rows if row.get("check") == "content_verdict"), {})
    period_rows = [row for row in rows if row.get("check") != "content_verdict"]
    ready = [row for row in period_rows if row.get("status") == "content_ready"]
    status = verdict.get("status", "missing")
    return _row(
        "source_book_content_check",
        status,
        "ready_for_import",
        f"{len(ready)}/{len(period_rows)} content-ready deliverables",
        f"scope={verdict.get('scope', '')}; {verdict.get('evidence', '')}",
        verdict.get("next_action", "Run source-book content check."),
    )


def _goal_verdict(rows: list[dict[str, str]]) -> dict[str, str]:
    by_gate = {row["gate"]: row for row in rows}
    sequence_ok = by_gate.get("filed_pdf_sequence", {}).get("status") == "complete"
    targets_ok = by_gate.get("target_values_coverage", {}).get("status") == "complete"
    quarters_ok = by_gate.get("quarter_acceptance", {}).get("status") == "complete"
    first_gate_ok = by_gate.get("first_gate_answer_check", {}).get("status") == "ready_for_rebuild"
    response_ok = by_gate.get("source_book_response_package", {}).get("status") == "ready_for_intake"
    content_ok = by_gate.get("source_book_content_check", {}).get("status") == "ready_for_import"
    scope_ok = by_gate.get("scope_alignment", {}).get("status") == "complete"
    if sequence_ok and targets_ok and quarters_ok and first_gate_ok and response_ok and content_ok and scope_ok:
        status = "complete"
        next_action = "All hard gates are green; perform final human review before relying on the audit."
    elif not scope_ok:
        status = "not_complete_scope_mismatch"
        next_action = by_gate.get("scope_alignment", {}).get("next_action", "Regenerate gate inputs for the same scope.")
    elif sequence_ok and not targets_ok:
        status = "not_complete_target_values_attention"
        next_action = by_gate.get("target_values_coverage", {}).get("next_action", "Fix target values coverage.")
    elif sequence_ok and not response_ok:
        status = "not_complete_waiting_for_xolo_books"
        next_action = "Send the Xolo source-book request and place the response under evidence/xolo-source-books."
    elif sequence_ok and response_ok and not content_ok:
        status = "not_complete_source_book_content_attention"
        next_action = by_gate.get("source_book_content_check", {}).get("next_action", "Fix source-book content check.")
    elif not sequence_ok:
        status = "not_complete_missing_filed_reports"
        next_action = by_gate.get("filed_pdf_sequence", {}).get("next_action", "Fix filed PDF sequence.")
    else:
        status = "not_complete_rebuild_required"
        next_action = "Apply confirmed Xolo source-book answers, rebuild the first gate, then rerun quarter acceptance."
    return _row(
        "goal_verdict",
        status,
        "all quarters verified one by one with amortization/source-book evidence",
        "; ".join(f"{row['gate']}={row['status']}" for row in rows),
        "Hard gates aggregate the filed-PDF baseline, target values, scope, quarter acceptance, first-gate answer check, source-book package, and source-book content.",
        next_action,
    )


def _row(gate: str, status: str, expected: str, actual: str, evidence: str, next_action: str) -> dict[str, str]:
    return {
        "gate": gate,
        "status": status,
        "expected": expected,
        "actual": actual,
        "evidence": evidence,
        "next_action": next_action,
    }


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _period_scope(periods: list[str]) -> str:
    if not periods:
        return ""
    return f"{periods[0]}..{periods[-1]}"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
