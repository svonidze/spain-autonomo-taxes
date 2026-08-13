from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter

from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import initialize, open as open_ledger_db
from autonomo_taxes.offboarding import build_offboarding_manifest_document


@dataclass(frozen=True)
class CutoverFixture:
    database: Path
    manifest: Path
    output_dir: Path
    supplier_transaction_id: str
    tgss_transaction_id: str
    manifest_artifact: Path


@pytest.mark.parametrize(
    ("broken_gate", "expected_gate"),
    [
        (None, None),
        ("accounting_data", "accounting_data"),
        ("posting", "posting"),
        ("aeat_books", "aeat_books"),
        ("archive", "offboarding_archive"),
        ("stale_manifest", "offboarding_archive"),
        ("operational_proof", "operational_acceptance"),
        ("tax_settlement", "required_tax_settlements"),
    ],
)
def test_real_cutover_producers_fail_closed_end_to_end(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    broken_gate: str | None,
    expected_gate: str | None,
) -> None:
    fixture = _ready_cutover_fixture(tmp_path, capsys)
    _break_gate(fixture, broken_gate)

    report = _run_shadow_close(fixture, capsys)

    assert report["summary"]["cutover_ready"] is (broken_gate is None)
    if expected_gate is None:
        assert {
            gate: report["gates"][gate]["status"]
            for gate in (
                "accounting_data",
                "posting",
                "aeat_books",
                "offboarding_archive",
                "operational_acceptance",
                "required_tax_settlements",
            )
        } == {
            "accounting_data": "ready",
            "posting": "ready",
            "aeat_books": "ready",
            "offboarding_archive": "ready",
            "operational_acceptance": "ready",
            "required_tax_settlements": "ready",
        }
        settlement = report["gates"]["required_tax_settlements"]["requirements"][0]
        assert settlement["expected_amount_minor"] == 263912
        assert settlement["evidenced_amount_minor"] == 263912
        assert settlement["amount_check_status"] == "matched"
        return

    assert report["gates"][expected_gate]["status"] != "ready"
    if broken_gate == "operational_proof":
        assert report["gates"]["operational_acceptance"]["missing_proofs"] == [
            "posted_independent_expense"
        ]
    elif broken_gate == "tax_settlement":
        assert report["gates"]["required_tax_settlements"]["missing_settlements"] == [
            {"selector": "2026-Q2:130", "reason": "payment_missing"}
        ]
    elif broken_gate == "archive":
        assert report["gates"]["offboarding_archive"]["hash_mismatch_paths"] == [
            str(fixture.manifest_artifact.resolve())
        ]
    elif broken_gate == "stale_manifest":
        assert report["gates"]["offboarding_archive"]["integrity_ok"] is True
        assert report["gates"]["offboarding_archive"]["manifest_fresh"] is False
        assert (
            report["gates"]["offboarding_archive"]["freshness_reason"]
            == "generated_on_mismatch"
        )


