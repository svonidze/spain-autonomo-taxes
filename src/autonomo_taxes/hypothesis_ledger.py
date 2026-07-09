from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .money import cents, format_es, parse_amount
from .parsers import quarter_end


HYPOTHESIS_SUMMARY_FIELDS = [
    "period",
    "target_casilla_02_delta",
    "included_raw_gross_eur",
    "excluded_nearest_subset_eur",
    "excluded_asset_direct_eur",
    "candidate_amortization_delta",
    "pre_plug_model_minus_target_eur",
    "balancing_adjustment_eur",
    "balancing_adjustment_pct_of_target",
    "balancing_adjustment_materiality",
    "hypothesis_ledger_delta",
    "hypothesis_minus_target_delta",
    "included_raw_rows",
    "excluded_nearest_rows",
    "excluded_asset_rows",
    "ignored_non_xolo_rows",
    "status",
    "open_confirmation",
]


@dataclass(frozen=True)
class HypothesisLedger:
    ledger_rows: list[dict[str, str]]
    summary_rows: list[dict[str, str]]


def build_hypothesis_ledger(
    row_audit_csv: Path,
    candidate_quarter_reconciliation_csv: Path,
) -> HypothesisLedger:
    """Build an unconfirmed ledger scenario that ties row evidence to targets.

    The output intentionally remains a hypothesis. It includes ordinary raw
    rows, excludes nearest-subset and direct asset candidates, adds the
    candidate amortization delta, then adds an explicit balancing adjustment
    for whatever is still not explained by those rules.
    """

    audit_rows = _group_by_period(_load_rows(row_audit_csv))
    candidate_rows = _load_rows(candidate_quarter_reconciliation_csv)
    ledger_rows: list[dict[str, str]] = []
    summary_rows: list[dict[str, str]] = []

    for candidate in candidate_rows:
        year = int((candidate.get("year") or "").strip())
        quarter = int((candidate.get("quarter") or "").strip())
        period = (candidate.get("period") or "").strip()
        target_delta = _amount_or_zero(candidate.get("target_casilla_02_delta"))
        amortization_delta = _amount_or_zero(candidate.get("candidate_amortization_delta"))
        all_period_rows = audit_rows.get(period, [])
        _validate_row_kinds(period, all_period_rows)
        period_rows = [row for row in all_period_rows if row["row_kind"] == "xolo_expense"]
        ignored_non_xolo_rows = len(all_period_rows) - len(period_rows)

        included_raw = Decimal("0.00")
        excluded_nearest = Decimal("0.00")
        excluded_asset = Decimal("0.00")
        included_raw_rows = 0
        excluded_nearest_rows = 0
        excluded_asset_rows = 0

        for row in period_rows:
            gross = parse_amount(row["gross_eur"]) if row["gross_eur"] else Decimal("0.00")
            include = _include_raw_row(row)
            deductible = gross if include else Decimal("0.00")
            if include:
                included_raw += gross
                included_raw_rows += 1
            elif row["classification"] == "nearest_exclusion_candidate":
                excluded_nearest += gross
                excluded_nearest_rows += 1
            elif row["classification"] == "asset_amortization_candidate":
                excluded_asset += gross
                excluded_asset_rows += 1
            ledger_rows.append(_raw_ledger_row(row, period, include, deductible))

        if amortization_delta != Decimal("0.00"):
            ledger_rows.append(_synthetic_row(year, quarter, period, "AMORT", amortization_delta))

        before_adjustment = cents(included_raw + amortization_delta)
        pre_plug_model_minus_target = cents(before_adjustment - target_delta)
        balancing_adjustment = cents(target_delta - before_adjustment)
        if balancing_adjustment != Decimal("0.00"):
            ledger_rows.append(_synthetic_row(year, quarter, period, "BALANCE", balancing_adjustment))

        hypothesis_delta = cents(before_adjustment + balancing_adjustment)
        diff = cents(hypothesis_delta - target_delta)
        materiality = _materiality(balancing_adjustment, target_delta)
        if diff != Decimal("0.00"):
            raise AssertionError(f"Hypothesis ledger failed to balance {period}: {diff}")
        summary_rows.append(
            {
                "period": period,
                "target_casilla_02_delta": _money(target_delta),
                "included_raw_gross_eur": _money(included_raw),
                "excluded_nearest_subset_eur": _money(excluded_nearest),
                "excluded_asset_direct_eur": _money(excluded_asset),
                "candidate_amortization_delta": _money(amortization_delta),
                "pre_plug_model_minus_target_eur": _money(pre_plug_model_minus_target),
                "balancing_adjustment_eur": _money(balancing_adjustment),
                "balancing_adjustment_pct_of_target": _percent(balancing_adjustment, target_delta),
                "balancing_adjustment_materiality": materiality,
                "hypothesis_ledger_delta": _money(hypothesis_delta),
                "hypothesis_minus_target_delta": _money(diff),
                "included_raw_rows": str(included_raw_rows),
                "excluded_nearest_rows": str(excluded_nearest_rows),
                "excluded_asset_rows": str(excluded_asset_rows),
                "ignored_non_xolo_rows": str(ignored_non_xolo_rows),
                "status": _status(balancing_adjustment, target_delta),
                "open_confirmation": _open_confirmation(
                    amortization_delta,
                    balancing_adjustment,
                    materiality,
                    excluded_nearest_rows,
                    excluded_asset_rows,
                ),
            }
        )

    return HypothesisLedger(ledger_rows=ledger_rows, summary_rows=summary_rows)


