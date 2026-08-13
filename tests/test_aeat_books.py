from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import tempfile

import pytest

from autonomo_taxes.aeat_books import (
    AeatBookProjectionError,
    build_aeat_book_projection,
    write_aeat_book_projection,
)
from autonomo_taxes.aeat_workbook import build_aeat_workbook_payload
from autonomo_taxes.ledger_db import initialize


def test_foreign_outside_scope_income_projects_to_cumulative_aeat_row() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with initialize(root / "ledger.sqlite") as db:
            activity = _profile_and_activity(db)
            customer = db.upsert_counterparty(
                external_key="customer-us",
                tax_id="TEST-TAX-ID-007",
                display_name="Foreign Customer",
                country_code="US",
            )
            db.upsert_counterparty_identity(
                counterparty_id=customer["counterparty_id"],
                identity_kind="official_id",
                country_code="US",
                identifier="TEST-TAX-ID-007",
                source_reference="Issued customer invoice evidence",
                source_hash="customer-identity-evidence-hash",
            )
            prior_document = db.upsert_document(
                external_key="income-document-q1",
                counterparty_id=customer["counterparty_id"],
                document_type="income_invoice",
                document_number="FACT-2026-001",
                issued_on="2026-03-31",
                period_key="2026-Q1",
                currency="EUR",
                total_minor=10000,
                lifecycle_status="approved",
                source_hash="income-document-q1-hash",
            )
            prior_transaction = db.add_transaction(
                external_key="income:FACT-2026-001",
                period_key="2026-Q1",
                transaction_date="2026-03-31",
                booking_date="2026-03-31",
                entry_type="income",
                description="Prior-quarter software development",
                amount_minor=10000,
                direction="credit",
                lifecycle_status="posted",
                document_id=prior_document["document_id"],
                counterparty_id=customer["counterparty_id"],
                business_activity_id=activity["business_activity_id"],
                source_hash="income-document-q1-hash",
            )
            db.add_detailed_tax_treatment(
                transaction_id=prior_transaction["transaction_id"],
                treatment_type="income",
                tax_code="outside_scope",
                aeat_invoice_type="F1",
                aeat_operation_key="01",
                aeat_operation_qualification="N2",
                rate_basis_points=0,
                taxable_base_minor=10000,
                vat_minor=0,
                deductible_irpf_minor=0,
                deductible_vat_minor=0,
                withholding_minor=0,
                include_modelo130=True,
                include_modelo303=True,
            )
            document = db.upsert_document(
                external_key="income-document",
                counterparty_id=customer["counterparty_id"],
                document_type="income_invoice",
                document_number="FACT-2026-002",
                issued_on="2026-07-01",
                period_key="2026-Q3",
                currency="USD",
                total_minor=659580,
                lifecycle_status="approved",
                source_hash="income-document-hash",
            )
            fx = db.add_fx_rate(
                rate_date="2026-07-01",
                base_currency="USD",
                quote_currency="EUR",
                rate="0.878502",
                rate_source="Banco de Espana",
                source_hash="official-fx-hash",
            )
            transaction = db.add_transaction(
                external_key="income:FACT-2026-002",
                period_key="2026-Q3",
                transaction_date="2026-07-01",
                booking_date="2026-07-01",
                entry_type="income",
                description="Software development",
                amount_minor=659580,
                currency="USD",
                amount_original_minor=659580,
                original_currency="USD",
                amount_eur_minor=579443,
                fx_rate_id=fx["fx_rate_id"],
                direction="credit",
                lifecycle_status="posted",
                document_id=document["document_id"],
                counterparty_id=customer["counterparty_id"],
                business_activity_id=activity["business_activity_id"],
                source_hash="income-document-hash",
            )
            db.add_detailed_tax_treatment(
                transaction_id=transaction["transaction_id"],
                treatment_type="income",
                tax_code="outside_scope",
                aeat_invoice_type="F1",
                aeat_operation_key="01",
                aeat_operation_qualification="N2",
                rate_basis_points=0,
                taxable_base_minor=579443,
                vat_minor=0,
                deductible_irpf_minor=0,
                deductible_vat_minor=0,
                withholding_minor=0,
                include_modelo130=True,
                include_modelo303=True,
            )
            db.add_transaction(
                external_key="approved-forecast",
                period_key="2026-Q3",
                transaction_date="2026-09-30",
                booking_date="2026-09-30",
                entry_type="expense",
                description="Forecast only",
                amount_minor=1000,
                lifecycle_status="approved",
                business_activity_id=activity["business_activity_id"],
            )

            projection = build_aeat_book_projection(db, period_key="2026-Q3")
            output = write_aeat_book_projection(root / "projection.json", projection)

        assert output.exists()
        assert projection["counts"]["income"] == 2
        assert projection["counts"]["expense"] == 0
        assert projection["data_projection_ready"] is False
        assert [row["code"] for row in projection["blockers"]] == [
            "approved_not_posted"
        ]
        assert projection["income_rows"][0]["autoliquidacion_periodo"] == "1T"
        row = projection["income_rows"][1]
        assert row["autoliquidacion_periodo"] == "3T"
        assert row["actividad_codigo"] == "A"
        assert row["actividad_tipo"] == "05"
        assert row["iae_grupo_epigrafe"] == "763"
        assert row["concepto_ingreso"] == "I01"
        assert row["calificacion_operacion"] == "N2"
        assert row["destinatario_id_tipo"] == "04"
        assert row["destinatario_pais"] == "US"
        assert row["destinatario_identificacion"] == "TEST-TAX-ID-007"
        assert row["factura_serie"] == "FACT-2026"
        assert row["factura_numero"] == "002"
        assert row["total_factura_eur"] == "5794.43"
        assert row["base_imponible_eur"] == "5794.43"


