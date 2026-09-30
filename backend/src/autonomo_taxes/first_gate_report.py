from __future__ import annotations

import csv
from pathlib import Path

from .money import format_es, parse_amount
from .source_book_wording import source_book_wording


FIRST_GATE_FIELDS = [
    "section",
    "period",
    "key",
    "status",
    "amount_eur",
    "fit_signal",
    "evidence_status",
    "finding",
    "source_ref",
    "xolo_question",
]


def build_first_gate_report(
    chronological_walkthrough_csv: Path,
    material_gap_drilldown_csv: Path,
    material_gap_context_csv: Path | None = None,
    source_findings_csv: Path | None = None,
) -> list[dict[str, str]]:
    chronological_rows = _load_rows(chronological_walkthrough_csv)
    first_gate = _first_unclosed(chronological_rows)
    if first_gate is None:
        return []
    period = first_gate["period"]
    rows: list[dict[str, str]] = [_gate_row(first_gate)]
    if material_gap_context_csv:
        rows.extend(_context_rows(period, _load_rows(material_gap_context_csv)))
    rows.extend(_hypothesis_rows(period, _load_rows(material_gap_drilldown_csv)))
    if source_findings_csv:
        rows.extend(_source_rows(period, _load_rows(source_findings_csv)))
    return rows


def write_first_gate_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIRST_GATE_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: source_book_wording(row.get(field, "")) for field in FIRST_GATE_FIELDS})


def write_first_gate_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text(
            "# Modelo 130 First Gate Report\n\nNo unclosed chronological gate was found.\n",
            encoding="utf-8",
        )
        return
    gate = next((row for row in rows if row["section"] == "gate"), None)
    if gate is None:
        path.write_text(
            "# Modelo 130 First Gate Report\n\nNo unclosed chronological gate was found.\n",
            encoding="utf-8",
        )
        return
    context_rows = [row for row in rows if row["section"] == "raw_context"]
    hypothesis_rows = [row for row in rows if row["section"] == "hypothesis"]
    source_rows = [row for row in rows if row["section"] == "source_finding"]
    unresolved = [
        row
        for row in hypothesis_rows
        if row["status"] in {"unresolved_required_evidence", "strong_candidate_pending_confirmation"}
    ]
    ruled_out = [row for row in hypothesis_rows if row["status"].startswith("ruled_out")]
    partial = [row for row in hypothesis_rows if row["status"].startswith("partial")]
    lines = [
        "# Modelo 130 First Gate Report",
        "",
        f"Period: `{gate['period']}`",
        "",
        "This report isolates the first unclosed chronological quarter. It is not filing advice and it does not close the quarter without Xolo's source books.",
        "",
        "## Gate",
        "",
        f"- Chronological gate: `{gate['key']}`.",
        f"- Target `casilla 02` delta: `{_fmt(gate['amount_eur'])}`.",
        f"- Local verdict: {_cell(gate['finding'])}",
        f"- Blocking evidence: {_cell(gate['evidence_status'])}",
        "",
    ]
    if context_rows:
        context = context_rows[0]
        lines.extend(
            [
                "## Raw Context",
                "",
                f"- Raw non-asset model: `{_fmt(context['amount_eur'])}`.",
                f"- Target minus raw non-asset: `{_fmt(context['fit_signal'])}`.",
                f"- Gap-sized asset/direct-expense row: {_cell(context['finding'])}",
                f"- Asset fit note: {_cell(context['status'])}",
                "",
            ]
        )
    lines.extend(
        [
            "## Local Hypothesis Verdicts",
            "",
            "| Hypothesis | Status | Amount | Fit | Evidence | Finding |",
            "|---|---|---:|---|---|---|",
        ]
    )
    for row in hypothesis_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["key"]),
                    row["status"],
                    _fmt(row["amount_eur"]),
                    _cell(row["fit_signal"]),
                    _cell(row["evidence_status"]),
                    _cell(row["finding"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"- Unresolved evidence-required hypotheses: `{', '.join(row['key'] for row in unresolved) if unresolved else 'none'}`.",
            f"- Partial local candidates that do not close the gate: `{', '.join(row['key'] for row in partial) if partial else 'none'}`.",
            f"- Locally ruled-out explanations: `{', '.join(row['key'] for row in ruled_out) if ruled_out else 'none'}`.",
            "- Local conclusion: this gate is not an arithmetic parser issue; it is a source-book/asset-treatment question.",
            "",
            "## Minimum Xolo Request",
            "",
        ]
    )
    for index, question in enumerate(_minimum_questions(gate, unresolved, context_rows), start=1):
        lines.append(f"{index}. {_cell(question)}")
    if source_rows:
        lines.extend(
            [
                "",
                "## Source Findings Already Preserved",
                "",
                "| Finding | Status | Source | Impact |",
                "|---|---|---|---|",
            ]
        )
        for row in source_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(row["key"]),
                        _cell(row["status"]),
                        _cell(row["source_ref"]),
                        _cell(row["finding"]),
                    ]
                )
                + " |"
            )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _first_unclosed(rows: list[dict[str, str]]) -> dict[str, str] | None:
    return next((row for row in rows if row.get("first_unclosed_period") == "yes"), None)


