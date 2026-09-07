from __future__ import annotations

import csv
from pathlib import Path


TARGET_VALUES_COVERAGE_FIELDS = [
    "period",
    "status",
    "report",
    "history_status",
    "xolo_compare_status",
    "target_values",
    "evidence",
    "next_action",
]

REQUIRED_HISTORY_FIELDS = [
    "target_casilla_01",
    "target_casilla_02",
    "target_casilla_02_delta",
    "target_casilla_07",
    "target_casilla_13",
    "target_casilla_19",
]


def build_target_values_coverage(
    *,
    tax_report_sequence_csv: Path,
    history_audit_csv: Path,
    xolo_calculation_compare_csv: Path,
) -> list[dict[str, str]]:
    sequence_rows = [row for row in _load_rows(tax_report_sequence_csv) if row.get("period") != "sequence_verdict"]
    history_by_period = {
        f"{row.get('year', '')}-Q{row.get('quarter', '')}": row
        for row in _load_rows(history_audit_csv)
        if row.get("year") and row.get("quarter")
    }
    compare_by_period = {
        row.get("period", ""): row
        for row in _load_rows(xolo_calculation_compare_csv)
        if row.get("period")
    }

    rows = [_period_row(sequence, history_by_period, compare_by_period) for sequence in sequence_rows]
    rows.append(_verdict_row(rows))
    return rows


def write_target_values_coverage_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TARGET_VALUES_COVERAGE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_target_values_coverage_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    verdict = next((row for row in rows if row["period"] == "target_values_verdict"), {})
    period_rows = [row for row in rows if row["period"] != "target_values_verdict"]
    open_rows = [row for row in period_rows if row["status"] != "matched"]
    lines = [
        "# Modelo 130 Target Values Coverage",
        "",
        "This report verifies that every filed Modelo 130 PDF in scope has extracted target casillas and matching Xolo calculation evidence.",
        "It does not prove source-book row treatment or asset amortization.",
        "",
        "## Verdict",
        "",
        f"- Status: `{verdict.get('status', 'unknown')}`.",
        f"- Periods checked: `{len(period_rows)}`.",
        f"- Periods needing attention: `{len(open_rows)}`.",
        f"- Next action: {verdict.get('next_action', '')}",
        "",
        "## Periods",
        "",
        "| Period | Status | Report | History | Xolo compare | Target values | Next action |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in period_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["period"]),
                    _cell(row["status"]),
                    _cell(row["report"]),
                    _cell(row["history_status"]),
                    _cell(row["xolo_compare_status"]),
                    _cell(row["target_values"]),
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _period_row(
    sequence: dict[str, str],
    history_by_period: dict[str, dict[str, str]],
    compare_by_period: dict[str, dict[str, str]],
) -> dict[str, str]:
    period = sequence.get("period", "")
    history = history_by_period.get(period, {})
    compare = compare_by_period.get(period, {})
    history_status, missing_history = _history_status(history)
    compare_status = _compare_status(compare)
    if sequence.get("status") != "found":
        status = "missing_filed_pdf"
        next_action = sequence.get("next_action", "Fix filed PDF sequence.")
    elif history_status != "complete":
        status = "missing_history_targets"
        next_action = "Regenerate audit-history and verify target casilla extraction from the filed PDF."
    elif compare_status != "matched":
        status = "xolo_compare_not_matched"
        next_action = "Refresh Xolo calculation compare or inspect the mismatched submitted target values."
    else:
        status = "matched"
        next_action = "Use these target values as the submitted quarter targets; source-book evidence remains separate."
    return {
        "period": period,
        "status": status,
        "report": history.get("report") or sequence.get("matched_files", ""),
        "history_status": history_status,
        "xolo_compare_status": compare_status,
        "target_values": _target_values(history),
        "evidence": _evidence(sequence, history, compare, missing_history),
        "next_action": next_action,
    }


def _verdict_row(rows: list[dict[str, str]]) -> dict[str, str]:
    matched = [row for row in rows if row["status"] == "matched"]
    if rows and len(matched) == len(rows):
        status = "complete"
        next_action = "Target casilla extraction is complete; continue with source-book and amortization gates."
    else:
        status = "attention_required"
        next_action = f"Resolve {len(rows) - len(matched)} period(s) before relying on submitted target values."
    return {
        "period": "target_values_verdict",
        "status": status,
        "report": "",
        "history_status": "",
        "xolo_compare_status": "",
        "target_values": "",
        "evidence": f"{len(matched)}/{len(rows)} periods matched.",
        "next_action": next_action,
    }


def _history_status(row: dict[str, str]) -> tuple[str, list[str]]:
    if not row:
        return "missing", REQUIRED_HISTORY_FIELDS
    missing = [field for field in REQUIRED_HISTORY_FIELDS if not row.get(field)]
    return ("complete" if not missing else "missing_fields", missing)


def _compare_status(row: dict[str, str]) -> str:
    if not row:
        return "missing"
    if row.get("xolo_status") != "submitted":
        return row.get("xolo_status") or "not_submitted"
    return row.get("comparison_status") or "missing_status"


def _target_values(row: dict[str, str]) -> str:
    if not row:
        return ""
    return "; ".join(
        [
            f"01={row.get('target_casilla_01', '')}",
            f"02={row.get('target_casilla_02', '')}",
            f"02_delta={row.get('target_casilla_02_delta', '')}",
            f"07={row.get('target_casilla_07', '')}",
            f"13={row.get('target_casilla_13', '')}",
            f"19={row.get('target_casilla_19', '')}",
        ]
    )


def _evidence(
    sequence: dict[str, str],
    history: dict[str, str],
    compare: dict[str, str],
    missing_history: list[str],
) -> str:
    parts = [
        f"sequence_status={sequence.get('status', '')}",
        f"history_report={history.get('report', '')}",
        f"xolo_filename={compare.get('xolo_filename', '')}",
        f"income_diff={compare.get('income_ytd_diff', '')}",
        f"expenses_diff={compare.get('expenses_ytd_diff', '')}",
        f"payable_diff={compare.get('payable_diff', '')}",
    ]
    if missing_history:
        parts.append("missing_history_fields=" + ",".join(missing_history))
    return "; ".join(parts)


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
