from __future__ import annotations

from datetime import date
from decimal import Decimal

from autonomo_taxes.period_prepare import build_period_preparation, write_period_preparation
from autonomo_taxes.periodic_actions import (
    build_periodic_actions,
    periodic_action_schedule_events,
)
from autonomo_taxes.tax_calendar import build_period_ics


def _codes(actions: list[dict[str, object]], code: str) -> list[dict[str, object]]:
    return [action for action in actions if action["code"] == code]


def _due_303(
    *,
    determination: str = "due",
    filing_status: str = "due",
    due_on: str | None = None,
    calendar_statutory_due_on: str | None = None,
    deadline_status: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "obligation_code": "303",
        "determination": determination,
        "filing_status": filing_status,
    }
    if due_on is not None:
        row["due_on"] = due_on
    if calendar_statutory_due_on is not None:
        row["calendar_statutory_due_on"] = calendar_statutory_due_on
    if deadline_status is not None:
        row["deadline_status"] = deadline_status
    return row


# --- Monthly coverage: prepared period + following quarter ---------------------


def test_monthly_actions_cover_the_period_and_the_following_quarter() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q3", "ends_on": "2026-09-30"},
        as_of=date(2026, 10, 1),
    )

    notifications = _codes(actions, "electronic_notifications_check")
    assert [row["due_on"] for row in notifications] == [
        "2026-07-31",
        "2026-08-31",
        "2026-09-30",
        "2026-10-31",
        "2026-11-30",
        "2026-12-31",
    ]
    # The period's own months are already over relative to as_of; the following
    # quarter's occurrences are the actionable ones.
    statuses = {row["due_on"]: row["status"] for row in notifications}
    assert statuses["2026-09-30"] == "overdue"
    assert statuses["2026-10-31"] == "upcoming"


def test_reta_funds_reminder_is_one_business_day_before_a_weekend_debit() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q1", "ends_on": "2026-03-31"},
        as_of=date(2026, 1, 1),
    )

    reta_funds = _codes(actions, "reta_contribution_funds_check")
    may = next(row for row in reta_funds if row["debit_on"] == "2026-05-29")
    # 2026-05-31 is a Sunday; the TGSS debit lands on Friday 2026-05-29, and the
    # funds-ready reminder is one business day earlier, Thursday 2026-05-28.
    assert may["due_on"] == "2026-05-28"


def test_reta_funds_reminder_precedes_a_weekday_debit_by_one_business_day() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q3", "ends_on": "2026-09-30"},
        as_of=date(2026, 7, 1),
    )

    reta_funds = _codes(actions, "reta_contribution_funds_check")
    september = next(row for row in reta_funds if row["debit_on"] == "2026-09-30")
    assert september["due_on"] == "2026-09-29"


def test_quarterly_backup_action_is_not_extended_to_the_following_quarter() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q3", "ends_on": "2026-09-30"},
        as_of=date(2026, 10, 1),
    )

    backups = _codes(actions, "period_backup_and_filed_confirmation")
    assert [row["due_on"] for row in backups] == ["2026-10-30"]


def test_annual_period_skips_the_quarterly_backup_action() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026", "ends_on": "2026-12-31"},
        as_of=date(2026, 1, 1),
    )

    assert _codes(actions, "period_backup_and_filed_confirmation") == []


# --- Q4 Modelo 303 refund-vs-carry-forward reminder -----------------------------


def test_probe_empty_q4_with_prior_compensation_still_triggers_the_reminder() -> None:
    # Regression for the review's probe: casilla 71 floors at 0 in "compensate"
    # mode while the favourable balance sits in 87/compensation_carryforward.
    actions = build_periodic_actions(
        period={"period_key": "2026-Q4", "ends_on": "2026-12-31"},
        as_of=date(2026, 10, 1),
        obligations=[_due_303(due_on="2027-01-30")],
        calculations={
            "303": {
                "blocked": False,
                "values": {
                    "71": Decimal("0.00"),
                    "72": Decimal("0.00"),
                    "73": Decimal("0.00"),
                    "87": Decimal("300.00"),
                    "compensation_carryforward": Decimal("300.00"),
                },
            }
        },
    )

    reminders = _codes(actions, "modelo303_q4_refund_choice")
    assert len(reminders) == 1
    assert reminders[0]["due_on"] == "2027-01-30"
    assert reminders[0]["compensation_carryforward_eur"] == "300.00"


