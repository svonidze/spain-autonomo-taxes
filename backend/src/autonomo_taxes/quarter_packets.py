from __future__ import annotations

import csv
from pathlib import Path

from .money import format_es, parse_amount
from .source_book_wording import source_book_wording


def build_quarter_packets(
    history_audit_csv: Path,
    quarter_closure_csv: Path,
    row_audit_csv: Path,
    source_findings_csv: Path | None = None,
    root_cause_narrowing_csv: Path | None = None,
    row_decisions_csv: Path | None = None,
    quarter_balance_bridge_csv: Path | None = None,
) -> list[dict[str, object]]:
    history_by_period = {
        f"{row['year']}-Q{row['quarter']}": row
        for row in _load_rows(history_audit_csv)
    }
    closure_rows = _load_rows(quarter_closure_csv)
    audit_by_period = _group_by_period(_load_rows(row_audit_csv))
    source_findings_by_period = _group_by_period(_load_rows(source_findings_csv)) if source_findings_csv else {}
    root_cause_by_period = {
        row["period"]: row
        for row in _load_rows(root_cause_narrowing_csv)
    } if root_cause_narrowing_csv else {}
    row_decisions_by_period = _group_by_period(_load_rows(row_decisions_csv)) if row_decisions_csv else {}
    balance_bridge_by_period = {
        row["period"]: row
        for row in _load_rows(quarter_balance_bridge_csv)
    } if quarter_balance_bridge_csv else {}

    packets: list[dict[str, object]] = []
    for closure in closure_rows:
        period = closure["period"]
        history = history_by_period.get(period, {})
        packet_rows = audit_by_period.get(period, [])
        root_cause = root_cause_by_period.get(period, {})
        row_decisions = row_decisions_by_period.get(period, [])
        balance_bridge = balance_bridge_by_period.get(period, {})
        packets.append(
            {
                "period": period,
                "filename": f"{period}.md",
                "history": history,
                "closure": closure,
                "root_cause": root_cause,
                "row_decisions": row_decisions,
                "balance_bridge": balance_bridge,
                "rows": packet_rows,
                "source_findings": source_findings_by_period.get(period, []),
                "markdown": _packet_markdown(
                    period,
                    history,
                    closure,
                    root_cause,
                    row_decisions,
                    balance_bridge,
                    packet_rows,
                    source_findings_by_period.get(period, []),
                ),
            }
        )
    return packets


