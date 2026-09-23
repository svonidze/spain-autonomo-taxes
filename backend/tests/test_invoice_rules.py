from __future__ import annotations

import csv
from datetime import date
import json
from pathlib import Path

import pytest

from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.review_packet import (
    ReviewPacketError,
    confirm_review_packet,
    prepare_review_work_item,
)
from autonomo_taxes.sheet_intake import INCOME_INTAKE_FIELDS
from autonomo_taxes.tax_rules import (
    INCOME_BEFORE_ACTIVITY_START_CODE,
    INVOICE_ISSUE_DEADLINE_CODE,
    invoice_issue_deadline,
    invoice_issue_deadline_warning,
)


def _activity(db, starts_on: str):
    profile = db.upsert_taxpayer_profile(
        tax_id="X0000000A",
        full_name="Example Taxpayer",
        source_hash="profile-source",
    )
    return db.upsert_business_activity(
        taxpayer_profile_id=profile["taxpayer_profile_id"],
        activity_key="software-development",
        aeat_activity_code="A",
        aeat_activity_type="05",
        iae_section="2",
        iae_group_epigraph="763",
        description="Software development",
        starts_on=starts_on,
        source_reference="Modelo 036",
        source_hash="activity-source",
    )


def _template(db, *, country_code: str = "US"):
    counterparty = db.upsert_counterparty(
        external_key=f"customer-{country_code.lower()}",
        tax_id="12-3456789",
        display_name="Example Customer",
        country_code=country_code,
        email="billing@example.com",
    )
    template = db.upsert_invoice_template(
        template_key="services",
        template_name="Software services",
        counterparty_id=counterparty["counterparty_id"],
        currency="USD",
        default_lines=[
            {
                "description": "Software development",
                "quantity": "1",
                "unit_amount_minor": 100000,
                "tax_code": "outside_scope",
                "channel_tax_code": "N2",
                "tax_rate_basis_points": 0,
            }
        ],
        channel_hint="external_compliant_channel",
        delivery_email="billing@example.com",
        recipient_address_line1="100 Example Street",
        recipient_city="Example City",
    )
    return counterparty, template


def _income_intake(
    tmp_path: Path,
    capsys,
    *,
    activity_starts_on: str | None = None,
    **row_changes: str,
) -> tuple[Path, str, list[dict]]:
    database = tmp_path / "ledger.sqlite"
    inbox = tmp_path / "Inbox"
    row = {
        "system_id": "",
        "file_name": "SYNTH-INCOME-001.txt",
        "customer": "Example Customer Inc",
        "invoice_number": "SYNTH-INCOME-001",
        "issue_date": "2026-08-15",
        "service_period_from": "2026-07-01",
        "service_period_to": "2026-07-31",
        "service_description": "Software development services",
        "gross_amount": "1000.00",
        "currency": "EUR",
        "payment_due_date": "",
        "correction_of": "",
        "notes": "",
        **row_changes,
    }
    issue_on = date.fromisoformat(row["issue_date"])
    folder = inbox / f"{issue_on.year}-Q{(issue_on.month - 1) // 3 + 1}" / "income_invoice"
    folder.mkdir(parents=True)
    (folder / row["file_name"]).write_text(
        f"Invoice No: {row['invoice_number']} Date: {row['issue_date']} Example Customer Inc. "
        "Software development services. Invoice total: 1000.00 EUR",
        encoding="utf-8",
    )
    source = tmp_path / "income.csv"
    with source.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(INCOME_INTAKE_FIELDS))
        writer.writeheader()
        writer.writerow(row)
    with initialize(database) as db:
        if activity_starts_on is not None:
            _activity(db, activity_starts_on)
    assert main(
        [
            "intake", "apply", "--db", str(database), "--tab", "income_intake",
            "--remote-csv", str(source), "--out-csv", str(tmp_path / "writeback.csv"),
            "--inbox-root", str(inbox), "--archive-root", str(tmp_path / "Evidence"),
        ]
    ) == 0
    transaction_id = json.loads(capsys.readouterr().out)["items"][0]["transaction_id"]
    with initialize(database) as db:
        issues = [
            dict(item)
            for item in db.connection.execute(
                """
                SELECT issue_code, severity, blocking, message FROM validation_issues
                WHERE subject_table = 'transactions' AND subject_id = ?
                """,
                (transaction_id,),
            )
        ]
    return database, transaction_id, issues


