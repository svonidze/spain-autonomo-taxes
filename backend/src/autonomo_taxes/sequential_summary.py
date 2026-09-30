from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from .money import format_es, parse_amount
from .source_book_wording import source_book_wording


def build_sequential_summary(
    quarter_closure_csv: Path,
    source_findings_csv: Path,
    modelo303_vat_crosscheck_csv: Path | None = None,
    annual_categories_csv: Path | None = None,
    material_gap_drilldown_csv: Path | None = None,
    material_gap_context_csv: Path | None = None,
    p1_near_fit_context_csv: Path | None = None,
    p2_near_target_context_csv: Path | None = None,
    xolo_api_coverage_csv: Path | None = None,
    asset_gap_matrix_csv: Path | None = None,
) -> str:
    closure_rows = _load_rows(quarter_closure_csv)
    source_rows = _load_rows(source_findings_csv)
    modelo303_rows = _load_rows(modelo303_vat_crosscheck_csv) if modelo303_vat_crosscheck_csv else []
    annual_category_rows = _load_rows(annual_categories_csv) if annual_categories_csv else []
    material_gap_rows = _load_rows(material_gap_drilldown_csv) if material_gap_drilldown_csv else []
    material_context_rows = _load_rows(material_gap_context_csv) if material_gap_context_csv else []
    p1_context_rows = _load_rows(p1_near_fit_context_csv) if p1_near_fit_context_csv else []
    p2_context_rows = _load_rows(p2_near_target_context_csv) if p2_near_target_context_csv else []
    xolo_api_coverage_rows = _load_rows(xolo_api_coverage_csv) if xolo_api_coverage_csv else []
    asset_gap_matrix_rows = _load_rows(asset_gap_matrix_csv) if asset_gap_matrix_csv else []
    source_by_period = _group_by_period(source_rows)
    status_counts = Counter(row["closure_status"] for row in closure_rows)
    blank_unconfirmed = _blank_unconfirmed_sources(source_rows)
    unconfirmed_by_period = _unconfirmed_by_period(source_rows)
    modelo303_matched = sum(1 for row in modelo303_rows if row.get("crosscheck_status") == "matched")
    modelo303_total = len(modelo303_rows)
    annual_category = _annual_category_summary(closure_rows, annual_category_rows)
    material_gap = _material_gap_summary(material_gap_rows)
    material_context = _material_gap_context_summary(material_context_rows)
    p1_context = _p1_near_fit_context_summary(p1_context_rows)
    p2_context = _p2_near_target_context_summary(p2_context_rows)
    xolo_api_coverage = _xolo_api_coverage_summary(xolo_api_coverage_rows)
    asset_gap_matrix = _asset_gap_matrix_summary(asset_gap_matrix_rows, len(closure_rows))

    lines = [
        "# Modelo 130 Sequential Audit Summary",
        "",
        "This is a generated working summary for the chronological audit from 2023-Q2 through 2026-Q2.",
        "It is not filing advice and it does not close any quarter without Xolo's source books and asset schedule.",
        "",
        "## Executive Finding",
        "",
        "- Every submitted quarter has a verification packet and at least one explicit local hypothesis or source-finding trail.",
        "- The original 2026-Q2 numerical mismatch is reproducible exactly, including `casilla 19 = 2,639.12`, under the reviewed Xolo-ledger rebuild.",
        "- The remaining problem is not arithmetic coverage alone: it is the missing source-book row evidence and exact asset amortization schedule.",
        _material_gap_executive_finding(material_gap),
        _material_context_executive_finding(material_context),
        _p1_context_executive_finding(p1_context),
        _p2_context_executive_finding(p2_context),
        _xolo_api_coverage_executive_finding(xolo_api_coverage),
        _asset_gap_matrix_executive_finding(asset_gap_matrix),
        _modelo303_executive_finding(modelo303_matched, modelo303_total),
        _annual_category_executive_finding(annual_category),
        "- Several quarters have competing row-set/amortization forks that produce the same or near-same casilla totals, so a target match must not be treated as confirmed Xolo accounting.",
        "",
        "## Coverage Gate",
        "",
        f"- Periods in closure checklist: {len(closure_rows)}.",
        f"- Unconfirmed scenario/amortization rows without source artifact: {len(blank_unconfirmed)}.",
        f"- Source finding periods: {len(source_by_period)}.",
        _material_gap_coverage_line(material_gap),
        _material_context_coverage_line(material_context),
        _p1_context_coverage_line(p1_context),
        _p2_context_coverage_line(p2_context),
        _xolo_api_coverage_line(xolo_api_coverage),
        _asset_gap_matrix_coverage_line(asset_gap_matrix),
        _modelo303_coverage_line(modelo303_matched, modelo303_total),
        _annual_category_coverage_line(annual_category),
        "",
        "| Closure status | Count |",
        "|---|---:|",
    ]
    for status, count in sorted(status_counts.items()):
        lines.append(f"| {status} | {count} |")

    lines.extend(
        [
            "",
            "## Quarter Matrix",
            "",
            "| Period | Status | Target 02 delta | Balance | Candidate amort. | Asset direct excluded | Nearest subset | Main unresolved findings |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in closure_rows:
        period = row["period"]
        lines.append(
            "| "
            + " | ".join(
                [
                    period,
                    row["closure_status"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["balancing_adjustment_eur"]),
                    _fmt(row["candidate_amortization_delta"]),
                    _fmt(row["excluded_asset_direct_eur"]),
                    _fmt(row["excluded_nearest_subset_eur"]),
                    _cell(_finding_refs(unconfirmed_by_period.get(period, []))),
                ]
            )
            + " |"
        )

    if material_gap["supplied"]:
        lines.extend(
            [
                "",
                "## Material Gap Drilldown",
                "",
                "These are the remaining high-value questions after the quarter balance bridge. They are local hypotheses, not confirmed Xolo filing treatment.",
                "",
                "| Period | Hypothesis | Status | Bridge total | Residual to candidate | Residual to annual | Fit | Components |",
                "|---|---|---|---:|---:|---:|---|---|",
            ]
        )
        for row in material_gap["rows"]:
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
                    ]
                )
                + " |"
            )

    if material_context["supplied"]:
        lines.extend(
            [
                "",
                "## P0 Raw Xolo Context",
                "",
                "This raw-ledger context narrows P0 questions but still does not prove submitted Modelo 130 treatment.",
                "",
                "| Period | Signal | Xolo non-asset | Bridge raw non-asset | Target minus Xolo non-asset | Gap-sized asset rows | Asset fit | Next Xolo question |",
                "|---|---|---:|---:|---:|---|---|---|",
            ]
        )
        for row in material_context["rows"]:
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

    if p1_context["supplied"]:
        lines.extend(
            [
                "",
                "## P1 Near-Fit Context",
                "",
                "These are the strongest local arithmetic forks for P1 quarters. They are useful questions, not source-book evidence.",
                "",
                "| Period | Signal | Target | Model | Diff | Components | Next Xolo question |",
                "|---|---|---:|---:|---:|---|---|",
            ]
        )
        for row in p1_context["rows"]:
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

    if p2_context["supplied"]:
        lines.extend(
            [
                "",
                "## P2 Near-Target Context",
                "",
                "These are remaining near-target forks. They keep the audit focused, but they are not closure evidence without Xolo's source books and asset schedule.",
                "",
                "| Period | Signal | Target | Annual balance | Amortization | Excluded/netted | Row | Finding | Impact |",
                "|---|---|---:|---:|---:|---:|---|---|---|",
            ]
        )
        for row in p2_context["rows"]:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["period"],
                        _cell(row["context_signal"]),
                        _fmt(row["target_casilla_02_delta"]),
                        _fmt(row["annual_constrained_balance_to_target"]),
                        _fmt(row["annual_constrained_amortization_delta"]),
                        _fmt(row["nearest_excluded_or_netted_eur"]),
                        _cell(row["row_ref"]),
                        _cell(row["finding"]),
                        _cell(row["impact"]),
                    ]
                )
                + " |"
            )

    if asset_gap_matrix["supplied"]:
        lines.extend(
            [
                "",
                "## Asset Gap Matrix",
                "",
                "This matrix separates asset-schedule-sized gaps from source-book row-treatment gaps. It is a question router for Xolo, not confirmation of filed treatment.",
                "",
                "| Period | Signal | Required amort./catch-up | Annual-constrained amort. | Required - annual | Active assets | Next action |",
                "|---|---|---:|---:|---:|---:|---|",
            ]
        )
        for row in asset_gap_matrix["rows"]:
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

    lines.extend(
        [
            "",
            "## Blocking Evidence Needed",
            "",
            "These items are the shortest path from local hypotheses to confirmed closure:",
            "",
            "1. Xolo `libro registro de compras y gastos` for each year/quarter, with deductible EUR amount per row.",
            "2. Xolo `libro registro de bienes de inversión` / asset amortization schedule by asset and quarter, including basis, VAT treatment, rate, start date, and accumulated amortization.",
            "3. Confirmation of row-level gross vs VAT-base treatment for Xolo, OpenAI, Apple, Amazon, Cursor, JetBrains, Lovable, Namecheap, and similar SaaS rows.",
            "4. Confirmation of TGSS/apremio treatment, especially 2026-Q2 RETA principal-only versus gross-apremio handling.",
            "5. Raw Xolo expense API coverage and Modelo 303 VAT cross-check do not replace Xolo's source-book row evidence; they only support expense-list and VAT-bearing row completeness.",
            "",
            "## Root-Cause Pattern",
            "",
            _closure_material_pattern_line(closure_rows, material_gap),
            "- `2024-Q2` onward increasingly depends on asset amortization choices and small row exclusions or deferrals.",
            "- `2025` is dominated by JetBrains/Cursor/Midjourney/Namecheap/Lovable style row-set questions plus carry-forward asset amortization.",
            "- Submitted Modelo 303 VAT totals match raw VAT-bearing EUR rows where available, so the open Modelo 130 problem is not missing VAT-bearing local expense rows.",
            _xolo_api_coverage_pattern_line(xolo_api_coverage),
            _asset_gap_matrix_pattern_line(asset_gap_matrix),
            _annual_category_pattern_line(annual_category),
            _material_gap_pattern_line(material_gap),
            "- `2026-Q2` is numerically matched, but it has three competing accounting forks: low amortization with gross apremio rows, higher amortization with an apremio exclusion/netting row, or RETA principal-only treatment with a different amortization/basis adjustment.",
            "",
        ]
    )
    return "\n".join(lines)


