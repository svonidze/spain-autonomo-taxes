"""Executable synthetic CLI walkthrough; always creates a new temporary ledger.

Run with an installed core: python docs/examples/agent_workflow.py
No user configuration, network provider, UI or pre-existing ledger is used.
The decisions below are fixture facts, never defaults for real invoices.
"""

from contextlib import redirect_stdout, redirect_stderr
from datetime import date
import io
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4


def walkthrough(root):
    from autonomo_taxes.cli import main

    root = Path(root).resolve()
    root.mkdir(mode=0o700, exist_ok=False)
    config = root / "config.json"
    config.write_text(
        json.dumps(
            {
                "ledger_db": str(root / "ledger.sqlite"),
                "inbox_root": str(root / "inbox"),
                "archive_root": str(root / "evidence"),
            }
        )
    )
    os.environ["AUTONOMO_PRIVATE_ROOT"] = str(root)
    counter = 0

    def command(*words, payload=None):
        nonlocal counter
        args = [
            "--config",
            str(config),
            *map(str, words),
            "--db",
            str(root / "ledger.sqlite"),
        ]
        if payload is not None:
            counter += 1
            request = root / f"request-{counter}.json"
            request.write_text(json.dumps(payload))
            request.chmod(0o600)
            args += ["--input", str(request)]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args)
        if code:
            raise AssertionError(
                f"{words[:2]} failed: {err.getvalue() or out.getvalue()}"
            )
        return json.loads(out.getvalue())

    command("db", "init")
    profile = command(
        "profile",
        "set",
        payload={
            "tax_id": "TEST-TAX-ID-001",
            "full_name": "Synthetic Taxpayer",
            "source_hash": "synthetic-registration",
        },
    )
    command(
        "profile",
        "activity-upsert",
        payload={
            "taxpayer_profile_id": profile["taxpayer_profile_id"],
            "activity_key": "synthetic-services",
            "aeat_activity_code": "A",
            "aeat_activity_type": "05",
            "iae_section": "2",
            "iae_group_epigraph": "763",
            "description": "Synthetic programming activity",
            "starts_on": "2023-01-01",
            "source_reference": "Synthetic registration",
            "source_hash": "synthetic-activity",
        },
    )
    today = date.today().isoformat()
    period = f"{date.today().year}-Q{(date.today().month - 1)//3 + 1}"

    def intake(number, kind, amount):
        source = root / f"{number}.txt"
        source.write_text(f"SYNTHETIC ONLY\n{number}\n{today}\nEUR {amount}\n")
        result = command(
            "intake",
            "local",
            source,
            payload={
                "kind": kind,
                "period": period,
                "issued_on": today,
                "document_number": number,
                "gross": amount,
                "currency": "EUR",
                "counterparty_name": (
                    "Synthetic Supplier"
                    if kind == "expense_invoice"
                    else "Synthetic Customer"
                ),
            },
        )
        assert result["transaction_id"]
        return result

    def decision(value, issues, income=False):
        value.update(
            outcome="approve",
            reason="Reviewed synthetic fixture facts",
            business_purpose="Synthetic professional software activity",
            document_valid=True,
            asset_decision="not_applicable" if income else "current_expense",
            asset_id=None,
            counterparty_changes={"country_code": "US" if income else "ES"},
        )
        value["tax_treatment"] = dict(
            tax_code="outside_scope" if income else "domestic_input",
            aeat_invoice_type="F1",
            aeat_operation_key="01",
            aeat_operation_qualification="N2" if income else "S1",
            aeat_exemption_code=None,
            aeat_reverse_charge=False,
            vat_investment_good=False,
            aeat_expense_concept=None if income else "G03",
            rate_basis_points=0 if income else 2100,
            deductible_ratio=1.0,
            taxable_base_minor=10000,
            vat_minor=0 if income else 2100,
            deductible_irpf_minor=0 if income else 10000,
            deductible_vat_minor=0 if income else 2100,
            withholding_minor=0,
            include_modelo130=True,
            include_modelo303=True,
            include_modelo347=False,
            rule_version_id=None,
            notes="Synthetic reviewed classification",
        )
        # Only these known issues are supported by this generated fixture.
        # Any new issue requires updating and reviewing the example, not blanket resolution.
        reasons = {
            "document_structural_review": "Generated text original inspected: number, date, currency and amount are present",
            "document_classification_review": "Generated original kind matches this synthetic income/expense scenario",
            "counterparty_tax_profile_review": "Country explicitly specified by this synthetic fixture",
            "transaction_tax_review": "Tax treatment explicitly specified by this synthetic fixture",
        }
        by_id = {issue["validation_issue_id"]: issue for issue in issues}
        if any(issue["issue_code"] not in reasons for issue in issues):
            raise AssertionError(
                "Unexpected issue in synthetic example; stop for review"
            )
        for resolution in value["issue_resolutions"]:
            issue = by_id[resolution["issue_id"]]
            resolution.update(action="resolve", reason=reasons[issue["issue_code"]])

    income = intake("SYN-INCOME", "income_invoice", "100.00")
    work = root / "income-work-item.json"
    command(
        "review", "work-item", "transaction:" + income["transaction_id"], "--out", work
    )
    packet = json.loads(work.read_text())["packet"]
    decision(packet["decision"], packet["state"]["issues"], income=True)
    command("review", "confirm-packet", payload={"packet": packet, "fx": None})
    current = command("transactions", "show", income["transaction_id"])["transaction"]
    command("review", "posting-preview", "--period", period)
    command(
        "review",
        "post",
        "transaction:" + income["transaction_id"],
        "--expected-row-version",
        current["row_version"],
    )
    assert (
        command("transactions", "show", income["transaction_id"])["transaction"][
            "lifecycle_status"
        ]
        == "posted"
    )
    command(
        "documents",
        "original",
        income["document_id"],
        "--out",
        root / "verified-original.txt",
    )

    for equipment in (False, True):
        record = intake(
            "SYN-EQUIPMENT" if equipment else "SYN-EXPENSE", "expense_invoice", "121.00"
        )
        identifier = record["transaction_id"]
        output = root / f"{identifier}-draft.json"
        command("expense", "draft", identifier, "--out", output)
        draft = json.loads(output.read_text())
        payload = draft["payload"]
        decision(payload["decision"], draft["source"]["issues"])
        payload["manual_review_reason"] = (
            "All generated text and facts inspected; synthetic fixture only"
        )
        payload["change_reason"] = "Reviewed synthetic original"
        if equipment:
            payload["asset"] = dict(
                description="Synthetic equipment",
                basis_minor=10000,
                business_use_ratio=1.0,
                annual_rate_basis_points=10000,
                placed_in_service_on=today,
                method="immediate",
                new_equipment=True,
                aeat_asset_type="23",
            )
        saved = command(
            "expense",
            "save",
            identifier,
            payload={
                "payload": payload,
                "expected_version": draft["draft_version"],
                "source_snapshot_hash": draft["source_snapshot_hash"],
            },
        )
        preview = command(
            "expense",
            "preview",
            identifier,
            payload={"expected_version": saved["draft_version"]},
        )
        request = {
            "expected_version": saved["draft_version"],
            "preview_token": preview["preview_token"],
            "request_id": str(uuid4()),
        }
        posted = command("expense", "confirm", identifier, payload=request)
        assert posted["posted"]
        reread = command("transactions", "show", identifier)
        assert reread["transaction"]["lifecycle_status"] == "posted"
        repeated = command("expense", "confirm", identifier, payload=request)
        assert repeated["transaction_id"] == posted["transaction_id"]
        command("expense", "follow-up", identifier)
        if equipment:
            schedule = command("assets", "schedule", posted["asset_id"])
            assert any(row["recognition_transaction_id"] for row in schedule["rows"])
    for name in ("summary", "taxes", "analytics"):
        command("period", name, "--period", period)
    return {
        "income": "posted",
        "expense": "posted",
        "equipment": "posted",
        "depreciation": "recognized",
        "repeated_request": "same transaction",
    }


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="autonomo-synthetic-guide-") as directory:
        print(json.dumps(walkthrough(Path(directory) / "private")))