def test_carryforward_derived_from_87_plus_72_when_the_summary_key_is_absent() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q4", "ends_on": "2026-12-31"},
        as_of=date(2026, 10, 1),
        obligations=[_due_303(due_on="2027-01-30")],
        calculations={
            "303": {
                "blocked": False,
                "values": {"71": Decimal("-45.00"), "72": Decimal("45.00"), "87": Decimal("0.00")},
            }
        },
    )

    reminders = _codes(actions, "modelo303_q4_refund_choice")
    assert len(reminders) == 1
    assert reminders[0]["compensation_carryforward_eur"] == "45.00"


def test_zero_carryforward_and_positive_result_skip_the_reminder() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q4", "ends_on": "2026-12-31"},
        as_of=date(2026, 10, 1),
        obligations=[_due_303(due_on="2027-01-30")],
        calculations={
            "303": {
                "blocked": False,
                "values": {
                    "71": Decimal("120.00"),
                    "72": Decimal("0.00"),
                    "73": Decimal("0.00"),
                    "87": Decimal("0.00"),
                    "compensation_carryforward": Decimal("0.00"),
                },
            }
        },
    )

    assert _codes(actions, "modelo303_q4_refund_choice") == []


def test_refund_already_requested_skips_the_reminder_even_with_a_carryforward() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q4", "ends_on": "2026-12-31"},
        as_of=date(2026, 10, 1),
        obligations=[_due_303(due_on="2027-01-30")],
        calculations={
            "303": {
                "blocked": False,
                "values": {
                    "71": Decimal("0.00"),
                    "72": Decimal("0.00"),
                    "73": Decimal("50.00"),
                    "87": Decimal("0.00"),
                    "compensation_carryforward": Decimal("300.00"),
                },
            }
        },
    )

    assert _codes(actions, "modelo303_q4_refund_choice") == []


def test_filed_303_obligation_skips_the_reminder() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q4", "ends_on": "2026-12-31"},
        as_of=date(2026, 10, 1),
        obligations=[_due_303(filing_status="filed", due_on="2027-01-30")],
        calculations={
            "303": {
                "blocked": False,
                "values": {"71": Decimal("0.00"), "compensation_carryforward": Decimal("300.00")},
            }
        },
    )

    assert _codes(actions, "modelo303_q4_refund_choice") == []


def test_absent_303_obligation_skips_the_reminder() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q4", "ends_on": "2026-12-31"},
        as_of=date(2026, 10, 1),
        obligations=[],
        calculations={},
    )

    assert _codes(actions, "modelo303_q4_refund_choice") == []


def test_unknown_303_obligation_without_a_calculation_reminds_unconditionally() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q4", "ends_on": "2026-12-31"},
        as_of=date(2026, 10, 1),
        obligations=[_due_303(determination="unknown", filing_status="unknown")],
        calculations={},
    )

    reminders = _codes(actions, "modelo303_q4_refund_choice")
    assert len(reminders) == 1
    assert reminders[0]["compensation_carryforward_eur"] is None
    # No due_on/calendar_statutory_due_on on the obligation: falls back to ends_on + 30 days.
    assert reminders[0]["due_on"] == "2027-01-30"


def test_blocked_calculation_reminds_unconditionally() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q4", "ends_on": "2026-12-31"},
        as_of=date(2026, 10, 1),
        obligations=[_due_303(due_on="2027-01-30")],
        calculations={"303": {"blocked": True, "reason": "not ready"}},
    )

    reminders = _codes(actions, "modelo303_q4_refund_choice")
    assert len(reminders) == 1


def test_non_q4_period_never_emits_the_refund_reminder() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q3", "ends_on": "2026-09-30"},
        as_of=date(2026, 7, 1),
        obligations=[_due_303(due_on="2026-10-20")],
        calculations={"303": {"blocked": False, "values": {"compensation_carryforward": Decimal("10.00")}}},
    )

    assert _codes(actions, "modelo303_q4_refund_choice") == []


