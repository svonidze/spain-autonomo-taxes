from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import re
from typing import Any, Mapping, Sequence

from .tax_calendar import TaxScheduleEvent, _effective_statutory_due


# Recurring, non-filing duties an autonomo must not miss. Filing deadlines come from
# reference/tax-calendars/<year>.json; this module covers everything around them.
#
# The Modelo 720/721 *threshold* determination itself already lives in
# obligations.py (_modelo720/_modelo721) and is not duplicated here: the
# MODELO720_721_THRESHOLD_REVIEW reminder below only tells the operator when to
# go check it, not what the thresholds are.
_PERIOD_PATTERN = re.compile(r"^(?P<year>20\d{2})(?:-Q(?P<quarter>[1-4]))?$")
SOURCE_CHECKED_ON = date(2026, 9, 23)

# How many months beyond the prepared period to also surface reminders for.
# `period prepare` normally runs once a period is already over, so limiting
# occurrences to the prepared period's own months would show only overdue
# items; extending one quarter forward keeps the next actionable dates visible.
_COVERAGE_EXTENSION_MONTHS = 3


@dataclass(frozen=True)
class PeriodicActionDefinition:
    code: str
    cadence: str
    title: str
    detail: str
    source_url: str
    source_citation: str
    source_kind: str
    source_checked_on: date = SOURCE_CHECKED_ON


ELECTRONIC_NOTIFICATIONS_CHECK = PeriodicActionDefinition(
    code="electronic_notifications_check",
    cadence="monthly",
    title="Check DEHu / AEAT and Social Security electronic notification inboxes",
    detail=(
        "Checking monthly is a minimum; also enable DEHu/AEAT and Seguridad Social "
        "email alerts so you are notified immediately instead of relying on this "
        "checklist alone. Electronic notification is mandatory for autonomos (not "
        "merely opt-in), and an unopened notification is deemed rejected ten "
        "calendar days after it becomes available, with the underlying procedure "
        "continuing regardless."
    ),
    source_url="https://www.boe.es/buscar/act.php?id=BOE-A-2015-10565",
    source_citation="Ley 39/2015, art. 43.2",
    source_kind="boe_law",
)

RETA_CONTRIBUTION_FUNDS_CHECK = PeriodicActionDefinition(
    code="reta_contribution_funds_check",
    cadence="monthly",
    title="Have funds ready for the RETA contribution direct debit",
    detail=(
        "Seguridad Social debits the monthly RETA (autonomo) contribution by TGSS "
        "domiciliation on the last business day of the month; have the funds ready "
        "one business day earlier. Check pending receipts there if a debit does "
        "not go through."
    ),
    source_url=(
        "https://portal.seg-social.gob.es/wps/portal/importass/importass/Categorias/"
        "Consulta+de+pagos+y+deudas/ConsultaRecibos"
    ),
    source_citation="TGSS Import@ss: Consulta de pagos y deudas",
    source_kind="tgss_procedure",
)

PERIOD_BACKUP_AND_FILED_CONFIRMATION = PeriodicActionDefinition(
    code="period_backup_and_filed_confirmation",
    cadence="quarterly",
    title="Back up period documents and confirm filed returns at AEAT",
    detail=(
        "AEAT does not keep the taxpayer's invoices, so back up this period's "
        "documents independently, then confirm the filed returns appear in AEAT's "
        "'declaraciones presentadas' service."
    ),
    source_url=(
        "https://sede.agenciatributaria.gob.es/Sede/en_gb/irpf/declaraciones-presentadas/"
        "consulta-declaraciones-presentadas.html"
    ),
    source_citation="AEAT: Consulta de declaraciones presentadas",
    source_kind="aeat_service",
)

MODELO303_Q4_REFUND_CHOICE = PeriodicActionDefinition(
    code="modelo303_q4_refund_choice",
    cadence="conditional",
    title="Choose devolucion instead of carrying forward a positive Q4 VAT balance",
    detail=(
        "A VAT balance in your favour still pending compensation can only be "
        "compensated in later self-assessments (LIVA art. 99.Cinco); at the "
        "year's last self-assessment you may instead request its refund as it "
        "stands at 31 December (LIVA art. 115.Uno), but once carried forward "
        "instead, it can only be refunded a year later. Re-run the Modelo 303 "
        "calculation with --final-vat-settlement refund before filing if you "
        "choose the refund."
    ),
    source_url="https://www.boe.es/buscar/act.php?id=BOE-A-1992-28740",
    source_citation="Ley 37/1992 (LIVA), art. 99.Cinco and art. 115.Uno",
    source_kind="boe_law",
)

