from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .money import cents, format_es, parse_amount


MATERIAL_GAP_CONTEXT_FIELDS = [
    "period",
    "priority",
    "target_casilla_02_delta",
    "annual_constrained_balance_to_target",
    "xolo_document_quarter_row_count",
    "xolo_document_quarter_unconverted_row_count",
    "xolo_document_quarter_gross_eur",
    "xolo_document_quarter_non_asset_gross_eur",
    "xolo_document_quarter_asset_candidate_gross_eur",
    "xolo_document_quarter_unconverted_rows",
    "bridge_raw_non_asset_delta",
    "bridge_raw_vs_xolo_non_asset_delta",
    "target_minus_xolo_non_asset_eur",
    "target_minus_xolo_asset_candidate_eur",
    "asset_candidate_rows",
    "gap_sized_asset_candidate_rows",
    "gap_sized_asset_candidate_fit",
    "actionable_hypotheses",
    "context_signal",
    "next_xolo_question",
]


def build_material_gap_context(
    quarter_acceptance_csv: Path,
    quarter_balance_bridge_csv: Path,
    xolo_expense_ledger_csv: Path,
    material_gap_drilldown_csv: Path | None = None,
) -> list[dict[str, str]]:
    acceptance_rows = [
        row for row in _load_rows(quarter_acceptance_csv) if row.get("priority") == "P0"
    ]
    bridge_by_period = {row["period"]: row for row in _load_rows(quarter_balance_bridge_csv)}
    material_by_period = _actionable_material_by_period(
        _load_rows(material_gap_drilldown_csv) if material_gap_drilldown_csv else []
    )
    xolo_by_period = _xolo_rows_by_period(_load_rows(xolo_expense_ledger_csv))

    rows: list[dict[str, str]] = []
    for acceptance in acceptance_rows:
        period = acceptance["period"]
        bridge = bridge_by_period.get(period)
        if bridge is None:
            raise ValueError(f"Missing quarter balance bridge row for {period}")

        xolo_rows = xolo_by_period.get(period, [])
        asset_rows = [row for row in xolo_rows if _is_asset_candidate(row)]
        non_asset_rows = [row for row in xolo_rows if not _is_asset_candidate(row)]
        unconverted_rows = [row for row in xolo_rows if _row_has_unconverted_currency(row)]
        material_rows = material_by_period.get(period, [])

        target = parse_amount(bridge["target_casilla_02_delta"])
        annual_balance = parse_amount(bridge["annual_constrained_balance_to_target"])
        bridge_raw = parse_amount(bridge["raw_non_asset_delta"])
        xolo_total = _sum_rows(xolo_rows)
        xolo_non_asset = _sum_rows(non_asset_rows)
        xolo_asset = _sum_rows(asset_rows)
        bridge_vs_xolo = cents(bridge_raw - xolo_non_asset)
        target_minus_non_asset = cents(target - xolo_non_asset)
        target_minus_asset = cents(target - xolo_asset)
        gap_sized_assets = [
            row for row in asset_rows if abs(_row_gross(row)) >= abs(annual_balance)
        ]
        context_signal = _context_signal(
            annual_balance,
            target_minus_asset,
            target_minus_non_asset,
            bridge_vs_xolo,
            gap_sized_assets,
            material_rows,
            unconverted_rows,
        )

        rows.append(
            {
                "period": period,
                "priority": acceptance["priority"],
                "target_casilla_02_delta": _money(target),
                "annual_constrained_balance_to_target": _money(annual_balance),
                "xolo_document_quarter_row_count": str(len(xolo_rows)),
                "xolo_document_quarter_unconverted_row_count": str(len(unconverted_rows)),
                "xolo_document_quarter_gross_eur": _money(xolo_total),
                "xolo_document_quarter_non_asset_gross_eur": _money(xolo_non_asset),
                "xolo_document_quarter_asset_candidate_gross_eur": _money(xolo_asset),
                "xolo_document_quarter_unconverted_rows": _format_original_rows(unconverted_rows),
                "bridge_raw_non_asset_delta": _money(bridge_raw),
                "bridge_raw_vs_xolo_non_asset_delta": _money(bridge_vs_xolo),
                "target_minus_xolo_non_asset_eur": _money(target_minus_non_asset),
                "target_minus_xolo_asset_candidate_eur": _money(target_minus_asset),
                "asset_candidate_rows": _format_rows(asset_rows),
                "gap_sized_asset_candidate_rows": _format_rows(gap_sized_assets),
                "gap_sized_asset_candidate_fit": _format_asset_fit(gap_sized_assets, annual_balance),
                "actionable_hypotheses": _format_hypotheses(material_rows),
                "context_signal": context_signal,
                "next_xolo_question": _next_question(
                    period,
                    annual_balance,
                    target,
                    asset_rows,
                    non_asset_rows,
                    gap_sized_assets,
                    context_signal,
                    unconverted_rows,
                ),
            }
        )
    return rows


