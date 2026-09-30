"""Versioned schedules for new, explicitly reviewed assets (not imported history)."""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

CALCULATION_VERSION = "calendar_daily_v1"
METHODS = {"immediate", "linear"}


def minor(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def quarter_end(value: date) -> date:
    month = ((value.month - 1) // 3 + 1) * 3
    return date(value.year, month, calendar.monthrange(value.year, month)[1])


def schedule(*, basis_minor: int, business_use_ratio: float, annual_rate_basis_points: int,
             placed_in_service_on: str, method: str) -> list[dict[str, Any]]:
    if method not in METHODS:
        raise ValueError("Choose immediate or linear depreciation")
    if type(basis_minor) is not int or basis_minor <= 0:
        raise ValueError("Amortizable basis must be a positive integer in EUR minor units")
    ratio = Decimal(str(business_use_ratio))
    if not ratio.is_finite() or not 0 < ratio <= 1:
        raise ValueError("Business use ratio must be above zero and at most one")
    if type(annual_rate_basis_points) is not int or not 100 <= annual_rate_basis_points <= 10000:
        raise ValueError("Annual rate must be between 1% and 100%")
    start = date.fromisoformat(placed_in_service_on)
    basis = minor(Decimal(basis_minor) * ratio)
    if basis <= 0:
        raise ValueError("Effective amortizable basis rounds to zero")
    if method == "immediate":
        return [{"period_key": f"{start.year}-Q{(start.month-1)//3+1}",
                 "recognition_on": start.isoformat(), "amount_minor": basis}]
    annual = Decimal(basis) * Decimal(annual_rate_basis_points) / 10000
    accumulated = Decimal(0)
    rounded = 0
    cursor = start
    rows = []
    while rounded < basis:
        end = quarter_end(cursor)
        accumulated += annual * Decimal((end - cursor).days + 1) / (366 if calendar.isleap(cursor.year) else 365)
        target = min(basis, minor(accumulated))
        if target > rounded:
            rows.append({"period_key": f"{end.year}-Q{(end.month-1)//3+1}",
                         "recognition_on": end.isoformat(), "amount_minor": target - rounded})
        rounded = target
        cursor = end + timedelta(days=1)
    return rows


def native_year_summary(connection, asset_id: str, year: int, *, through_date: str | None = None) -> dict[str, Any] | None:
    """Actual native recognition only; schedules never become external evidence."""
    import hashlib
    import json
    plan = connection.execute("SELECT * FROM asset_depreciation_plans WHERE asset_id=?", (asset_id,)).fetchone()
    if plan is None:
        return None
    cutoff = through_date or f"{year}-12-31"
    rows = connection.execute("""SELECT ae.*,t.lifecycle_status AS recognition_status,t.transaction_date,
        tt.deductible_irpf_minor FROM amortization_entries ae
        LEFT JOIN transactions t ON t.transaction_id=ae.recognition_transaction_id
        LEFT JOIN tax_treatments tt ON tt.transaction_id=t.transaction_id AND tt.treatment_type='invoice_review' AND tt.jurisdiction='ES'
        WHERE ae.asset_id=? ORDER BY ae.recognition_on,ae.amortization_entry_id""", (asset_id,)).fetchall()
    prior, current, pending = 0, 0, []
    proof = []
    for row in rows:
        recognized = row["recognition_status"] in {"posted", "included_in_snapshot"}
        valid = recognized and row["deductible_irpf_minor"] == row["amount_minor"] and row["transaction_date"] == row["recognition_on"]
        if row["tax_year"] == year and (not valid or not row["include_in_books"]):
            pending.append(row["amortization_entry_id"])
        if valid and row["transaction_date"] <= cutoff:
            if row["tax_year"] < year:
                prior += row["amount_minor"]
            elif row["tax_year"] == year:
                current += row["amount_minor"]
            proof.append([row["recognition_transaction_id"], row["amount_minor"], row["source_hash"]])
    return {"prior_minor": prior, "current_minor": current, "pending": pending,
            "source_hash": hashlib.sha256(json.dumps(proof).encode()).hexdigest(),
            "source_kind": "native_posted_journals"}
