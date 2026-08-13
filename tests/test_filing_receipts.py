from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from autonomo_taxes.cli import main
from autonomo_taxes.filing_evidence import FilingEvidence
from autonomo_taxes.filing_receipts import (
    build_filing_receipt_payload,
    sha256_file,
    verify_filing_receipt,
)
from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.shadow_close import summarize_required_tax_settlements


M130_KEYS = (
    "01",
    "02",
    "03",
    "04",
    "05",
    "06",
    "07",
    "12",
    "13",
    "14",
    "15",
    "16",
    "17",
    "18",
    "19",
)


def _m130_values(overrides: dict[str, str]) -> dict[str, str]:
    values = {key: "0.00" for key in M130_KEYS}
    values.update(overrides)
    return values


def _evidence(
    *,
    form: str,
    period: str,
    filed_values: dict[str, str],
    source_sha256: str = "A" * 64,
    extraction_status: str | None = None,
) -> FilingEvidence:
    return FilingEvidence(
        form_code=form,
        period_key=period,
        filed_on="2026-10-07T12:34:56",
        submission_reference="202613009400430F",
        justificante_number="1307001157635",
        verification_code="QD558KKTDLCUTEG4",
        source_sha256=source_sha256,
        source_reference="filed.pdf",
        payload={
            "form": form,
            "period": period,
            "evidence_kind": "filed_return_pdf",
            "extraction_status": extraction_status
            or ("casillas_extracted" if form == "130" else "output_and_deductible"),
            "filed_values": filed_values,
        },
    )


def test_modelo130_receipt_matches_prepared_casillas() -> None:
    calculation = {
        "form": "130",
        "period": "2026-Q3",
        "values": {
            **_m130_values(
                {"01": "1000.00", "02": "50.00", "03": "950.00", "19": "190.00"}
            ),
            "difficult_expenses": "50.00",
        },
    }
    evidence = _evidence(
        form="130",
        period="2026-Q3",
        filed_values=_m130_values(
            {"01": "1000.00", "02": "50.00", "03": "950.00", "19": "190.00"}
        ),
    )

    verification = verify_filing_receipt(
        evidence,
        calculation,
        expected_form="130",
        expected_period="2026-Q3",
        calculation_sha256="B" * 64,
    )

    assert verification["status"] == "matched"
    assert verification["mismatches"] == {}
    payload = build_filing_receipt_payload(evidence, calculation, verification)
    assert payload["filed_values"]["19"] == "190.00"
    assert payload["values"]["19"] == "190.00"


def test_receipt_mismatch_is_fail_closed() -> None:
    calculation = {
        "form": "130",
        "period": "2026-Q3",
        "values": _m130_values({"01": "1000.00", "02": "50.00", "19": "190.00"}),
    }
    evidence = _evidence(
        form="130",
        period="2026-Q3",
        filed_values=_m130_values(
            {"01": "1000.00", "02": "50.00", "19": "189.98"}
        ),
    )

    verification = verify_filing_receipt(
        evidence,
        calculation,
        expected_form="130",
        expected_period="2026-Q3",
        calculation_sha256="B" * 64,
    )

    assert verification["status"] == "mismatch"
    assert verification["mismatches"] == {"19": "0.02"}
    with pytest.raises(ValueError, match="mismatched receipt"):
        build_filing_receipt_payload(evidence, calculation, verification)


def test_receipt_requires_typed_aeat_submission_metadata() -> None:
    evidence = replace(
        _evidence(
            form="130",
            period="2026-Q3",
            filed_values=_m130_values(
                {"01": "1000.00", "02": "50.00", "19": "190.00"}
            ),
        ),
        verification_code=None,
    )

    with pytest.raises(ValueError, match="verification_code"):
        verify_filing_receipt(
            evidence,
            {
                "form": "130",
                "period": "2026-Q3",
                "values": _m130_values(
                    {"01": "1000.00", "02": "50.00", "19": "190.00"}
                ),
            },
            expected_form="130",
            expected_period="2026-Q3",
            calculation_sha256="B" * 64,
        )