def write_quarter_packets(out_dir: Path, packets: list[dict[str, object]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for packet in packets:
        (out_dir / str(packet["filename"])).write_text(str(packet["markdown"]), encoding="utf-8")
    (out_dir / "index.md").write_text(_index_markdown(packets), encoding="utf-8")


def _packet_markdown(
    period: str,
    history: dict[str, str],
    closure: dict[str, str],
    root_cause: dict[str, str],
    row_decisions: list[dict[str, str]],
    balance_bridge: dict[str, str],
    rows: list[dict[str, str]],
    source_findings: list[dict[str, str]] | None = None,
) -> str:
    xolo_rows = [row for row in rows if row.get("row_kind") == "xolo_expense"]
    synthetic_rows = [row for row in rows if row.get("row_kind") == "synthetic_gap"]
    asset_rows = [row for row in xolo_rows if row.get("classification") == "asset_amortization_candidate"]
    nearest_rows = [row for row in xolo_rows if row.get("classification") == "nearest_exclusion_candidate"]
    ordinary_rows = [row for row in xolo_rows if row not in asset_rows and row not in nearest_rows]

    lines = [
        f"# Modelo 130 {period} Verification Packet",
        "",
        "This packet is for sequential review of one submitted quarter.",
        "It is not closed until the Xolo source books and any required asset schedule evidence are recorded.",
        "",
        "## Submitted Declaration",
        "",
        f"- Report: `{history.get('report', '')}`",
        f"- Casilla 01: `{_fmt(history.get('target_casilla_01', ''))}`",
        f"- Casilla 02 YTD: `{_fmt(history.get('target_casilla_02', ''))}`",
        f"- Casilla 02 quarter delta: `{_fmt(closure.get('target_casilla_02_delta', ''))}`",
        f"- Casilla 19: `{_fmt(history.get('target_casilla_19', ''))}`",
        "",
        "## Closure Status",
        "",
        f"- Status: `{closure['closure_status']}`",
        f"- Required Xolo evidence: {closure['required_xolo_evidence']}",
        f"- Next Xolo question: {closure['next_question']}",
        "",
        "## Reconciliation Signal",
        "",
        f"- Pre-plug model minus target: `{_fmt(closure['pre_plug_model_minus_target_eur'])}`",
        f"- Balancing adjustment: `{_fmt(closure['balancing_adjustment_eur'])}` ({closure['balancing_adjustment_pct_of_target']}% of quarter target delta, `{closure['balancing_adjustment_materiality']}`)",
        f"- Candidate amortization delta: `{_fmt(closure['candidate_amortization_delta'])}`",
        f"- Excluded asset direct amount: `{_fmt(closure['excluded_asset_direct_eur'])}`",
        f"- Nearest excluded subset: `{_fmt(closure['excluded_nearest_subset_eur'])}`",
        "",
        "## Balance Bridge",
        "",
    ]
    lines.extend(_balance_bridge_table(balance_bridge))
    lines.extend(
        [
            "",
            "## Root-Cause Gates",
            "",
        ]
    )
    lines.extend(_root_cause_table(root_cause))
    lines.extend(
        [
            "",
            "## Open Row Decisions",
            "",
        ]
    )
    lines.extend(_row_decisions_table(row_decisions))
    lines.extend(
        [
            "",
            "## Local Source Checks",
            "",
        ]
    )
    lines.extend(_source_findings_table(source_findings or []))
    lines.extend(
        [
            "",
            "## Asset Candidates",
            "",
        ]
    )
    lines.extend(_rows_table(asset_rows))
    lines.extend(["", "## Nearest Exclusion Candidates", ""])
    lines.extend(_rows_table(nearest_rows))
    lines.extend(["", "## Other Raw Xolo Rows", ""])
    lines.extend(_rows_table(ordinary_rows))
    lines.extend(["", "## Synthetic Gap Rows", ""])
    lines.extend(_rows_table(synthetic_rows))
    lines.extend(
        [
            "",
            "## Acceptance Criteria For This Quarter",
            "",
            "- Xolo source-book row set is recorded.",
            "- Every local raw row is marked included, excluded, netted, reversed, or used on another basis.",
            "- Any asset amortization amount is tied to a confirmed asset schedule.",
            "- Confirmed register delta equals submitted casilla 02 quarter delta within 0.02 EUR.",
            "",
        ]
    )
    return "\n".join(lines)


def _index_markdown(packets: list[dict[str, object]]) -> str:
    lines = [
        "# Modelo 130 Quarter Verification Packets",
        "",
        "Use these packets in chronological order, starting from `2023-Q2`.",
        "A packet is a working checklist, not proof that the quarter is closed.",
        "",
        "| Period | Status | Target 02 delta | Balance | Materiality | Packet | Next question |",
        "|---|---|---:|---:|---|---|---|",
    ]
    for packet in packets:
        closure = packet["closure"]
        assert isinstance(closure, dict)
        filename = str(packet["filename"])
        lines.append(
            "| "
            + " | ".join(
                [
                    closure["period"],
                    closure["closure_status"],
                    _fmt(closure["target_casilla_02_delta"]),
                    _fmt(closure["balancing_adjustment_eur"]),
                    closure["balancing_adjustment_materiality"],
                    f"[{filename}]({filename})",
                    _cell(closure["next_question"]),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def _rows_table(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["none"]
    lines = [
        "| Classification | Date | Number | Recipient | Gross EUR | Base EUR | Notes |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row.get("classification", ""),
                    row.get("date", ""),
                    _cell(row.get("number", "")),
                    _cell(row.get("recipient", "")),
                    _fmt(row.get("gross_eur", "")),
                    _fmt(row.get("base_eur", "")),
                    _cell(row.get("notes", "")),
                ]
            )
            + " |"
        )
    return lines


def _root_cause_table(row: dict[str, str]) -> list[str]:
    if not row:
        return ["not supplied"]
    return [
        "| Annual-constrained residual | M303 VAT | Annual professional base diff | Eliminated locally | Remaining causes |",
        "|---:|---|---:|---|---|",
        "| "
        + " | ".join(
            [
                _fmt(row.get("annual_constrained_residual", "")),
                _cell(row.get("modelo303_vat_status", "")),
                _fmt(row.get("annual_professional_base_diff", "")),
                _cell(row.get("eliminated_causes", "")),
                _cell(row.get("remaining_causes", "")),
            ]
        )
        + " |",
    ]


def _balance_bridge_table(row: dict[str, str]) -> list[str]:
    if not row:
        return ["not supplied"]
    return [
        "| Equation | Annual balance | Signal |",
        "|---|---:|---|",
        "| "
        + " | ".join(
            [
                f"`{_cell(row.get('equation', ''))}`",
                _fmt(row.get("annual_constrained_balance_to_target", "")),
                _cell(row.get("balance_signal", "")),
            ]
        )
        + " |",
        "",
        "| Raw non-asset | Annual-constrained amortization | Excluded/netted | Required evidence |",
        "|---:|---:|---:|---|",
        "| "
        + " | ".join(
            [
                _fmt(row.get("raw_non_asset_delta", "")),
                _fmt(row.get("annual_constrained_amortization_delta", "")),
                _fmt(row.get("nearest_excluded_or_netted_eur", "")),
                _cell(row.get("required_xolo_evidence", "")),
            ]
        )
        + " |",
    ]


def _row_decisions_table(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["not supplied"]
    lines = [
        "| Priority | Decision | Row | Amount | Question | Context |",
        "|---|---|---|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row.get("priority", "")),
                    _cell(row.get("decision_kind", "")),
                    _cell(_decision_row_label(row)),
                    _fmt(row.get("amount_eur", "")),
                    _cell(row.get("question", "")),
                    _cell(row.get("root_cause_context", "")),
                ]
            )
            + " |"
        )
    return lines


def _source_findings_table(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["none"]
    lines = [
        "| Row Ref | Status | Source Document | Finding | Impact |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row.get("row_ref", "")),
                    _cell(row.get("source_status", "")),
                    _cell(row.get("source_document", "")),
                    _cell(row.get("finding", "")),
                    _cell(row.get("impact", "")),
                ]
            )
            + " |"
        )
    return lines


def _decision_row_label(row: dict[str, str]) -> str:
    parts = [row.get("date", ""), row.get("number", ""), row.get("recipient", "")]
    value = " ".join(part for part in parts if part)
    return value or row.get("classification", "")


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _group_by_period(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row.get("period", ""), []).append(row)
    return grouped


def _fmt(value: str | None) -> str:
    if not value:
        return ""
    return format_es(parse_amount(value))


def _cell(value: str) -> str:
    return source_book_wording(value).replace("|", "\\|").replace("\n", " ")