MODELO720_721_THRESHOLD_REVIEW = PeriodicActionDefinition(
    code="modelo720_721_threshold_review",
    cadence="annual",
    title="Check Modelo 720/721 foreign-asset reporting thresholds",
    detail=(
        "Review foreign account, securities, real-estate and virtual-currency "
        "values against the Modelo 720/721 reporting thresholds. These "
        "thresholds apply to the values held on 31 December of the previous "
        "year, before the annual filing window (1 January to 31 March) closes."
    ),
    source_url="https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GI34.shtml",
    source_citation=(
        "AEAT Modelo 720 procedure page; see also Modelo 721 "
        "(https://sede.agenciatributaria.gob.es/Sede/procedimientoini/GI55.shtml)"
    ),
    source_kind="aeat_service",
)

RETA_BASE_CHANGE_WINDOW = PeriodicActionDefinition(
    code="reta_base_change_window",
    cadence="recurring_window",
    title="RETA contribution base change window closes",
    detail=(
        "The RETA contribution base can be changed up to six times a year. A request "
        "filed by this date takes effect on 1 March, 1 May, 1 July, 1 September, "
        "1 November or 1 January, following the two-month request window."
    ),
    source_url=(
        "https://portal.seg-social.gob.es/wps/portal/importass/importass/Categorias/"
        "Altas,+bajas+y+modificaciones/Bajas+y+modificaciones/ModDatosAutonomos"
    ),
    source_citation="LGSS art. 308; TGSS Import@ss: Modificacion de datos de autonomos",
    source_kind="tgss_procedure",
)

PERIODIC_ACTION_DEFINITIONS: tuple[PeriodicActionDefinition, ...] = (
    ELECTRONIC_NOTIFICATIONS_CHECK,
    RETA_CONTRIBUTION_FUNDS_CHECK,
    PERIOD_BACKUP_AND_FILED_CONFIRMATION,
    MODELO303_Q4_REFUND_CHOICE,
    MODELO720_721_THRESHOLD_REVIEW,
    RETA_BASE_CHANGE_WINDOW,
)

# (deadline_month, effective_month, effective_year_offset). The request window is the
# two calendar months before the deadline's month; effective_year_offset handles the
# Nov-Dec window, whose 1 January effective date falls in the following year.
_BASE_CHANGE_WINDOWS: tuple[tuple[int, int, int], ...] = (
    (2, 3, 0),
    (4, 5, 0),
    (6, 7, 0),
    (8, 9, 0),
    (10, 11, 0),
    (12, 1, 1),
)


def _period_months(period_key: str) -> tuple[tuple[int, int], ...]:
    match = _PERIOD_PATTERN.fullmatch(period_key)
    if match is None:
        raise ValueError(f"Unsupported period key: {period_key}")
    year = int(match.group("year"))
    quarter = match.group("quarter")
    if quarter is None:
        return tuple((year, month) for month in range(1, 13))
    start_month = (int(quarter) - 1) * 3 + 1
    return tuple((year, month) for month in range(start_month, start_month + 3))


def _following_months(period_end: date, count: int) -> tuple[tuple[int, int], ...]:
    year, month = period_end.year, period_end.month
    result: list[tuple[int, int]] = []
    for _ in range(count):
        month += 1
        if month > 12:
            month = 1
            year += 1
        result.append((year, month))
    return tuple(result)


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _last_business_day(year: int, month: int) -> date:
    # ponytail: plain Sat/Sun-back-off rule, no Spanish public-holiday calendar.
    # Upgrade with a holiday table if a TGSS debit date ever lands on one.
    day = _month_end(year, month)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _previous_business_day(day: date) -> date:
    # ponytail: same plain Sat/Sun-back-off rule as _last_business_day.
    result = day - timedelta(days=1)
    while result.weekday() >= 5:
        result -= timedelta(days=1)
    return result


def _obligation_row(
    obligations: Sequence[Mapping[str, Any]], code: str
) -> Mapping[str, Any] | None:
    for row in obligations:
        if str(row.get("obligation_code")) == code:
            return row
    return None


def _occurrence(
    definition: PeriodicActionDefinition,
    *,
    due_on: date,
    as_of: date,
    **extra: Any,
) -> dict[str, Any]:
    overdue = due_on < as_of
    return {
        "code": definition.code,
        "cadence": definition.cadence,
        "title": definition.title,
        "detail": definition.detail,
        "due_on": due_on.isoformat(),
        "days_until": (due_on - as_of).days,
        "overdue": overdue,
        "status": "overdue" if overdue else "upcoming",
        "source_url": definition.source_url,
        "source_citation": definition.source_citation,
        "source_kind": definition.source_kind,
        "source_checked_on": definition.source_checked_on.isoformat(),
        **extra,
    }


