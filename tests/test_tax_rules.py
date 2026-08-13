from decimal import Decimal
import unittest

from autonomo_taxes.tax_rules import (
    DIFFICULT_EXPENSE_CAP_EUR,
    calculate_difficult_expenses,
    difficult_expense_rate_for_year,
    difficult_expense_rule_for_year,
    filed_form_codes_from_values,
    recognize_tax_form_filename,
)


class TaxRulesTests(unittest.TestCase):
    def test_difficult_expense_rule_is_year_versioned(self):
        self.assertEqual(difficult_expense_rate_for_year(2023), Decimal("0.07"))
        self.assertEqual(difficult_expense_rate_for_year(2024), Decimal("0.05"))
        self.assertEqual(difficult_expense_rule_for_year(2026).annual_cap_eur, DIFFICULT_EXPENSE_CAP_EUR)
        self.assertEqual(calculate_difficult_expenses(2023, Decimal("1000.00"), Decimal("0.00")), Decimal("70.00"))
        self.assertEqual(calculate_difficult_expenses(2024, Decimal("1000.00"), Decimal("0.00")), Decimal("50.00"))
        self.assertEqual(
            calculate_difficult_expenses(2026, Decimal("100000.00"), Decimal("0.00")),
            Decimal("2000.00"),
        )

    def test_recognizes_dynamic_form_filenames(self):
        quarterly = recognize_tax_form_filename("modelo_349_2026_q2_exampleei.pdf")
        annual = recognize_tax_form_filename("MOD-347-2025-summary.pdf")
        crypto = recognize_tax_form_filename("M721 2024.pdf")
        nonresident_quarter = recognize_tax_form_filename("MOD 216 2T 2026.pdf")
        nonresident_annual = recognize_tax_form_filename("Modelo 296 2026.pdf")

        self.assertIsNotNone(quarterly)
        self.assertEqual(quarterly.category, "modelo349_report")
        self.assertEqual(quarterly.period, "2026-Q2")
        self.assertIsNotNone(annual)
        self.assertEqual(annual.category, "modelo347_report")
        self.assertEqual(annual.period, "2025")
        self.assertIsNotNone(crypto)
        self.assertEqual(crypto.category, "modelo721_report")
        self.assertEqual(crypto.period, "2024")
        self.assertEqual(nonresident_quarter.category, "modelo216_report")
        self.assertEqual(nonresident_quarter.period, "2026-Q2")
        self.assertEqual(nonresident_annual.category, "modelo296_report")
        self.assertEqual(nonresident_annual.period, "2026")

    def test_filed_form_codes_accept_codes_and_filenames(self):
        filed = filed_form_codes_from_values(
            {
                "130",
                "modelo_303_2026_q2.pdf",
                "modelo349_report",
                "M721 2024.pdf",
            }
        )

        self.assertEqual(filed, {"130", "303", "349", "721"})

    def test_q4_submission_date_is_not_mistaken_for_tax_year(self):
        recognized = recognize_tax_form_filename("MOD 130 4T presentado 2024-01-20.pdf")
        self.assertIsNotNone(recognized)
        self.assertEqual(recognized.period, "2023-Q4")


if __name__ == "__main__":
    unittest.main()
