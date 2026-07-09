from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from .asset_gap_matrix import ASSET_GAP_SIGNAL_PRIORITY
from .money import format_es, parse_amount
from .source_book_wording import source_book_wording


def build_xolo_closure_request(
    quarter_closure_csv: Path,
    source_findings_csv: Path | None = None,
    row_decisions_csv: Path | None = None,
    root_cause_narrowing_csv: Path | None = None,
    evidence_inventory_csv: Path | None = None,
    local_attention_bridge_csv: Path | None = None,
    material_gap_drilldown_csv: Path | None = None,
    material_gap_context_csv: Path | None = None,
    p1_near_fit_context_csv: Path | None = None,
    p2_near_target_context_csv: Path | None = None,
    asset_gap_matrix_csv: Path | None = None,
) -> str:
    rows = _load_rows(quarter_closure_csv)
    material = [row for row in rows if row["closure_status"] == "blocked_material_unexplained_adjustment"]
    asset = [row for row in rows if row["has_asset_decision"] == "yes"]
    exclusions = [row for row in rows if row["has_nearest_exclusion"] == "yes"]
    source_findings = _source_finding_highlights(source_findings_csv) if source_findings_csv else []
    row_decisions = _row_decision_highlights(row_decisions_csv) if row_decisions_csv else []
    root_cause_rows = _root_cause_highlights(root_cause_narrowing_csv) if root_cause_narrowing_csv else []
    inventory = _inventory_summary(evidence_inventory_csv) if evidence_inventory_csv else {}
    local_attention_rows = _local_attention_highlights(local_attention_bridge_csv) if local_attention_bridge_csv else []
    material_gap_rows = _material_gap_highlights(material_gap_drilldown_csv) if material_gap_drilldown_csv else []
    material_context_rows = _material_context_highlights(material_gap_context_csv) if material_gap_context_csv else []
    p1_context_rows = _p1_context_highlights(p1_near_fit_context_csv) if p1_near_fit_context_csv else []
    p2_context_rows = _p2_context_highlights(p2_near_target_context_csv) if p2_near_target_context_csv else []
    asset_gap_rows = _asset_gap_highlights(asset_gap_matrix_csv) if asset_gap_matrix_csv else []

    lines = [
        "# Xolo Modelo 130 Closure Request",
        "",
        "Draft-only artifact. Do not send automatically.",
        "",
        "## Request To Xolo",
        "",
        "Hello,",
        "",
        "I am reconstructing the submitted Modelo 130 declarations from 2023-Q2 through 2026-Q2 and need the source-book basis that Xolo used.",
        "The expense UI export is not sufficient because it does not show the `libro registro de compras y gastos`, `libro registro de bienes de inversión`, quarterly tie-outs, row inclusion, amortization, or deductible basis per row.",
        "",
        "Please provide:",
        "",
        "1. The `libro registro de compras y gastos` for 2023, 2024, 2025, and 2026 through 2T, with date, booking date/period, supplier, invoice number, category, original amount/currency, FX rate/date/source, deductible base, VAT treatment, EUR deductible amount used in casilla 02, and whether the row was included, excluded, netted, reversed, deferred, corrected, or adjusted.",
        "2. The `libro registro de bienes de inversión` / full asset amortization schedule used for Modelo 130 and annual Renta/Modelo 100: asset id, acquisition date, acquisition basis, VAT treatment, start date, method, amortization rate, quarterly amortization amount, accumulated amortization by quarter, catch-up flag, and incentive flag if any.",
        "3. Quarterly tie-outs from the source books to filed Modelo 130 casillas 01, 02, 03, and 07 for every quarter from 2023-Q2 through 2026-Q2.",
        "4. Please answer with source-book/register/schedule exports if available; local target-fitting arithmetic is only being used to route the questions and should not be treated as confirmed Xolo accounting.",
        "",
    ]
    if root_cause_rows:
        lines.extend(
            [
                "## Local Root-Cause Gates Already Checked",
                "",
                "These gates do not replace Xolo's source books. They show which local explanations have been narrowed so Xolo can answer the remaining accounting questions directly.",
                "",
                "| Period | Residual after annual asset lens | Annual professional base diff | Locally eliminated | Still open |",
                "|---|---:|---:|---|---|",
            ]
        )
        for row in root_cause_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["period"],
                        _fmt(row["annual_constrained_residual"]),
                        _fmt(row.get("annual_professional_base_diff", "")),
                        _cell(row["eliminated_causes"]),
                        _cell(row["remaining_causes"]),
                    ]
                )
                + " |"
            )
        lines.append("")

    if asset_gap_rows:
        signal_counts = _signal_counts(asset_gap_rows)
        lines.extend(
            [
                "## Asset/Register Split From Local Matrix",
                "",
                "The local matrix separates quarters where an asset schedule could explain the gap from quarters where row exclusions, netting, VAT/base treatment, or catch-up must also exist. This is why the requested Xolo answer needs source exports rather than a narrative explanation.",
                "",
                "- Signals: " + "; ".join(f"`{signal}`={count}" for signal, count in sorted(signal_counts.items())) + ".",
                "",
                "| Period | Signal | Required amort./catch-up | Annual-constrained amort. | Required - annual | Active assets | Required answer |",
                "|---|---|---:|---:|---:|---:|---|",
            ]
        )
        for row in asset_gap_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["period"],
                        _cell(row["amortization_gap_signal"]),
                        _fmt(row["required_amortization_or_catchup_delta"]),
                        _fmt(row["annual_constrained_amortization_delta"]),
                        _fmt(row["required_minus_annual_constrained_amortization"]),
                        row["active_asset_count"],
                        _cell(row["next_action"]),
                    ]
                )
                + " |"
            )
        lines.append("")

    lines.extend(
        [
            "## Material Quarter Gaps",
            "",
            "| Period | Target 02 delta | Pre-plug residual | Balancing adjustment | Plug % | Required answer |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in material:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["pre_plug_model_minus_target_eur"]),
                    _fmt(row["balancing_adjustment_eur"]),
                    row["balancing_adjustment_pct_of_target"] + "%",
                    _cell(row["next_question"]),
                ]
            )
            + " |"
        )

    if material_gap_rows:
        lines.extend(
            [
                "",
                "## Material Gap Drilldown",
                "",
                "These are local hypotheses for the material gaps. They are not confirmed filing treatment; they define the shortest questions for Xolo's source books.",
                "",
                "| Period | Hypothesis | Status | Bridge total | Residual to candidate | Residual to annual | Fit | Components | Xolo question |",
                "|---|---|---|---:|---:|---:|---|---|---|",
            ]
        )
        for row in material_gap_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["period"],
                        _cell(row["hypothesis_id"]),
                        _cell(row["hypothesis_status"]),
                        _fmt(row["hypothesis_total_eur"]),
                        _fmt(row["residual_to_candidate_balance_eur"]),
                        _fmt(row["residual_to_annual_balance_eur"]),
                        _cell(row["fit_signal"]),
                        _cell(row["components"]),
                        _cell(row["xolo_questions"]),
                    ]
                )
                + " |"
            )

    if material_context_rows:
        lines.extend(
            [
                "",
                "## P0 Raw Xolo Context",
                "",
                "These rows explain why the P0 questions are currently prioritized. They still require Xolo's source books and asset schedule before any quarter can be closed.",
                "",
                "| Period | Context signal | Xolo non-asset | Bridge raw non-asset | Target minus Xolo non-asset | Gap-sized asset rows | Asset fit | Xolo question |",
                "|---|---|---:|---:|---:|---|---|---|",
            ]
        )
        for row in material_context_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["period"],
                        _cell(row["context_signal"]),
                        _fmt(row["xolo_document_quarter_non_asset_gross_eur"]),
                        _fmt(row["bridge_raw_non_asset_delta"]),
                        _fmt(row["target_minus_xolo_non_asset_eur"]),
                        _cell(row["gap_sized_asset_candidate_rows"] or "none"),
                        _cell(row.get("gap_sized_asset_candidate_fit", "") or "none"),
                        _cell(row["next_xolo_question"]),
                    ]
                )
                + " |"
            )

    if p1_context_rows:
        lines.extend(
            [
                "",
                "## P1 Near-Fit Context",
                "",
                "These are the strongest local arithmetic forks after the P0 material gaps. Please confirm the source-book row set and basis rather than treating these fits as proof.",
                "",
                "| Period | Signal | Target | Model | Diff | Components | Xolo question |",
                "|---|---|---:|---:|---:|---|---|",
            ]
        )
        for row in p1_context_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["period"],
                        _cell(row["context_signal"]),
                        _fmt(row["target_delta_eur"]),
                        _fmt(row["model_amount_eur"]),
                        _fmt(row["diff_to_target_eur"]),
                        _cell(row["components"]),
                        _cell(row["xolo_question"]),
                    ]
                )
                + " |"
            )

    if p2_context_rows:
        lines.extend(
            [
                "",
                "## P2 Near-Target Context",
                "",
                "These are lower-priority near-target forks that still require Xolo's source books and asset schedule. Please confirm row inclusion and deductible basis rather than treating the arithmetic fits as proof.",
                "",
                "| Period | Signal | Row | Target | Annual balance | Amortization | Excluded/netted | Finding | Impact |",
                "|---|---|---|---:|---:|---:|---:|---|---|",
            ]
        )
        for row in p2_context_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["period"],
                        _cell(row["context_signal"]),
                        _cell(row["row_ref"]),
                        _fmt(row["target_casilla_02_delta"]),
                        _fmt(row["annual_constrained_balance_to_target"]),
                        _fmt(row["annual_constrained_amortization_delta"]),
                        _fmt(row["nearest_excluded_or_netted_eur"]),
                        _cell(row["finding"]),
                        _cell(row["impact"]),
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## Asset Schedule Rows To Confirm",
            "",
            "| Period | Candidate amortization | Asset rows |",
            "|---|---:|---|",
        ]
    )
    for row in asset:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    _fmt(row["candidate_amortization_delta"]),
                    _cell(row["asset_rows"] or "no direct asset row this quarter; schedule carry-forward"),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Excluded Or Netted Candidate Rows",
            "",
            "| Period | Candidate rows |",
            "|---|---|",
        ]
    )
    for row in exclusions:
        lines.append("| " + row["period"] + " | " + _cell(row["nearest_exclusion_rows"]) + " |")

    if row_decisions:
        lines.extend(
            [
                "",
                "## Concrete Row-Level Decisions",
                "",
                "These are the shortest row-level checks needed to close the remaining residuals. Please answer with the source-book deductible EUR amount and treatment for each row.",
                "",
                "| Priority | Period | Decision | Row | Amount | Question | Context |",
                "|---|---|---|---|---:|---|---|",
            ]
        )
        for row in row_decisions:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["priority"],
                        row["period"],
                        _cell(row["decision_kind"]),
                        _cell(_row_label(row)),
                        _fmt(row["amount_eur"]),
                        _cell(row["question"]),
                        _cell(row.get("root_cause_context", "")),
                    ]
                )
                + " |"
            )

    if source_findings:
        lines.extend(
            [
                "",
                "## Source-Finding Highlights To Preserve",
                "",
                "These rows are already checked locally or deliberately marked as unconfirmed. Please do not treat a target-fitting hypothesis as a confirmed Xolo filing decision.",
                "",
                "| Period | Row | Status | Finding | Impact |",
                "|---|---|---|---|---|",
            ]
        )
        for row in source_findings:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["period"],
                        _cell(row["row_ref"]),
                        _cell(row["source_status"]),
                        _cell(row["finding"]),
                        _cell(row["impact"]),
                    ]
                )
                + " |"
            )

    if local_attention_rows:
        lines.extend(
            [
                "",
                "## Local Archive Attention Candidates",
                "",
                "These local files are not enough to close the returns, but they narrow the evidence questions for 2023/2024 residuals.",
                "",
                "| Priority | Period | Document | Signal | Effect | Question |",
                "|---|---|---|---|---:|---|",
            ]
        )
        for row in local_attention_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["priority"],
                        row["period"],
                        _cell(row["local_document"]),
                        _cell(row["gap_fit_signal"]),
                        _fmt(row.get("known_effect_eur") or row.get("known_local_amount_eur") or ""),
                        _cell(row["xolo_question"]),
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## Local Evidence Already Checked",
            "",
            "- Local Xolo export contains expense documents and submitted PDF declarations, but no source-book export for `compras y gastos` or `bienes de inversión`.",
            "- Local Xolo expense API snapshot contains UI expense rows only; it does not contain row-level Modelo 130 inclusion, booking period, FX/VAT basis, or asset amortization schedule.",
            "- Modelo 100 PDFs expose annual aggregate amortization, not the per-asset source-book schedule needed to close quarterly Modelo 130.",
        ]
    )
    if inventory:
        lines.append(
            "- Evidence inventory found "
            f"{inventory.get('modelo130_report', '0')} Modelo 130 reports, "
            f"{inventory.get('candidate_submitted_register', '0')} candidate source-book/register files, and "
            f"{inventory.get('candidate_asset_schedule', '0')} candidate asset/amortization schedule files."
        )
    lines.append("")
    return "\n".join(lines)


