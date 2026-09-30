from __future__ import annotations

import unittest

from autonomo_taxes.source_book_wording import source_book_wording


class SourceBookWordingTests(unittest.TestCase):
    def test_rewrites_plural_without_bookss_typo(self):
        text = source_book_wording("treated in the submitted registers for Q2/Q3/Q4")

        self.assertEqual(text, "treated in the source books for Q2/Q3/Q4")
        self.assertNotIn("bookss", text)

    def test_rewrites_modelo_130_register_phrase(self):
        text = source_book_wording("deductible expense amount used in the submitted Modelo 130 register")

        self.assertEqual(text, "deductible expense amount used in the source books")

    def test_rewrites_row_level_register_wording(self):
        text = source_book_wording("not the submitted row-level expense register or submitted per-row register")

        self.assertEqual(text, "not the source-book row treatment or source-book row detail")

    def test_rewrites_submitted_row_question_without_touching_submitted_casilla(self):
        text = source_book_wording("Which submitted row explains the submitted casilla 02 delta?")

        self.assertEqual(text, "Which source-book row explains the submitted casilla 02 delta?")

    def test_rewrites_period_specific_register_phrases(self):
        text = source_book_wording(
            "Did Xolo's submitted 3T 2023 register include it, did the filed Modelo 130 2T 2026 register deduct it, "
            "and did the submitted Modelo 130 2T 2023 register use a partial amount?"
        )

        self.assertEqual(
            text,
            "Did Xolo's source books for 3T 2023 include it, did the source books for filed Modelo 130 2T 2026 deduct it, "
            "and did the source books for Modelo 130 2T 2023 use a partial amount?",
        )

    def test_rewrites_xolo_register_confirmation(self):
        text = source_book_wording(
            "needs Xolo register confirmation and does not prove the submitted Xolo register row treatment"
        )

        self.assertEqual(
            text,
            "needs Xolo source-book confirmation and does not prove the Xolo source-book row treatment",
        )

    def test_rewrites_capitalized_register_without_mangling_registration(self):
        text = source_book_wording("Submitted register evidence, not submitted registration metadata.")

        self.assertEqual(text, "Source books evidence, not submitted registration metadata.")


if __name__ == "__main__":
    unittest.main()