def test_reverse_charge_keeps_expense_concept_and_uses_primary_identity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            activity = _profile_and_activity(db)
            supplier = db.upsert_counterparty(
                external_key="supplier-us",
                tax_id="7804391",
                display_name="Foreign Supplier",
                country_code="US",
            )
            db.upsert_counterparty_identity(
                counterparty_id=supplier["counterparty_id"],
                identity_kind="official_id",
                country_code="US",
                identifier="TEST-TAX-ID-003",
                source_reference="Government registry extract",
                source_hash="government-registry-extract-hash",
            )
            document = db.upsert_document(
                counterparty_id=supplier["counterparty_id"],
                document_type="expense_invoice",
                document_number="SUP-100",
                issued_on="2026-04-01",
                period_key="2026-Q2",
                total_minor=10000,
                lifecycle_status="approved",
                source_hash="expense-document-hash",
            )
            transaction = db.add_transaction(
                period_key="2026-Q2",
                transaction_date="2026-04-01",
                booking_date="2026-04-02",
                entry_type="expense",
                description="Professional service",
                amount_minor=10000,
                amount_eur_minor=10000,
                lifecycle_status="posted",
                document_id=document["document_id"],
                counterparty_id=supplier["counterparty_id"],
                business_activity_id=activity["business_activity_id"],
            )
            db.add_detailed_tax_treatment(
                transaction_id=transaction["transaction_id"],
                treatment_type="expense",
                tax_code="non_eu_service_expense",
                aeat_invoice_type="F1",
                aeat_operation_key="01",
                aeat_reverse_charge=True,
                aeat_expense_concept="G19",
                rate_basis_points=2100,
                taxable_base_minor=10000,
                vat_minor=2100,
                deductible_irpf_minor=10000,
                deductible_vat_minor=2100,
                include_modelo130=True,
                include_modelo303=True,
                notes="reason_code=G19",
            )

            projection = build_aeat_book_projection(db, period_key="2026-Q2")

        assert projection["data_projection_ready"] is True
        row = projection["expense_rows"][0]
        assert row["concepto_gasto"] == "G19"
        assert row["clave_operacion"] == "01"
        assert row["bien_inversion"] == "N"
        assert row["inversion_sujeto_pasivo"] == "S"
        assert row["tipo_iva_percent"] == "21.00"
        assert row["gasto_deducible_eur"] == "100.00"
        assert row["total_factura_eur"] == "121.00"
        assert row["base_imponible_eur"] == "100.00"
        assert row["cuota_iva_soportado_eur"] == "21.00"
        assert row["expedidor_id_tipo"] == "04"
        assert row["expedidor_pais"] == "US"
        assert row["expedidor_identificacion"] == "TEST-TAX-ID-003"
        payload = build_aeat_workbook_payload(
            projection,
            template_check={
                "valid": True,
                "actual_sha256": "a" * 64,
                "expected_sha256": "a" * 64,
                "errors": [],
            },
        )
        expense_cells = payload["sheets"]["RECIBIDAS_GASTOS"]["rows"][0]
        assert payload["payload_ready"] is True
        assert expense_cells[5:8] == ["F1", "G19", "100.00"]
        assert expense_cells[19:22] == ["01", "N", "S"]


