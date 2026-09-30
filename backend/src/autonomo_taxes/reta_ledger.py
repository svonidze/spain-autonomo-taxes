"""Ledger side of the RETA bracket check: posted income, alta, bases, blockers.

``reta.bracket_check`` stays pure; this module reads the canonical ledger and
reports what makes its year-to-date income untrustworthy as reason codes.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .ledger_db import LedgerDB
from .money import cents
from .reta import BaseElection, bracket_check, parse_reta_table
from .tax_engine import ZERO, CalculationBlocked, calculate_modelo100_business_support
from .tax_row_loader import load_tax_rows
from .tax_rules import difficult_expense_rule_for_year

SOCIAL_SECURITY_CONCEPT = "G45"
UNPOSTED_STATUSES = ("received", "extracted", "needs_review", "approved")


class RetaInputUnavailable(ValueError):
    """The check cannot run at all; ``reason`` is the code a caller reports."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def ledger_bracket_check(
    db: LedgerDB,
    *,
    year: int,
    as_of: date,
    through: date | None = None,
) -> dict[str, Any]:
    """Run ``bracket_check`` on the posted rows of 1 January..``through``.

    ``through`` defaults to the last month end before ``as_of`` (31 December
    for a past year). Nothing is projected.
    """
    if through is None:
        through = min(date(year, 12, 31), as_of.replace(day=1) - timedelta(days=1))
        if through < date(year, 1, 31):
            raise RetaInputUnavailable("window_empty", f"No month of {year} has ended by {as_of.isoformat()}")
    start = date(year, 1, 1)
    profiles = db.connection.execute("SELECT taxpayer_profile_id FROM taxpayer_profile").fetchall()
    if len(profiles) != 1:
        raise RetaInputUnavailable("profile_unavailable", "The RETA check requires exactly one taxpayer profile")
    stored = db.reta_rate_table(year)
    table = None if stored is None else parse_reta_table(stored["payload_json"].encode("utf-8"))
    alta_periods = [
        (date.fromisoformat(row["starts_on"]), date.fromisoformat(row["ends_on"]) if row["ends_on"] else None)
        for row in db.list_business_activities(taxpayer_profile_id=profiles[0][0])
    ]
    rows = db.list_reta_base_elections()
    starts = [date.fromisoformat(row["effective_from"]) for row in rows]
    reasons: list[str] = []

    # Approved rows of a filed Xolo source book count as posted, as in the
    # period dashboard; verify-history adjustments never reach production.
    authoritative = {row["transaction_id"] for row in db.list_authoritative_history_transactions(year=year)}
    placeholders = ", ".join("?" for _ in UNPOSTED_STATUSES)
    unposted = [
        row[0]
        for row in db.connection.execute(
            f"""
            SELECT transaction_id FROM transactions
            WHERE transaction_date BETWEEN ? AND ? AND lifecycle_status IN ({placeholders})
              AND entry_type <> 'verify_history_adjustment'
            ORDER BY transaction_date, transaction_id
            """,
            (start.isoformat(), through.isoformat(), *UNPOSTED_STATUSES),
        )
        if row[0] not in authoritative
    ]
    if unposted:
        reasons.append("unposted_rows_in_window")

    known_periods = {row["period_key"] for row in db.list_periods()}
    missing_periods = []
    for quarter in range(1, (through.month + 2) // 3 + 1):
        first = date(year, quarter * 3 - 2, 1)
        last = min(date(year, quarter * 3, monthrange(year, quarter * 3)[1]), through)
        in_alta = any(begin <= last and (end is None or end >= first) for begin, end in alta_periods)
        if in_alta and f"{year}-Q{quarter}" not in known_periods:
            missing_periods.append(f"{year}-Q{quarter}")
    if missing_periods:
        reasons.append("period_missing")

    # An election applies until the next one starts; keep those in force on a window day.
    ends = [following - timedelta(days=1) for following in starts[1:]] + [through]
    in_window = [row for row, begin, end in zip(rows, starts, ends) if begin <= through and end >= start]
    if len({row["worker_kind"] for row in in_window}) > 1:
        reasons.append("worker_kind_changed")
    worker_kind = in_window[-1]["worker_kind"] if in_window else "individual"

    # Ley 20/2007 art. 38 ter: months 13-24 of a tarifa plana need their own
    # TGSS resolution (income under the SMI). A run that reaches month 13 with
    # no row recorded from that day is an unverified extension.
    run_start: date | None = None
    for row, begin, end in zip(rows, starts, ends):
        if row["regime"] != "tarifa_plana":
            run_start = None
            continue
        run_start = run_start or begin
        month_13 = _plus_year(run_start)
        if begin < month_13 <= end and max(month_13, start) <= min(end, through):
            reasons.append("tarifa_plana_extension_unverified")
            break

    irpf_net = contributions = blocked = None
    try:
        tax_rows = [
            row
            for row in load_tax_rows(db, year, allow_authoritative_history=True)
            if row.tax_date <= through
        ]
        rule = difficult_expense_rule_for_year(year)
        support = calculate_modelo100_business_support(
            tax_rows,
            year=year,
            difficult_expenses_rate=rule.rate,
            difficult_expenses_cap=rule.annual_cap_eur,
        )
    except CalculationBlocked as exc:
        blocked = str(exc)
    else:
        irpf_net = _minor(support.values["business_net_income"])
        # Deducted contributions only; a non-deductible apremio surcharge is not in them.
        contributions = _minor(sum(
            (
                row.deductible_irpf_eur
                for row in tax_rows
                if row.aeat_expense_concept == SOCIAL_SECURITY_CONCEPT
                and row.include_modelo130
                and row.kind != "income"
            ),
            ZERO,
        ))

    result = bracket_check(
        table,
        year=year,
        as_of=as_of,
        through=through,
        irpf_net_income_minor=irpf_net,
        contributions_added_back_minor=contributions,
        worker_kind=worker_kind,
        alta_periods=alta_periods,
        elections=[
            BaseElection(begin, row["regime"], row["monthly_base_minor"]) for row, begin in zip(rows, starts)
        ],
        reasons=reasons,
    )
    result["ledger"] = {
        "unposted_transaction_ids": unposted,
        "missing_periods": missing_periods,
        "blocked_detail": blocked,
    }
    return result


def _plus_year(day: date) -> date:
    try:
        return day.replace(year=day.year + 1)
    except ValueError:  # 29 February: month 13 starts on 1 March.
        return date(day.year + 1, 3, 1)


def _minor(value: Decimal) -> int:
    return int(cents(value) * 100)