def test_shadow_close_rejects_v2_historical_only_manifest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _ready_cutover_fixture(tmp_path, capsys)
    v3_manifest = json.loads(fixture.manifest.read_text(encoding="utf-8"))
    fixture.manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "report_type": v3_manifest["report_type"],
                "generated_on": v3_manifest["generated_on"],
                "source_roots": v3_manifest["source_roots"],
                "rows": v3_manifest["rows"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    report = _run_shadow_close(fixture, capsys)

    archive_gate = report["gates"]["offboarding_archive"]
    assert report["summary"]["cutover_ready"] is False
    assert archive_gate["status"] == "blocked"
    assert archive_gate["integrity_ok"] is True
    assert archive_gate["strict_readiness"] is False
    assert archive_gate["strict_readiness_reasons"] == [
        "schema_v2_historical_only"
    ]


def _ready_cutover_fixture(
    root: Path,
    capsys: pytest.CaptureFixture[str],
) -> CutoverFixture:
    database = root / "ledger.sqlite"
    archive_root = root / "evidence"
    supplier_evidence = root / "supplier-factura.pdf"
    supplier_evidence.write_bytes(b"immutable supplier factura")
    supplier_digest = hashlib.sha256(supplier_evidence.read_bytes()).hexdigest()

    with initialize(database) as db:
        profile = db.upsert_taxpayer_profile(
            tax_id="X0000000A",
            full_name="Example Taxpayer",
            source_hash="profile-source",
        )
        activity = db.upsert_business_activity(
            taxpayer_profile_id=profile["taxpayer_profile_id"],
            activity_key="software-development",
            aeat_activity_code="A",
            aeat_activity_type="05",
            iae_section="2",
            iae_group_epigraph="763",
            description="Software development",
            starts_on="2023-05-31",
            source_reference="Modelo 036 fixture",
            source_hash="activity-source",
        )
        supplier = db.upsert_counterparty(
            external_key="supplier-live",
            tax_id="B12345678",
            display_name="Supplier Example SL",
            country_code="ES",
        )
        db.upsert_counterparty(
            external_key="tgss",
            tax_id="Q2827003A",
            display_name="Synthetic Party 011",
            country_code="ES",
        )
        batch = db.add_import_batch(
            source_name="Inbox/2026-Q3/expense_invoice/supplier-factura.pdf",
            source_hash="independent-inbox-batch",
            batch_key="inbox:supplier-live",
        )
        document = db.upsert_document(
            external_key=f"sha256:{supplier_digest}",
            counterparty_id=supplier["counterparty_id"],
            import_batch_id=batch["import_batch_id"],
            document_type="expense_invoice",
            document_number="SUP-2026-001",
            issued_on="2026-07-10",
            period_key="2026-Q3",
            currency="EUR",
            total_minor=12100,
            lifecycle_status="needs_review",
            source_hash=supplier_digest,
        )
        document = db.set_document_storage(
            document["document_id"],
            source_path=str(supplier_evidence.resolve()),
            mime_type="application/pdf",
            expected_row_version=document["row_version"],
        )
        supplier_transaction = db.add_transaction(
            external_key="supplier-live-transaction",
            period_key="2026-Q3",
            transaction_date="2026-07-10",
            booking_date="2026-07-10",
            entry_type="expense",
            description="Reviewed business software expense",
            amount_minor=12100,
            lifecycle_status="needs_review",
            document_id=document["document_id"],
            counterparty_id=supplier["counterparty_id"],
            business_activity_id=activity["business_activity_id"],
            source_hash="supplier-live-transaction-source",
        )
        db.add_detailed_tax_treatment(
            transaction_id=supplier_transaction["transaction_id"],
            treatment_type="expense",
            tax_code="domestic_input",
            aeat_invoice_type="F1",
            aeat_operation_key="01",
            aeat_reverse_charge=False,
            aeat_expense_concept="G03",
            rate_basis_points=2100,
            deductible_ratio=1.0,
            taxable_base_minor=10000,
            vat_minor=2100,
            deductible_irpf_minor=10000,
            deductible_vat_minor=2100,
            withholding_minor=0,
            include_modelo130=True,
            include_modelo303=True,
            notes="Independent reviewed supplier expense",
        )
        obligation = db.add_obligation(
            period_key="2026-Q2",
            obligation_code="130",
            determination="due",
            filing_status="filed",
            filed_at="2026-07-08",
            blocking=False,
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
            "review",
            "confirm",
            "--db",
            str(database),
            f"document:{document['document_id']}",
            "--expected-row-version",
            str(document["row_version"]),
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "review",
            "confirm",
            "--db",
            str(database),
            f"transaction:{supplier_transaction['transaction_id']}",
            "--expected-row-version",
            str(supplier_transaction["row_version"]),
        ]
    ) == 0
    supplier_approved = json.loads(capsys.readouterr().out)
    assert main(
        [
            "review",
            "post",
            "--db",
            str(database),
            f"transaction:{supplier_transaction['transaction_id']}",
            "--expected-row-version",
            str(supplier_approved["row_version"]),
        ]
    ) == 0
    capsys.readouterr()

    tgss_evidence = root / "tgss-debit.txt"
    tgss_evidence.write_text(
        "TGSS debit receipt 2026-07-15 reference RETA-2026-07 amount EUR 370.59",
        encoding="utf-8",
    )
    assert main(
        [
            "expense",
            "record",
            "--db",
            str(database),
            "--kind",
            "social-security",
            "--evidence",
            str(tgss_evidence),
            "--date",
            "2026-07-15",
            "--amount-eur",
            "370.59",
            "--deductible-eur",
            "370.59",
            "--reference",
            "RETA-2026-07",
            "--archive-root",
            str(archive_root),
        ]
    ) == 0
    tgss = json.loads(capsys.readouterr().out)
    assert main(
        [
            "review",
            "post",
            "--db",
            str(database),
            tgss["review_id"],
            "--expected-row-version",
            str(tgss["transaction_row_version"]),
        ]
    ) == 0
    capsys.readouterr()

    tax_evidence = root / "modelo130-q2-debit.json"
    tax_evidence.write_text(
        json.dumps(
            {
                "paid_on": "2026-07-20",
                "amount": "2639.12",
                "reference": "Modelo 130 2026-Q2",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    assert main(
        [
            "bank",
            "record-payment",
            "--db",
            str(database),
            "--obligation-id",
            obligation["obligation_id"],
            "--evidence",
            str(tax_evidence),
            "--paid-on",
            "2026-07-20",
            "--amount",
            "2639.12",
            "--source-system",
            "bank_debit_evidence",
            "--external-id",
            "q2-modelo130-debit",
            "--reference",
            "Modelo 130 2026-Q2",
            "--counterparty-name",
            "AEAT",
            "--archive-root",
            str(archive_root),
        ]
    ) == 0
    capsys.readouterr()

    manifest_dir = root / "offboarding-evidence"
    manifest_dir.mkdir()
    artifact_paths = (
        Path("raw_exports") / "2026-07-21" / "dataexport.zip",
        Path("libro_registro_gastos_2026.xlsx"),
        Path("modelo_130_2t_2026.pdf"),
        Path("nrc_justificante_2t_2026.pdf"),
        Path("invoice_channel_evidence_email.txt"),
    )
    artifacts: list[Path] = []
    for index, relative_path in enumerate(artifact_paths, start=1):
        artifact = manifest_dir / relative_path
        artifact.parent.mkdir(parents=True, exist_ok=True)
        if artifact.suffix.casefold() == ".pdf":
            writer = PdfWriter()
            writer.add_blank_page(width=72, height=72)
            with artifact.open("wb") as handle:
                writer.write(handle)
        else:
            artifact.write_text(f"offboarding artifact {index}", encoding="utf-8")
        artifacts.append(artifact)
    with initialize(database) as db:
        db.create_filing_snapshot(
            "2026-Q2",
            payload={"form": "130", "filed_values": {"19": "2639.12"}},
            filed_on="2026-07-08",
            status="baseline",
            form_code="130",
            source_hash=hashlib.sha256(artifacts[2].read_bytes()).hexdigest(),
            source_reference=str(artifacts[2]),
        )
    manifest = root / "offboarding-manifest.json"
    with open_ledger_db(database, read_only=True) as db:
        manifest_document = build_offboarding_manifest_document(
            artifacts,
            generated_on=date(2026, 7, 21),
            database=db,
        )
    manifest.write_text(
        json.dumps(manifest_document, indent=2),
        encoding="utf-8",
    )
    return CutoverFixture(
        database=database,
        manifest=manifest,
        output_dir=root / "shadow-close",
        supplier_transaction_id=supplier_transaction["transaction_id"],
        tgss_transaction_id=tgss["transaction_id"],
        manifest_artifact=artifacts[0],
    )


def _break_gate(fixture: CutoverFixture, broken_gate: str | None) -> None:
    if broken_gate is None:
        return
    if broken_gate == "archive":
        fixture.manifest_artifact.write_text("mutated after manifest", encoding="utf-8")
        return
    if broken_gate == "stale_manifest":
        payload = json.loads(fixture.manifest.read_text(encoding="utf-8"))
        payload["generated_on"] = "2026-07-20"
        fixture.manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return
    with initialize(fixture.database) as db:
        if broken_gate == "accounting_data":
            db.add_validation_issue(
                period_key="2026-Q3",
                issue_code="cutover-fixture-review",
                severity="error",
                message="Synthetic unresolved accounting decision",
                blocking=True,
                source_hash="cutover-fixture-review-source",
            )
        elif broken_gate == "posting":
            _add_mapping_fixture(db, key="approved-pending", lifecycle_status="approved")
        elif broken_gate == "aeat_books":
            _add_mapping_fixture(
                db,
                key="posted-unmapped",
                lifecycle_status="posted",
                aeat_expense_concept=None,
            )
        elif broken_gate == "operational_proof":
            db.connection.execute(
                "UPDATE transactions SET lifecycle_status = 'rejected' WHERE transaction_id = ?",
                (fixture.supplier_transaction_id,),
            )
            db.connection.execute(
                "UPDATE transactions SET lifecycle_status = 'rejected' WHERE transaction_id = ?",
                (fixture.tgss_transaction_id,),
            )
            db.connection.commit()
        elif broken_gate == "tax_settlement":
            db.connection.execute("DELETE FROM payments")
            db.connection.commit()
        else:
            raise AssertionError(f"Unknown broken gate: {broken_gate}")


def _add_mapping_fixture(
    db,
    *,
    key: str,
    lifecycle_status: str,
    aeat_expense_concept: str | None = "G03",
) -> None:
    activity = db.list_business_activities()[0]
    counterparty = db.upsert_counterparty(
        external_key=f"counterparty-{key}",
        tax_id="B87654321",
        display_name=f"Supplier {key} SL",
        country_code="ES",
    )
    document = db.upsert_document(
        external_key=f"document-{key}",
        counterparty_id=counterparty["counterparty_id"],
        document_type="expense_invoice",
        document_number=f"INV-{key}",
        issued_on="2026-07-18",
        period_key="2026-Q3",
        total_minor=1210,
        lifecycle_status="approved",
        source_hash=f"document-source-{key}",
    )
    transaction = db.add_transaction(
        external_key=f"transaction-{key}",
        period_key="2026-Q3",
        transaction_date="2026-07-18",
        booking_date="2026-07-18",
        entry_type="expense",
        description=f"Mapping fixture {key}",
        amount_minor=1210,
        lifecycle_status=lifecycle_status,
        document_id=document["document_id"],
        counterparty_id=counterparty["counterparty_id"],
        business_activity_id=activity["business_activity_id"],
        source_hash=f"transaction-source-{key}",
    )
    db.add_detailed_tax_treatment(
        transaction_id=transaction["transaction_id"],
        treatment_type="expense",
        tax_code="domestic_input",
        aeat_invoice_type="F1",
        aeat_operation_key="01",
        aeat_reverse_charge=False,
        aeat_expense_concept=aeat_expense_concept,
        rate_basis_points=2100,
        taxable_base_minor=1000,
        vat_minor=210,
        deductible_irpf_minor=1000,
        deductible_vat_minor=210,
        withholding_minor=0,
        include_modelo130=True,
        include_modelo303=True,
    )


def _run_shadow_close(
    fixture: CutoverFixture,
    capsys: pytest.CaptureFixture[str],
) -> dict[str, object]:
    assert main(
        [
            "period",
            "shadow-close",
            "--db",
            str(fixture.database),
            "2026-Q3",
            "--as-of",
            "2026-07-21",
            "--operational-proof-since",
            "2026-07-01",
            "--required-settled-obligation",
            "2026-Q2:130",
            "--offboarding-manifest",
            str(fixture.manifest),
            "--out-dir",
            str(fixture.output_dir),
        ]
    ) == 0
    capsys.readouterr()
    return json.loads(
        (fixture.output_dir / "shadow-close.json").read_text(encoding="utf-8")
    )
