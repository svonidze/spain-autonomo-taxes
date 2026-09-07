from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
import re

from .money import cents, format_es, parse_amount


TIMING_AUDIT_FIELDS = [
    "year",
    "period",
    "closure_status",
    "balancing_adjustment_eur",
    "cumulative_year_balance_eur",
    "year_balance_total_eur",
    "period_timing_signal",
    "year_timing_signal",
    "timing_source_refs",
    "timing_source_count",
    "next_local_action",
]

TIMING_KEYWORDS = re.compile(
    r"(catch[- ]?up|carry|carried|defer|deferr|net|nett|cancel|correct|transition|"
    r"principal|surcharge|apremio|regularization|revers|prior[- ]?quarter)",
    re.I,
)

NEAR_ZERO_YEAR_THRESHOLD = Decimal("5.00")
NEAR_ZERO_PERIOD_THRESHOLD = Decimal("1.00")


def build_timing_audit(
    quarter_closure_csv: Path,
    source_findings_csv: Path | None = None,
) -> list[dict[str, str]]:
    closure_rows = sorted(_load_rows(quarter_closure_csv), key=lambda row: _period_key(row["period"]))
    source_by_period = _group_timing_source_refs(_load_rows(source_findings_csv)) if source_findings_csv else {}
    totals_by_year: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    cumulative_by_year: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    balances_by_period: dict[str, Decimal] = {}

    for row in closure_rows:
        balance = _amount(row["balancing_adjustment_eur"])
        balances_by_period[row["period"]] = balance
        totals_by_year[_period_year(row["period"])] += balance

    output: list[dict[str, str]] = []
    for row in closure_rows:
        period = row["period"]
        year = _period_year(period)
        balance = balances_by_period[period]
        cumulative_by_year[year] = cents(cumulative_by_year[year] + balance)
        timing_refs = source_by_period.get(period, [])
        output.append(
            {
                "year": year,
                "period": period,
                "closure_status": row["closure_status"],
                "balancing_adjustment_eur": _money(balance),
                "cumulative_year_balance_eur": _money(cumulative_by_year[year]),
                "year_balance_total_eur": _money(totals_by_year[year]),
                "period_timing_signal": _period_signal(balance),
                "year_timing_signal": _year_signal(totals_by_year[year]),
                "timing_source_refs": "; ".join(timing_refs),
                "timing_source_count": str(len(timing_refs)),
                "next_local_action": _next_local_action(balance, totals_by_year[year], timing_refs),
            }
        )
    return output


def write_timing_audit_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TIMING_AUDIT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_timing_audit_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_year = _group_by_year(rows)
    near_zero_years = [year for year, items in by_year.items() if items[0]["year_timing_signal"] == "annual_net_near_zero_after_hypotheses"]
    material_years = [year for year, items in by_year.items() if items[0]["year_timing_signal"] != "annual_net_near_zero_after_hypotheses"]

    lines = [
        "# Modelo 130 Timing And Carry-Forward Audit",
        "",
        "This report is a local diagnostic over the current quarter-closure hypotheses.",
        "It does not prove Xolo's source-book treatment; it shows whether remaining balances look like isolated material adjustments or timing/netting patterns after the current row and amortization hypotheses.",
        "",
        "## Summary",
        "",
        f"- Periods checked: `{len(rows)}`.",
        f"- Annual near-zero residual years after current hypotheses: `{', '.join(near_zero_years) if near_zero_years else 'none'}`.",
        f"- Annual material residual years after current hypotheses: `{', '.join(material_years) if material_years else 'none'}`.",
        "- Near-zero years should be treated as asset-schedule/row-inclusion confirmation work, not as proof of Xolo accounting.",
        "- Material residual years still need a source-book adjustment, catch-up, reclassification, or missing/non-Xolo row explanation.",
        "",
        "## Year Totals",
        "",
        "| Year | Balance total | Signal | Timing source refs |",
        "|---|---:|---|---|",
    ]
    for year, items in by_year.items():
        refs = sorted({ref for row in items for ref in _split_refs(row["timing_source_refs"])})
        lines.append(
            "| "
            + " | ".join(
                [
                    year,
                    _fmt(items[0]["year_balance_total_eur"]),
                    items[0]["year_timing_signal"],
                    _cell("; ".join(refs)),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Period Timeline",
            "",
            "| Period | Balance | Cumulative year balance | Period signal | Timing refs | Next local action |",
            "|---|---:|---:|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    _fmt(row["balancing_adjustment_eur"]),
                    _fmt(row["cumulative_year_balance_eur"]),
                    row["period_timing_signal"],
                    _cell(row["timing_source_refs"]),
                    _cell(row["next_local_action"]),
                ]
            )
            + " |"
        )

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _load_rows(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _group_timing_source_refs(rows: list[dict[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        searchable = " ".join(
            [
                row.get("row_ref", ""),
                row.get("source_status", ""),
                row.get("finding", ""),
                row.get("impact", ""),
            ]
        )
        if TIMING_KEYWORDS.search(searchable):
            grouped[row.get("period", "")].append(row.get("row_ref", ""))
    return {period: sorted(ref for ref in refs if ref) for period, refs in grouped.items()}


def _group_by_year(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["year"]].append(row)
    return dict(sorted(grouped.items()))


def _period_signal(balance: Decimal) -> str:
    if abs(balance) <= NEAR_ZERO_PERIOD_THRESHOLD:
        return "period_residual_near_zero_after_hypotheses"
    if balance > Decimal("0.00"):
        return "target_above_model_needs_addition_or_catchup"
    return "model_above_target_needs_exclusion_or_basis_reduction"


def _year_signal(total: Decimal) -> str:
    if abs(total) <= NEAR_ZERO_YEAR_THRESHOLD:
        return "annual_net_near_zero_after_hypotheses"
    if total > Decimal("0.00"):
        return "annual_positive_submitted_adjustment_remains"
    return "annual_negative_exclusion_or_basis_reduction_remains"


def _next_local_action(balance: Decimal, year_total: Decimal, timing_refs: list[str]) -> str:
    if abs(year_total) <= NEAR_ZERO_YEAR_THRESHOLD:
        return "Prioritize confirming asset schedule and exact included/excluded rows; annual residual is already near zero under current hypotheses."
    if timing_refs:
        return "Review timing source refs and ask Xolo to confirm whether these rows were carried, corrected, deferred, netted, or reversed in the source books."
    if balance > Decimal("0.00"):
        return "Look for missing source-book additions, catch-up rows, reclassifications, or non-Xolo local rows."
    if balance < Decimal("0.00"):
        return "Look for exclusions, VAT/base reductions, netting, reversals, or deferred rows."
    return "No local action beyond source-book confirmation."


def _period_year(period: str) -> str:
    return period.split("-", 1)[0]


def _period_key(period: str) -> tuple[int, int]:
    year, quarter = period.split("-Q", 1)
    return int(year), int(quarter)


def _amount(value: str) -> Decimal:
    return parse_amount(value) if value else Decimal("0.00")


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _split_refs(value: str) -> list[str]:
    return [part for part in value.split("; ") if part]


def _fmt(value: str) -> str:
    return format_es(parse_amount(value))


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
