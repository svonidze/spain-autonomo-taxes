from autonomo_test_support.paths import REPO_ROOT
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.period_prepare import build_period_preparation, write_period_preparation
from autonomo_taxes.tax_calendar import (
    build_period_ics,
    build_period_schedule_events,
    entry_as_record,
    load_tax_calendar,
)


def _obligation(form: str, determination: str = "due") -> dict[str, object]:
    return {
        "obligation_code": form,
        "determination": determination,
        "filing_status": "due" if determination == "due" else "unknown",
        "filing_opens_on": "2026-10-01",
        "internal_due_on": "2026-10-14",
        "direct_debit_cutoff_on": "2026-10-15",
        "due_on": "2026-10-20",
    }


def test_period_ics_is_stable_and_contains_operational_tax_dates() -> None:
    events = build_period_schedule_events(
        period_key="2026-Q3",
        period_ends_on=date(2026, 9, 30),
        obligations=[_obligation("130"), _obligation("303")],
    )
    first = build_period_ics(
        period_key="2026-Q3",
        period_ends_on=date(2026, 9, 30),
        obligations=[_obligation("130"), _obligation("303")],
    )
    second = build_period_ics(
        period_key="2026-Q3",
        period_ends_on=date(2026, 9, 30),
        obligations=[_obligation("130"), _obligation("303")],
    )

    assert first == second
    for event in events:
        assert f"UID:2026-Q3-{event.kind}@spain-autonomo-taxes" in first
        assert f"DTSTART;VALUE=DATE:{event.event_date:%Y%m%d}" in first
    assert "DTSTART;VALUE=DATE:20260930" in first
    assert "DTSTART;VALUE=DATE:20261015" in first
    assert "DTSTART;VALUE=DATE:20261020" in first
    assert "UID:2026-Q3-statutory@spain-autonomo-taxes" in first
    cash_event = first.split(
        "UID:2026-Q3-cash@spain-autonomo-taxes", maxsplit=1
    )[1].split("END:VEVENT", maxsplit=1)[0]
    assert "DTSTART;VALUE=DATE:20261014" in cash_event
    assert "DTSTART;VALUE=DATE:20261019" not in cash_event


def test_period_ics_skips_unscheduled_or_provisional_form_without_crashing() -> None:
    scheduled = _obligation("130")
    missing = {
        "obligation_code": "216",
        "determination": "unknown",
        "filing_status": "unknown",
        "filing_opens_on": None,
        "internal_due_on": None,
        "direct_debit_cutoff_on": None,
        "due_on": None,
    }
    provisional = {
        "obligation_code": "349",
        "determination": "unknown",
        "filing_status": "unknown",
        "filing_opens_on": "2027-01-01",
        "internal_due_on": "2027-01-24",
        "direct_debit_cutoff_on": None,
        "due_on": None,
        "calendar_statutory_due_on": "2027-01-30",
        "deadline_status": "provisional",
    }

    calendar = build_period_ics(
        period_key="2026-Q3",
        period_ends_on=date(2026, 9, 30),
        obligations=[scheduled, missing, provisional],
    )

    assert "Modelo 130" in calendar
    assert "Modelo 216" not in calendar
    assert "Modelo 349" not in calendar


def test_period_ics_keeps_stable_uids_when_determinations_are_mixed() -> None:
    calendar = build_period_ics(
        period_key="2026-Q3",
        period_ends_on=date(2026, 9, 30),
        obligations=[_obligation("130"), _obligation("349", determination="unknown")],
    )

    assert calendar.count("UID:2026-Q3-statutory@spain-autonomo-taxes") == 1
    assert "UID:2026-Q3-statutory-20261020" not in calendar


def test_period_preparation_writes_calculations_cash_and_calendar(tmp_path) -> None:
    obligations = [_obligation("130"), _obligation("303")]
    report = build_period_preparation(
        period={"period_key": "2026-Q3", "ends_on": "2026-09-30"},
        as_of=date(2026, 10, 1),
        dashboard={"blocking_items": []},
        calculations={
            "130": {"blocked": False, "values": {"19": Decimal("777.29"), "result": Decimal("777.29")}},
            "303": {"blocked": False, "values": {"71": Decimal("-21.00"), "result": Decimal("-21.00")}},
        },
        obligations=obligations,
        available_eur=Decimal("1000.00"),
        cash_buffer_eur=None,
    )

    assert report["filing_ready"] is True
    assert report["manual_filing"][0]["casillas"] == [
        {"casilla": "19", "value": "777.29"}
    ]
    assert report["manual_filing"][0]["cash_action"]["status"] == "payment_due"
    outputs = write_period_preparation(report, tmp_path)
    assert outputs["modelo130"].is_file()
    assert outputs["modelo303"].is_file()
    assert "| Casilla |" in outputs["modelo130_markdown"].read_text(encoding="utf-8")
    assert outputs["cash_check"].is_file()
    calendar_ics = outputs["calendar_ics"].read_text(encoding="utf-8")
    assert calendar_ics.startswith("BEGIN:VCALENDAR")
    cash_event = calendar_ics.split(
        "UID:2026-Q3-cash@spain-autonomo-taxes", maxsplit=1
    )[1].split("END:VEVENT", maxsplit=1)[0]
    assert "DTSTART;VALUE=DATE:20261014" in cash_event
    assert "Required tax: 777.29 EUR" in cash_event
    assert "recommended reserve: 877.29 EUR" in cash_event
    assert "available: 1000.00 EUR" in cash_event
    assert "status: sufficient" in cash_event
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "### Modelo 130" in markdown
    assert "Casilla 19: 777.29" in markdown
    assert "--period 2026-Q3 --form 130" in markdown