def build_periodic_actions(
    *,
    period: Mapping[str, Any],
    as_of: date,
    obligations: Sequence[Mapping[str, Any]] = (),
    calculations: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    period_key = str(period["period_key"])
    period_end = date.fromisoformat(str(period["ends_on"]))
    months = _period_months(period_key)
    period_start = date(months[0][0], months[0][1], 1)
    is_quarter = _PERIOD_PATTERN.fullmatch(period_key).group("quarter") is not None

    extended_months = months + _following_months(
        period_end, _COVERAGE_EXTENSION_MONTHS
    )
    extended_end = _month_end(*extended_months[-1])

    actions: list[dict[str, Any]] = []

    for year, month in extended_months:
        notification_due = _month_end(year, month)
        actions.append(
            _occurrence(ELECTRONIC_NOTIFICATIONS_CHECK, due_on=notification_due, as_of=as_of)
        )
        debit_on = _last_business_day(year, month)
        actions.append(
            _occurrence(
                RETA_CONTRIBUTION_FUNDS_CHECK,
                due_on=_previous_business_day(debit_on),
                as_of=as_of,
                debit_on=debit_on.isoformat(),
            )
        )

    if is_quarter:
        actions.append(
            _occurrence(
                PERIOD_BACKUP_AND_FILED_CONFIRMATION,
                due_on=period_end + timedelta(days=30),
                as_of=as_of,
            )
        )

    if period_key.endswith("-Q4"):
        obligation_row = _obligation_row(obligations, "303")
        obligation_active = bool(
            obligation_row is not None
            and obligation_row.get("determination") in {"due", "unknown"}
            and obligation_row.get("filing_status") not in {"filed", "waived"}
        )
        if obligation_active:
            calculation = (calculations or {}).get("303")
            carryforward_eur: Decimal | None = None
            refund_already_requested = False
            calculation_usable = calculation is not None and not calculation.get("blocked")
            if calculation_usable:
                values = calculation.get("values") or {}
                raw_carryforward = values.get("compensation_carryforward")
                if raw_carryforward is None:
                    raw_87 = values.get("87")
                    raw_72 = values.get("72")
                    if raw_87 is not None or raw_72 is not None:
                        raw_carryforward = Decimal(str(raw_87 or "0")) + Decimal(
                            str(raw_72 or "0")
                        )
                if raw_carryforward is not None:
                    carryforward_eur = Decimal(str(raw_carryforward))
                raw_refund_requested = values.get("73")
                refund_already_requested = (
                    raw_refund_requested is not None
                    and Decimal(str(raw_refund_requested)) > 0
                )
            calculation_available = calculation_usable and carryforward_eur is not None
            emit_refund_reminder = (
                not calculation_available or carryforward_eur > 0
            ) and not refund_already_requested
            if emit_refund_reminder:
                due_on = (
                    _effective_statutory_due(obligation_row)
                    if obligation_row is not None
                    else None
                ) or (period_end + timedelta(days=30))
                actions.append(
                    _occurrence(
                        MODELO303_Q4_REFUND_CHOICE,
                        due_on=due_on,
                        as_of=as_of,
                        compensation_carryforward_eur=(
                            str(carryforward_eur) if carryforward_eur is not None else None
                        ),
                    )
                )

    if period_key.endswith("-Q1"):
        actions.append(
            _occurrence(
                MODELO720_721_THRESHOLD_REVIEW,
                due_on=date(months[0][0], 3, 31),
                as_of=as_of,
            )
        )

    seen_deadlines: set[date] = set()
    for year in {period_start.year, extended_end.year}:
        for deadline_month, effective_month, effective_year_offset in _BASE_CHANGE_WINDOWS:
            deadline = _month_end(year, deadline_month)
            if deadline in seen_deadlines or not (period_start <= deadline <= extended_end):
                continue
            seen_deadlines.add(deadline)
            effective_on = date(year + effective_year_offset, effective_month, 1)
            actions.append(
                _occurrence(
                    RETA_BASE_CHANGE_WINDOW,
                    due_on=deadline,
                    as_of=as_of,
                    effective_on=effective_on.isoformat(),
                )
            )

    actions.sort(key=lambda row: (row["due_on"], row["code"]))
    return actions


def _action_description(action: Mapping[str, Any]) -> str:
    parts = [str(action["detail"])]
    if action.get("effective_on"):
        parts.append(f"Effective on {action['effective_on']}.")
    if action.get("debit_on"):
        parts.append(f"Debit date: {action['debit_on']}.")
    if action.get("compensation_carryforward_eur"):
        parts.append(
            f"Pending compensation balance: {action['compensation_carryforward_eur']} EUR."
        )
    parts.append(f"Status: {action['status']}.")
    parts.append(f"Source: {action['source_citation']} ({action['source_url']}).")
    return "\n".join(parts)


def periodic_action_schedule_events(
    actions: Sequence[Mapping[str, Any]],
) -> list[TaxScheduleEvent]:
    events: list[TaxScheduleEvent] = []
    for action in actions:
        events.append(
            TaxScheduleEvent(
                kind=str(action["code"]),
                event_date=date.fromisoformat(str(action["due_on"])),
                summary=str(action["title"]),
                description=_action_description(action),
                forms=(),
                determinations=(),
                statutory_due_on=None,
                category="recurring-action",
            )
        )
    return events
