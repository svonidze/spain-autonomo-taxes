from __future__ import annotations

from decimal import Decimal

import pytest

from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.tax_engine import CalculationBlocked
from autonomo_taxes.tax_row_loader import load_tax_rows


def _add_treatment(db, transaction_id: str, *, deductible_minor: int = 0) -> None:
    db.add_detailed_tax_treatment(
        transaction_id=transaction_id,
        treatment_type="accounting",
        tax_code="outside_scope",
        taxable_base_minor=deductible_minor,
        deductible_irpf_minor=deductible_minor,
        include_modelo130=True,
    )


def test_loader_only_includes_approved_rows_for_explicit_forecast_period(tmp_path) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        posted = db.add_transaction(
            external_key="posted",
            period_key="2026-Q3",
            transaction_date="2026-07-01",
            booking_date="2026-07-01",
            entry_type="income",
            description="Posted",
            amount_minor=10000,
            currency="EUR",
            lifecycle_status="posted",
        )
        approved = db.add_transaction(
            external_key="approved",
            period_key="2026-Q3",
            transaction_date="2026-07-02",
            booking_date="2026-07-02",
            entry_type="expense",
            description="Approved",
            amount_minor=2000,
            currency="EUR",
            lifecycle_status="approved",
        )
        _add_treatment(db, posted["transaction_id"])
        _add_treatment(db, approved["transaction_id"], deductible_minor=2000)

        actual = load_tax_rows(db, 2026)
        projected = load_tax_rows(db, 2026, include_approved_periods={"2026-Q3"})

    assert [row.transaction_id for row in actual] == [posted["transaction_id"]]
    assert {row.transaction_id for row in projected} == {
        posted["transaction_id"],
        approved["transaction_id"],
    }
    assert next(row for row in projected if row.kind == "expense").deductible_irpf_eur == Decimal("20.00")


def test_loader_never_treats_foreign_minor_units_as_eur(tmp_path) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        transaction = db.add_transaction(
            external_key="usd-without-fx",
            period_key="2026-Q3",
            transaction_date="2026-07-01",
            booking_date="2026-07-01",
            entry_type="income",
            description="USD without FX",
            amount_minor=10000,
            currency="USD",
            amount_original_minor=10000,
            original_currency="USD",
            lifecycle_status="posted",
        )
        _add_treatment(db, transaction["transaction_id"])

        with pytest.raises(CalculationBlocked, match="missing an explicit EUR amount for USD"):
            load_tax_rows(db, 2026)


