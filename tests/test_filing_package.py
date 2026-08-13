from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.filing_package import verify_filing_package, write_filing_package
from autonomo_taxes.ledger_db import LedgerDB, initialize


class FilingPackageTests(unittest.TestCase):
    def test_writes_manifest_with_artifact_hashes_rule_versions_and_snapshot_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self._database(root / "ledger.sqlite3") as db:
                self._seed_period_export(db)
                manifest_path = write_filing_package(db, root / "package", period_key="2026-Q2")

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["manifest_version"], 2)
        self.assertEqual(manifest["package_type"], "ledgerdb_export_only")
        self.assertEqual(manifest["package_stage"], "submission_ready")
        self.assertTrue(manifest["submission_ready"])
        self.assertEqual(manifest["readiness_blockers"], [])
        self.assertEqual(manifest["period"]["period_key"], "2026-Q2")
        self.assertEqual([artifact["filename"] for artifact in manifest["artifacts"]], ["assets.csv", "expense.csv", "income.csv", "payments.csv"])
        self.assertEqual(len(manifest["rule_versions"]), 2)
        self.assertEqual([row["rule_name"] for row in manifest["rule_versions"]], ["modelo130-expense", "modelo130-income"])
        self.assertEqual(len(manifest["snapshot_refs"]), 1)
        self.assertTrue(all(len(artifact["sha256"]) == 64 for artifact in manifest["artifacts"]))

    def test_verifies_manifest_after_backup_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary_path = root / "primary.sqlite3"
            backup_path = root / "backup.sqlite3"
            restored_path = root / "restored.sqlite3"
            package_dir = root / "package"
            with self._database(primary_path) as primary:
                self._seed_period_export(primary)
                manifest_path = write_filing_package(primary, package_dir, period_key="2026-Q2")
                primary.backup_to(backup_path)

            with LedgerDB.initialize(restored_path) as restored:
                restored.restore_from(backup_path)
                verification = verify_filing_package(restored, manifest_path)

        self.assertTrue(verification["ok"])
        self.assertTrue(verification["manifest_matches"])
        self.assertEqual(verification["missing_artifacts"], [])
        self.assertEqual(verification["mismatched_artifacts"], [])

    def test_open_period_package_is_explicitly_draft_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self._database(root / "ledger.sqlite3") as db:
                db.ensure_period("2026-Q2")
                manifest_path = write_filing_package(db, root / "package", period_key="2026-Q2")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["package_stage"], "draft_review")
        self.assertFalse(manifest["submission_ready"])
        self.assertEqual(
            manifest["readiness_blockers"],
            ["period_not_closed", "final_filing_snapshot_missing"],
        )

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
        db.close_period("2026-Q2", expected_row_version=1)
        db.create_filing_snapshot(
            "2026-Q2",
            payload={"form": "modelo130", "mode": "export-only"},
            filed_on="2026-07-20T10:00:00+00:00",
            status="final",
            snapshot_hash="snapshot-q2-export-only",
        )


if __name__ == "__main__":
    unittest.main()
