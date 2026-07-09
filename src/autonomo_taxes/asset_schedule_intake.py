from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
import re

from .money import cents, parse_amount
from .parsers import quarter_end


ASSET_SCHEDULE_INTAKE_FIELDS = [
    "asset_id",
    "period",
    "asset_date",
    "purchase_period",
    "recipient",
    "number",
    "currency",
    "gross_basis_eur",
    "base_basis_eur",
    "candidate_scenario",
    "candidate_included",
    "candidate_basis",
    "candidate_rate",
    "candidate_amortization_delta_eur",
    "candidate_amortization_ytd_eur",
    "annual_constraint_source",
    "annual_constrained_quarter_total_delta",
    "annual_constrained_model_minus_target_delta",
    "xolo_ui_depreciable_asset",
    "xolo_ui_asset_status",
    "xolo_confirmed_included",
    "xolo_confirmed_basis_eur",
    "xolo_confirmed_rate",
    "xolo_confirmed_start_date",
    "xolo_confirmed_delta_eur",
    "xolo_confirmed_ytd_eur",
    "xolo_confirmed_source",
    "question",
]


CANDIDATE_SCENARIO = "exclude_2023_usd_low_value_or_subscription_vat_base_25pct"
CANDIDATE_RATE = Decimal("0.25")


def build_asset_schedule_intake(
    asset_candidates_csv: Path,
    candidate_quarter_reconciliation_csv: Path,
    annual_constrained_assets_csv: Path | None = None,
    asset_ui_evidence_csv: Path | None = None,
) -> list[dict[str, str]]:
    assets = _load_rows(asset_candidates_csv)
    periods = _load_rows(candidate_quarter_reconciliation_csv)
    annual_by_period = _annual_by_period(annual_constrained_assets_csv) if annual_constrained_assets_csv else {}
    ui_by_asset = _asset_ui_by_key(asset_ui_evidence_csv) if asset_ui_evidence_csv else {}
    rows: list[dict[str, str]] = []
    previous_ytd: dict[tuple[str, int], Decimal] = defaultdict(lambda: Decimal("0.00"))

    for period_row in periods:
        year = int(period_row["year"])
        quarter = int(period_row["quarter"])
        period = period_row["period"]
        end = quarter_end(year, quarter)
        annual = annual_by_period.get(period, {})
        for asset in assets:
            purchase_date = date.fromisoformat(asset["date"])
            if purchase_date > end:
                continue
            asset_id = _asset_id(asset)
            ui = ui_by_asset.get(_match_key(asset), {})
            included = not _is_excluded_2023_low_value_or_subscription(asset)
            basis = parse_amount(asset["base_basis_eur"]) if asset.get("base_basis_eur") else Decimal("0.00")
            ytd = _straight_line_ytd(basis, purchase_date, end, CANDIDATE_RATE) if included else Decimal("0.00")
            previous_key = (asset_id, year)
            delta = cents(ytd - previous_ytd[previous_key])
            previous_ytd[previous_key] = ytd
            rows.append(
                {
                    "asset_id": asset_id,
                    "period": period,
                    "asset_date": asset["date"],
                    "purchase_period": asset["purchase_period"],
                    "recipient": asset["recipient"],
                    "number": asset["number"],
                    "currency": asset["currency"],
                    "gross_basis_eur": asset["gross_basis_eur"],
                    "base_basis_eur": asset["base_basis_eur"],
                    "candidate_scenario": CANDIDATE_SCENARIO,
                    "candidate_included": "yes" if included else "no",
                    "candidate_basis": "vat_base",
                    "candidate_rate": str(CANDIDATE_RATE),
                    "candidate_amortization_delta_eur": _money(delta),
                    "candidate_amortization_ytd_eur": _money(ytd),
                    "annual_constraint_source": annual.get("annual_constraint_source", ""),
                    "annual_constrained_quarter_total_delta": annual.get("annual_constrained_amortization_delta", ""),
                    "annual_constrained_model_minus_target_delta": annual.get(
                        "annual_constrained_model_minus_target_delta", ""
                    ),
                    "xolo_ui_depreciable_asset": ui.get("ui_depreciable_asset", ""),
                    "xolo_ui_asset_status": ui.get("status", ""),
                    "xolo_confirmed_included": "",
                    "xolo_confirmed_basis_eur": "",
                    "xolo_confirmed_rate": "",
                    "xolo_confirmed_start_date": "",
                    "xolo_confirmed_delta_eur": "",
                    "xolo_confirmed_ytd_eur": "",
                    "xolo_confirmed_source": "",
                    "question": _question(asset, period, included, ui),
                }
            )
    return rows


