from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.books import BookExportError, write_accounting_books
from autonomo_taxes.ledger_db import LedgerDB, initialize


class BookExportTests(unittest.TestCase):
    def test_writes_deterministic_period_books_with_separate_filed_and_recomputed_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self._database(root / "ledger.sqlite3") as db:
                self._seed_period_export(db)

                export = write_accounting_books(db, root / "exports", period_key="2026-Q2")

            income_rows = self._read_rows(export.files["income"])
            expense_rows = self._read_rows(export.files["expense"])
            asset_rows = self._read_rows(export.files["assets"])
            payment_rows = self._read_rows(export.files["payments"])

        self.assertEqual(export.row_counts, {"income": 1, "expense": 1, "assets": 1, "payments": 1})
        self.assertEqual(income_rows[0]["filed_taxable_base_minor"], "95000")
        self.assertEqual(income_rows[0]["recomputed_taxable_base_minor"], "100000")
        self.assertEqual(expense_rows[0]["filed_deductible_irpf_minor"], "3000")
        self.assertEqual(expense_rows[0]["recomputed_deductible_irpf_minor"], "5000")
        self.assertEqual(asset_rows[0]["filed_period_amortization_minor"], "6000")
        self.assertEqual(asset_rows[0]["recomputed_period_amortization_minor"], "604")
        self.assertEqual(payment_rows[0]["filed_amount_minor"], "12100")
        self.assertEqual(payment_rows[0]["recomputed_amount_minor"], "12000")

    def test_rejects_transactions_without_tax_treatments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self._database(root / "ledger.sqlite3") as db:
                db.add_transaction(
                    external_key="untreated-expense",
                    period_key="2026-Q2",
                    transaction_date="2026-04-03",
                    booking_date="2026-04-03",
                    entry_type="expense",
                    description="Missing treatment",
                    amount_minor=1000,
                    lifecycle_status="posted",
                )

                with self.assertRaises(BookExportError) as error:
                    write_accounting_books(db, root / "exports", period_key="2026-Q2")

        self.assertIn("untreated-expense", str(error.exception))

    def test_approved_forecast_rows_never_enter_official_books(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self._database(root / "ledger.sqlite3") as db:
                transaction = db.add_transaction(
                    external_key="approved-forecast",
                    period_key="2026-Q3",
                    transaction_date="2026-09-30",
                    booking_date="2026-09-30",
                    entry_type="expense",
                    description="Forecast amortization",
                    amount_minor=10000,
                    lifecycle_status="approved",
                )
                db.add_detailed_tax_treatment(
                    transaction_id=transaction["transaction_id"],
                    treatment_type="expense",
                    tax_code="historical_g03",
                    deductible_irpf_minor=2500,
                    include_modelo130=True,
                )

                export = write_accounting_books(db, root / "exports", period_key="2026-Q3")

            expense_rows = self._read_rows(export.files["expense"])

        self.assertEqual(expense_rows, [])
        self.assertEqual(export.row_counts["expense"], 0)

    def test_rejects_orphan_amortization_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self._database(root / "ledger.sqlite3") as db:
                asset = db.add_asset(
                    asset_code="ORPHAN-ASSET",
                    cost_minor=120000,
                    currency="EUR",
                    depreciation_method="linear",
                    useful_life_months=48,
                    amortizable_base_minor=120000,
                    source_hash="asset-orphan",
                )
                db.add_amortization_entry(
                    asset_id=asset["asset_id"],
                    period_key="2026-Q2",
                    amount_minor=5000,
                    source_hash="amort-orphan",
                )

                with self.assertRaises(BookExportError) as error:
                    write_accounting_books(db, root / "exports", period_key="2026-Q2")

        self.assertIn("ORPHAN-ASSET", str(error.exception))

    def test_prorates_first_quarter_from_placed_in_service_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self._database(root / "ledger.sqlite3") as db:
                transaction = db.add_transaction(
                    external_key="late-quarter-asset",
                    period_key="2026-Q2",
                    transaction_date="2026-06-30",
                    booking_date="2026-06-30",
                    entry_type="expense",
                    description="Asset placed in service on quarter end",
                    amount_minor=10000,
                    lifecycle_status="posted",
                )
                db.add_detailed_tax_treatment(
                    transaction_id=transaction["transaction_id"],
                    treatment_type="expense",
                    tax_code="capital_asset",
                    taxable_base_minor=10000,
                    deductible_irpf_minor=0,
                    include_modelo130=True,
                )
                db.add_asset(
                    asset_code="LATE-Q2-ASSET",
                    acquisition_transaction_id=transaction["transaction_id"],
                    placed_in_service_on="2026-06-30",
                    cost_minor=10000,
                    amortizable_base_minor=10000,
                    currency="EUR",
                    depreciation_method="linear",
                    useful_life_months=48,
                    source_hash="late-q2-asset",
                )

                export = write_accounting_books(db, root / "exports", period_key="2026-Q2")

            rows = self._read_rows(export.files["assets"])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["recomputed_period_amortization_minor"], "7")

    def _database(self, path: Path) -> LedgerDB:
        return initialize(path)

    def _seed_period_export(self, db: LedgerDB) -> None:
        counterparty = db.upsert_counterparty(
            external_key="customer-1",
            tax_id="ESA12345678",
            display_name="Consulting Client",
        )
        supplier = db.upsert_counterparty(
            external_key="supplier-1",
            tax_id="ESB12345678",
            display_name="Office Supplier",
        )
        income_doc = db.upsert_document(
            external_key="income-doc",
            counterparty_id=counterparty["counterparty_id"],
            document_type="invoice",
            document_number="INC-2026-001",
            issued_on="2026-04-02",
            period_key="2026-Q2",
            lifecycle_status="posted",
        )
        expense_doc = db.upsert_document(
            external_key="expense-doc",
            counterparty_id=supplier["counterparty_id"],
            document_type="invoice",
            document_number="EXP-2026-001",
            issued_on="2026-04-04",
            period_key="2026-Q2",
            lifecycle_status="posted",
        )
        income_rule = db.add_rule_version(
            rule_name="modelo130-income",
            version="2026.1",
            source_hash="rule-income",
        )
        expense_rule = db.add_rule_version(
            rule_name="modelo130-expense",
            version="2026.1",
            source_hash="rule-expense",
        )
        income_tx = db.add_transaction(
            external_key="income-1",
            period_key="2026-Q2",
            transaction_date="2026-04-02",
            booking_date="2026-04-02",
            entry_type="income",
            description="April consulting work",
            amount_minor=100000,
            amount_eur_minor=100000,
            lifecycle_status="posted",
            document_id=income_doc["document_id"],
            counterparty_id=counterparty["counterparty_id"],
        )
        expense_tx = db.add_transaction(
            external_key="expense-1",
            period_key="2026-Q2",
            transaction_date="2026-04-04",
            booking_date="2026-04-04",
            entry_type="expense",
            description="Laptop",
            amount_minor=12100,
            amount_eur_minor=12100,
            lifecycle_status="posted",
            document_id=expense_doc["document_id"],
            counterparty_id=supplier["counterparty_id"],
        )
        db.add_detailed_tax_treatment(
            transaction_id=income_tx["transaction_id"],
            treatment_type="income",
            tax_code="service_income",
            taxable_base_minor=95000,
            vat_minor=5000,
            include_modelo130=True,
            rule_version_id=income_rule["rule_version_id"],
        )
        db.add_detailed_tax_treatment(
            transaction_id=expense_tx["transaction_id"],
            treatment_type="expense",
            tax_code="deductible_expense",
            taxable_base_minor=10000,
            vat_minor=2100,
            deductible_ratio=0.5,
            deductible_irpf_minor=3000,
            deductible_vat_minor=900,
            include_modelo130=True,
            rule_version_id=expense_rule["rule_version_id"],
        )
        asset = db.add_asset(
            asset_code="LAPTOP-2026",
            acquisition_transaction_id=expense_tx["transaction_id"],
            document_id=expense_doc["document_id"],
            placed_in_service_on="2026-04-04",
            cost_minor=12100,
            amortizable_base_minor=10000,
            currency="EUR",
            depreciation_method="linear",
            useful_life_months=24,
            business_use_ratio=0.5,
            source_hash="asset-laptop",
        )
        db.add_amortization_entry(
            asset_id=asset["asset_id"],
            period_key="2026-Q2",
            amount_minor=6000,
            source_hash="amort-laptop-q2",
        )
        db.add_payment(
            transaction_id=expense_tx["transaction_id"],
            paid_on="2026-04-05",
            amount_minor=12100,
            currency="EUR",
            fee_minor=100,
            fee_currency="EUR",
            match_status="exact",
            source_hash="payment-expense-1",
        )

    def _read_rows(self, path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))


if __name__ == "__main__":
    unittest.main()