def write_xolo_closure_request(path: Path, markdown: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _source_finding_highlights(path: Path) -> list[dict[str, str]]:
    rows = _load_rows(path)
    return [
        row
        for row in rows
        if row.get("source_status", "").startswith("unconfirmed")
        or row.get("row_ref", "").startswith("scenario_")
    ]


def _row_decision_highlights(path: Path) -> list[dict[str, str]]:
    rows = _load_rows(path)
    priority_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        rows,
        key=lambda row: (
            priority_order.get(row.get("priority", ""), 9),
            row.get("period", ""),
            row.get("decision_kind", ""),
            row.get("date", ""),
            row.get("number", ""),
        ),
    )


def _root_cause_highlights(path: Path) -> list[dict[str, str]]:
    rows = _load_rows(path)
    return [
        row
        for row in rows
        if row.get("closure_status")
        in {
            "blocked_material_unexplained_adjustment",
            "pending_asset_schedule_confirmation",
            "pending_row_exclusion_confirmation",
        }
    ]


def _inventory_summary(path: Path) -> dict[str, str]:
    return {row["category"]: row["count"] for row in _load_rows(path)}


def _local_attention_highlights(path: Path) -> list[dict[str, str]]:
    rows = _load_rows(path)
    priority_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        [row for row in rows if row.get("priority") in {"high", "medium"}],
        key=lambda row: (
            priority_order.get(row.get("priority", ""), 9),
            row.get("period", ""),
            row.get("local_document", ""),
        ),
    )


