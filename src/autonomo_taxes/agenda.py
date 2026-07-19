from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from .tax_calendar import (
    TaxScheduleEvent,
    build_period_schedule_events,
    obligation_has_confirmed_schedule,
)


_TERMINAL_FILING_STATUSES = {"filed", "waived"}
_CASH_READY_STATUSES = {"not_required", "sufficient"}


def build_tax_agenda(
    *,
    periods: Sequence[Mapping[str, Any]],
    obligations_by_period: Mapping[str, Sequence[Mapping[str, Any]]],
    as_of: date,
    horizon_days: int,
    cash_period: str | None = None,
    cash_check: Mapping[str, Any] | None = None,
    settlements: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    through = as_of + timedelta(days=horizon_days)
    history_floor = date(as_of.year, 1, 1)
    events: list[dict[str, Any]] = []
    unscheduled: list[dict[str, Any]] = []

    for period in sorted(periods, key=lambda row: (str(row["starts_on"]), str(row["period_key"]))):
        period_key = str(period["period_key"])
        period_obligations = [
            dict(row)
            for row in obligations_by_period.get(period_key, ())
            if row.get("determination") in {"due", "unknown"}
            and row.get("filing_status") not in _TERMINAL_FILING_STATUSES
        ]
        if not period_obligations:
            continue

        scheduled = [
            row for row in period_obligations if obligation_has_confirmed_schedule(row)
        ]
        missing_schedule = [
            row for row in period_obligations if not obligation_has_confirmed_schedule(row)
        ]
        period_end = date.fromisoformat(str(period["ends_on"]))
        period_start = date.fromisoformat(str(period["starts_on"]))
        if missing_schedule and period_end >= history_floor and period_start <= through:
            unscheduled.append(
                {
                    "period": period_key,
                    "period_ends_on": period_end.isoformat(),
                    "forms": sorted(str(row["obligation_code"]) for row in missing_schedule),
                    "determinations": {
                        str(row["obligation_code"]): str(row["determination"])
                        for row in sorted(
                            missing_schedule,
                            key=lambda item: str(item["obligation_code"]),
                        )
                    },
                    "status": (
                        "classification_required"
                        if period_end < as_of
                        else "awaiting_confirmed_calendar"
                    ),
                    "reason": "confirmed_deadline_missing",
                }
            )
        if not scheduled:
            continue

        schedule_events = build_period_schedule_events(
            period_key=period_key,
            period_ends_on=period_end,
            obligations=scheduled,
            cash_check=cash_check if period_key == cash_period else None,
        )
        for event in schedule_events:
            if _unknown_only(event) and event.kind in {
                "intake-close",
                "draft",
                "cash",
                "evidence",
            }:
                continue
            classification = classify_checkpoint(event, as_of=as_of)
            if not _include_checkpoint(
                event,
                classification=classification,
                as_of=as_of,
                through=through,
                period_end=period_end,
                history_floor=history_floor,
            ):
                continue
            events.append(
                {
                    "period": period_key,
                    "date": event.event_date.isoformat(),
                    "kind": event.kind,
                    "summary": _agenda_summary(event),
                    "description": event.description,
                    "forms": list(event.forms),
                    "determinations": dict(event.determinations),
                    "days_until": (event.event_date - as_of).days,
                    **classification,
                }
            )

    events.extend(
        _settlement_events(
            settlements,
            as_of=as_of,
            through=through,
            history_floor=history_floor,
        )
    )
    events.sort(key=lambda row: (str(row["date"]), str(row["kind"]), str(row["period"])))

    hard_overdue = sum(1 for event in events if event["overdue"])
    warning_count = sum(1 for event in events if event["severity"] == "warning")
    cash = (
        {"period": cash_period, "check": cash_check}
        if cash_period is not None and cash_check is not None
        else None
    )
    cash_attention = bool(
        cash_check is not None
        and str(cash_check.get("status")) not in _CASH_READY_STATUSES
    )
    if hard_overdue:
        status = "overdue"
    elif warning_count or cash_attention:
        status = "attention"
    elif events or unscheduled:
        status = "upcoming"
    else:
        status = "empty"

    return {
        "schema_version": 1,
        "report_type": "tax_payment_cash_agenda",
        "as_of": as_of.isoformat(),
        "through": through.isoformat(),
        "horizon_days": horizon_days,
        "status": status,
        "summary": {
            "event_count": len(events),
            "hard_overdue_count": hard_overdue,
            "warning_count": warning_count,
            "unscheduled_period_count": len(unscheduled),
            "cash_status": cash_check.get("status") if cash_check is not None else None,
        },
        "next_action": events[0] if events else None,
        "events": events,
        "unscheduled_obligations": unscheduled,
        "cash": cash,
    }


def classify_checkpoint(
    event: TaxScheduleEvent,
    *,
    as_of: date,
) -> dict[str, Any]:
    days_until = (event.event_date - as_of).days
    determinations = dict(event.determinations)
    has_due_form = "due" in determinations.values()
    has_unknown_form = "unknown" in determinations.values()
    unknown_only = has_unknown_form and not has_due_form

    if days_until > 0:
        return {
            "status": "classification_due" if unknown_only else "upcoming",
            "severity": "info",
            "overdue": False,
        }
    if days_until == 0:
        if event.kind == "statutory" and has_due_form:
            status = "due_today"
        elif event.kind == "statutory" and has_unknown_form:
            status = "classification_due_today"
        elif event.kind == "direct-debit":
            status = (
                "classification_cutoff_today"
                if unknown_only
                else "direct_debit_cutoff_today"
            )
        else:
            status = "due_today"
        return {"status": status, "severity": "warning", "overdue": False}
    if event.kind == "statutory" and has_due_form:
        return {"status": "overdue", "severity": "critical", "overdue": True}
    if event.kind == "statutory" and has_unknown_form:
        return {
            "status": "classification_overdue",
            "severity": "warning",
            "overdue": False,
        }
    if (
        event.kind == "direct-debit"
        and event.statutory_due_on is not None
        and as_of <= event.statutory_due_on
    ):
        return {
            "status": (
                "classification_cutoff_passed"
                if unknown_only
                else "missed_direct_debit"
            ),
            "severity": "warning",
            "overdue": False,
        }
    return {"status": "elapsed", "severity": "info", "overdue": False}


def select_cash_period(
    *,
    periods: Sequence[Mapping[str, Any]],
    obligations_by_period: Mapping[str, Sequence[Mapping[str, Any]]],
    as_of: date,
) -> str | None:
    future: list[tuple[date, date, str]] = []
    overdue: list[tuple[date, date, str]] = []
    history_floor = date(as_of.year, 1, 1)
    for period in periods:
        period_key = str(period["period_key"])
        if period.get("period_type") != "quarter" or period.get("status") != "open":
            continue
        deadlines: list[date] = []
        for obligation in obligations_by_period.get(period_key, ()):
            if (
                obligation.get("determination") != "due"
                or obligation.get("filing_status") in _TERMINAL_FILING_STATUSES
                or not obligation_has_confirmed_schedule(obligation)
            ):
                continue
            raw_due = obligation.get("due_on") or obligation.get(
                "calendar_statutory_due_on"
            )
            if raw_due:
                due = date.fromisoformat(str(raw_due))
                if due >= history_floor:
                    deadlines.append(due)
        if deadlines:
            deadline = min(deadlines)
            candidate = (
                deadline,
                date.fromisoformat(str(period["starts_on"])),
                period_key,
            )
            if deadline >= as_of:
                future.append(candidate)
            else:
                overdue.append(candidate)
    if future:
        return min(future)[2]
    return max(overdue)[2] if overdue else None


def _include_checkpoint(
    event: TaxScheduleEvent,
    *,
    classification: Mapping[str, Any],
    as_of: date,
    through: date,
    period_end: date,
    history_floor: date,
) -> bool:
    if as_of <= event.event_date <= through:
        return True
    status = classification["status"]
    if status in {
        "overdue",
        "missed_direct_debit",
        "classification_cutoff_passed",
    }:
        return True
    return status == "classification_overdue" and period_end >= history_floor


def _settlement_events(
    settlements: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    through: date,
    history_floor: date,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for settlement in settlements:
        if settlement.get("status") == "ready" or not settlement.get("due_on"):
            continue
        due_on = date.fromisoformat(str(settlement["due_on"]))
        if due_on > through or due_on < history_floor:
            continue
        if due_on < as_of:
            status = "payment_confirmation_overdue"
        elif due_on == as_of:
            status = "payment_confirmation_due_today"
        else:
            status = "payment_confirmation_due"
        form = str(settlement["form"])
        events.append(
            {
                "period": str(settlement["period"]),
                "date": due_on.isoformat(),
                "kind": "payment-confirmation",
                "summary": "Confirm tax payment and archive bank evidence",
                "description": f"Modelo {form}",
                "forms": [form],
                "determinations": {form: "filed"},
                "days_until": (due_on - as_of).days,
                "status": status,
                "severity": "warning",
                "overdue": False,
                "settlement_status": settlement.get("status"),
                "expected_amount_eur": settlement.get("expected_amount_eur"),
            }
        )
    return events


def _unknown_only(event: TaxScheduleEvent) -> bool:
    determinations = dict(event.determinations).values()
    return "unknown" in determinations and "due" not in determinations


def _agenda_summary(event: TaxScheduleEvent) -> str:
    if not _unknown_only(event):
        return event.summary
    if event.kind == "direct-debit":
        return "Resolve form applicability before direct-debit cutoff"
    if event.kind == "statutory":
        return "Resolve form applicability before statutory deadline"
    return "Resolve form applicability"
