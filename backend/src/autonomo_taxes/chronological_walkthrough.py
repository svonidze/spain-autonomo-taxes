from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from .money import format_es, parse_amount
from .source_book_wording import source_book_wording


CHRONOLOGICAL_WALKTHROUGH_FIELDS = [
    "order",
    "period",
    "first_unclosed_period",
    "chronological_gate",
    "closure_status",
    "target_casilla_02_delta",
    "pre_plug_residual_eur",
    "balancing_adjustment_eur",
    "asset_gap_signal",
    "required_amortization_or_catchup_delta",
    "annual_constrained_amortization_delta",
    "required_minus_annual_constrained_amortization",
    "active_asset_count",
    "local_verdict",
    "blocking_evidence",
    "next_action",
    "unresolved_source_refs",
]


def build_chronological_walkthrough(
    quarter_closure_csv: Path,
    asset_gap_matrix_csv: Path,
    source_findings_csv: Path | None = None,
) -> list[dict[str, str]]:
    closure_rows = sorted(_load_rows(quarter_closure_csv), key=lambda row: _period_key(row["period"]))
    asset_by_period = {row["period"]: row for row in _load_rows(asset_gap_matrix_csv)}
    source_by_period = _source_refs_by_period(_load_rows(source_findings_csv)) if source_findings_csv else {}
    rows: list[dict[str, str]] = []
    first_unclosed_period = ""
    for index, closure in enumerate(closure_rows, start=1):
        period = closure["period"]
        asset = asset_by_period.get(period, {})
        signal = asset.get("amortization_gap_signal", "")
        gate = _chronological_gate(closure.get("closure_status", ""), signal)
        if not first_unclosed_period and gate != "local_closed":
            first_unclosed_period = period
        rows.append(
            {
                "order": str(index),
                "period": period,
                "first_unclosed_period": "yes" if first_unclosed_period == period else "no",
                "chronological_gate": gate,
                "closure_status": closure.get("closure_status", ""),
                "target_casilla_02_delta": closure.get("target_casilla_02_delta", ""),
                "pre_plug_residual_eur": closure.get("pre_plug_model_minus_target_eur", ""),
                "balancing_adjustment_eur": closure.get("balancing_adjustment_eur", ""),
                "asset_gap_signal": signal,
                "required_amortization_or_catchup_delta": asset.get(
                    "required_amortization_or_catchup_delta", ""
                ),
                "annual_constrained_amortization_delta": asset.get("annual_constrained_amortization_delta", ""),
                "required_minus_annual_constrained_amortization": asset.get(
                    "required_minus_annual_constrained_amortization", ""
                ),
                "active_asset_count": asset.get("active_asset_count", ""),
                "local_verdict": _local_verdict(signal, closure.get("closure_status", "")),
                "blocking_evidence": _blocking_evidence(signal, closure.get("closure_status", "")),
                "next_action": asset.get("next_action") or closure.get("next_question", ""),
                "unresolved_source_refs": _join_refs(source_by_period.get(period, [])),
            }
        )
    return rows


