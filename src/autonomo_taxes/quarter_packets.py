from __future__ import annotations

import csv
from pathlib import Path

from .money import format_es, parse_amount


def build_quarter_packets(
    history_audit_csv: Path,
    quarter_closure_csv: Path,
    row_audit_csv: Path,
    source_findings_csv: Path | None = None,
) -> list[dict[str, object]]:
    history_by_period = {
        f"{row['year']}-Q{row['quarter']}": row
        for row in _load_rows(history_audit_csv)
    }
    closure_rows = _load_rows(quarter_closure_csv)
    audit_by_period = _group_by_period(_load_rows(row_audit_csv))
    source_findings_by_period = _group_by_period(_load_rows(source_findings_csv)) if source_findings_csv else {}

    packets: list[dict[str, object]] = []
    for closure in closure_rows:
        period = closure["period"]
        history = history_by_period.get(period, {})
        packet_rows = audit_by_period.get(period, [])
        packets.append(
            {
                "period": period,
                "filename": f"{period}.md",
                "history": history,
                "closure": closure,
                "rows": packet_rows,
                "source_findings": source_findings_by_period.get(period, []),
                "markdown": _packet_markdown(
                    period,
                    history,
                    closure,
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
        "It is not closed until the Xolo submitted register and any required asset schedule evidence are recorded.",
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
        "## Local Source Checks",
        "",
    ]
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
            "- Xolo submitted register row set is recorded.",
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
    return value.replace("|", "\\|").replace("\n", " ")
