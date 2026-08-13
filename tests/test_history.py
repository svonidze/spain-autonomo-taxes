from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.history import (
    RawXoloExpense,
    load_raw_xolo_expenses,
    _nearest_excluded_subset,
    _raw_quarter_totals,
    _raw_ytd_totals,
)


class HistoryAuditTests(unittest.TestCase):
    def test_usd_computer_hardware_is_treated_as_asset_candidate(self):
        rows = [
            RawXoloExpense(
                date=date(2023, 6, 15),
                recipient="Synthetic Party 001",
                expense_type="Computer hardware & software",
                number="2023-06-30",
                amount_original=Decimal("125.00"),
                currency="USD",
                subtotal_amount=Decimal("125.00"),
            ),
            RawXoloExpense(
                date=date(2023, 6, 30),
                recipient="TGSS",
                expense_type="Social security & prof. fees",
                number="177918930315",
                amount_original=Decimal("85.71"),
                currency="EUR",
                subtotal_amount=Decimal("85.71"),
            ),
        ]

        totals = _raw_ytd_totals(rows, 2023, 2, Decimal("0.90"))
        quarter = _raw_quarter_totals(rows, 2023, 2, Decimal("0.90"))

        self.assertEqual(totals["asset_gross_eur"], Decimal("112.50"))
        self.assertEqual(totals["non_asset_gross_eur"], Decimal("85.71"))
        self.assertEqual(quarter["asset_gross_eur"], Decimal("112.50"))
        self.assertEqual(quarter["non_asset_gross_eur"], Decimal("85.71"))
        self.assertGreater(totals["asset_amortization_estimate_gross"], Decimal("0.00"))

    def test_enriched_gross_eur_overrides_derived_fx(self):
        rows = [
            RawXoloExpense(
                date=date(2023, 6, 15),
                recipient="Synthetic Party 001",
                expense_type="Computer hardware & software",
                number="2023-06-30",
                amount_original=Decimal("125.00"),
                currency="USD",
                subtotal_amount=Decimal("125.00"),
                gross_eur=Decimal("115.54"),
                vat_base_eur=Decimal("115.54"),
            )
        ]

        quarter = _raw_quarter_totals(rows, 2023, 2, Decimal("0.90"))

        self.assertEqual(quarter["asset_gross_eur"], Decimal("115.54"))

    def test_untrusted_enriched_gross_eur_falls_back_to_derived_fx(self):
        rows = [
            RawXoloExpense(
                date=date(2023, 6, 15),
                recipient="Synthetic Party 001",
                expense_type="Computer hardware & software",
                number="2023-06-30",
                amount_original=Decimal("125.00"),
                currency="USD",
                subtotal_amount=Decimal("125.00"),
                gross_eur=Decimal("115.54"),
                vat_base_eur=Decimal("115.54"),
                detail_confidence="currency_mismatch_review",
            )
        ]

        quarter = _raw_quarter_totals(rows, 2023, 2, Decimal("0.90"))

        self.assertEqual(quarter["asset_gross_eur"], Decimal("112.50"))

    def test_enriched_vat_base_and_asset_flag_are_loaded_from_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "xolo.csv"
            path.write_text(
                "date,recipient,type,number,amount_original,currency,subtotal_amount,gross_eur,vat_base_eur,detail_confidence,is_depreciable_asset\n"
                "2023-06-30,GitHub,Professional expenses,2023-06-30,125.00,USD,125.00,115.54,115.54,detail_page_exchange_rate,yes\n",
                encoding="utf-8",
            )

            rows = load_raw_xolo_expenses(path)
            quarter = _raw_quarter_totals(rows, 2023, 2, Decimal("0.90"))

        self.assertTrue(rows[0].is_asset_like)
        self.assertEqual(quarter["asset_gross_eur"], Decimal("115.54"))
        self.assertEqual(quarter["asset_base_eur"], Decimal("115.54"))

    def test_blank_enriched_values_round_trip_to_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "xolo.csv"
            path.write_text(
                "date,recipient,type,number,amount_original,currency,subtotal_amount,gross_eur,vat_base_eur,detail_confidence,is_depreciable_asset\n"
                "2023-06-30,GitHub,Professional expenses,2023-06-30,125.00,USD,125.00,,,missing_detail,no\n",
                encoding="utf-8",
            )

            rows = load_raw_xolo_expenses(path)

        self.assertIsNone(rows[0].gross_eur)
        self.assertIsNone(rows[0].vat_base_eur)

    def test_nearest_excluded_subset_ignores_asset_candidates(self):
        rows = [
            self._expense("2024-04-01", "Xolo", "Professional expenses", "INV1", "66.55"),
            self._expense("2024-04-30", "TGSS", "Social security & prof. fees", "TGSS1", "86.66"),
            self._expense("2024-05-07", "Apple Retail Spain", "Computer hardware & software", "ASSET", "642.95"),
            self._expense("2024-05-13", "Translator", "Professional expenses", "T1", "55.80"),
        ]

        subset = _nearest_excluded_subset(rows, 2024, 2, None, Decimal("153.21"))

        self.assertIsNotNone(subset)
        assert subset is not None
        self.assertEqual(subset.total, Decimal("153.21"))
        self.assertEqual(subset.error, Decimal("0.00"))
        self.assertEqual([row.number for row, _ in subset.rows], ["INV1", "TGSS1"])

    def _expense(
        self,
        issued: str,
        recipient: str,
        expense_type: str,
        number: str,
        amount: str,
    ) -> RawXoloExpense:
        return RawXoloExpense(
            date=date.fromisoformat(issued),
            recipient=recipient,
            expense_type=expense_type,
            number=number,
            amount_original=Decimal(amount),
            currency="EUR",
            subtotal_amount=Decimal(amount),
        )


if __name__ == "__main__":
    unittest.main()