def write_chronological_walkthrough_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CHRONOLOGICAL_WALKTHROUGH_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_chronological_walkthrough_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    first = next((row for row in rows if row["first_unclosed_period"] == "yes"), None)
    raw_above = [row["period"] for row in rows if row["asset_gap_signal"] == "raw_non_asset_above_target"]
    catchup = [row["period"] for row in rows if row["asset_gap_signal"] == "ordinary_amortization_too_small"]
    first_asset_gate = [
        row["period"]
        for row in rows
        if row["asset_gap_signal"] == "excluded_asset_or_register_adjustment_required"
    ]
    row_exclusions = [
        row["period"]
        for row in rows
        if row["asset_gap_signal"] == "asset_amortization_above_required_row_exclusions_needed"
    ]
    lines = [
        "# Modelo 130 Chronological Walkthrough",
        "",
        "This report walks the submitted Modelo 130 quarters in filing order and keeps the amortization lens attached to each quarter.",
        "It is a question router, not source-book evidence; no quarter is closed without Xolo's source books and asset schedule.",
        "",
        "## Summary",
        "",
        f"- Quarters walked: `{len(rows)}`.",
        f"- First unclosed chronological gate: `{first['period'] if first else 'none'}`.",
        f"- First asset/direct-expense treatment gates: `{', '.join(first_asset_gate) if first_asset_gate else 'none'}`.",
        f"- Raw non-asset rows already above target before amortization: `{', '.join(raw_above) if raw_above else 'none'}`.",
        f"- Ordinary amortization too small / catch-up pressure: `{', '.join(catchup) if catchup else 'none'}`.",
        f"- Asset schedule must be paired with row exclusions/netting: `{', '.join(row_exclusions) if row_exclusions else 'none'}`.",
        "",
        "## Walkthrough",
        "",
        "| # | Period | Gate | Target 02 delta | Required amort./catch-up | Annual-constrained amort. | Required - annual | First blocker | Local verdict | Next action |",
        "|---:|---|---|---:|---:|---:|---:|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["order"],
                    row["period"],
                    row["chronological_gate"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["required_amortization_or_catchup_delta"]),
                    _fmt(row["annual_constrained_amortization_delta"]),
                    _fmt(row["required_minus_annual_constrained_amortization"]),
                    row["first_unclosed_period"],
                    _cell(row["local_verdict"]),
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Evidence Gates",
            "",
            "| Period | Blocking evidence | Unresolved source refs |",
            "|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    _cell(row["blocking_evidence"]),
                    _cell(row["unresolved_source_refs"] or "none"),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _chronological_gate(closure_status: str, asset_signal: str) -> str:
    if closure_status == "closed":
        return "local_closed"
    if asset_signal == "excluded_asset_or_register_adjustment_required":
        return "first_asset_treatment_gate"
    if asset_signal == "raw_non_asset_above_target":
        return "row_set_before_amortization"
    if asset_signal == "ordinary_amortization_too_small":
        return "catchup_or_reclassification_gate"
    if asset_signal == "asset_amortization_above_required_row_exclusions_needed":
        return "asset_schedule_plus_row_set_gate"
    if asset_signal in {"annual_constrained_amortization_near_required", "candidate_amortization_near_required"}:
        return "asset_schedule_confirmation_gate"
    if "asset" in closure_status:
        return "asset_schedule_confirmation_gate"
    if "row_exclusion" in closure_status:
        return "row_set_confirmation_gate"
    return "material_source_book_gate"


def _local_verdict(asset_signal: str, closure_status: str) -> str:
    if closure_status == "closed":
        return "Local evidence closes this quarter."
    if asset_signal == "excluded_asset_or_register_adjustment_required":
        return (
            "Local raw non-asset rows do not reach the submitted expense delta; an active asset/direct-expense row "
            "or another source-book adjustment must explain the first gap."
        )
    if asset_signal == "raw_non_asset_above_target":
        return (
            "Raw non-asset rows already exceed the submitted expense delta, so adding amortization worsens the fit; "
            "source-book exclusions, netting, or basis changes must come first."
        )
    if asset_signal == "ordinary_amortization_too_small":
        return (
            "Raw non-asset rows are below target, but ordinary annual-constrained amortization is too small; "
            "catch-up, direct expensing, reclassification, or cross-quarter booking is required."
        )
    if asset_signal == "asset_amortization_above_required_row_exclusions_needed":
        return (
            "Raw non-asset rows are below target, but the annual-constrained asset lens overshoots; "
            "the asset schedule must be paired with exclusions, netting, or basis differences."
        )
    if asset_signal.endswith("_near_required"):
        return "The amortization lens is near the required amount, but the exact source-book asset schedule is still required."
    return "Local arithmetic narrows the question, but source-book evidence is still required."


def _blocking_evidence(asset_signal: str, closure_status: str) -> str:
    if closure_status == "closed":
        return "none"
    if asset_signal == "excluded_asset_or_register_adjustment_required":
        return "source-book treatment for active asset/direct-expense rows; asset schedule if capitalized"
    if asset_signal == "raw_non_asset_above_target":
        return "source-book row exclusions, netting, VAT/base treatment, or deferrals before amortization"
    if asset_signal == "ordinary_amortization_too_small":
        return "source-book catch-up/reclassification rows and asset schedule"
    if asset_signal == "asset_amortization_above_required_row_exclusions_needed":
        return "asset schedule plus source-book exclusions, netting, or deductible-basis differences"
    if asset_signal.endswith("_near_required"):
        return "exact asset schedule by asset and quarter"
    if "row_exclusion" in closure_status:
        return "source-book row inclusion/exclusion and deductible basis"
    return "source-book deductible EUR rows and any balancing adjustment"


def _source_refs_by_period(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if _is_unresolved_source(row):
            grouped[row.get("period", "")].append(row.get("row_ref", ""))
    return grouped


def _is_unresolved_source(row: dict[str, str]) -> bool:
    status = row.get("source_status", "")
    row_ref = row.get("row_ref", "")
    return status.startswith("unconfirmed") or "pending" in status or row_ref.startswith("scenario_")


def _join_refs(refs: list[str]) -> str:
    clean = [ref for ref in refs if ref]
    if len(clean) <= 4:
        return "; ".join(clean)
    return "; ".join(clean[:4]) + f"; +{len(clean) - 4} more"


def _load_rows(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _period_key(period: str) -> tuple[int, int]:
    year, quarter = period.split("-Q", 1)
    return int(year), int(quarter)


def _fmt(value: str) -> str:
    if value == "":
        return ""
    return format_es(parse_amount(value))


def _cell(text: str) -> str:
    return source_book_wording(text).replace("|", "\\|").replace("\n", " ")