def test_historical_amortization_links_only_one_asset_by_supplier_and_invoice_date(
    tmp_path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        counterparty = db.upsert_counterparty(
            external_key="apple-spain",
            display_name="Synthetic Party 016",
        )
        source_document = db.upsert_document(
            external_key="q3-macbook-amortization",
            counterparty_id=counterparty["counterparty_id"],
            document_type="gastos_book",
            document_number="SYNTH-DOCUMENT-031-amortization",
            issued_on="2026-04-08",
            period_key="2026-Q3",
            lifecycle_status="approved",
        )
        transaction = db.add_transaction(
            external_key="q3-macbook-amortization",
            period_key="2026-Q3",
            transaction_date="2026-09-30",
            booking_date="2026-09-30",
            entry_type="expense",
            description="MacBook quarterly amortization",
            amount_minor=10712,
            lifecycle_status="approved",
            document_id=source_document["document_id"],
            counterparty_id=counterparty["counterparty_id"],
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="accounting",
            tax_code="historical_g03",
            taxable_base_minor=10712,
            deductible_irpf_minor=10712,
            include_modelo130=True,
        )
        asset_document = db.upsert_document(
            external_key="macbook-annual-evidence",
            counterparty_id=counterparty["counterparty_id"],
            document_type="xolo_annual_asset_evidence",
            document_number="MacBook Pro 14",
            issued_on="2026-04-08",
            lifecycle_status="approved",
        )
        asset = db.add_asset(
            asset_code="MACBOOK-2026",
            cost_minor=199400,
            currency="EUR",
            depreciation_method="straight_line",
            source_hash="macbook-asset",
            document_id=asset_document["document_id"],
        )

        unique = next(
            row
            for row in db.list_tax_rows(year=2026)
            if row["transaction_id"] == transaction["transaction_id"]
        )
        assert unique["asset_id"] == asset["asset_id"]
        assert unique["asset_count"] == 1

        second_document = db.upsert_document(
            external_key="ambiguous-annual-evidence",
            counterparty_id=counterparty["counterparty_id"],
            document_type="xolo_annual_asset_evidence",
            document_number="Second asset same day",
            issued_on="2026-04-08",
            lifecycle_status="approved",
        )
        db.add_asset(
            asset_code="SECOND-ASSET-2026",
            cost_minor=10000,
            currency="EUR",
            depreciation_method="straight_line",
            source_hash="second-asset",
            document_id=second_document["document_id"],
        )

        ambiguous = next(
            row
            for row in db.list_tax_rows(year=2026)
            if row["transaction_id"] == transaction["transaction_id"]
        )
        assert ambiguous["asset_id"] is None
        assert ambiguous["asset_count"] == 2
        with pytest.raises(CalculationBlocked, match="linked to multiple assets"):
            load_tax_rows(db, 2026, include_approved_periods={"2026-Q3"})


def test_direct_asset_link_takes_precedence_over_inferred_candidate(tmp_path) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        counterparty = db.upsert_counterparty(
            external_key="apple-spain",
            display_name="Synthetic Party 016",
        )
        source_document = db.upsert_document(
            external_key="amortization-source",
            counterparty_id=counterparty["counterparty_id"],
            document_type="gastos_book",
            document_number="SYNTH-DOCUMENT-031-amortization",
            issued_on="2026-04-08",
            period_key="2026-Q3",
            lifecycle_status="approved",
        )
        transaction = db.add_transaction(
            external_key="amortization-transaction",
            period_key="2026-Q3",
            transaction_date="2026-09-30",
            booking_date="2026-09-30",
            entry_type="expense",
            description="Quarterly amortization",
            amount_minor=10712,
            lifecycle_status="approved",
            document_id=source_document["document_id"],
            counterparty_id=counterparty["counterparty_id"],
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="accounting",
            tax_code="historical_g03",
            taxable_base_minor=10712,
            deductible_irpf_minor=10712,
            include_modelo130=True,
        )
        inferred_document = db.upsert_document(
            external_key="inferred-asset-evidence",
            counterparty_id=counterparty["counterparty_id"],
            document_type="xolo_annual_asset_evidence",
            document_number="Inferred asset",
            issued_on="2026-04-08",
            lifecycle_status="approved",
        )
        db.add_asset(
            asset_code="INFERRED-ASSET",
            cost_minor=10000,
            currency="EUR",
            depreciation_method="straight_line",
            source_hash="inferred-asset",
            document_id=inferred_document["document_id"],
        )
        direct_document = db.upsert_document(
            external_key="direct-asset-evidence",
            counterparty_id=counterparty["counterparty_id"],
            document_type="xolo_annual_asset_evidence",
            document_number="Direct asset",
            issued_on="2026-04-07",
            lifecycle_status="approved",
        )
        direct_asset = db.add_asset(
            asset_code="DIRECT-ASSET",
            cost_minor=199400,
            currency="EUR",
            depreciation_method="straight_line",
            source_hash="direct-asset",
            document_id=direct_document["document_id"],
            acquisition_transaction_id=transaction["transaction_id"],
        )

        row = next(
            value
            for value in db.list_tax_rows(year=2026)
            if value["transaction_id"] == transaction["transaction_id"]
        )

    assert row["asset_id"] == direct_asset["asset_id"]
    assert row["asset_count"] == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_loader_merges_inferred_asset_from_historical_treatment_regardless_of_order(
    reverse: bool,
) -> None:
    common = {
        "transaction_id": "amortization-transaction",
        "period_key": "2026-Q3",
        "transaction_date": "2026-09-30",
        "entry_type": "expense",
        "amount_minor": 10712,
        "amount_eur_minor": 10712,
        "currency": "EUR",
        "original_currency": "EUR",
        "lifecycle_status": "approved",
        "counterparty_id": "apple",
        "counterparty_name": "Synthetic Party 016",
        "country_code": "ES",
        "taxable_base_minor": 10712,
        "vat_minor": 0,
        "deductible_irpf_minor": 10712,
        "deductible_vat_minor": 0,
        "withholding_minor": 0,
        "include_modelo130": 1,
        "include_modelo303": 0,
        "include_modelo347": 0,
    }

    class OrderedTreatments:
        def list_tax_rows(self, *, year):
            assert year == 2026
            rows = [
                {**common, "tax_code": "unknown", "asset_id": None, "asset_count": 0},
                {
                    **common,
                    "tax_code": "historical_g03",
                    "asset_id": "macbook-asset",
                    "asset_count": 1,
                },
            ]
            return list(reversed(rows)) if reverse else rows

    rows = load_tax_rows(
        OrderedTreatments(),
        2026,
        include_approved_periods={"2026-Q3"},
    )

    assert len(rows) == 1
    assert rows[0].asset_id == "macbook-asset"
    assert rows[0].tax_code == "historical_g03"