def _material_gap_highlights(path: Path) -> list[dict[str, str]]:
    rows = [
        row
        for row in _load_rows(path)
        if row.get("hypothesis_status") != "ruled_out_local_hypothesis"
    ]
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault((row["period"], row["hypothesis_id"]), []).append(row)
    status_order = {
        "unresolved_required_evidence": 0,
        "strong_candidate_pending_confirmation": 1,
        "partial_candidate_not_in_xolo_raw": 2,
        "unresolved_side_components": 3,
    }
    highlights: list[dict[str, str]] = []
    for (period, hypothesis_id), group in grouped.items():
        first = group[0]
        components = []
        questions = []
        for row in group:
            effect = _fmt(row.get("effect_closes_gap_eur", ""))
            counts = row.get("counts_in_best_bridge", "")
            components.append(f"{row['component']} ({effect}, counts={counts})")
            question = row.get("xolo_question", "")
            if question and question not in questions:
                questions.append(question)
        highlights.append(
            {
                "period": period,
                "hypothesis_id": hypothesis_id,
                "hypothesis_status": first["hypothesis_status"],
                "hypothesis_total_eur": first["hypothesis_total_eur"],
                "residual_to_candidate_balance_eur": first["residual_to_candidate_balance_eur"],
                "residual_to_annual_balance_eur": first["residual_to_annual_balance_eur"],
                "fit_signal": first["fit_signal"],
                "components": "; ".join(components),
                "xolo_questions": "; ".join(questions),
            }
        )
    return sorted(
        highlights,
        key=lambda row: (
            row["period"],
            status_order.get(row["hypothesis_status"], 9),
            row["hypothesis_id"],
        ),
    )