def write_hypothesis_summary_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HYPOTHESIS_SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_hypothesis_markdown(path: Path, result: HypothesisLedger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Modelo 130 Hypothesis Ledger",
        "",
        "This report converts the row audit into an unconfirmed ledger scenario.",
        "It is useful for checking every quarter arithmetically, but it is not proof of Xolo's submitted register.",
        "The post-plug diff is zero by construction; the useful signal is the pre-plug residual and balancing adjustment size.",
        "Rows marked as candidate amortization or balancing adjustments require Xolo confirmation before future filing use.",
        "",
        "| Period | Status | Target delta | Raw included | Nearest excluded | Assets direct excluded | Amortization | Pre-plug residual | Balance | Plug % | Post-plug diff |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result.summary_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["status"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["included_raw_gross_eur"]),
                    _fmt(row["excluded_nearest_subset_eur"]),
                    _fmt(row["excluded_asset_direct_eur"]),
                    _fmt(row["candidate_amortization_delta"]),
                    _fmt(row["pre_plug_model_minus_target_eur"]),
                    _fmt(row["balancing_adjustment_eur"]),
                    row["balancing_adjustment_pct_of_target"] + "%",
                    _fmt(row["hypothesis_minus_target_delta"]),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Open Confirmations", ""])
    for row in result.summary_rows:
        lines.append(f"- {row['period']}: {row['open_confirmation']}")
    path.write_text("\n".join(lines), encoding="utf-8")


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _group_by_period(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["period"], []).append(row)
    return grouped


def _validate_row_kinds(period: str, rows: list[dict[str, str]]) -> None:
    allowed = {"xolo_expense", "synthetic_gap"}
    unexpected = sorted({row.get("row_kind", "") for row in rows} - allowed)
    if unexpected:
        raise ValueError(f"Unexpected row_kind values for {period}: {', '.join(unexpected)}")


def _include_raw_row(row: dict[str, str]) -> bool:
    return row["classification"] not in {
        "asset_amortization_candidate",
        "nearest_exclusion_candidate",
    }


def _raw_ledger_row(
    row: dict[str, str],
    period: str,
    include: bool,
    deductible: Decimal,
) -> dict[str, str]:
    classification = row["classification"]
    return {
        "xolo_url": row["xolo_url"],
        "xolo_id": row["xolo_id"],
        "recipient": row["recipient"],
        "type": row["type"],
        "number": row["number"],
        "date": row["date"],
        "amount_original": row["amount_original"] or row["gross_eur"] or "0.00",
        "currency": row["currency"] or "EUR",
        "gross_eur": row["gross_eur"],
        "vat_base_eur": row["base_eur"],
        "irpf_deductible_eur": _money(deductible),
        "inclusion_quarter": period,
        "include_in_modelo130": "yes" if include else "no",
        "source_document_path": row["xolo_url"],
        "confidence": _confidence(classification),
        "notes": (f"Annual-best hypothesis treats row as {classification}. {row['notes']}").strip(),
    }


def _synthetic_row(
    year: int,
    quarter: int,
    period: str,
    kind: str,
    amount: Decimal,
) -> dict[str, str]:
    row_date = quarter_end(year, quarter)
    if kind == "AMORT":
        recipient = "Xolo asset amortization schedule"
        expense_type = "Asset amortization"
        confidence = "inferred_hypothesis_candidate_amortization"
        notes = (
            "Pending confirmation: annual-best asset scenario amortization delta; "
            "not the Xolo-confirmed asset schedule."
        )
    else:
        recipient = "Xolo submitted-register adjustment"
        expense_type = "Reconciliation residual"
        confidence = "inferred_hypothesis_balancing_adjustment"
        if amount >= Decimal("0.00"):
            notes = (
                "Pending confirmation: unexplained positive adjustment needed after raw rows, "
                "nearest exclusions, and candidate amortization."
            )
        else:
            notes = (
                "Pending confirmation: unexplained exclusion or reduction needed after raw rows, "
                "nearest exclusions, and candidate amortization."
            )
    return {
        "xolo_url": "",
        "xolo_id": "",
        "recipient": recipient,
        "type": expense_type,
        "number": f"HYP-{period}-{kind}",
        "date": row_date.isoformat(),
        "amount_original": _money(amount),
        "currency": "EUR",
        "gross_eur": _money(amount),
        "vat_base_eur": _money(amount),
        "irpf_deductible_eur": _money(amount),
        "inclusion_quarter": period,
        "include_in_modelo130": "yes",
        "source_document_path": "",
        "confidence": confidence,
        "notes": notes,
    }


def _confidence(classification: str) -> str:
    if classification == "asset_amortization_candidate":
        return "hypothesis_asset_direct_excluded"
    if classification == "nearest_exclusion_candidate":
        return "hypothesis_nearest_subset_excluded"
    if classification == "raw_non_asset_context_for_catch_up":
        return "hypothesis_raw_gross_context"
    return "hypothesis_raw_gross"


def _status(balancing_adjustment: Decimal, target_delta: Decimal) -> str:
    if balancing_adjustment == Decimal("0.00"):
        return "matches_target_without_balancing_adjustment"
    if _materiality(balancing_adjustment, target_delta) == "material":
        return "matches_target_only_via_material_unexplained_plug"
    return "matches_target_with_minor_inferred_balancing_adjustment"


def _open_confirmation(
    amortization_delta: Decimal,
    balancing_adjustment: Decimal,
    materiality: str,
    excluded_nearest_rows: int,
    excluded_asset_rows: int,
) -> str:
    parts: list[str] = []
    if amortization_delta != Decimal("0.00"):
        parts.append("confirm asset amortization schedule")
    if excluded_asset_rows:
        parts.append("confirm direct asset rows are excluded")
    if excluded_nearest_rows:
        parts.append("confirm nearest-subset excluded/netted rows")
    if balancing_adjustment != Decimal("0.00"):
        if materiality == "material":
            parts.append("confirm material balancing adjustment source")
        else:
            parts.append("confirm minor balancing adjustment source")
    if not parts:
        return "confirm raw gross treatment"
    return "; ".join(parts)


def _amount_or_zero(value: str | None) -> Decimal:
    if value is None or value.strip() == "":
        return Decimal("0.00")
    return parse_amount(value)


def _materiality(balancing_adjustment: Decimal, target_delta: Decimal) -> str:
    if balancing_adjustment == Decimal("0.00"):
        return "none"
    if abs(balancing_adjustment) > Decimal("20.00"):
        return "material"
    if abs(target_delta) > Decimal("0.00") and (abs(balancing_adjustment) / abs(target_delta)) > Decimal("0.02"):
        return "material"
    return "minor"


def _percent(value: Decimal, target: Decimal) -> str:
    if target == Decimal("0.00"):
        return "0.00"
    return f"{(abs(value) / abs(target) * Decimal('100')).quantize(Decimal('0.01')):.2f}"


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return format_es(parse_amount(value))