def test_reverse_charge_rejects_irpf_deduction_above_reviewed_capacity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            activity = _profile_and_activity(db)
            supplier = db.upsert_counterparty(
                external_key="supplier-us-overallocated",
                tax_id="TEST-TAX-ID-003",
                display_name="Foreign Supplier",
                country_code="US",
            )
            db.upsert_counterparty_identity(
                counterparty_id=supplier["counterparty_id"],
                identity_kind="official_id",
                country_code="US",
                identifier="TEST-TAX-ID-003",
                source_reference="Government registry extract",
                source_hash="overallocated-government-registry-hash",
            )
            document = db.upsert_document(
                counterparty_id=supplier["counterparty_id"],
                document_type="expense_invoice",
                document_number="SUP-OVERALLOCATED",
                issued_on="2026-04-01",
                period_key="2026-Q2",
                total_minor=10000,
                lifecycle_status="approved",
                source_hash="expense-overallocated-document-hash",
            )
            transaction = db.add_transaction(
                period_key="2026-Q2",
                transaction_date="2026-04-01",
                booking_date="2026-04-02",
                entry_type="expense",
                description="Professional service",
                amount_minor=10000,
                lifecycle_status="posted",
                document_id=document["document_id"],
                counterparty_id=supplier["counterparty_id"],
                business_activity_id=activity["business_activity_id"],
            )
            db.add_detailed_tax_treatment(
                transaction_id=transaction["transaction_id"],
                treatment_type="expense",
                tax_code="non_eu_service_expense",
                aeat_invoice_type="F1",
                aeat_operation_key="01",
                aeat_reverse_charge=True,
                aeat_expense_concept="G19",
                rate_basis_points=2100,
                taxable_base_minor=10000,
                vat_minor=2100,
                deductible_irpf_minor=999999,
                deductible_vat_minor=2100,
                include_modelo130=True,
                include_modelo303=True,
            )

            projection = build_aeat_book_projection(db, period_key="2026-Q2")

        assert projection["data_projection_ready"] is False
        assert projection["expense_rows"] == []
        assert projection["blockers"][0]["code"] == "row_mapping_incomplete"
        assert "exceeds" in projection["blockers"][0]["message"]


