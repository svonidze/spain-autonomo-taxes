from autonomo_test_support.paths import FIXTURE_ROOT
from decimal import Decimal
from datetime import date
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.parsers import LedgerEntry
from autonomo_taxes.xolo_ledger import (
    XoloExpenseRow,
    import_xolo_expense_csv,
    load_xolo_expense_ledger,
    reconcile_xolo_expenses,
    rows_for_modelo130,
    xolo_ledger_total,
)


FIXTURE = FIXTURE_ROOT / "xolo_expenses.synthetic.csv"


class XoloLedgerTests(unittest.TestCase):
    def test_synthetic_fixture_totals_each_quarter(self):
        rows = load_xolo_expense_ledger(FIXTURE)

        self.assertEqual(xolo_ledger_total(rows, 2032, 1), Decimal("137.84"))
        self.assertEqual(xolo_ledger_total(rows, 2032, 2), Decimal("517.92"))

    def test_june_rows_are_included_in_direct_q2_match(self):
        rows = load_xolo_expense_ledger(FIXTURE)
        included_numbers = {row.number for row in rows_for_modelo130(rows, 2032, 2)}

        self.assertIn("SYN-2032-06-A", included_numbers)
        self.assertIn("SYN-2032-06-B", included_numbers)
        self.assertIn("SYN-2032-06-C", included_numbers)

    def test_asset_amortization_is_separate_from_asset_purchase(self):
        rows = load_xolo_expense_ledger(FIXTURE)
        by_number = {row.number: row for row in rows}

        self.assertFalse(by_number["SYN-ASSET-PURCHASE"].include_in_modelo130)
        self.assertEqual(by_number["SYN-ASSET-PURCHASE"].irpf_deductible_eur, Decimal("0.00"))
        self.assertEqual(by_number["SYN-AMORTIZATION-Q1"].irpf_deductible_eur, Decimal("12.34"))
        self.assertEqual(by_number["SYN-AMORTIZATION-Q2"].irpf_deductible_eur, Decimal("8.76"))

    def test_reconciliation_classifies_asset_schedule_and_post_filing_rows(self):
        rows = load_xolo_expense_ledger(FIXTURE)
        reconciliation = reconcile_xolo_expenses(rows, [], 2032, 2)
        by_number = {row["number"]: row["status"] for row in reconciliation}

        self.assertEqual(by_number["SYN-AMORTIZATION-Q1"], "asset_amortization_decision")
        self.assertEqual(by_number["SYN-AMORTIZATION-Q2"], "asset_amortization_decision")
        self.assertEqual(by_number["SYN-PENDING-REVIEW"], "deductibility_pending_confirmation")
        self.assertEqual(by_number["SYN-2032-06-A"], "missing_locally")
        self.assertEqual(by_number["SYN-2032-06-B"], "missing_locally")

    def test_reconciliation_uses_source_document_for_duplicate_same_day_amounts(self):
        row = self._xolo_row(
            number="SYN-MATCH-001",
            source_document_path="EXPENSE/SYNTHETIC_MATCH_02.pdf",
        )
        local = [
            self._local_entry("SYNTHETIC_MATCH_01.pdf"),
            self._local_entry("SYNTHETIC_MATCH_02.pdf"),
        ]

        reconciliation = reconcile_xolo_expenses([row], local, 2032, 2)

        self.assertEqual(reconciliation[0]["status"], "matched_local")
        self.assertEqual(reconciliation[0]["local_document"], "SYNTHETIC_MATCH_02.pdf")

    def test_reconciliation_does_not_guess_between_ambiguous_amount_only_matches(self):
        row = self._xolo_row(number="", source_document_path="")
        local = [
            self._local_entry("first.pdf", counterparty="Unknown"),
            self._local_entry("second.pdf", counterparty="Unknown"),
        ]

        reconciliation = reconcile_xolo_expenses([row], local, 2032, 2)

        self.assertEqual(reconciliation[0]["status"], "missing_locally")
        self.assertEqual(reconciliation[0]["local_document"], "")

    def test_import_raw_xolo_csv_creates_review_skeleton(self):
        content = (
            "xolo_url,xolo_id,recipient,type,number,date,amount_original,currency,subtotal_amount\n"
            "https://example.invalid/expense/SYN-RAW-1,SYN-RAW-1,Synthetic Supplier One,Professional expenses,SYN-N1,2032-01-02,121.00,EUR,100.00\n"
            "https://example.invalid/expense/SYN-RAW-2,SYN-RAW-2,Synthetic Supplier Two,Professional expenses,SYN-N2,2032-01-03,10.00,USD,10.00\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.csv"
            path.write_text(content, encoding="utf-8")

            rows = import_xolo_expense_csv(path, {"USD": Decimal("0.90")})

        self.assertEqual(rows[0]["gross_eur"], "121.00")
        self.assertEqual(rows[0]["vat_base_eur"], "100.00")
        self.assertEqual(rows[1]["gross_eur"], "9.00")
        self.assertEqual(rows[1]["include_in_modelo130"], "no")

    def _xolo_row(self, number: str, source_document_path: str) -> XoloExpenseRow:
        return XoloExpenseRow(
            xolo_url="",
            xolo_id="synthetic-row",
            recipient="Synthetic Service Authority",
            expense_type="Multiple",
            number=number,
            date=date(2032, 5, 10),
            amount_original=Decimal("78.90"),
            currency="EUR",
            gross_eur=Decimal("78.90"),
            vat_base_eur=None,
            irpf_deductible_eur=Decimal("78.90"),
            inclusion_quarter="2032-Q2",
            include_in_modelo130=True,
            source_document_path=source_document_path,
            confidence="xolo_ui",
            notes="",
        )

    def _local_entry(self, document: str, counterparty: str = "Synthetic Authority") -> LedgerEntry:
        return LedgerEntry(
            kind="expense",
            date=date(2032, 5, 10),
            document=document,
            counterparty=counterparty,
            description=document,
            amount_original=Decimal("78.90"),
            currency="EUR",
            amount_eur=Decimal("78.90"),
            deductible_eur=Decimal("78.90"),
            category="test",
            confidence="test",
            review_required=False,
            notes="",
        )


if __name__ == "__main__":
    unittest.main()
