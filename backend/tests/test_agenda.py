from __future__ import annotations
from autonomo_test_support.paths import REPO_ROOT

from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from autonomo_taxes.agenda import build_tax_agenda, select_cash_period
from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.tax_calendar import entry_as_record, load_tax_calendar


CALENDAR_PATH = REPO_ROOT / "config" / "tax-calendar-2026.json"


def _period(
    key: str = "2026-Q3",
    *,
    starts_on: str = "2026-07-01",
    ends_on: str = "2026-09-30",
) -> dict[str, object]:
    return {
        "period_key": key,
        "period_type": "quarter",
        "starts_on": starts_on,
        "ends_on": ends_on,
        "status": "open",
    }


def _obligation(
    form: str,
    *,
    determination: str = "due",
    filing_status: str | None = None,
) -> dict[str, object]:
    return {
        "obligation_code": form,
        "determination": determination,
        "filing_status": filing_status
        or ("due" if determination == "due" else "unknown"),
        "filing_opens_on": "2026-10-01",
        "internal_due_on": "2026-10-14",
        "direct_debit_cutoff_on": "2026-10-15",
        "due_on": "2026-10-20",
        "calendar_statutory_due_on": "2026-10-20",
        "deadline_status": "confirmed",
    }


def test_agenda_lists_forward_checkpoints_in_date_order() -> None:
    report = build_tax_agenda(
        periods=[_period()],
        obligations_by_period={
            "2026-Q3": [_obligation("130"), _obligation("303")]
        },
        as_of=date(2026, 7, 19),
        horizon_days=120,
    )

    assert report["status"] == "upcoming"
    assert report["through"] == "2026-11-16"
    assert [event["date"] for event in report["events"]] == sorted(
        event["date"] for event in report["events"]
    )
    assert report["events"][0]["kind"] == "intake-close"
    assert report["events"][0]["days_until"] == 73
    assert report["events"][0]["forms"] == ["130", "303"]
    assert all(event["overdue"] is False for event in report["events"])


def test_agenda_excludes_filed_forms_and_marks_due_statutory_deadline_overdue() -> None:
    report = build_tax_agenda(
        periods=[_period()],
        obligations_by_period={
            "2026-Q3": [
                _obligation("130"),
                _obligation("303", filing_status="filed"),
            ]
        },
        as_of=date(2026, 10, 21),
        horizon_days=30,
    )

    statutory = next(event for event in report["events"] if event["kind"] == "statutory")
    assert statutory["forms"] == ["130"]
    assert statutory["status"] == "overdue"
    assert statutory["severity"] == "critical"
    assert statutory["overdue"] is True
    assert report["status"] == "overdue"


def test_agenda_treats_missed_direct_debit_as_advisory_before_statutory_due() -> None:
    report = build_tax_agenda(
        periods=[_period()],
        obligations_by_period={"2026-Q3": [_obligation("130")]},
        as_of=date(2026, 10, 16),
        horizon_days=30,
    )

    debit = next(event for event in report["events"] if event["kind"] == "direct-debit")
    assert debit["status"] == "missed_direct_debit"
    assert debit["severity"] == "warning"
    assert debit["overdue"] is False
    assert next(
        event for event in report["events"] if event["kind"] == "statutory"
    )["status"] == "upcoming"


def test_agenda_does_not_mislabel_unknown_form_as_overdue_filing() -> None:
    report = build_tax_agenda(
        periods=[_period()],
        obligations_by_period={
            "2026-Q3": [_obligation("216", determination="unknown")]
        },
        as_of=date(2026, 10, 21),
        horizon_days=30,
    )

    statutory = next(event for event in report["events"] if event["kind"] == "statutory")
    assert statutory["status"] == "classification_overdue"
    assert statutory["severity"] == "warning"
    assert statutory["overdue"] is False
    assert report["summary"]["hard_overdue_count"] == 0

    before_deadline = build_tax_agenda(
        periods=[_period()],
        obligations_by_period={
            "2026-Q3": [_obligation("216", determination="unknown")]
        },
        as_of=date(2026, 10, 16),
        horizon_days=30,
    )
    debit = next(
        event
        for event in before_deadline["events"]
        if event["kind"] == "direct-debit"
    )
    assert debit["status"] == "classification_cutoff_passed"
    assert debit["summary"] == (
        "Resolve form applicability before direct-debit cutoff"
    )
    assert all(
        event["kind"] not in {"intake-close", "draft", "cash", "evidence"}
        for event in before_deadline["events"]
    )