def test_non_deductible_vat_is_included_in_deductible_irpf_expense() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            activity = _profile_and_activity(db)
            supplier = db.upsert_counterparty(
                external_key="openai-ireland",
                tax_id="IE1234567AB",
                display_name="Synthetic Party 010",
                country_code="IE",
            )
            db.upsert_counterparty_identity(
                counterparty_id=supplier["counterparty_id"],
                identity_kind="vat_id",
                country_code="IE",
                identifier="IE1234567AB",
                source_reference="Reviewed supplier invoice",
                source_hash="openai-identity-evidence-hash",
            )
            document = db.upsert_document(
                counterparty_id=supplier["counterparty_id"],
                document_type="expense_invoice",
                document_number="SYNTH-DOCUMENT-012",
                issued_on="2026-07-16",
                period_key="2026-Q3",
                total_minor=22900,
                lifecycle_status="approved",
                source_hash="openai-2qfsqplo-0005-document-hash",
            )
            transaction = db.add_transaction(
                period_key="2026-Q3",
                transaction_date="2026-07-16",
                booking_date="2026-07-16",
                entry_type="expense",
                description="OpenAI subscription",
                amount_minor=22900,
                lifecycle_status="posted",
                document_id=document["document_id"],
                counterparty_id=supplier["counterparty_id"],
                business_activity_id=activity["business_activity_id"],
            )
            db.add_detailed_tax_treatment(
                transaction_id=transaction["transaction_id"],
                treatment_type="expense",
                tax_code="domestic_input",
                aeat_invoice_type="F1",
                aeat_operation_key="01",
                aeat_reverse_charge=False,
                aeat_expense_concept="G19",
                rate_basis_points=2100,
                taxable_base_minor=18926,
                vat_minor=3974,
                deductible_irpf_minor=22900,
                deductible_vat_minor=0,
                include_modelo130=True,
                include_modelo303=False,
            )

            projection = build_aeat_book_projection(db, period_key="2026-Q3")

        assert projection["data_projection_ready"] is True
        assert projection["counts"]["expense"] == 1
        assert projection["blockers"] == []
        row = projection["expense_rows"][0]
        assert row["factura_expedidor_serie_numero"] == "SYNTH-DOCUMENT-012"
        assert row["book_component"] == "taxable"
        assert row["total_factura_eur"] == "229.00"
        assert row["base_imponible_eur"] == "189.26"
        assert row["tipo_iva_percent"] == "21.00"
        assert row["cuota_iva_soportado_eur"] == "39.74"
        assert row["cuota_deducible_eur"] == "0.00"
        assert row["gasto_deducible_eur"] == "229.00"


def test_projection_fails_closed_without_taxpayer_or_transaction_activity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            with pytest.raises(AeatBookProjectionError, match="taxpayer profile"):
                build_aeat_book_projection(db, period_key="2026-Q3")

            profile = db.upsert_taxpayer_profile(
                tax_id="X0000000A",
                full_name="Example Taxpayer",
                source_hash="profile-source",
            )
            db.upsert_business_activity(
                taxpayer_profile_id=profile["taxpayer_profile_id"],
                activity_key="first",
                aeat_activity_code="A",
                aeat_activity_type="05",
                iae_section="2",
                iae_group_epigraph="763",
                description="First activity",
                starts_on="2023-01-01",
                source_reference="Reviewed source",
            )
            db.upsert_business_activity(
                taxpayer_profile_id=profile["taxpayer_profile_id"],
                activity_key="second",
                aeat_activity_code="A",
                aeat_activity_type="05",
                iae_section="2",
                iae_group_epigraph="769",
                description="Second activity",
                starts_on="2023-01-01",
                source_reference="Reviewed source",
            )
            customer = db.upsert_counterparty(
                tax_id="B00000000",
                display_name="Domestic Customer",
                country_code="ES",
            )
            document = db.upsert_document(
                counterparty_id=customer["counterparty_id"],
                document_type="income_invoice",
                document_number="F-1",
                issued_on="2026-07-01",
                period_key="2026-Q3",
                total_minor=10000,
                lifecycle_status="approved",
                source_hash="domestic-document",
            )
            transaction = db.add_transaction(
                period_key="2026-Q3",
                transaction_date="2026-07-01",
                booking_date="2026-07-01",
                entry_type="income",
                description="Unassigned activity",
                amount_minor=10000,
                lifecycle_status="posted",
                document_id=document["document_id"],
                counterparty_id=customer["counterparty_id"],
            )
            db.add_detailed_tax_treatment(
                transaction_id=transaction["transaction_id"],
                treatment_type="income",
                tax_code="outside_scope",
                aeat_invoice_type="F1",
                aeat_operation_key="01",
                aeat_operation_qualification="N2",
                taxable_base_minor=10000,
                vat_minor=0,
                deductible_irpf_minor=0,
            )

            projection = build_aeat_book_projection(db, period_key="2026-Q3")

        assert projection["counts"]["income"] == 0
        assert projection["blockers"][0]["code"] == "row_mapping_incomplete"
        assert "not linked to a business activity" in projection["blockers"][0]["message"]