def _material_context_highlights(path: Path) -> list[dict[str, str]]:
    return sorted(_load_rows(path), key=lambda row: row["period"])


def _p1_context_highlights(path: Path) -> list[dict[str, str]]:
    return sorted(_load_rows(path), key=lambda row: row["period"])


def _p2_context_highlights(path: Path) -> list[dict[str, str]]:
    return sorted(_load_rows(path), key=lambda row: (row["period"], row["context_signal"], row["row_ref"]))


def _asset_gap_highlights(path: Path) -> list[dict[str, str]]:
    rows = _load_rows(path)
    return sorted(
        rows,
        key=lambda row: (
            ASSET_GAP_SIGNAL_PRIORITY.get(row.get("amortization_gap_signal", ""), 99),
            row.get("period", ""),
        ),
    )


def _signal_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    return dict(Counter(row["amortization_gap_signal"] for row in rows))


def _row_label(row: dict[str, str]) -> str:
    parts = [row.get("date", ""), row.get("number", ""), row.get("recipient", "")]
    value = " ".join(part for part in parts if part)
    return value or row.get("classification", "")


def _fmt(value: str) -> str:
    return "" if value == "" else format_es(parse_amount(value))


def _cell(value: str) -> str:
    return source_book_wording(value).replace("|", "\\|").replace("\n", " ")
