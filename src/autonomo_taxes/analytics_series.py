from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from .cash_check import build_cash_check
from .tax_result_view import TAX_FORM_KEYS

ACTUAL_STATUSES = ("posted", "included_in_snapshot")
APPROVED_STATUSES = ("approved",)
REVIEW_STATUSES = ("received", "extracted", "needs_review")
EXCLUDED_STATUSES = ("duplicate", "rejected", "void")
REVIEW_AGING_BUCKETS = ("0-7", "8-30", "31-90", "90+")
COUNTERPARTY_TOP_LIMIT = 5
TREATMENT_MONEY_KEYS = (
    "taxable_base_minor",
    "vat_minor",
    "deductible_irpf_minor",
    "deductible_vat_minor",
)
QUARTER_END_MONTH = {1: 3, 2: 6, 3: 9, 4: 12}
MONTH_END_DAY = {3: 31, 6: 30, 9: 30, 12: 31}


@dataclass(frozen=True)
class AnalyticsQuery:
    period_key: str
    as_of: date

    @property
    def year(self) -> int:
        return int(self.period_key[:4])

    @property
    def quarter(self) -> int:
        return int(self.period_key[-1])


def build_analytics(
    connection: sqlite3.Connection,
    query: AnalyticsQuery,
    *,
    year_forms: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    through_month = QUARTER_END_MONTH[query.quarter]
    through = date(query.year, through_month, MONTH_END_DAY[through_month])
    quality = {
        "missing_fx_transaction_count": 0,
        "unclassified_expense_count": 0,
        "future_posted_transaction_count": 0,
        "conflicting_treatment_count": 0,
    }
    transactions = _load_transactions(connection, year=query.year, quality=quality)
    for transaction in transactions:
        if transaction["eur_minor"] is None:
            quality["missing_fx_transaction_count"] += 1
        if _scope_key(transaction, query.as_of) == "future_posted":
            quality["future_posted_transaction_count"] += 1
    datasets = {
        "business_result": _business_result(transactions, query=query, through=through),
        "cumulative_net": _cumulative_net(transactions, query=query, through=through),
        "quarterly_tax_due": _quarterly_tax_due(year_forms),
        "iva_position": _iva_position(year_forms),
        "reserve_bullet": _reserve_bullet(query.period_key, year_forms),
        "ytd_comparison": _ytd_comparison(transactions, query=query),
        "expense_structure": _expense_structure(
            transactions, query=query, through=through, quality=quality
        ),
        "review_aging": _review_aging(transactions, query=query),
        "counterparty_concentration": _counterparty_concentration(
            transactions, query=query, through=through
        ),
        "amortization": _amortization(connection, year=query.year),
    }
    return {
        "schema_version": 1,
        "scope": {
            "period": query.period_key,
            "year": query.year,
            "from": date(query.year, 1, 1).isoformat(),
            "through": through.isoformat(),
            "as_of": query.as_of.isoformat(),
            "currency": "EUR",
            "money_unit": "eur_minor",
        },
        "policy": {
            "actual_statuses": list(ACTUAL_STATUSES),
            "approved_statuses": list(APPROVED_STATUSES),
            "review_statuses": list(REVIEW_STATUSES),
            "excluded_statuses": list(EXCLUDED_STATUSES),
            "date_field": "transaction_date",
            "fx_policy": "stored_eur_amount_only",
        },
        "periods": [
            _period_summary(period_key, entry)
            for period_key, entry in year_forms.items()
        ],
        "datasets": datasets,
        "quality": quality,
    }


def _period_summary(period_key: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    period = entry.get("period")
    if not period:
        return {
            "period_key": period_key,
            "status": "missing",
            "starts_on": None,
            "ends_on": None,
            "amendment_period_key": None,
            "amendment_reason": None,
        }
    return {
        "period_key": period_key,
        "status": period.get("status"),
        "starts_on": period.get("starts_on"),
        "ends_on": period.get("ends_on"),
        "amendment_period_key": period.get("amendment_period_key"),
        "amendment_reason": period.get("amendment_reason"),
    }


def _load_transactions(
    connection: sqlite3.Connection,
    *,
    year: int,
    quality: dict[str, int],
) -> list[dict[str, Any]]:
    excluded = ", ".join(f"'{status}'" for status in EXCLUDED_STATUSES)
    rows = connection.execute(
        f"""
        SELECT
            t.transaction_id,
            t.transaction_date,
            t.entry_type,
            t.lifecycle_status,
            t.amount_minor,
            t.currency,
            t.amount_eur_minor,
            t.created_at,
            t.counterparty_id,
            c.display_name AS counterparty_name,
            tt.treatment_id,
            tt.taxable_base_minor,
            tt.vat_minor,
            tt.deductible_irpf_minor,
            tt.deductible_vat_minor,
            tt.aeat_expense_concept
        FROM transactions t
        LEFT JOIN counterparties c ON c.counterparty_id = t.counterparty_id
        LEFT JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
        WHERE t.transaction_date >= ?
          AND t.transaction_date <= ?
          AND t.lifecycle_status NOT IN ({excluded})
        ORDER BY t.transaction_date, t.transaction_id, tt.treatment_id
        """,
        (date(year - 1, 1, 1).isoformat(), date(year, 12, 31).isoformat()),
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    conflicted: set[str] = set()
    for row in rows:
        entry_type = str(row["entry_type"])
        if not (entry_type.startswith("income") or entry_type.startswith("expense")):
            continue
        transaction_id = str(row["transaction_id"])
        bucket = grouped.get(transaction_id)
        if bucket is None:
            bucket = {
                "transaction_id": transaction_id,
                "transaction_date": str(row["transaction_date"]),
                "kind": "income" if entry_type.startswith("income") else "expense",
                "lifecycle_status": str(row["lifecycle_status"]),
                "created_at": str(row["created_at"] or ""),
                "counterparty_id": row["counterparty_id"],
                "counterparty_name": row["counterparty_name"],
                "eur_minor": _eur_minor(row),
                "taxable_base_minor": None,
                "vat_minor": None,
                "deductible_irpf_minor": None,
                "deductible_vat_minor": None,
                "aeat_expense_concept": None,
                "treatment_count": 0,
            }
            grouped[transaction_id] = bucket
        if row["treatment_id"] is None:
            continue
        bucket["treatment_count"] += 1
        for key in TREATMENT_MONEY_KEYS:
            value = row[key]
            if value is None:
                continue
            current = bucket[key]
            if current is not None and int(current) != int(value):
                conflicted.add(transaction_id)
                continue
            bucket[key] = int(value)
        concept = row["aeat_expense_concept"]
        if concept is not None:
            current_concept = bucket["aeat_expense_concept"]
            if current_concept is not None and current_concept != str(concept):
                conflicted.add(transaction_id)
                continue
            bucket["aeat_expense_concept"] = str(concept)
    quality["conflicting_treatment_count"] = len(conflicted)
    return [
        bucket
        for transaction_id, bucket in grouped.items()
        if transaction_id not in conflicted
    ]


def _eur_minor(row: Mapping[str, Any]) -> int | None:
    if row["amount_eur_minor"] is not None:
        return int(row["amount_eur_minor"])
    if str(row["currency"] or "").upper() == "EUR":
        return int(row["amount_minor"])
    return None


def _scope_key(transaction: Mapping[str, Any], as_of: date) -> str:
    status = transaction["lifecycle_status"]
    transaction_date = date.fromisoformat(transaction["transaction_date"])
    if status in ACTUAL_STATUSES:
        return "actual" if transaction_date <= as_of else "future_posted"
    if status in APPROVED_STATUSES:
        return "approved_unposted" if transaction_date <= as_of else "approved_future"
    return "review"


def _income_base_minor(transaction: Mapping[str, Any]) -> int | None:
    base = transaction["taxable_base_minor"]
    if base:
        return int(base)
    return transaction["eur_minor"]


def _month_keys(year: int, through_month: int) -> list[str]:
    return [f"{year}-{month:02d}" for month in range(1, through_month + 1)]


def _quarter_keys(year: int, through_quarter: int) -> list[str]:
    return [f"{year}-Q{index}" for index in range(1, through_quarter + 1)]


def _month_elapsed(bucket: str, as_of: date) -> bool:
    return date.fromisoformat(f"{bucket}-01") <= as_of


def _quarter_elapsed(bucket: str, as_of: date) -> bool:
    quarter = int(bucket[-1])
    return date(int(bucket[:4]), quarter * 3 - 2, 1) <= as_of


def _business_result(
    transactions: list[dict[str, Any]],
    *,
    query: AnalyticsQuery,
    through: date,
) -> dict[str, Any]:
    months = _month_keys(query.year, through.month)
    quarters = _quarter_keys(query.year, query.quarter)
    series: dict[str, dict[str, dict[str, int]]] = {
        scope: {
            "income_base_minor": dict.fromkeys(months, 0),
            "deductible_expense_minor": dict.fromkeys(months, 0),
        }
        for scope in ("actual", "approved_unposted", "approved_future")
    }
    for transaction in transactions:
        month = transaction["transaction_date"][:7]
        if month not in series["actual"]["income_base_minor"]:
            continue
        scope = _scope_key(transaction, query.as_of)
        if scope not in series:
            continue
        if transaction["kind"] == "income":
            value = _income_base_minor(transaction)
            if value is None:
                continue
            series[scope]["income_base_minor"][month] += value
        else:
            series[scope]["deductible_expense_minor"][month] += int(
                transaction["deductible_irpf_minor"] or 0
            )

    def monthly_values(scope: str, measure: str, *, elapsed_only: bool) -> list[int | None]:
        values: list[int | None] = []
        for month in months:
            if elapsed_only and not _month_elapsed(month, query.as_of):
                values.append(None)
                continue
            values.append(series[scope][measure][month])
        return values

    def quarterly_values(scope: str, measure: str, *, elapsed_only: bool) -> list[int | None]:
        values: list[int | None] = []
        for quarter_key in quarters:
            quarter = int(quarter_key[-1])
            quarter_months = [
                f"{query.year}-{month:02d}"
                for month in range(quarter * 3 - 2, quarter * 3 + 1)
                if f"{query.year}-{month:02d}" in series[scope][measure]
            ]
            if elapsed_only and not _quarter_elapsed(quarter_key, query.as_of):
                values.append(None)
                continue
            values.append(sum(series[scope][measure][month] for month in quarter_months))
        return values

    def block(buckets: list[str], values_for: Any) -> dict[str, Any]:
        return {
            "buckets": buckets,
            "actual": {
                "income_base_minor": values_for("actual", "income_base_minor", True),
                "deductible_expense_minor": values_for(
                    "actual", "deductible_expense_minor", True
                ),
            },
            "approved_unposted": {
                "income_base_minor": values_for(
                    "approved_unposted", "income_base_minor", False
                ),
                "deductible_expense_minor": values_for(
                    "approved_unposted", "deductible_expense_minor", False
                ),
            },
            "approved_future": {
                "income_base_minor": values_for(
                    "approved_future", "income_base_minor", False
                ),
                "deductible_expense_minor": values_for(
                    "approved_future", "deductible_expense_minor", False
                ),
            },
        }

    return {
        "measure": "irpf_basis",
        "monthly": block(
            months,
            lambda scope, measure, elapsed: monthly_values(
                scope, measure, elapsed_only=elapsed
            ),
        ),
        "quarterly": block(
            quarters,
            lambda scope, measure, elapsed: quarterly_values(
                scope, measure, elapsed_only=elapsed
            ),
        ),
    }


def _cumulative_net(
    transactions: list[dict[str, Any]],
    *,
    query: AnalyticsQuery,
    through: date,
) -> dict[str, Any]:
    months = _month_keys(query.year, through.month)
    actual_net = dict.fromkeys(months, 0)
    reviewed_net = dict.fromkeys(months, 0)
    for transaction in transactions:
        month = transaction["transaction_date"][:7]
        if month not in actual_net:
            continue
        scope = _scope_key(transaction, query.as_of)
        if scope not in {"actual", "approved_unposted", "approved_future"}:
            continue
        if transaction["kind"] == "income":
            value = _income_base_minor(transaction)
            if value is None:
                continue
        else:
            value = -int(transaction["deductible_irpf_minor"] or 0)
        reviewed_net[month] += value
        if scope == "actual":
            actual_net[month] += value
    actual_values: list[int | None] = []
    projected_values: list[int] = []
    actual_running = 0
    projected_running = 0
    for month in months:
        projected_running += reviewed_net[month]
        projected_values.append(projected_running)
        if _month_elapsed(month, query.as_of):
            actual_running += actual_net[month]
            actual_values.append(actual_running)
        else:
            actual_values.append(None)
    return {
        "measure": "net_before_difficult_expenses",
        "buckets": months,
        "actual_minor": actual_values,
        "projected_minor": projected_values,
    }


def _form_values(entry: Mapping[str, Any], form_key: str) -> tuple[dict[str, str], str]:
    forms = entry.get("forms") or {}
    form = forms.get(form_key) or {}
    values = form.get("values") or {}
    source = str(form.get("display_state") or "unavailable")
    return values, source


def _text_to_minor(text: Any) -> int | None:
    if text is None:
        return None
    try:
        amount = Decimal(str(text))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    return int((amount * 100).to_integral_value())


def _modelo303_point(values: Mapping[str, str], source: str) -> dict[str, Any]:
    result_minor = _text_to_minor(values.get("71") or values.get("result"))
    carryforward_minor = _text_to_minor(
        values.get("compensation_carryforward") or values.get("72")
    )
    refund_minor = _text_to_minor(values.get("73"))
    if result_minor is None:
        disposition = None
    elif result_minor > 0:
        disposition = "payable"
    elif refund_minor:
        disposition = "refund"
    elif result_minor < 0 or (carryforward_minor or 0) > 0:
        disposition = "compensate"
    else:
        disposition = "none"
    return {
        "result_minor": result_minor,
        "payable_minor": max(result_minor, 0) if result_minor is not None else None,
        "carryforward_minor": carryforward_minor,
        "refund_requested_minor": refund_minor,
        "disposition": disposition,
        "source": source,
    }


def _quarterly_tax_due(year_forms: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    for period_key, entry in year_forms.items():
        m130_values, m130_source = _form_values(entry, TAX_FORM_KEYS["130"])
        m303_values, m303_source = _form_values(entry, TAX_FORM_KEYS["303"])
        m130_result = _text_to_minor(m130_values.get("19"))
        points.append(
            {
                "period_key": period_key,
                "modelo130": {
                    "result_minor": m130_result,
                    "payable_minor": (
                        max(m130_result, 0) if m130_result is not None else None
                    ),
                    "source": m130_source,
                },
                "modelo303": _modelo303_point(m303_values, m303_source),
            }
        )
    return {"points": points}


def _iva_position(year_forms: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    for period_key, entry in year_forms.items():
        values, source = _form_values(entry, TAX_FORM_KEYS["303"])
        point = _modelo303_point(values, source)
        points.append(
            {
                "period_key": period_key,
                "output_vat_minor": _text_to_minor(values.get("27")),
                "deductible_input_vat_minor": _text_to_minor(values.get("45")),
                "result_minor": point["result_minor"],
                "carryforward_minor": point["carryforward_minor"],
                "refund_requested_minor": point["refund_requested_minor"],
                "disposition": point["disposition"],
                "source": source,
            }
        )
    return {"points": points}


def _decimal_to_minor(value: Any) -> int | None:
    if value is None:
        return None
    return int((Decimal(str(value)) * 100).to_integral_value())


def _reserve_bullet(
    period_key: str,
    year_forms: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    entry = year_forms.get(period_key) or {}
    obligations = entry.get("obligations") or []
    due_forms = {
        str(row.get("obligation_code") or "")
        for row in obligations
        if row.get("determination") == "due"
    }
    due_forms.discard("")
    calculations: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    for form_code, form_key in TAX_FORM_KEYS.items():
        values, source = _form_values(entry, form_key)
        sources[form_code] = source
        if values:
            calculations[form_code] = {"blocked": False, "values": values}
    check = build_cash_check(
        calculations,
        due_forms=due_forms,
        available_eur=None,
    )
    return {
        "period_key": period_key,
        "status": check["status"],
        "required_tax_minor": _decimal_to_minor(check["required_tax_eur"]),
        "buffer_minor": _decimal_to_minor(check["buffer_eur"]),
        "recommended_reserve_minor": _decimal_to_minor(check["recommended_reserve_eur"]),
        "available_minor": None,
        "tax_shortfall_minor": _decimal_to_minor(check["tax_shortfall_eur"]),
        "reserve_shortfall_minor": _decimal_to_minor(check["reserve_shortfall_eur"]),
        "forms": [
            {
                "form": form["form"],
                "payable_minor": _decimal_to_minor(form["payable_eur"]),
                "status": form["status"],
                "source": sources.get(str(form["form"])),
            }
            for form in check["forms"]
        ],
    }


def _ytd_comparison(
    transactions: list[dict[str, Any]],
    *,
    query: AnalyticsQuery,
) -> dict[str, Any]:
    if query.as_of.year < query.year:
        through_month = 0
    elif query.as_of.year > query.year:
        through_month = 12
    else:
        through_month = query.as_of.month

    def totals(year: int) -> dict[str, int | None] | None:
        income = 0
        deductible = 0
        seen = False
        for transaction in transactions:
            transaction_date = date.fromisoformat(transaction["transaction_date"])
            if transaction_date.year != year:
                continue
            if transaction["lifecycle_status"] not in ACTUAL_STATUSES:
                continue
            if through_month == 0 or transaction_date.month > through_month:
                continue
            if transaction["kind"] == "income":
                value = _income_base_minor(transaction)
                if value is None:
                    continue
                income += value
                seen = True
            else:
                deductible += int(transaction["deductible_irpf_minor"] or 0)
                seen = True
        if not seen:
            return None
        return {
            "taxable_income_minor": income,
            "deductible_expense_minor": deductible,
            "net_minor": income - deductible,
        }

    empty = {
        "taxable_income_minor": None,
        "deductible_expense_minor": None,
        "net_minor": None,
    }
    return {
        "measure": "net_before_difficult_expenses",
        "through_month": through_month,
        "current_year": {"year": query.year, **(totals(query.year) or empty)},
        "previous_year": {"year": query.year - 1, **(totals(query.year - 1) or empty)},
    }


def _expense_structure(
    transactions: list[dict[str, Any]],
    *,
    query: AnalyticsQuery,
    through: date,
    quality: dict[str, int],
) -> dict[str, Any]:
    buckets: dict[str, dict[str, int]] = {}
    unclassified_count = 0
    for transaction in transactions:
        if transaction["kind"] != "expense":
            continue
        transaction_date = date.fromisoformat(transaction["transaction_date"])
        if transaction_date.year != query.year or transaction_date > through:
            continue
        if _scope_key(transaction, query.as_of) not in {
            "actual",
            "approved_unposted",
            "approved_future",
        }:
            continue
        gross = transaction["eur_minor"]
        if gross is None:
            continue
        concept = transaction["aeat_expense_concept"]
        if concept is None:
            unclassified_count += 1
        key = concept or "unclassified"
        bucket = buckets.setdefault(key, {"gross_minor": 0, "deductible_minor": 0})
        bucket["gross_minor"] += int(gross)
        bucket["deductible_minor"] += int(transaction["deductible_irpf_minor"] or 0)
    quality["unclassified_expense_count"] = unclassified_count
    ordered = sorted(
        buckets.items(),
        key=lambda item: (-item[1]["gross_minor"], item[0]),
    )
    return {
        "scope_statuses": list(ACTUAL_STATUSES + APPROVED_STATUSES),
        "buckets": [
            {
                "concept": key,
                "gross_minor": bucket["gross_minor"],
                "deductible_minor": bucket["deductible_minor"],
                "non_deductible_minor": bucket["gross_minor"] - bucket["deductible_minor"],
            }
            for key, bucket in ordered
        ],
    }


def _age_bucket(created_at: str, as_of: date) -> str:
    try:
        created = date.fromisoformat(created_at[:10])
    except ValueError:
        created = as_of
    age_days = max((as_of - created).days, 0)
    if age_days <= 7:
        return "0-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    return "90+"


def _review_aging(
    transactions: list[dict[str, Any]],
    *,
    query: AnalyticsQuery,
) -> dict[str, Any]:
    statuses = (*REVIEW_STATUSES, "approved_unposted")
    counts = {status: dict.fromkeys(REVIEW_AGING_BUCKETS, 0) for status in statuses}
    known_amounts = {status: dict.fromkeys(REVIEW_AGING_BUCKETS, 0) for status in statuses}
    approved_overdue = 0
    for transaction in transactions:
        status = transaction["lifecycle_status"]
        if status in REVIEW_STATUSES:
            series = status
        elif _scope_key(transaction, query.as_of) == "approved_unposted":
            series = "approved_unposted"
            approved_overdue += 1
        else:
            continue
        bucket = _age_bucket(transaction["created_at"], query.as_of)
        counts[series][bucket] += 1
        if transaction["eur_minor"] is not None:
            known_amounts[series][bucket] += abs(int(transaction["eur_minor"]))
    return {
        "as_of": query.as_of.isoformat(),
        "buckets": list(REVIEW_AGING_BUCKETS),
        "counts": {
            status: [counts[status][bucket] for bucket in REVIEW_AGING_BUCKETS]
            for status in statuses
        },
        "known_amount_minor": {
            status: [known_amounts[status][bucket] for bucket in REVIEW_AGING_BUCKETS]
            for status in statuses
        },
        "approved_overdue_count": approved_overdue,
    }


def _counterparty_concentration(
    transactions: list[dict[str, Any]],
    *,
    query: AnalyticsQuery,
    through: date,
) -> dict[str, Any]:
    totals: dict[str, dict[str, Any]] = {}
    for transaction in transactions:
        if transaction["kind"] != "income":
            continue
        transaction_date = date.fromisoformat(transaction["transaction_date"])
        if transaction_date.year != query.year or transaction_date > through:
            continue
        if _scope_key(transaction, query.as_of) not in {
            "actual",
            "approved_unposted",
            "approved_future",
        }:
            continue
        value = _income_base_minor(transaction)
        if value is None:
            continue
        key = str(transaction["counterparty_id"] or "")
        bucket = totals.setdefault(
            key,
            {
                "counterparty_id": transaction["counterparty_id"],
                "name": transaction["counterparty_name"],
                "income_minor": 0,
            },
        )
        bucket["income_minor"] += value
    ordered = sorted(
        totals.values(),
        key=lambda item: (-item["income_minor"], str(item["counterparty_id"] or "")),
    )
    top = ordered[:COUNTERPARTY_TOP_LIMIT]
    other_minor = sum(item["income_minor"] for item in ordered[COUNTERPARTY_TOP_LIMIT:])
    return {
        "scope_statuses": list(ACTUAL_STATUSES + APPROVED_STATUSES),
        "top": top,
        "other_minor": other_minor,
    }


def _amortization(connection: sqlite3.Connection, *, year: int) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT
            p.period_key,
            ae.asset_id,
            ae.amount_minor,
            a.asset_code,
            a.description
        FROM amortization_entries ae
        JOIN periods p ON p.period_id = ae.period_id
        JOIN assets a ON a.asset_id = ae.asset_id
        WHERE ae.include_in_books = 1
          AND p.period_key LIKE ?
        ORDER BY p.period_key, a.asset_code, ae.asset_id
        """,
        (f"{year}-Q%",),
    ).fetchall()
    by_period: dict[str, dict[str, Any]] = {}
    for row in rows:
        period_key = str(row["period_key"])
        point = by_period.setdefault(
            period_key,
            {"period_key": period_key, "total_minor": 0, "assets": []},
        )
        amount = int(row["amount_minor"])
        point["total_minor"] += amount
        point["assets"].append(
            {
                "asset_id": str(row["asset_id"]),
                "label": str(row["description"] or row["asset_code"]),
                "amount_minor": amount,
            }
        )
    return {
        "includes": "include_in_books_only",
        "points": [by_period[key] for key in sorted(by_period)],
    }