def _codes(issues: list[dict]) -> set[str]:
    return {issue["issue_code"] for issue in issues}


# Commit A: issue deadline (RD 1619/2012 art. 11.1) and activity start.


@pytest.mark.parametrize(
    ("accrued_on", "deadline"),
    [
        ("2026-07-31", "2026-08-15"),
        ("2026-07-01", "2026-08-15"),
        ("2026-01-31", "2026-02-15"),
        ("2026-12-31", "2027-01-15"),
    ],
)
def test_invoice_issue_deadline_is_the_15th_of_the_following_month(accrued_on, deadline):
    assert invoice_issue_deadline(date.fromisoformat(accrued_on)) == date.fromisoformat(deadline)


def test_invoice_issue_deadline_warning_accepts_the_15th_and_flags_the_16th():
    accrued = date(2026, 7, 31)
    assert invoice_issue_deadline_warning(accrued_on=accrued, issued_on=date(2026, 8, 15)) is None
    message = invoice_issue_deadline_warning(accrued_on=accrued, issued_on=date(2026, 8, 16))
    assert message is not None
    assert "2026-08-15" in message and "treated as a business" in message


def test_outgoing_draft_warns_only_after_the_issue_deadline(tmp_path: Path):
    with initialize(tmp_path / "ledger.sqlite") as db:
        _, template = _template(db)
        on_time = db.create_outgoing_invoice_draft(
            draft_key="services:2026-06-on-time",
            invoice_template_id=template["invoice_template_id"],
            period_key="2026-Q3",
            service_on="2026-06-30",
            planned_issue_on="2026-07-15",
        )
        late = db.create_outgoing_invoice_draft(
            draft_key="services:2026-06-late",
            invoice_template_id=template["invoice_template_id"],
            period_key="2026-Q3",
            service_on="2026-06-30",
            planned_issue_on="2026-07-16",
        )
        shown = db.get_outgoing_invoice_draft(late["outgoing_invoice_draft_id"])

    assert on_time["warnings"] == []
    assert [warning["code"] for warning in late["warnings"]] == [INVOICE_ISSUE_DEADLINE_CODE]
    assert "RD 1619/2012" in late["warnings"][0]["source"]
    assert shown["warnings"] == late["warnings"]
    assert late["total_minor"] == on_time["total_minor"]


def test_income_issued_on_the_15th_after_month_end_service_has_no_deadline_issue(
    tmp_path: Path, capsys
):
    _, _, issues = _income_intake(tmp_path, capsys, issue_date="2026-08-15")
    assert INVOICE_ISSUE_DEADLINE_CODE not in _codes(issues)


def test_income_issued_on_the_16th_gets_a_non_blocking_deadline_issue(tmp_path: Path, capsys):
    database, transaction_id, issues = _income_intake(tmp_path, capsys, issue_date="2026-08-16")
    deadline = [issue for issue in issues if issue["issue_code"] == INVOICE_ISSUE_DEADLINE_CODE]
    assert len(deadline) == 1
    assert deadline[0]["severity"] == "warning"
    assert deadline[0]["blocking"] == 0
    assert "treated as a business" in deadline[0]["message"]
    with initialize(database) as db:
        work_item = prepare_review_work_item(db, f"transaction:{transaction_id}")
    assert work_item["supported"] is True


def test_income_on_the_activity_start_date_has_no_activity_issue(tmp_path: Path, capsys):
    _, _, issues = _income_intake(
        tmp_path,
        capsys,
        activity_starts_on="2026-07-01",
        issue_date="2026-07-31",
    )
    assert INCOME_BEFORE_ACTIVITY_START_CODE not in _codes(issues)


def test_service_period_before_activity_start_is_a_blocking_review_issue(
    tmp_path: Path, capsys
):
    database, transaction_id, issues = _income_intake(
        tmp_path,
        capsys,
        activity_starts_on="2026-07-02",
        issue_date="2026-07-31",
    )
    activity = [
        issue for issue in issues if issue["issue_code"] == INCOME_BEFORE_ACTIVITY_START_CODE
    ]
    assert len(activity) == 1
    assert activity[0]["blocking"] == 1
    assert activity[0]["severity"] == "warning"
    assert "service period start 2026-07-01 precedes" in activity[0]["message"]
    with initialize(database) as db:
        work_item = prepare_review_work_item(db, f"transaction:{transaction_id}")
    assert work_item["supported"] is True
    assert "confirm_income_recognition" in work_item["requirement_step_codes"]