def test_unknown_obligation_blocks_preparation() -> None:
    report = build_period_preparation(
        period={"period_key": "2026-Q3", "ends_on": "2026-09-30"},
        as_of=date(2026, 7, 17),
        dashboard={"blocking_items": []},
        calculations={},
        obligations=[_obligation("216", determination="unknown")],
        available_eur=None,
        cash_buffer_eur=None,
    )

    assert report["status"] == "blocked"
    assert report["unknown_forms"] == ["216"]


def test_future_approved_forecast_does_not_block_open_period_preparation() -> None:
    expected = {
        "kind": "approved_forecast_pending",
        "reference": "amortization-1",
        "tax_date": "2026-09-30",
    }
    report = build_period_preparation(
        period={"period_key": "2026-Q3", "ends_on": "2026-09-30"},
        as_of=date(2026, 7, 18),
        dashboard={"blocking_items": [], "expected_items": [expected]},
        calculations={},
        obligations=[],
        available_eur=None,
        cash_buffer_eur=None,
    )

    assert report["status"] == "in_progress"
    assert report["preparation_ready"] is True
    assert report["workflow_items"] == []
    assert report["expected_items"] == [expected]


def test_period_end_reports_missing_cash_as_filing_blocker() -> None:
    report = build_period_preparation(
        period={"period_key": "2026-Q3", "ends_on": "2026-09-30"},
        as_of=date(2026, 10, 1),
        dashboard={"blocking_items": []},
        calculations={
            "130": {
                "blocked": False,
                "values": {"19": Decimal("100.00"), "result": Decimal("100.00")},
            }
        },
        obligations=[_obligation("130")],
        available_eur=None,
        cash_buffer_eur=None,
    )

    assert report["status"] == "blocked"
    assert report["preparation_ready"] is True
    assert report["filing_ready"] is False
    assert report["filing_blockers"] == [
        {"kind": "cash_not_ready", "detail": "cash-check status is not_checked"}
    ]


def test_period_prepare_cli_builds_real_manual_filing_bundle(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    output = tmp_path / "prepare"
    calendar_path = REPO_ROOT / "reference" / "tax-calendars" / "2026.json"
    with initialize(database) as db:
        calendar = load_tax_calendar(calendar_path)
        db.import_tax_calendar(
            [
                entry_as_record(entry)
                for entry in calendar.entries
                if entry.period_key == "2026-Q3"
                and entry.form_code in {"130", "303", "349"}
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
        for form in ("130", "303", "349"):
            db.add_obligation(
                period_key="2026-Q3",
                obligation_code=form,
                determination="due",
                filing_status="due",
                explanation=f"Modelo {form} fixture is due",
                due_on="2026-10-20",
            )

    assert main(
        [
            "period",
            "prepare",
            "--db",
            str(database),
            "2026-Q3",
            "--as-of",
            "2026-10-01",
            "--available-eur",
            "1000.00",
            "--out-dir",
            str(output),
        ]
    ) == 0
    emitted = json.loads(capsys.readouterr().out)
    report = json.loads((output / "preparation.json").read_text(encoding="utf-8"))
    assert emitted["filing_ready"] is True
    assert report["due_forms"] == ["130", "303", "349"]
    assert report["cash_check"]["required_tax_eur"] == "190.00"
    assert report["calculations"]["130"]["form"] == "130"
    assert report["calculations"]["130"]["period"] == "2026-Q3"
    modelo130_checklist = next(
        item for item in report["manual_filing"] if item["form"] == "130"
    )
    modelo349_checklist = next(
        item for item in report["manual_filing"] if item["form"] == "349"
    )
    assert {row["casilla"] for row in modelo130_checklist["casillas"]} >= {
        "01",
        "02",
        "03",
        "04",
        "07",
        "12",
        "14",
        "17",
        "19",
    }
    assert modelo130_checklist["cash_action"]["payable_eur"] == "190.00"
    assert modelo349_checklist["receipt_command"] is None
    assert (output / "calculations" / "modelo130.json").is_file()
    assert (output / "calculations" / "modelo303.json").is_file()
    modelo349 = output / "calculations" / "modelo349.md"
    assert modelo349.is_file()
    assert "No intra-community operations declared." in modelo349.read_text(
        encoding="utf-8"
    )
    assert (output / "tax-actions.ics").is_file()
    assert (output / "books" / "filing-package-manifest.json").is_file()