def write_material_gap_context_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATERIAL_GAP_CONTEXT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_material_gap_context_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 130 Material Gap Context",
        "",
        "This report adds raw Xolo ledger context to the P0 material-gap quarters.",
        "It does not confirm submitted Modelo 130 treatment; it identifies the shortest row-level questions for Xolo's filed register and asset schedule.",
        "",
        "## Summary",
        "",
        f"- P0 material-gap periods: `{len(rows)}`.",
        "- Confirmed closed from this context alone: `0`.",
        "",
        "| Period | Target 02 delta | Annual balance | Raw Xolo rows | Xolo non-asset | Bridge raw non-asset | Target minus Xolo non-asset | Asset fit | Signal |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["annual_constrained_balance_to_target"]),
                    row["xolo_document_quarter_row_count"],
                    _fmt(row["xolo_document_quarter_non_asset_gross_eur"]),
                    _fmt(row["bridge_raw_non_asset_delta"]),
                    _fmt(row["target_minus_xolo_non_asset_eur"]),
                    _cell(row["gap_sized_asset_candidate_fit"] or ""),
                    row["context_signal"],
                ]
            )
            + " |"
        )

    lines.extend(["", "## Period Context", ""])
    for row in rows:
        lines.extend(
            [
                f"### {row['period']}",
                "",
                f"- Context signal: `{row['context_signal']}`",
                f"- Asset candidate rows: {_cell(row['asset_candidate_rows']) or 'none'}",
                f"- Non-EUR rows without booked EUR amount: {_cell(row['xolo_document_quarter_unconverted_rows']) or 'none'}",
                f"- Gap-sized asset candidate rows: {_cell(row['gap_sized_asset_candidate_rows']) or 'none'}",
                f"- Gap-sized asset fit: {_cell(row['gap_sized_asset_candidate_fit']) or 'none'}",
                f"- Actionable hypotheses: {_cell(row['actionable_hypotheses']) or 'none'}",
                f"- Next Xolo question: {_cell(row['next_xolo_question'])}",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _context_signal(
    annual_balance: Decimal,
    target_minus_asset: Decimal,
    target_minus_non_asset: Decimal,
    bridge_vs_xolo: Decimal,
    gap_sized_assets: list[dict[str, str]],
    material_rows: list[dict[str, str]],
    unconverted_rows: list[dict[str, str]],
) -> str:
    if unconverted_rows:
        return "xolo_raw_has_unconverted_currency_rows_requires_fx"
    if gap_sized_assets and abs(target_minus_asset) <= Decimal("0.02"):
        return "target_matches_asset_candidate_if_non_assets_excluded"
    if (
        abs(annual_balance - target_minus_non_asset) <= Decimal("0.02")
        and abs(bridge_vs_xolo) <= Decimal("0.02")
        and gap_sized_assets
    ):
        return "gap_matches_excluded_asset_candidate_context"
    if abs(bridge_vs_xolo) > Decimal("1.00") and any(
        row.get("hypothesis_status") == "strong_candidate_pending_confirmation"
        for row in material_rows
    ):
        return "local_bridge_differs_from_xolo_raw_with_strong_cross_quarter_hypotheses"
    if abs(bridge_vs_xolo) > Decimal("1.00"):
        return "local_bridge_raw_non_asset_differs_from_xolo_raw"
    return "material_hypotheses_require_register_confirmation"


def _next_question(
    period: str,
    annual_balance: Decimal,
    target: Decimal,
    asset_rows: list[dict[str, str]],
    non_asset_rows: list[dict[str, str]],
    gap_sized_assets: list[dict[str, str]],
    context_signal: str,
    unconverted_rows: list[dict[str, str]],
) -> str:
    if context_signal == "xolo_raw_has_unconverted_currency_rows_requires_fx":
        rows = _format_original_rows(unconverted_rows)
        return (
            f"For {period}, Xolo raw rows include non-EUR amounts without booked EUR values: {rows}. "
            "Please provide the submitted Modelo 130 register EUR deductible amount and FX/basis for these rows."
        )
    if context_signal == "target_matches_asset_candidate_if_non_assets_excluded":
        assets = _format_rows(asset_rows)
        non_assets = _format_rows(non_asset_rows)
        return (
            f"For {period}, submitted casilla 02 delta matches the asset/category rows at {format_es(target)} EUR. "
            f"Did Xolo's submitted register include these row(s): {assets}, and exclude, defer, or reclassify these non-asset row(s): {non_assets}?"
        )
    if context_signal == "gap_matches_excluded_asset_candidate_context" and gap_sized_assets:
        rows = _format_rows(gap_sized_assets)
        return (
            f"For {period}, did Xolo's submitted register use {format_es(annual_balance)} EUR "
            f"from this asset/category row as partial deduction, direct-expense reclassification, or amortization: {rows}?"
        )
    if context_signal == "local_bridge_raw_non_asset_differs_from_xolo_raw":
        return (
            f"For {period}, confirm the submitted row set and tax basis because the local bridge raw "
            "non-asset total differs from the raw Xolo ledger."
        )
    if context_signal == "local_bridge_differs_from_xolo_raw_with_strong_cross_quarter_hypotheses":
        return (
            f"For {period}, confirm the submitted row set, cross-quarter carry-ins, and tax basis "
            "for the strong material-gap hypotheses listed in the drilldown."
        )
    return f"For {period}, identify the submitted-register rows or asset schedule that explain the material gap."


def _actionable_material_by_period(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("hypothesis_status") in {
            "unresolved_required_evidence",
            "strong_candidate_pending_confirmation",
            "partial_candidate_not_in_xolo_raw",
            "unresolved_side_components",
        }:
            grouped[row["period"]].append(row)
    return dict(grouped)


def _xolo_rows_by_period(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        date = row.get("date", "")
        if not date:
            continue
        grouped[_period_from_date(date)].append(row)
    return dict(grouped)


def _period_from_date(value: str) -> str:
    year = value[:4]
    month = int(value[5:7])
    quarter = ((month - 1) // 3) + 1
    return f"{year}-Q{quarter}"


def _is_asset_candidate(row: dict[str, str]) -> bool:
    text = " ".join([row.get("type", ""), row.get("recipient", ""), row.get("notes", "")]).lower()
    return "computer hardware" in text or "asset candidate" in text


def _sum_rows(rows: list[dict[str, str]]) -> Decimal:
    total = Decimal("0.00")
    for row in rows:
        total += _row_gross(row)
    return cents(total)


def _row_gross(row: dict[str, str]) -> Decimal:
    gross_eur = row.get("gross_eur")
    if gross_eur and _trusted_detail_amount(row):
        return parse_amount(gross_eur)
    currency = (row.get("currency") or "").upper()
    if currency and currency != "EUR":
        return Decimal("0.00")
    value = row.get("match_amount") or row.get("amount_original") or "0"
    return parse_amount(value)


def _row_has_unconverted_currency(row: dict[str, str]) -> bool:
    currency = (row.get("currency") or "").upper()
    return bool(currency and currency != "EUR" and (not row.get("gross_eur") or not _trusted_detail_amount(row)))


def _trusted_detail_amount(row: dict[str, str]) -> bool:
    confidence = row.get("detail_confidence") or ""
    if not confidence:
        return True
    return confidence in {"detail_page_eur", "detail_page_exchange_rate"}


def _format_rows(rows: list[dict[str, str]]) -> str:
    return "; ".join(
        " ".join(
            part
            for part in (
                row.get("date", ""),
                row.get("xolo_id", ""),
                row.get("recipient", ""),
                row.get("number", ""),
                _money(_row_gross(row)),
                row.get("type", ""),
            )
            if part
        )
        for row in rows
    )


def _format_original_rows(rows: list[dict[str, str]]) -> str:
    return "; ".join(
        " ".join(
            part
            for part in (
                row.get("date", ""),
                row.get("xolo_id", ""),
                row.get("recipient", ""),
                row.get("number", ""),
                row.get("amount_original", ""),
                row.get("currency", ""),
                row.get("type", ""),
            )
            if part
        )
        for row in rows
    )


def _format_asset_fit(rows: list[dict[str, str]], gap: Decimal) -> str:
    if not rows:
        return ""
    parts = []
    for row in rows:
        gross = abs(_row_gross(row))
        if gross == Decimal("0.00"):
            continue
        pct = (abs(gap) / gross * Decimal("100")).quantize(Decimal("0.01"))
        parts.append(
            f"{row.get('xolo_id', '') or row.get('number', '')}: gap {_money(abs(gap))} is {pct}% of {_money(gross)}"
        )
    return "; ".join(parts)


def _format_hypotheses(rows: list[dict[str, str]]) -> str:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["hypothesis_id"]].append(row)
    parts = []
    for hypothesis_id, group in sorted(grouped.items()):
        first = group[0]
        parts.append(
            f"{hypothesis_id} {first['hypothesis_status']} total {_fmt(first.get('hypothesis_total_eur', ''))}"
        )
    return "; ".join(parts)


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return format_es(parse_amount(value)) if value else ""


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