def test_reminder_due_date_prefers_the_obligation_due_on_over_the_fallback() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q4", "ends_on": "2026-12-31"},
        as_of=date(2026, 10, 1),
        obligations=[_due_303(due_on="2027-01-26")],
        calculations={},
    )

    reminders = _codes(actions, "modelo303_q4_refund_choice")
    assert reminders[0]["due_on"] == "2027-01-26"


def test_reminder_due_date_ignores_a_provisional_calendar_date_without_due_on() -> None:
    # Exercises tax_calendar._effective_statutory_due's provisional guard: a
    # provisional calendar_statutory_due_on with no due_on is not authoritative.
    actions = build_periodic_actions(
        period={"period_key": "2026-Q4", "ends_on": "2026-12-31"},
        as_of=date(2026, 10, 1),
        obligations=[
            _due_303(
                calendar_statutory_due_on="2027-01-15",
                deadline_status="provisional",
            )
        ],
        calculations={},
    )

    reminders = _codes(actions, "modelo303_q4_refund_choice")
    assert reminders[0]["due_on"] == "2027-01-30"  # ends_on + 30 days fallback


def test_reminder_due_date_uses_a_confirmed_calendar_date_without_due_on() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q4", "ends_on": "2026-12-31"},
        as_of=date(2026, 10, 1),
        obligations=[
            _due_303(
                calendar_statutory_due_on="2027-01-30",
                deadline_status="confirmed",
            )
        ],
        calculations={},
    )

    reminders = _codes(actions, "modelo303_q4_refund_choice")
    assert reminders[0]["due_on"] == "2027-01-30"


# --- Modelo 720/721 threshold review --------------------------------------------


def test_q1_period_reminds_to_check_720_721_thresholds() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q1", "ends_on": "2026-03-31"},
        as_of=date(2026, 1, 1),
    )

    reminders = _codes(actions, "modelo720_721_threshold_review")
    assert len(reminders) == 1
    assert reminders[0]["due_on"] == "2026-03-31"
    assert "obligations.py" not in reminders[0]["detail"]
    assert "_modelo720" not in reminders[0]["detail"]


def test_non_q1_period_skips_the_720_721_reminder() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q2", "ends_on": "2026-06-30"},
        as_of=date(2026, 4, 1),
    )

    assert _codes(actions, "modelo720_721_threshold_review") == []


# --- RETA base-change window -----------------------------------------------------


def test_base_change_window_inside_period_is_included() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q1", "ends_on": "2026-03-31"},
        as_of=date(2026, 3, 15),
    )

    windows = _codes(actions, "reta_base_change_window")
    assert [row["due_on"] for row in windows] == ["2026-02-28", "2026-04-30", "2026-06-30"]
    assert windows[0]["effective_on"] == "2026-03-01"


def test_base_change_window_outside_period_but_in_next_quarter_is_included() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q1", "ends_on": "2026-03-31"},
        as_of=date(2026, 3, 15),
    )

    windows = _codes(actions, "reta_base_change_window")
    # 2026-04-30 falls in Q2, outside Q1 itself, but inside the one-quarter
    # coverage extension, so it must appear.
    assert "2026-04-30" in [row["due_on"] for row in windows]


def test_base_change_window_can_yield_two_deadlines_in_one_quarter() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q2", "ends_on": "2026-06-30"},
        as_of=date(2026, 4, 1),
    )

    windows = [
        row for row in _codes(actions, "reta_base_change_window") if row["due_on"] < "2026-07-01"
    ]
    assert [row["due_on"] for row in windows] == ["2026-04-30", "2026-06-30"]
    assert [row["effective_on"] for row in windows] == ["2026-05-01", "2026-07-01"]


def test_base_change_window_uses_the_leap_day_in_a_leap_year() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2028-Q1", "ends_on": "2028-03-31"},
        as_of=date(2028, 1, 1),
    )

    windows = _codes(actions, "reta_base_change_window")
    assert windows[0]["due_on"] == "2028-02-29"

    notifications = _codes(actions, "electronic_notifications_check")
    assert "2028-02-29" in [row["due_on"] for row in notifications]


# --- ICS export -------------------------------------------------------------------