def test_uploaded_income_before_activity_start_is_flagged_without_a_service_period(
    tmp_path: Path, capsys
):
    database = tmp_path / "ledger.sqlite"
    invoice = tmp_path / "SYNTH-INCOME-002.txt"
    invoice.write_text(
        "Invoice No: SYNTH-INCOME-002 Date: 2026-07-09 Example Customer Inc. "
        "Software development services. Invoice total: 1000.00 EUR",
        encoding="utf-8",
    )
    with initialize(database) as db:
        _activity(db, "2026-07-10")
    assert main(
        [
            "ingest", "--db", str(database), str(invoice), "--kind", "income_invoice",
            "--period", "2026-Q3", "--issued-on", "2026-07-09", "--gross", "1000.00",
            "--currency", "EUR", "--archive-root", str(tmp_path / "Evidence"),
        ]
    ) == 0
    transaction_id = json.loads(capsys.readouterr().out)["transaction"]["transaction_id"]
    with initialize(database) as db:
        codes = {
            row["issue_code"]
            for row in db.connection.execute(
                "SELECT issue_code FROM validation_issues WHERE subject_id = ?",
                (transaction_id,),
            )
        }
    assert INCOME_BEFORE_ACTIVITY_START_CODE in codes
    assert INVOICE_ISSUE_DEADLINE_CODE not in codes

    # Re-ingesting the same source reuses the transaction and adds no second issue.
    assert main(
        [
            "ingest", "--db", str(database), str(invoice), "--kind", "income_invoice",
            "--period", "2026-Q3", "--issued-on", "2026-07-09", "--gross", "1000.00",
            "--currency", "EUR", "--archive-root", str(tmp_path / "Evidence"),
        ]
    ) == 0
    repeated = json.loads(capsys.readouterr().out)["transaction"]
    assert repeated["created"] is False
    assert repeated["transaction_id"] == transaction_id
    with initialize(database) as db:
        assert db.connection.execute(
            "SELECT COUNT(*) FROM validation_issues WHERE issue_code = ? AND subject_id = ?",
            (INCOME_BEFORE_ACTIVITY_START_CODE, transaction_id),
        ).fetchone()[0] == 1


def test_income_without_a_recorded_activity_has_no_activity_issue(tmp_path: Path, capsys):
    _, _, issues = _income_intake(tmp_path, capsys, issue_date="2026-07-31")
    assert INCOME_BEFORE_ACTIVITY_START_CODE not in _codes(issues)


def test_late_corrective_income_invoice_has_no_deadline_issue(tmp_path: Path, capsys):
    _, _, issues = _income_intake(
        tmp_path, capsys, issue_date="2026-08-16", correction_of="SYNTH-INCOME-000"
    )
    codes = _codes(issues)
    assert INVOICE_ISSUE_DEADLINE_CODE not in codes
    assert "invoice_correction_review" in codes


def test_guided_review_cannot_approve_with_an_unresolved_activity_issue(
    tmp_path: Path, capsys
):
    database, transaction_id, _ = _income_intake(
        tmp_path, capsys, activity_starts_on="2026-07-02", issue_date="2026-07-31"
    )
    with initialize(database) as db:
        packet = prepare_review_work_item(db, f"transaction:{transaction_id}")["packet"]
        activity_issue_id = next(
            issue["validation_issue_id"]
            for issue in packet["state"]["issues"]
            if issue["issue_code"] == INCOME_BEFORE_ACTIVITY_START_CODE
        )
        packet["decision"]["outcome"] = "approve"
        packet["decision"]["reason"] = "Synthetic review"
        for resolution in packet["decision"]["issue_resolutions"]:
            if resolution["issue_id"] != activity_issue_id:
                resolution.update(action="resolve", reason="Synthetic resolution")
        with pytest.raises(ReviewPacketError, match="Every blocking issue.*" + activity_issue_id):
            confirm_review_packet(db, packet)
