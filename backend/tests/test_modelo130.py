from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.modelo130 import (
    calculate_modelo130,
    difficult_expenses,
    extract_modelo130_values_from_text,
    _extract_modelo130_values_from_positioned_words,
    previous_positive_payments_with_warnings,
)


class Modelo130Tests(unittest.TestCase):
    def test_difficult_expenses_are_five_percent_capped(self):
        self.assertEqual(difficult_expenses(Decimal("100000"), Decimal("10000")), Decimal("2000.00"))
        self.assertEqual(difficult_expenses(Decimal("10000"), Decimal("8000")), Decimal("100.00"))
        self.assertEqual(difficult_expenses(Decimal("1000"), Decimal("1200")), Decimal("0.00"))
        self.assertEqual(
            difficult_expenses(Decimal("1000"), Decimal("0"), rate=Decimal("0.07")),
            Decimal("70.00"),
        )

    def test_q2_xolo_formula_when_inputs_match(self):
        result = calculate_modelo130(
            income_ytd=Decimal("36770.89"),
            deductible_before_difficult_ytd=Decimal("8885.98"),
            previous_positive_casilla_07=Decimal("2659.01"),
        )
        self.assertEqual(result.casilla_02, Decimal("10280.23"))
        self.assertEqual(result.casilla_03, Decimal("26490.66"))
        self.assertEqual(result.casilla_04, Decimal("5298.13"))
        self.assertEqual(result.casilla_19, Decimal("2639.12"))

    def test_formula_can_exclude_difficult_expenses_for_xolo_audit(self):
        result = calculate_modelo130(
            income_ytd=Decimal("36770.89"),
            deductible_before_difficult_ytd=Decimal("10280.23"),
            previous_positive_casilla_07=Decimal("2659.01"),
            include_difficult_expenses=False,
        )
        self.assertEqual(result.casilla_02, Decimal("10280.23"))
        self.assertEqual(result.casilla_19, Decimal("2639.12"))

    def test_extract_modelo130_values_from_text(self):
        text = """
        example
        exampleEI
        2026 2T
        36.770,89
        10.280,23
        26.490,66
        5.298,13
        2.659,01
        2.639,12
        """
        values = extract_modelo130_values_from_text(text, 2026, 2)
        self.assertEqual(values["01"], Decimal("36770.89"))
        self.assertEqual(values["05"], Decimal("2659.01"))
        self.assertEqual(values["19"], Decimal("2639.12"))

    def test_extract_modelo130_values_from_positioned_words_handles_minoracion(self):
        words = [
            (Decimal("528.8"), Decimal("604.0"), "5.757,20"),
            (Decimal("536.3"), Decimal("592.2"), "115,54"),
            (Decimal("528.8"), Decimal("579.5"), "5.641,66"),
            (Decimal("528.8"), Decimal("567.7"), "1.128,33"),
            (Decimal("527.8"), Decimal("496.4"), "1.128,33"),
            (Decimal("528.8"), Decimal("387.1"), "1.128,33"),
            (Decimal("536.3"), Decimal("375.3"), "100,00"),
            (Decimal("528.8"), Decimal("362.6"), "1.028,33"),
            (Decimal("528.8"), Decimal("302.7"), "1.028,33"),
            (Decimal("528.3"), Decimal("267.4"), "1.028,33"),
        ]

        values = _extract_modelo130_values_from_positioned_words(words)

        self.assertEqual(values["01"], Decimal("5757.20"))
        self.assertEqual(values["05"], Decimal("0.00"))
        self.assertEqual(values["07"], Decimal("1128.33"))
        self.assertEqual(values["13"], Decimal("100.00"))
        self.assertEqual(values["19"], Decimal("1028.33"))

    def test_missing_previous_reports_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            total, warnings = previous_positive_payments_with_warnings(Path(tmp), 2026, 3)
        self.assertEqual(total, Decimal("0.00"))
        self.assertEqual(len(warnings), 2)
        self.assertIn("2026 1T", warnings[0])


if __name__ == "__main__":
    unittest.main()