def test_ics_distinguishes_periodic_actions_from_tax_filing_deadlines() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q1", "ends_on": "2026-03-31"},
        as_of=date(2026, 1, 1),
    )
    obligation = {
        "obligation_code": "130",
        "determination": "due",
        "filing_status": "due",
        "filing_opens_on": "2026-04-01",
        "internal_due_on": "2026-04-14",
        "direct_debit_cutoff_on": "2026-04-15",
        "due_on": "2026-04-20",
    }

    calendar = build_period_ics(
        period_key="2026-Q1",
        period_ends_on=date(2026, 3, 31),
        obligations=[obligation],
        extra_events=periodic_action_schedule_events(actions),
    )

    assert "CATEGORIES:TAX-FILING" in calendar
    assert "CATEGORIES:RECURRING-ACTION" in calendar
    assert "UID:2026-Q1-modelo720_721_threshold_review@spain-autonomo-taxes" in calendar


def test_ics_event_description_carries_status_and_effective_on() -> None:
    actions = build_periodic_actions(
        period={"period_key": "2026-Q1", "ends_on": "2026-03-31"},
        as_of=date(2026, 3, 15),
    )
    events = periodic_action_schedule_events(actions)
    window_event = next(e for e in events if e.kind == "reta_base_change_window")

    assert "Effective on 2026-03-01." in window_event.description
    assert "Status: overdue." in window_event.description


# --- End-to-end via build_period_preparation / write_period_preparation --------


def test_periodic_actions_appear_in_the_preparation_report_markdown_and_ics(
    tmp_path,
) -> None:
    report = build_period_preparation(
        period={"period_key": "2026-Q4", "ends_on": "2026-12-31"},
        as_of=date(2026, 10, 1),
        dashboard={"blocking_items": []},
        calculations={
            "303": {
                "blocked": False,
                "values": {
                    "71": Decimal("0.00"),
                    "87": Decimal("300.00"),
                    "72": Decimal("0.00"),
                    "73": Decimal("0.00"),
                    "compensation_carryforward": Decimal("300.00"),
                },
            }
        },
        obligations=[_due_303(due_on="2027-01-30")],
        available_eur=None,
        cash_buffer_eur=None,
    )

    assert "periodic_actions" in report
    codes = {action["code"] for action in report["periodic_actions"]}
    assert "modelo303_q4_refund_choice" in codes
    assert "electronic_notifications_check" in codes

    outputs = write_period_preparation(report, tmp_path)
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "## Periodic actions (no filing form)" in markdown
    assert "modelo303_q4_refund_choice" in markdown
    assert "Pending balance 300.00 EUR." in markdown
    assert "(overdue)" in markdown or "(upcoming)" in markdown

    ics = outputs["calendar_ics"].read_text(encoding="utf-8")
    assert "CATEGORIES:RECURRING-ACTION" in ics


def test_existing_period_preparation_keys_are_unchanged_alongside_periodic_actions() -> None:
    report = build_period_preparation(
        period={"period_key": "2026-Q3", "ends_on": "2026-09-30"},
        as_of=date(2026, 10, 1),
        dashboard={"blocking_items": []},
        calculations={
            "130": {"blocked": False, "values": {"19": Decimal("777.29"), "result": Decimal("777.29")}},
            "303": {"blocked": False, "values": {"71": Decimal("-21.00"), "result": Decimal("-21.00")}},
        },
        obligations=[
            {
                "obligation_code": "130",
                "determination": "due",
                "filing_status": "due",
                "filing_opens_on": "2026-10-01",
                "internal_due_on": "2026-10-14",
                "direct_debit_cutoff_on": "2026-10-15",
                "due_on": "2026-10-20",
            },
            {
                "obligation_code": "303",
                "determination": "due",
                "filing_status": "due",
                "filing_opens_on": "2026-10-01",
                "internal_due_on": "2026-10-14",
                "direct_debit_cutoff_on": "2026-10-15",
                "due_on": "2026-10-20",
            },
        ],
        available_eur=Decimal("1000.00"),
        cash_buffer_eur=None,
    )

    # Byte-for-byte the same assertions the original (pre-periodic-actions) test
    # made, to guard against the new feature disturbing existing behaviour.
    assert report["filing_ready"] is True
    assert report["manual_filing"][0]["casillas"] == [{"casilla": "19", "value": "777.29"}]
    assert report["manual_filing"][0]["cash_action"]["status"] == "payment_due"
    assert "periodic_actions" in report
