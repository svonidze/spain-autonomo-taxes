from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.tax_calendar import entry_as_record, load_tax_calendar


CALENDAR_PATH = Path(__file__).parents[1] / "config" / "tax-calendar-2026.json"


def test_official_2026_calendar_expands_grouped_forms_with_confirmed_q3_dates() -> None:
    loaded = load_tax_calendar(CALENDAR_PATH)

    assert loaded.calendar_year == 2026
    assert len(loaded.source_file_hash) == 64
    assert len(loaded.entries) == 33
    q3 = [entry for entry in loaded.entries if entry.period_key == "2026-Q3"]
    assert {entry.form_code for entry in q3} == {"111", "115", "130", "216", "303", "349"}
    assert {entry.statutory_due_on.isoformat() for entry in q3} == {"2026-10-20"}
    assert {entry.internal_due_on.isoformat() for entry in q3} == {"2026-10-14"}
    assert next(entry for entry in q3 if entry.form_code == "130").direct_debit_cutoff_on.isoformat() == "2026-10-15"
    assert next(entry for entry in q3 if entry.form_code == "349").direct_debit_cutoff_on is None
    assert all(entry.deadline_status == "confirmed" for entry in loaded.entries)
    assert all(entry.source_url.startswith("https://sede.agenciatributaria.gob.es/") for entry in loaded.entries)


def test_calendar_rejects_duplicate_form_period_and_unmarked_provisional_date(
    tmp_path: Path,
) -> None:
    payload = {
        "calendar_year": 2027,
        "source_checked_on": "2026-07-17",
        "source_url": "https://sede.agenciatributaria.gob.es/Sede/calendario.html",
        "entries": [
            {
                "period_key": "2026-Q4",
                "forms": ["130", "130"],
                "filing_opens_on": "2027-01-01",
                "internal_due_on": "2027-01-24",
                "direct_debit_cutoff_on": "2027-01-25",
                "statutory_due_on": "2027-01-30",
                "deadline_status": "provisional",
                "notes": "planning only"
            }
        ]
    }
    path = tmp_path / "calendar.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="must say that the date must be replaced"):
        load_tax_calendar(path)

    payload["entries"][0]["notes"] = "Replace after AEAT publishes the 2027 calendar."
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate tax calendar entry"):
        load_tax_calendar(path)


def test_sqlite_calendar_import_is_idempotent_versioned_and_enriches_obligations(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    loaded = load_tax_calendar(CALENDAR_PATH)
    records = [entry_as_record(entry) for entry in loaded.entries]

    with initialize(database) as db:
        db.add_obligation(
            period_key="2026-Q3",
            obligation_code="130",
            determination="due",
            filing_status="due",
            explanation="Direct-estimation activity",
        )
        db.add_obligation(
            period_key="2026-Q3",
            obligation_code="303",
            determination="due",
            filing_status="due",
            due_on="2026-10-19",
            explanation="VAT-taxable activity",
        )
        imported = db.import_tax_calendar(records)
        repeated = db.import_tax_calendar(records)

        assert len(imported) == 33
        assert {row["row_version"] for row in repeated} == {1}
        obligations = {
            row["obligation_code"]: row
            for row in db.list_obligations_with_deadlines(period_key="2026-Q3")
        }
        assert obligations["130"]["due_on"] == "2026-10-20"
        assert obligations["130"]["internal_due_on"] == "2026-10-14"
        assert obligations["130"]["deadline_status"] == "confirmed"
        assert obligations["130"]["calendar_deadline_mismatch"] is False
        assert obligations["303"]["due_on"] == "2026-10-19"
        assert obligations["303"]["calendar_deadline_mismatch"] is True

        changed_entry = replace(
            loaded.entries[0],
            internal_due_on=loaded.entries[0].filing_opens_on,
            source_hash="changed-source-hash",
        )
        changed = db.import_tax_calendar([entry_as_record(changed_entry)])
        assert changed[0]["row_version"] == 2


def test_failed_multirow_calendar_import_rolls_back_every_calendar_row(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    loaded = load_tax_calendar(CALENDAR_PATH)
    records = [entry_as_record(loaded.entries[0]), entry_as_record(loaded.entries[1])]
    records[1]["source_url"] = "https://example.invalid/not-aeat"

    with initialize(database) as db:
        with pytest.raises(ValueError, match="official AEAT source"):
            db.import_tax_calendar(records)
        assert db.list_tax_calendar_entries() == []
        assert db.connection.execute("SELECT COUNT(*) FROM periods").fetchone()[0] == 0


def test_provisional_calendar_never_supplies_an_effective_due_date(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    record = {
        "calendar_year": 2027,
        "period_key": "2026-Q4",
        "form_code": "130",
        "filing_opens_on": "2027-01-01",
        "internal_due_on": "2027-01-24",
        "direct_debit_cutoff_on": "2027-01-25",
        "statutory_due_on": "2027-01-30",
        "deadline_status": "provisional",
        "source_url": "https://sede.agenciatributaria.gob.es/Sede/calendario-contribuyente.html",
        "source_checked_on": "2026-07-17",
        "notes": "Planning date only.",
        "source_hash": "provisional-calendar-source",
    }

    with initialize(database) as db:
        with pytest.raises(ValueError, match="must say that the date must be replaced"):
            db.import_tax_calendar([record])
        assert db.connection.execute("SELECT COUNT(*) FROM periods").fetchone()[0] == 0

        record["notes"] = "Replace after AEAT publishes the 2027 calendar."
        db.import_tax_calendar([record])
        db.add_obligation(
            period_key="2026-Q4",
            obligation_code="130",
            determination="due",
            filing_status="due",
            explanation="Direct-estimation activity",
        )
        obligation = db.list_obligations_with_deadlines(period_key="2026-Q4")[0]

        assert obligation["deadline_status"] == "provisional"
        assert obligation["calendar_statutory_due_on"] == "2027-01-30"
        assert obligation["due_on"] is None
        assert obligation["calendar_deadline_mismatch"] is False