def test_projection_fails_closed_for_foreign_generic_tax_id_without_identity_evidence() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            activity = _profile_and_activity(db)
            supplier = db.upsert_counterparty(
                external_key="unreviewed-foreign-supplier",
                tax_id="12-3456789",
                display_name="Unreviewed Foreign Supplier",
                country_code="US",
            )
            document = db.upsert_document(
                counterparty_id=supplier["counterparty_id"],
                document_type="expense_invoice",
                document_number="UNREVIEWED-1",
                issued_on="2026-04-01",
                period_key="2026-Q2",
                total_minor=10000,
                lifecycle_status="approved",
                source_hash="unreviewed-foreign-document",
            )
            transaction = db.add_transaction(
                period_key="2026-Q2",
                transaction_date="2026-04-01",
                booking_date="2026-04-01",
                entry_type="expense",
                description="Foreign service",
                amount_minor=10000,
                lifecycle_status="posted",
                document_id=document["document_id"],
                counterparty_id=supplier["counterparty_id"],
                business_activity_id=activity["business_activity_id"],
            )
            db.add_detailed_tax_treatment(
                transaction_id=transaction["transaction_id"],
                treatment_type="expense",
                tax_code="aeat_g19",
                aeat_invoice_type="F1",
                aeat_operation_key="01",
                aeat_reverse_charge=False,
                aeat_expense_concept="G19",
                taxable_base_minor=10000,
                vat_minor=0,
                deductible_irpf_minor=10000,
                deductible_vat_minor=0,
            )

            projection = build_aeat_book_projection(db, period_key="2026-Q2")

        assert projection["counts"]["expense"] == 0
        assert projection["blockers"][0]["code"] == "row_mapping_incomplete"
        assert "source-backed AEAT identity" in projection["blockers"][0]["message"]


def test_projection_blocks_irpf_base_reused_as_incoherent_iva_base() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            activity = _profile_and_activity(db)
            supplier = db.upsert_counterparty(
                tax_id="TEST-TAX-ID-001",
                display_name="Example Professional",
                country_code="ES",
            )
            document = db.upsert_document(
                counterparty_id=supplier["counterparty_id"],
                document_type="expense_invoice",
                document_number="PRO-1",
                issued_on="2026-03-31",
                period_key="2026-Q1",
                total_minor=9908,
                lifecycle_status="approved",
                source_hash="professional-document-hash",
            )
            transaction = db.add_transaction(
                period_key="2026-Q1",
                transaction_date="2026-03-31",
                booking_date="2026-03-31",
                entry_type="expense",
                description="Professional service with suplido",
                amount_minor=9908,
                lifecycle_status="posted",
                document_id=document["document_id"],
                counterparty_id=supplier["counterparty_id"],
                business_activity_id=activity["business_activity_id"],
            )
            db.add_detailed_tax_treatment(
                transaction_id=transaction["transaction_id"],
                treatment_type="expense",
                tax_code="domestic_input",
                aeat_invoice_type="F1",
                aeat_operation_key="01",
                aeat_reverse_charge=False,
                aeat_expense_concept="G19",
                rate_basis_points=2100,
                taxable_base_minor=8267,
                vat_minor=1641,
                deductible_irpf_minor=8267,
                deductible_vat_minor=1641,
                include_modelo130=True,
                include_modelo303=True,
            )

            projection = build_aeat_book_projection(db, period_key="2026-Q1")

        assert projection["counts"]["expense"] == 0
        assert projection["blockers"][0]["code"] == "row_mapping_incomplete"
        assert "do not reconcile" in projection["blockers"][0]["message"]