def test_agenda_keeps_due_and_unknown_forms_in_separate_events() -> None:
    report = build_tax_agenda(
        periods=[_period()],
        obligations_by_period={
            "2026-Q3": [
                _obligation("130"),
                _obligation("349", determination="unknown"),
            ]
        },
        as_of=date(2026, 10, 1),
        horizon_days=30,
    )

    statutory = [
        event for event in report["events"] if event["kind"] == "statutory"
    ]
    assert [(event["forms"], event["status"]) for event in statutory] == [
        (["130"], "upcoming"),
        (["349"], "classification_due"),
    ]


def test_agenda_includes_payment_confirmation_and_optional_cash() -> None:
    cash = {
        "required_tax_eur": Decimal("777.29"),
        "recommended_reserve_eur": Decimal("877.29"),
        "available_eur": Decimal("800.00"),
        "status": "reserve_shortfall",
    }
    report = build_tax_agenda(
        periods=[_period()],
        obligations_by_period={"2026-Q3": [_obligation("130")]},
        as_of=date(2026, 7, 19),
        horizon_days=120,
        cash_period="2026-Q3",
        cash_check=cash,
        settlements=[
            {
                "period": "2026-Q2",
                "form": "130",
                "due_on": "2026-07-20",
                "expected_amount_eur": "2639.12",
                "status": "payment_missing",
            }
        ],
    )

    payment = next(
        event for event in report["events"] if event["kind"] == "payment-confirmation"
    )
    assert payment["period"] == "2026-Q2"
    assert payment["forms"] == ["130"]
    assert payment["status"] == "payment_confirmation_due"
    assert report["cash"]["period"] == "2026-Q3"
    assert report["cash"]["check"] is cash
    assert report["status"] == "attention"


def test_agenda_empty_ledger_is_a_valid_empty_report() -> None:
    report = build_tax_agenda(
        periods=[],
        obligations_by_period={},
        as_of=date(2026, 7, 19),
        horizon_days=120,
    )

    assert report["status"] == "empty"
    assert report["events"] == []
    assert report["unscheduled_obligations"] == []
    assert report["cash"] is None


def test_cash_period_stays_on_recent_unfiled_quarter_after_deadline() -> None:
    period = _period()
    obligations = {"2026-Q3": [_obligation("130")]}

    assert select_cash_period(
        periods=[period],
        obligations_by_period=obligations,
        as_of=date(2026, 10, 21),
    ) == "2026-Q3"


def test_agenda_cli_cash_matches_period_prepare_and_surfaces_q2_payment(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    agenda_path = tmp_path / "agenda.json"
    prepare_dir = tmp_path / "prepare"
    with initialize(database) as db:
        calendar = load_tax_calendar(CALENDAR_PATH)
        db.import_tax_calendar(
            [
                entry_as_record(entry)
                for entry in calendar.entries
                if entry.period_key in {"2026-Q2", "2026-Q3"}
                and entry.form_code in {"130", "303"}
            ]
        )
        transaction = db.add_transaction(
            external_key="q3-issued-invoice",
            period_key="2026-Q3",
            transaction_date="2026-07-01",
            booking_date="2026-07-01",
            entry_type="income",
            description="Issued service invoice",
            amount_minor=100000,
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="income",
            tax_code="outside_scope",
            taxable_base_minor=100000,
            include_modelo130=True,
            include_modelo303=True,
        )
        for form in ("130", "303"):
            db.add_obligation(
                period_key="2026-Q3",
                obligation_code=form,
                determination="due",
                filing_status="due",
                explanation=f"Modelo {form} fixture is due",
                due_on="2026-10-20",
            )
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            determination="due",
            filing_status="filed",
            explanation="Filed Q2 payment",
            due_on="2026-07-20",
        )
        db.create_filing_snapshot(
            "2026-Q2",
            payload={"form": "130", "filed_values": {"19": "2639.12"}},
            filed_on="2026-07-08",
            status="baseline",
            form_code="130",
        )

    assert main(
        [
            "agenda",
            "--db",
            str(database),
            "--as-of",
            "2026-07-19",
            "--available-eur",
            "1000.00",
            "--out",
            str(agenda_path),
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "period",
            "prepare",
            "--db",
            str(database),
            "2026-Q3",
            "--as-of",
            "2026-07-19",
            "--available-eur",
            "1000.00",
            "--out-dir",
            str(prepare_dir),
        ]
    ) == 0
    capsys.readouterr()

    agenda = json.loads(agenda_path.read_text(encoding="utf-8"))
    prepared = json.loads(
        (prepare_dir / "cash-check.json").read_text(encoding="utf-8")
    )
    assert agenda["cash"]["period"] == "2026-Q3"
    assert agenda["cash"]["check"] == prepared
    payment = next(
        event for event in agenda["events"] if event["kind"] == "payment-confirmation"
    )
    assert payment["expected_amount_eur"] == "2639.12"