def test_modelo303_receipt_compares_filed_aggregate_values() -> None:
    calculation = {
        "form": "303",
        "period": "2026-Q3",
        "values": {
            "150": "0.00",
            "01": "0.00",
            "04": "0.00",
            "07": "100.00",
            "10": "50.00",
            "12": "0.00",
            "27": "31.50",
            "28": "40.00",
            "30": "0.00",
            "32": "0.00",
            "34": "0.00",
            "36": "50.00",
            "38": "0.00",
            "45": "18.90",
            "71": "12.60",
            "rule_source": "official AEAT fixture",
        },
    }
    evidence = _evidence(
        form="303",
        period="2026-Q3",
        filed_values={
            "output_base": "150.00",
            "output_vat": "31.50",
            "deductible_base": "90.00",
            "deductible_vat": "18.90",
            "result": "12.60",
        },
    )

    verification = verify_filing_receipt(
        evidence,
        calculation,
        expected_form="303",
        expected_period="2026-Q3",
        calculation_sha256="C" * 64,
    )

    assert verification["status"] == "matched"
    assert verification["compared_values"]["output_base"]["calculated"] == "150.00"


def test_record_receipt_cli_archives_typed_snapshot_and_is_idempotent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "ledger.sqlite"
    archive = tmp_path / "archive"
    filed_pdf = tmp_path / "filed.pdf"
    filed_pdf.write_bytes(b"filed-pdf-fixture")
    calculation_path = tmp_path / "modelo130.json"
    calculation_path.write_text(
        json.dumps(
            {
                "blocked": False,
                "form": "130",
                "period": "2026-Q3",
                "values": _m130_values(
                    {"01": "1000.00", "02": "50.00", "19": "190.00"}
                ),
                "lineage": {"01": ["income-1"], "02": ["expense-1"]},
            }
        ),
        encoding="utf-8",
    )
    with initialize(database) as db:
        db.ensure_period(
            "2026-Q3",
            starts_on=date(2026, 7, 1).isoformat(),
            ends_on=date(2026, 9, 30).isoformat(),
        )
        db.close_period("2026-Q3")

    evidence = _evidence(
        form="130",
        period="2026-Q3",
        filed_values=_m130_values(
            {"01": "1000.00", "02": "50.00", "19": "190.00"}
        ),
        source_sha256=sha256_file(filed_pdf),
    )
    command = [
        "filings",
        "record-receipt",
        "--db",
        str(database),
        "--period",
        "2026-Q3",
        "--form",
        "130",
        "--filed-pdf",
        str(filed_pdf),
        "--calculation",
        str(calculation_path),
        "--archive-root",
        str(archive),
    ]
    with patch(
        "autonomo_taxes.filing_evidence.extract_filing_evidence",
        return_value=evidence,
    ):
        assert main(command) == 0
        first = json.loads(capsys.readouterr().out)
        assert main(command) == 0
        second = json.loads(capsys.readouterr().out)

    assert first["recorded"] is True
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    archived = list((archive / "2026-Q3" / "filed_return_pdf").glob("*.pdf"))
    assert len(archived) == 1
    with initialize(database) as db:
        row = db.connection.execute(
            "SELECT * FROM filing_snapshots"
        ).fetchone()
        assert row["form_code"] == "130"
        assert row["submission_reference"] == evidence.submission_reference
        assert row["justificante_number"] == evidence.justificante_number
        assert row["verification_code"] == evidence.verification_code
        payload = json.loads(row["payload_json"])
        assert payload["receipt_verification"]["status"] == "matched"
        assert payload["receipt_verification"]["calculation_sha256"] == sha256_file(
            calculation_path
        )


def test_record_receipt_cli_mismatch_does_not_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "ledger.sqlite"
    filed_pdf = tmp_path / "filed.pdf"
    filed_pdf.write_bytes(b"different-filed-pdf-fixture")
    calculation_path = tmp_path / "modelo130.json"
    calculation_path.write_text(
        json.dumps(
            {
                "form": "130",
                "period": "2026-Q3",
                "values": _m130_values(
                    {"01": "1000.00", "02": "50.00", "19": "190.00"}
                ),
            }
        ),
        encoding="utf-8",
    )
    evidence = _evidence(
        form="130",
        period="2026-Q3",
        filed_values=_m130_values(
            {"01": "1000.00", "02": "50.00", "19": "189.98"}
        ),
        source_sha256=sha256_file(filed_pdf),
    )
    command = [
        "filings",
        "record-receipt",
        "--db",
        str(database),
        "--period",
        "2026-Q3",
        "--form",
        "130",
        "--filed-pdf",
        str(filed_pdf),
        "--calculation",
        str(calculation_path),
        "--dry-run",
    ]

    with patch(
        "autonomo_taxes.filing_evidence.extract_filing_evidence",
        return_value=evidence,
    ):
        assert main(command) == 2

    emitted = json.loads(capsys.readouterr().out)
    assert emitted["ok"] is False
    assert emitted["verification"]["mismatches"] == {"19": "0.02"}
    assert not database.exists()