def test_projection_preserves_suplidos_outside_iva_base() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            activity = _profile_and_activity(db)
            supplier = db.upsert_counterparty(
                tax_id="TEST-TAX-ID-001",
                display_name="Example Professional",
                country_code="ES",
            )
            document = db.upsert_document(
                counterparty_id=supplier["counterparty_id"],
                document_type="expense_invoice",
                document_number="PRO-2",
                issued_on="2026-03-31",
                period_key="2026-Q1",
                total_minor=9908,
                lifecycle_status="approved",
                source_hash="professional-document-with-suplido-hash",
            )
            transaction = db.add_transaction(
                period_key="2026-Q1",
                transaction_date="2026-03-31",
                booking_date="2026-03-31",
                entry_type="expense",
                description="Professional service with reviewed suplido",
                amount_minor=9908,
                lifecycle_status="posted",
                document_id=document["document_id"],
                counterparty_id=supplier["counterparty_id"],
                business_activity_id=activity["business_activity_id"],
            )
            db.add_detailed_tax_treatment(
                transaction_id=transaction["transaction_id"],
                treatment_type="expense",
                tax_code="domestic_input",
                aeat_invoice_type="F1",
                aeat_operation_key="01",
                aeat_reverse_charge=False,
                aeat_expense_concept="G19",
                rate_basis_points=2100,
                taxable_base_minor=7816,
                vat_minor=1641,
                deductible_irpf_minor=8267,
                deductible_vat_minor=1641,
                include_modelo130=True,
                include_modelo303=True,
            )

            projection = build_aeat_book_projection(db, period_key="2026-Q1")

        assert projection["data_projection_ready"] is True
        assert projection["counts"]["expense"] == 2
        taxable, non_taxable = projection["expense_rows"]
        assert taxable["book_component"] == "taxable"
        assert taxable["total_factura_eur"] == "94.57"
        assert taxable["base_imponible_eur"] == "78.16"
        assert taxable["tipo_iva_percent"] == "21.00"
        assert taxable["cuota_iva_soportado_eur"] == "16.41"
        assert taxable["cuota_deducible_eur"] == "16.41"
        assert taxable["gasto_deducible_eur"] == "78.16"
        assert non_taxable["book_component"] == "non_taxable"
        assert non_taxable["total_factura_eur"] == "4.51"
        assert non_taxable["base_imponible_eur"] == "4.51"
        assert non_taxable["tipo_iva_percent"] == ""
        assert non_taxable["cuota_iva_soportado_eur"] == ""
        assert non_taxable["gasto_deducible_eur"] == "4.51"
        assert sum(
            Decimal(row["total_factura_eur"])
            for row in projection["expense_rows"]
        ) == Decimal("99.08")
        assert sum(
            Decimal(row["gasto_deducible_eur"])
            for row in projection["expense_rows"]
        ) == Decimal("82.67")


def test_projection_blocks_net_withholding_mixed_with_non_taxable_residual() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            activity = _profile_and_activity(db)
            supplier = db.upsert_counterparty(
                tax_id="TEST-TAX-ID-001",
                display_name="Example Professional",
                country_code="ES",
            )
            document = db.upsert_document(
                counterparty_id=supplier["counterparty_id"],
                document_type="expense_invoice",
                document_number="PRO-3",
                issued_on="2026-03-31",
                period_key="2026-Q1",
                total_minor=9908,
                lifecycle_status="approved",
                source_hash="professional-document-net-withholding-hash",
            )
            transaction = db.add_transaction(
                period_key="2026-Q1",
                transaction_date="2026-03-31",
                booking_date="2026-03-31",
                entry_type="expense",
                description="Professional service with suplido and withholding",
                amount_minor=9608,
                lifecycle_status="posted",
                document_id=document["document_id"],
                counterparty_id=supplier["counterparty_id"],
                business_activity_id=activity["business_activity_id"],
            )
            db.add_detailed_tax_treatment(
                transaction_id=transaction["transaction_id"],
                treatment_type="expense",
                tax_code="domestic_input",
                aeat_invoice_type="F1",
                aeat_operation_key="01",
                aeat_reverse_charge=False,
                aeat_expense_concept="G19",
                rate_basis_points=2100,
                taxable_base_minor=7816,
                vat_minor=1641,
                deductible_irpf_minor=8267,
                deductible_vat_minor=1641,
                withholding_minor=300,
                include_modelo130=True,
                include_modelo303=True,
            )

            projection = build_aeat_book_projection(db, period_key="2026-Q1")

        assert projection["counts"]["expense"] == 0
        assert projection["blockers"][0]["code"] == "row_mapping_incomplete"
        assert "explicit reviewed gross allocation" in projection["blockers"][0]["message"]


