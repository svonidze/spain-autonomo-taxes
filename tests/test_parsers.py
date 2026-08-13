from decimal import Decimal
import tempfile
import unittest
from pathlib import Path

from autonomo_taxes.money import parse_amount
from autonomo_taxes.parsers import (
    LedgerEntry,
    apply_fx,
    parse_income_invoice,
    parse_expense,
    parse_us_numeric_date,
    scan_income_dir,
)


class ParserTests(unittest.TestCase):
    def test_parse_amount_formats(self):
        self.assertEqual(parse_amount("36.770,89"), Decimal("36770.89"))
        self.assertEqual(parse_amount("6 281,71 USD"), Decimal("6281.71"))
        self.assertEqual(parse_amount("$1,234.56"), Decimal("1234.56"))

    def test_parse_income_invoice_text(self):
        text = """
        INVOICE
        Invoice No: FACT-2026-00001
        Date: 2026-01-03
        Sent to: Sent by:
        Example Customer One Example Sender
        Invoice total: 6 281,71 USD
        """
        entry = parse_income_invoice(Path("ipg.pdf"), text)
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.date.isoformat(), "2026-01-03")
        self.assertEqual(entry.currency, "USD")
        self.assertEqual(entry.amount_original, Decimal("6281.71"))

    def test_parse_xolo_fee_text(self):
        text = """
        Example Accounting Provider
        FACTURA
        Fecha de emisión: 01/04/2026
        Base (sin IVA): 59,00 EUR
        VAT 21% 12,39 EUR
        Total: 71,39 EUR
        """
        entry = parse_expense(Path("xolo.pdf"), text)
        self.assertFalse(entry.review_required)
        self.assertEqual(entry.date.isoformat(), "2026-04-01")
        self.assertEqual(entry.deductible_eur, Decimal("59.00"))

    def test_parse_namecheap_us_date(self):
        self.assertEqual(parse_us_numeric_date("5/8/2026").isoformat(), "2026-05-08")
        text = """
        RECEIPT
        Synthetic Party 002
        Order Date : 6/4/2026 7:58:24 AM
        Sub Total $131.96
        TOTAL $131.96
        """
        entry = parse_expense(Path("namecheap.pdf"), text)
        self.assertEqual(entry.date.isoformat(), "2026-06-04")
        self.assertEqual(entry.amount_original, Decimal("131.96"))

    def test_parse_tgss_debit_without_person_specific_literals(self):
        text = """
        TGSS. COTIZACION
        EXAMPLE CONTRIBUTOR 12345-67 370,59
        """

        entry = parse_expense(Path("tgss.pdf"), text)

        self.assertEqual(entry.counterparty, "TGSS")
        self.assertEqual(entry.amount_original, Decimal("370.59"))
        self.assertFalse(entry.review_required)

    def test_parse_plenitude_uses_invoice_date_not_consumption_period(self):
        text = """
        Eni Plenitude Iberia, SL
        DATOS DE FACTURA DE ELECTRICIDAD
        Nº de factura: SYNTH-DOCUMENT-014
        Periodo de consumo: 08/06/2026 a 07/07/2026
        Fecha de factura : 13/07/2026
        Base imponible 89,56 €
        IVA General (21%) 18,81 €
        TOTAL IMPORTE FACTURA 108,37 €
        """

        entry = parse_expense(Path("plenitude.pdf"), text)

        self.assertEqual(entry.date.isoformat(), "2026-07-13")
        self.assertEqual(entry.amount_original, Decimal("108.37"))
        self.assertEqual(entry.amount_eur, Decimal("108.37"))
        self.assertIsNone(entry.deductible_eur)
        self.assertEqual(entry.description, "SYNTH-DOCUMENT-014")
        self.assertEqual(entry.category, "home_utility_review")
        self.assertTrue(entry.review_required)

    def test_apply_fx_preserves_manual_eur_amount(self):
        entry = LedgerEntry(
            kind="income",
            date=None,
            document="manual.csv",
            counterparty="Bank",
            description="Manual bank credit",
            amount_original=Decimal("100.00"),
            currency="USD",
            amount_eur=Decimal("85.00"),
            deductible_eur=None,
            category="manual_income",
            confidence="manual",
            review_required=False,
        )
        warnings = apply_fx([entry], {"USD": Decimal("0.90")})
        self.assertEqual(warnings, [])
        self.assertEqual(entry.amount_eur, Decimal("85.00"))
        self.assertIn("configured FX USD->0.90 not applied", entry.notes)

    def test_scan_income_queues_non_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "invoice.png").write_text("scan", encoding="utf-8")
            parsed, manual = scan_income_dir(path)
        self.assertEqual(parsed, [])
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0].category, "unsupported income invoice")


if __name__ == "__main__":
    unittest.main()