def write_asset_schedule_intake_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ASSET_SCHEDULE_INTAKE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_asset_schedule_intake_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_asset: dict[str, list[dict[str, str]]] = defaultdict(list)
    by_period: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for row in rows:
        by_asset[row["asset_id"]].append(row)
        by_period[row["period"]] += parse_amount(row["candidate_amortization_delta_eur"])

    lines = [
        "# Asset Schedule Intake",
        "",
        "This is a working intake table for Xolo's submitted asset amortization schedule.",
        "It records the local candidate per-asset quarterly amortization, but it does not confirm Xolo treatment.",
        "Xolo UI asset evidence is classification evidence only; it still does not confirm the submitted asset schedule.",
        "",
        "## Summary",
        "",
        f"- Asset rows: `{len(by_asset)}`.",
        f"- Asset-quarter rows: `{len(rows)}`.",
        f"- Candidate scenario: `{CANDIDATE_SCENARIO}`.",
        "- Xolo-confirmed fields are intentionally blank until the submitted schedule is available.",
        "",
        "## Candidate Quarter Totals",
        "",
        "| Period | Candidate asset amortization delta |",
        "|---|---:|",
    ]
    for period in sorted(by_period):
        lines.append(f"| {period} | {_money(by_period[period])} |")

    lines.extend(
        [
            "",
            "## Asset Questions",
            "",
            "| Asset | First period | Candidate included | UI asset? | UI status | Basis | Last candidate YTD | Question |",
            "|---|---|---|---|---|---:|---:|---|",
        ]
    )
    for asset_id, asset_rows in sorted(by_asset.items()):
        first = asset_rows[0]
        last = asset_rows[-1]
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(asset_id),
                    first["period"],
                    first["candidate_included"],
                    first["xolo_ui_depreciable_asset"],
                    _cell(first["xolo_ui_asset_status"]),
                    first["base_basis_eur"],
                    last["candidate_amortization_ytd_eur"],
                    _cell(first["question"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _annual_by_period(path: Path) -> dict[str, dict[str, str]]:
    return {row["period"]: row for row in _load_rows(path)}


def _asset_ui_by_key(path: Path) -> dict[tuple[str, str, str], dict[str, str]]:
    return {_match_key(row): row for row in _load_rows(path)}


def _straight_line_ytd(amount: Decimal, purchase_date: date, period_end: date, rate: Decimal) -> Decimal:
    if purchase_date > period_end:
        return Decimal("0.00")
    year_start = date(period_end.year, 1, 1)
    active_start = max(purchase_date, year_start)
    if active_start > period_end:
        return Decimal("0.00")
    days = Decimal(str((period_end - active_start).days + 1))
    return cents(amount * rate * days / Decimal("365"))


def _is_excluded_2023_low_value_or_subscription(asset: dict[str, str]) -> bool:
    if not asset["date"].startswith("2023-") or asset["currency"] != "USD":
        return False
    text = f"{asset['recipient']} {asset['number']}".lower()
    if "github" in text or "linqpad" in text:
        return True
    return parse_amount(asset["gross_basis_eur"]) < Decimal("300.00")


def _asset_id(asset: dict[str, str]) -> str:
    raw = f"{asset['date']}-{asset['number']}-{asset['recipient']}"
    value = re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-").upper()
    return value[:80]


def _question(asset: dict[str, str], period: str, included: bool, ui: dict[str, str]) -> str:
    if ui.get("status") == "local_asset_candidate_without_ui_banner":
        return (
            f"For {period}, Xolo UI did not show a depreciable-asset banner for "
            f"{asset['date']} {asset['number']} {asset['recipient']}; confirm whether this row was direct-expensed, "
            "split/multiple treatment, or capitalized in the investment-goods book."
        )
    if not included:
        if ui.get("status") == "ui_confirms_depreciable_asset":
            return (
                f"For {period}, Xolo UI marks {asset['date']} {asset['number']} {asset['recipient']} "
                "as a depreciable asset, but the local candidate excludes it; confirm whether it was "
                "direct-expensed, capitalized and amortized, excluded, or booked in another source-book row."
            )
        return (
            f"For {period}, confirm whether {asset['date']} {asset['number']} {asset['recipient']} "
            "was direct-expensed, capitalized, or excluded; the local candidate excludes it."
        )
    if ui.get("status") == "ui_confirms_depreciable_asset":
        return (
            f"For {period}, Xolo UI marks {asset['date']} {asset['number']} {asset['recipient']} as a depreciable asset; "
            "confirm the submitted schedule basis, rate, start date, quarter amortization, and YTD amortization."
        )
    return (
        f"For {period}, confirm Xolo asset schedule for {asset['date']} {asset['number']} {asset['recipient']}: "
        "basis, rate, start date, quarter amortization, and YTD amortization."
    )


def _match_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        row.get("date") or row.get("asset_date", ""),
        _normalize(row.get("recipient", "")),
        _normalize(row.get("number", "")),
    )


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