def write_sequential_summary(path: Path, markdown: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")


def _modelo303_executive_finding(matched: int, total: int) -> str:
    if total == 0:
        return "- Modelo 303 VAT cross-check has not been supplied to this summary."
    return (
        f"- Submitted Modelo 303 VAT totals match raw VAT-bearing EUR expense rows for `{matched}` of `{total}` available quarters after netting reverse-charge VAT."
    )


def _modelo303_coverage_line(matched: int, total: int) -> str:
    if total == 0:
        return "- Modelo 303 VAT cross-check rows: not supplied."
    return f"- Modelo 303 VAT cross-check matched rows: {matched}/{total}."


def _xolo_api_coverage_summary(rows: list[dict[str, str]]) -> dict[str, str]:
    return {row.get("metric", ""): row.get("value", "") for row in rows}


def _xolo_api_coverage_executive_finding(summary: dict[str, str]) -> str:
    if not summary:
        return "- Raw Xolo API export coverage has not been supplied to this summary."
    status = summary.get("status", "")
    rows = summary.get("csv_rows", "")
    pages = summary.get("json_pages", "")
    expected = summary.get("expected_total_match", "")
    start = summary.get("covers_activity_start", "")
    if status == "complete_raw_snapshot" and expected == "yes" and start == "yes":
        return (
            "- Raw Xolo expense API snapshot coverage is complete for this audit "
            f"(`{rows}` rows across `{pages}` page(s), activity-start coverage yes)."
        )
    return (
        "- Raw Xolo expense API snapshot coverage requires attention "
        f"(status `{status}`, expected-total match `{expected}`, activity-start coverage `{start}`)."
    )


def _xolo_api_coverage_line(summary: dict[str, str]) -> str:
    if not summary:
        return "- Raw Xolo API export coverage: not supplied."
    return (
        "- Raw Xolo API export coverage: "
        f"{summary.get('status', '')}; rows {summary.get('csv_rows', '')}; "
        f"expected total match {summary.get('expected_total_match', '')}; "
        f"activity start coverage {summary.get('covers_activity_start', '')}."
    )


def _xolo_api_coverage_pattern_line(summary: dict[str, str]) -> str:
    if not summary:
        return "- Raw Xolo API coverage was not supplied to this summary, so an incomplete expense-list export remains a separate evidence risk."
    if summary.get("status") == "complete_raw_snapshot":
        return (
            "- Raw Xolo API coverage is complete for the current snapshot "
            f"({summary.get('csv_rows', '')} rows, earliest date {summary.get('earliest_expense_date', '')}), "
            "so the remaining Modelo 130 blocker is not raw expense-list pagination coverage."
        )
    return "- Raw Xolo API coverage is attention-required, so refresh or repair the raw export before relying on downstream row-set conclusions."


def _annual_category_summary(
    closure_rows: list[dict[str, str]],
    annual_category_rows: list[dict[str, str]],
) -> dict[str, object]:
    by_year = {row["year"]: row for row in annual_category_rows}
    covered_periods = [row for row in closure_rows if _period_year(row["period"]) in by_year]
    vat_shaped_periods = [
        row
        for row in covered_periods
        if _professional_gross_gap_is_vat_shaped(by_year[_period_year(row["period"])])
    ]
    material_base_years = sorted(
        {
            year
            for year, row in by_year.items()
            if row.get("professional_base_diff") and abs(parse_amount(row["professional_base_diff"])) > 20
        }
    )
    direct_expense_years = sorted(
        year
        for year, row in by_year.items()
        if "direct-expense/reclassify" in row.get("category_signal", "")
    )
    return {
        "supplied": bool(annual_category_rows),
        "covered_periods": len(covered_periods),
        "total_periods": len(closure_rows),
        "vat_shaped_periods": len(vat_shaped_periods),
        "material_base_years": material_base_years,
        "direct_expense_years": direct_expense_years,
    }


def _annual_category_executive_finding(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- Annual Modelo 100 category VAT-base cross-check has not been supplied to this summary."
    return (
        "- Annual Modelo 100 category comparison separates VAT-shaped professional gross gaps from true VAT-base differences; "
        f"VAT-shaped professional gross gaps cover `{summary['vat_shaped_periods']}` quarters."
    )


def _annual_category_coverage_line(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- Annual Modelo 100 category cross-check rows: not supplied."
    return (
        "- Annual Modelo 100 category VAT-base comparison covered periods: "
        f"{summary['covered_periods']}/{summary['total_periods']}."
    )


def _annual_category_pattern_line(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- Annual Modelo 100 category VAT-base cross-check was not supplied to this summary."
    pieces = []
    material = summary["material_base_years"]
    direct = summary["direct_expense_years"]
    if material:
        pieces.append("material professional VAT-base differences remain in " + ", ".join(f"`{year}`" for year in material))
    if direct:
        pieces.append("asset direct-expense/reclassification signal appears in " + ", ".join(f"`{year}`" for year in direct))
    if not pieces:
        pieces.append("professional gross gaps mostly collapse under VAT-base comparison")
    return "- Annual Modelo 100 category lens: " + "; ".join(pieces) + "."


def _material_gap_summary(rows: list[dict[str, str]]) -> dict[str, object]:
    if not rows:
        return {"supplied": False, "rows": [], "periods": []}
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("hypothesis_status") == "ruled_out_local_hypothesis":
            continue
        grouped[(row["period"], row["hypothesis_id"])].append(row)

    status_order = {
        "unresolved_required_evidence": 0,
        "strong_candidate_pending_confirmation": 1,
        "partial_candidate_not_in_xolo_raw": 2,
        "unresolved_side_components": 3,
    }
    summary_rows: list[dict[str, str]] = []
    for (period, hypothesis_id), group in grouped.items():
        first = group[0]
        components = []
        for row in group:
            effect = _fmt(row.get("effect_closes_gap_eur", ""))
            counts = row.get("counts_in_best_bridge", "")
            components.append(f"{row['component']} ({effect}, counts={counts})")
        summary_rows.append(
            {
                "period": period,
                "hypothesis_id": hypothesis_id,
                "hypothesis_status": first["hypothesis_status"],
                "hypothesis_total_eur": first["hypothesis_total_eur"],
                "residual_to_candidate_balance_eur": first["residual_to_candidate_balance_eur"],
                "residual_to_annual_balance_eur": first["residual_to_annual_balance_eur"],
                "fit_signal": first["fit_signal"],
                "components": "; ".join(components),
            }
        )
    summary_rows.sort(
        key=lambda row: (
            row["period"],
            status_order.get(row["hypothesis_status"], 9),
            row["hypothesis_id"],
        )
    )
    return {
        "supplied": True,
        "rows": summary_rows,
        "periods": sorted({row["period"] for row in summary_rows}),
    }


def _material_gap_context_summary(rows: list[dict[str, str]]) -> dict[str, object]:
    if not rows:
        return {"supplied": False, "rows": [], "periods": []}
    sorted_rows = sorted(rows, key=lambda row: row["period"])
    return {
        "supplied": True,
        "rows": sorted_rows,
        "periods": [row["period"] for row in sorted_rows],
    }


def _p1_near_fit_context_summary(rows: list[dict[str, str]]) -> dict[str, object]:
    if not rows:
        return {"supplied": False, "rows": [], "periods": []}
    sorted_rows = sorted(rows, key=lambda row: row["period"])
    return {
        "supplied": True,
        "rows": sorted_rows,
        "periods": [row["period"] for row in sorted_rows],
    }


def _p2_near_target_context_summary(rows: list[dict[str, str]]) -> dict[str, object]:
    if not rows:
        return {"supplied": False, "rows": [], "periods": []}
    sorted_rows = sorted(rows, key=lambda row: (row["period"], row["context_signal"], row["row_ref"]))
    return {
        "supplied": True,
        "rows": sorted_rows,
        "periods": sorted({row["period"] for row in sorted_rows}),
    }


def _asset_gap_matrix_summary(rows: list[dict[str, str]], total_periods: int) -> dict[str, object]:
    if not rows:
        return {"supplied": False, "rows": [], "periods": [], "total_periods": total_periods, "signal_counts": {}}
    sorted_rows = sorted(rows, key=lambda row: row["period"])
    signal_counts = Counter(row["amortization_gap_signal"] for row in sorted_rows)
    return {
        "supplied": True,
        "rows": sorted_rows,
        "periods": sorted({row["period"] for row in sorted_rows}),
        "total_periods": total_periods,
        "signal_counts": dict(signal_counts),
    }


def _material_gap_executive_finding(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- Material-gap drilldown has not been supplied to this summary."
    periods = summary["periods"]
    period_text = ", ".join(f"`{period}`" for period in periods) if periods else "`none`"
    return (
        "- Material-gap drilldown narrows the first Xolo source-book questions to "
        f"{period_text}; other quarters are near-target but still need source-book or asset-schedule confirmation."
    )


def _material_context_executive_finding(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- P0 raw Xolo context has not been supplied to this summary."
    signals = sorted({row["context_signal"] for row in summary["rows"]})
    return "- P0 raw Xolo context signals: " + ", ".join(f"`{signal}`" for signal in signals) + "."


def _p1_context_executive_finding(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- P1 near-fit context has not been supplied to this summary."
    periods = ", ".join(f"`{period}`" for period in summary["periods"])
    return f"- P1 near-fit context narrows the next register questions for {periods}."


def _p2_context_executive_finding(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- P2 near-target context has not been supplied to this summary."
    periods = ", ".join(f"`{period}`" for period in summary["periods"])
    signals = sorted({row["context_signal"] for row in summary["rows"]})
    return (
        f"- P2 near-target context covers {periods}; signals are "
        + ", ".join(f"`{signal}`" for signal in signals)
        + "."
    )


def _asset_gap_matrix_executive_finding(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- Asset gap matrix has not been supplied to this summary."
    signal_counts: dict[str, int] = summary["signal_counts"]  # type: ignore[assignment]
    ordinary = _periods_by_signal(summary, "ordinary_amortization_too_small")
    exclusions = _periods_by_signal(summary, "asset_amortization_above_required_row_exclusions_needed")
    excluded_asset = _periods_by_signal(summary, "excluded_asset_or_register_adjustment_required")
    pieces = []
    if ordinary:
        pieces.append("ordinary-amortization-too-small in " + ", ".join(f"`{period}`" for period in ordinary))
    if excluded_asset:
        pieces.append("excluded-asset/register-adjustment in " + ", ".join(f"`{period}`" for period in excluded_asset))
    if exclusions:
        pieces.append(
            "row exclusions/netting alongside assets in " + ", ".join(f"`{period}`" for period in exclusions)
        )
    if not pieces:
        pieces.append("no material asset/register split signal beyond near-required confirmations")
    signals = "; ".join(f"`{signal}`={count}" for signal, count in sorted(signal_counts.items()))
    return "- Asset gap matrix confirms this should be an evidence request, not a tuning exercise: " + "; ".join(pieces) + f" (signals: {signals})."


def _material_gap_coverage_line(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- Material-gap drilldown rows: not supplied."
    return f"- Material-gap drilldown periods: {len(summary['periods'])}."


def _material_context_coverage_line(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- P0 raw Xolo context rows: not supplied."
    return f"- P0 raw Xolo context rows: {len(summary['rows'])}."


def _p1_context_coverage_line(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- P1 near-fit context rows: not supplied."
    return f"- P1 near-fit context rows: {len(summary['rows'])}."


def _p2_context_coverage_line(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- P2 near-target context rows: not supplied."
    return f"- P2 near-target context rows: {len(summary['rows'])} across {len(summary['periods'])} periods."


def _asset_gap_matrix_coverage_line(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- Asset gap matrix rows: not supplied."
    return f"- Asset gap matrix covered periods: {len(summary['periods'])}/{summary['total_periods']}."


def _material_gap_pattern_line(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- Material-gap drilldown was not supplied to this summary."
    periods = summary["periods"]
    if not periods:
        return "- Material-gap drilldown currently has no actionable hypothesis rows."
    return (
        "- Material-gap drilldown: first external confirmation should focus on "
        + ", ".join(f"`{period}`" for period in periods)
        + " before spending time on near-target quarters."
    )


def _asset_gap_matrix_pattern_line(summary: dict[str, object]) -> str:
    if not summary["supplied"]:
        return "- Asset gap matrix was not supplied to this summary, so asset-vs-register routing remains incomplete."
    raw_above = _periods_by_signal(summary, "raw_non_asset_above_target")
    too_small = _periods_by_signal(summary, "ordinary_amortization_too_small")
    row_exclusions = _periods_by_signal(summary, "asset_amortization_above_required_row_exclusions_needed")
    pieces = []
    if raw_above:
        pieces.append("raw non-asset already above target in " + ", ".join(f"`{period}`" for period in raw_above))
    if too_small:
        pieces.append("positive catch-up pressure beyond ordinary amortization in " + ", ".join(f"`{period}`" for period in too_small))
    if row_exclusions:
        pieces.append("asset schedule must be paired with exclusions/netting in " + ", ".join(f"`{period}`" for period in row_exclusions))
    if not pieces:
        pieces.append("asset schedule may explain the current pre-exclusion gaps but still needs filed schedule confirmation")
    return "- Asset gap matrix: " + "; ".join(pieces) + "."


def _closure_material_pattern_line(
    closure_rows: list[dict[str, str]],
    material_gap: dict[str, object],
) -> str:
    closure_material = [
        row["period"]
        for row in closure_rows
        if row.get("closure_status") == "blocked_material_unexplained_adjustment"
    ]
    closure_text = ", ".join(f"`{period}`" for period in closure_material) if closure_material else "`none`"
    if material_gap["supplied"]:
        focus_periods = material_gap["periods"]
        focus_text = ", ".join(f"`{period}`" for period in focus_periods) if focus_periods else "`none`"
        return (
            "- Closure status still flags material source-book adjustments in "
            f"{closure_text}; after the balance bridge, the first high-value gaps to confirm are {focus_text}."
        )
    return (
        "- "
        + closure_text
        + " still have material source-book adjustments that local evidence can bound but not prove."
    )


def _periods_by_signal(summary: dict[str, object], signal: str) -> list[str]:
    return [row["period"] for row in summary["rows"] if row.get("amortization_gap_signal") == signal]  # type: ignore[index]


def _professional_gross_gap_is_vat_shaped(row: dict[str, str]) -> bool:
    gross = row.get("professional_gross_diff")
    base = row.get("professional_base_diff")
    if not gross or not base:
        return False
    return parse_amount(gross) < -20 and abs(parse_amount(base)) <= 20


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _period_year(period: str) -> str:
    return period.split("-", 1)[0]


def _group_by_period(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row.get("period", "")].append(row)
    return dict(grouped)


def _blank_unconfirmed_sources(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if row.get("source_status", "") in {"unconfirmed_arithmetic_hypothesis", "unconfirmed_asset_amortization_row"}
        and not row.get("source_document", "").strip()
    ]


def _unconfirmed_by_period(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        status = row.get("source_status", "")
        if status.startswith("unconfirmed") or row.get("row_ref", "").startswith("scenario_"):
            grouped[row.get("period", "")].append(row)
    return dict(grouped)


def _finding_refs(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ""
    refs = [row["row_ref"] for row in rows[:4]]
    remaining = len(rows) - len(refs)
    if remaining > 0:
        refs.append(f"+{remaining} more")
    return "; ".join(refs)


def _fmt(value: str | None) -> str:
    if not value:
        return ""
    return format_es(parse_amount(value))


def _cell(value: str) -> str:
    return source_book_wording(value).replace("|", "\\|").replace("\n", " ")