def test_asset_book_row_reconciles_annual_evidence_to_amortizable_base() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with initialize(Path(tmp) / "ledger.sqlite") as db:
            activity = _profile_and_activity(db)
            supplier = db.upsert_counterparty(
                external_key="asset-supplier",
                tax_id="TEST-TAX-ID-006",
                display_name="Asset Supplier",
                country_code="ES",
            )
            document = db.upsert_document(
                counterparty_id=supplier["counterparty_id"],
                document_type="expense_invoice",
                document_number="2125047414",
                issued_on="2025-06-01",
                period_key="2025-Q2",
                total_minor=166500,
                lifecycle_status="approved",
                source_hash="asset-document-hash",
            )
            asset = db.add_asset(
                asset_code="PHONE-2025",
                document_id=document["document_id"],
                placed_in_service_on="2025-06-01",
                cost_minor=137603,
                amortizable_base_minor=137603,
                currency="EUR",
                depreciation_method="linear",
                annual_rate_basis_points=2600,
                source_hash="asset-source-hash",
            )
            asset = db.update_asset_book_profile(
                asset["asset_id"],
                business_activity_id=activity["business_activity_id"],
                aeat_asset_type="23",
                description="Business phone",
                aeat_asset_identifier="EPI",
                aeat_amortization_method="02",
                source_invoice_number="2125047414",
                acquisition_taxable_base_minor=137603,
                acquisition_vat_rate_basis_points=2100,
                acquisition_deductible_vat_minor=28897,
                iva_treatment="fully_deductible",
                business_use_ratio=1.0,
                book_profile_source_reference="Official 2026 asset book row 2",
                book_profile_source_hash="asset-book-hash",
                expected_row_version=asset["row_version"],
            )
            repeated = db.update_asset_book_profile(
                asset["asset_id"],
                business_activity_id=activity["business_activity_id"],
                aeat_asset_type="23",
                description="Business phone",
                aeat_asset_identifier="EPI",
                aeat_amortization_method="02",
                source_invoice_number="2125047414",
                acquisition_taxable_base_minor=137603,
                acquisition_vat_rate_basis_points=2100,
                acquisition_deductible_vat_minor=28897,
                iva_treatment="fully_deductible",
                business_use_ratio=1.0,
                book_profile_source_reference="Official 2026 asset book row 2",
                book_profile_source_hash="asset-book-hash",
                expected_row_version=asset["row_version"] - 1,
            )
            assert repeated["row_version"] == asset["row_version"]
            db.add_amortization_entry(
                asset_id=asset["asset_id"],
                period_key="2025",
                amount_minor=20837,
                entry_kind="annual_evidence",
                tax_year=2025,
                include_in_books=False,
                source_hash="asset-2025-evidence",
            )
            db.add_amortization_entry(
                asset_id=asset["asset_id"],
                period_key="2026",
                amount_minor=35776,
                entry_kind="annual_evidence",
                tax_year=2026,
                source_book_line_id="Official 2026 asset book row 2",
                include_in_books=False,
                source_hash="asset-2026-evidence",
            )

            projection = build_aeat_book_projection(db, period_key="2026-Q3")

        assert projection["data_projection_ready"] is True
        assert projection["counts"]["assets"] == 1
        row = projection["asset_rows"][0]
        assert row["tipo_bien"] == "23"
        assert row["autoliquidacion_periodo"] == "0A"
        assert row["amortizacion_acumulada_inicio_eur"] == "208.37"
        assert row["amortizacion_cuota_resultante_eur"] == "357.76"
        assert row["amortizacion_acumulada_final_eur"] == "566.13"
        assert row["amortizacion_pendiente_eur"] == "809.90"
        assert row["inicio_tipo_iva_percent"] == "21.00"
        assert row["inicio_prorrata_definitiva_percent"] == "100.00"
        assert row["inicio_cuota_deducible_eur"] == "288.97"


def _profile_and_activity(db):
    profile = db.upsert_taxpayer_profile(
        tax_id="X0000000A",
        full_name="Example Taxpayer",
        source_hash="modelo-036-hash",
    )
    return db.upsert_business_activity(
        taxpayer_profile_id=profile["taxpayer_profile_id"],
        activity_key="software-development",
        aeat_activity_code="A",
        aeat_activity_type="05",
        iae_section="2",
        iae_group_epigraph="763",
        description="Programmers and computer analysts",
        starts_on="2023-05-31",
        source_reference="Modelo 036",
        source_hash="activity-source-hash",
    )
