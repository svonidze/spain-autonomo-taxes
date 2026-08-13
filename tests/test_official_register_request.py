from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.official_register_request import BANNED_FIRST_CONTACT_PHRASES, build_official_register_request


class OfficialRegisterRequestTests(unittest.TestCase):
    def test_first_contact_asks_for_offboarding_records_without_internal_fields(self):
        markdown = build_official_register_request()
        lowered = markdown.lower()

        self.assertIn("standard Xolo Data export", markdown)
        self.assertIn("invoice, expense, and tax-report PDFs", markdown)
        self.assertIn("not included in the standard Data export", markdown)
        self.assertIn("official accounting/register books", markdown)
        self.assertIn("Libro registro de ingresos", markdown)
        self.assertIn("Libro registro de gastos", markdown)
        self.assertIn("Libro registro de bienes de inversión", markdown)
        self.assertIn("Libro registro de provisiones de fondos y suplidos", markdown)
        self.assertIn("IVA registration books", markdown)
        self.assertIn("Libro registro de facturas expedidas", markdown)
        self.assertIn("Libro registro de facturas recibidas", markdown)
        self.assertIn("Libro registro de determinadas operaciones intracomunitarias", markdown)
        self.assertIn("justificantes de presentación", markdown)
        self.assertIn("NRC/payment references", markdown)
        self.assertIn("Standard bookkeeping exports and year-end summaries", markdown)
        self.assertIn("2023, 2024, 2025, and 2026", markdown)
        self.assertIn("up to and including 2T 2026", markdown)
        for phrase in BANNED_FIRST_CONTACT_PHRASES:
            self.assertNotIn(phrase, lowered)

    def test_cli_writes_official_register_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_md = Path(tmp) / "message.md"

            exit_code = main(
                [
                    "audit-official-register-request",
                    "--out-md",
                    str(out_md),
                    "--through-period",
                    "2T 2026",
                ]
            )

            markdown = out_md.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertIn("Message To Send To Xolo", markdown)
        self.assertIn("AEAT-compatible", markdown)
        self.assertIn("Request for my official accounting books and filing records", markdown)


if __name__ == "__main__":
    unittest.main()
