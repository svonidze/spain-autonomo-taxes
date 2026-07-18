from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from .ledger_db import LedgerDB
from .tax_rules import ANNUAL_FORM_CODES


_CLOSED_PERIOD_STATUSES = {"closed", "amended"}
_DATA_VALIDATION_KEYS = (
    "blocking_issues",
    "review_documents",
    "review_transactions",
    "target_derived_fx",
    "xolo_recorded_fx_after_cutover",
    "invalid_amount_transactions",
    "out_of_period_transactions",
)


def assess_annual_readiness(
    database: LedgerDB,
    *,
    year: int,
    form_code: str | None = None,
    allow_authoritative_history: bool = False,
) -> dict[str, Any]:
    if form_code is not None and form_code not in ANNUAL_FORM_CODES:
        raise ValueError(f"Unsupported annual form: {form_code}")

    activities = [
        row for row in database.list_business_activities() if _overlaps_year(row, year)
    ]
    expected_quarters = _expected_quarters(activities, year)
    period_rows = {row["period_key"]: row for row in database.list_periods()}
    blockers: list[dict[str, Any]] = []
    quarter_status: list[dict[str, Any]] = []

    if not activities:
        blockers.append({"kind": "business_activity_inventory_missing", "year": year})

    for period_key in expected_quarters:
        period = period_rows.get(period_key)
        if period is None:
            quarter_status.append({"period": period_key, "status": "missing"})
            blockers.append({"kind": "quarter_missing", "period": period_key})
            continue
        status = str(period["status"])
        quarter_status.append({"period": period_key, "status": status})
        if status not in _CLOSED_PERIOD_STATUSES:
            blockers.append(
                {"kind": "quarter_not_closed", "period": period_key, "status": status}
            )
        validation = database.validate_period(
            period_key,
            allow_authoritative_history=allow_authoritative_history,
        )
        categories = _validation_categories(validation, include_obligations=True)
        if categories:
            blockers.append(
                {
                    "kind": "quarter_accounting_unresolved",
                    "period": period_key,
                    "categories": categories,
                }
            )

    annual_key = str(year)
    annual_period = period_rows.get(annual_key)
    if annual_period is None:
        blockers.append({"kind": "annual_period_missing", "period": annual_key})
        annual_status = "missing"
        annual_obligations: list[dict[str, Any]] = []
    else:
        annual_status = str(annual_period["status"])
        annual_validation = database.validate_period(
            annual_key,
            allow_authoritative_history=allow_authoritative_history,
        )
        categories = _validation_categories(annual_validation, include_obligations=False)
        if categories:
            blockers.append(
                {
                    "kind": "annual_accounting_unresolved",
                    "period": annual_key,
                    "categories": categories,
                }
            )
        annual_obligations = database.list_obligations_with_deadlines(
            period_key=annual_key
        )

    asset_validation = database.validate_asset_year(year)
    asset_summary = {
        "ready": bool(asset_validation["ready"]),
        "asset_count": len(asset_validation["assets"]),
        "issue_count": len(asset_validation["issues"]),
        "issue_codes": sorted(
            {str(row["issue_code"]) for row in asset_validation["issues"]}
        ),
    }
    asset_schedule_required = form_code is None or form_code == "100"
    asset_summary["required_for_requested_scope"] = asset_schedule_required
    if asset_schedule_required and not asset_summary["ready"]:
        blockers.append(
            {
                "kind": "annual_asset_schedule_unresolved",
                "year": year,
                "issue_codes": asset_summary["issue_codes"],
            }
        )

    obligations_by_code = {
        str(row["obligation_code"]): row for row in annual_obligations
    }
    requested_forms: Iterable[str] = (
        (form_code,) if form_code is not None else ANNUAL_FORM_CODES
    )
    form_status: list[dict[str, Any]] = []
    for code in requested_forms:
        obligation = obligations_by_code.get(code)
        if obligation is None:
            form_status.append(
                {
                    "form": code,
                    "determination": "missing",
                    "filing_status": "missing",
                }
            )
            blockers.append({"kind": "annual_obligation_missing", "form": code})
            continue
        determination = str(obligation["determination"])
        filing_status = str(obligation["filing_status"])
        form_status.append(
            {
                "form": code,
                "determination": determination,
                "filing_status": filing_status,
                "due_on": obligation.get("due_on"),
            }
        )
        if determination == "unknown":
            blockers.append({"kind": "annual_obligation_unknown", "form": code})

    return {
        "schema_version": 1,
        "report_type": "annual_calculation_readiness",
        "year": year,
        "requested_form": form_code,
        "ready": not blockers,
        "expected_quarters": list(expected_quarters),
        "quarters": quarter_status,
        "annual_period": {"period": annual_key, "status": annual_status},
        "asset_year": asset_summary,
        "forms": form_status,
        "blockers": blockers,
        "calculation_stage": "complete_year" if not blockers else "not_ready",
    }


def annual_readiness_error(report: dict[str, Any]) -> str:
    labels = []
    for blocker in report["blockers"]:
        reference = blocker.get("period") or blocker.get("form") or blocker.get("year")
        labels.append(
            f"{blocker['kind']}:{reference}" if reference is not None else blocker["kind"]
        )
    return "; ".join(labels)


def _expected_quarters(
    activities: Iterable[dict[str, Any]],
    year: int,
) -> tuple[str, ...]:
    periods = []
    for quarter in range(1, 5):
        quarter_start, quarter_end = _quarter_bounds(year, quarter)
        if any(_overlaps(row, quarter_start, quarter_end) for row in activities):
            periods.append(f"{year}-Q{quarter}")
    return tuple(periods)


def _overlaps_year(activity: dict[str, Any], year: int) -> bool:
    return _overlaps(activity, date(year, 1, 1), date(year, 12, 31))


def _overlaps(activity: dict[str, Any], starts_on: date, ends_on: date) -> bool:
    activity_start = date.fromisoformat(str(activity["starts_on"]))
    activity_end = (
        date.fromisoformat(str(activity["ends_on"]))
        if activity.get("ends_on")
        else date.max
    )
    return activity_start <= ends_on and activity_end >= starts_on


def _quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    start_month = (quarter - 1) * 3 + 1
    end_month = quarter * 3
    end_day = 31 if end_month in {3, 12} else 30
    return date(year, start_month, 1), date(year, end_month, end_day)


def _validation_categories(
    validation: dict[str, Any],
    *,
    include_obligations: bool,
) -> list[str]:
    categories = [key for key in _DATA_VALIDATION_KEYS if validation.get(key)]
    if include_obligations and validation.get("unresolved_obligations"):
        categories.append("unresolved_obligations")
    return categories