def _gate_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "section": "gate",
        "period": row["period"],
        "key": row.get("chronological_gate", ""),
        "status": row.get("closure_status", ""),
        "amount_eur": row.get("target_casilla_02_delta", ""),
        "fit_signal": row.get("asset_gap_signal", ""),
        "evidence_status": row.get("blocking_evidence", ""),
        "finding": row.get("local_verdict", ""),
        "source_ref": "runs/modelo130_chronological_walkthrough.csv",
        "xolo_question": row.get("next_action", ""),
    }


def _context_rows(period: str, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for row in rows:
        if row.get("period") != period:
            continue
        output.append(
            {
                "section": "raw_context",
                "period": period,
                "key": row.get("context_signal", ""),
                "status": row.get("gap_sized_asset_candidate_fit", ""),
                "amount_eur": row.get("bridge_raw_non_asset_delta", ""),
                "fit_signal": row.get("target_minus_xolo_non_asset_eur", ""),
                "evidence_status": "raw_xolo_context_not_source_book_evidence",
                "finding": row.get("gap_sized_asset_candidate_rows") or row.get("asset_candidate_rows", ""),
                "source_ref": "runs/modelo130_material_gap_context.csv",
                "xolo_question": row.get("next_xolo_question", ""),
            }
        )
    return output


def _hypothesis_rows(period: str, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for row in rows:
        if row.get("period") != period:
            continue
        output.append(
            {
                "section": "hypothesis",
                "period": period,
                "key": row.get("hypothesis_id", ""),
                "status": row.get("hypothesis_status", ""),
                "amount_eur": row.get("effect_closes_gap_eur", ""),
                "fit_signal": row.get("fit_signal", ""),
                "evidence_status": row.get("evidence_status", ""),
                "finding": row.get("notes", ""),
                "source_ref": row.get("source_ref", ""),
                "xolo_question": row.get("xolo_question", ""),
            }
        )
    return sorted(output, key=_hypothesis_sort_key)


def _source_rows(period: str, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for row in rows:
        if row.get("period") != period:
            continue
        output.append(
            {
                "section": "source_finding",
                "period": period,
                "key": row.get("row_ref", ""),
                "status": row.get("source_status", ""),
                "amount_eur": "",
                "fit_signal": "",
                "evidence_status": row.get("source_status", ""),
                "finding": row.get("impact", ""),
                "source_ref": row.get("source_document", ""),
                "xolo_question": "",
            }
        )
    return output


def _minimum_questions(
    gate: dict[str, str],
    unresolved: list[dict[str, str]],
    context_rows: list[dict[str, str]],
) -> list[str]:
    period = gate["period"]
    questions = [
        f"For {period}, provide the source-book tie-out from deductible expense rows to filed Modelo 130 casilla 02.",
    ]
    if context_rows:
        questions.append(
            f"For {period}, confirm source-book treatment for the gap-sized asset/direct-expense row: {context_rows[0]['finding']}."
        )
    questions.extend(row["xolo_question"] for row in unresolved if row["xolo_question"])
    if gate["xolo_question"]:
        questions.append(gate["xolo_question"])
    return _dedupe(questions)


def _hypothesis_sort_key(row: dict[str, str]) -> tuple[int, str]:
    status_order = {
        "unresolved_required_evidence": 0,
        "strong_candidate_pending_confirmation": 1,
        "partial_candidate_not_in_xolo_raw": 2,
        "ruled_out_local_hypothesis": 3,
    }
    return status_order.get(row["status"], 9), row["key"]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _fmt(value: str) -> str:
    return format_es(parse_amount(value)) if value else ""


def _cell(value: str) -> str:
    return source_book_wording(value).replace("|", "\\|").replace("\n", " ")