def test_tax_procedure_receipt_keeps_original_settlement_amount_unambiguous(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "ledger.sqlite"
    archive = tmp_path / "archive"
    evidence = tmp_path / "rectification-receipt.pdf"
    evidence.write_bytes(b"aeat-rectification-receipt")
    with initialize(database) as db:
        db.ensure_period("2026-Q2")
        db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            filing_status="filed",
            determination="due",
            blocking=False,
        )
        original = db.create_filing_snapshot(
            "2026-Q2",
            payload={"form": "130", "filed_values": {"19": "2639.12"}},
            filed_on="2026-07-08",
            status="baseline",
            form_code="130",
            source_hash="original-modelo130",
        )

    command = [
        "filings",
        "record-procedure-receipt",
        "--db",
        str(database),
        "--period",
        "2026-Q2",
        "--form",
        "130",
        "--procedure",
        "rectification",
        "--stage",
        "submitted",
        "--evidence",
        str(evidence),
        "--occurred-on",
        "2026-07-21",
        "--reference",
        "GZ28-2026-Q2-130",
        "--amount-eur",
        "264.90",
        "--archive-root",
        str(archive),
    ]
    assert main(command) == 0
    first = json.loads(capsys.readouterr().out)
    with initialize(database) as db:
        db.create_filing_snapshot(
            "2026-Q2",
            payload={"form": "130", "filed_values": {"19": "2639.12"}},
            filed_on="2026-07-08T12:49:45",
            status="baseline",
            form_code="130",
            source_hash="later-extracted-original-modelo130",
        )
    assert main(command) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["snapshot_status"] == "procedure_submitted"
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    archived = list(
        (archive / "2026-Q2" / "tax_procedure_receipt").glob("*.pdf")
    )
    assert len(archived) == 1
    with initialize(database) as db:
        snapshots = db.connection.execute(
            "SELECT * FROM filing_snapshots ORDER BY created_at"
        ).fetchall()
        assert len(snapshots) == 3
        procedure = next(row for row in snapshots if row["status"] == "procedure_submitted")
        payload = json.loads(procedure["payload_json"])
        assert payload["original_payable_eur"] == "2639.12"
        assert payload["procedure_amount_eur"] == "264.90"
        assert payload["original_filing_lineage"] == [
            {
                "filed_on": "2026-07-08",
                "filing_snapshot_id": original["filing_snapshot_id"],
                "source_hash": "original-modelo130",
                "status": "baseline",
            }
        ]
        settlement = summarize_required_tax_settlements(
            db,
            selectors=[("2026-Q2", "130")],
        )

    requirement = settlement["requirements"][0]
    assert requirement["status"] == "payment_missing"
    assert requirement["expected_amount_minor"] == 263912
    assert requirement["amount_check_status"] == "pending_payment"


def test_tax_procedure_receipt_rejects_ambiguous_original_filing(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    evidence = tmp_path / "rectification-receipt.pdf"
    evidence.write_bytes(b"aeat-rectification-receipt")
    with initialize(database) as db:
        db.ensure_period("2026-Q2")
        for amount, source_hash in (
            ("2639.12", "original-modelo130"),
            ("2374.22", "incorrect-second-filing"),
        ):
            db.create_filing_snapshot(
                "2026-Q2",
                payload={"form": "130", "filed_values": {"19": amount}},
                filed_on="2026-07-08",
                status="baseline",
                form_code="130",
                source_hash=source_hash,
            )

    with pytest.raises(
        ValueError,
        match="Original Modelo 130 payable amount is ambiguous: 2374.22, 2639.12",
    ):
        main(
            [
                "filings",
                "record-procedure-receipt",
                "--db",
                str(database),
                "--period",
                "2026-Q2",
                "--form",
                "130",
                "--procedure",
                "rectification",
                "--stage",
                "submitted",
                "--evidence",
                str(evidence),
                "--occurred-on",
                "2026-07-21",
                "--reference",
                "GZ28-2026-Q2-130",
                "--amount-eur",
                "264.90",
                "--dry-run",
            ]
        )
