from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.xolo_expense_details import (
    enrich_raw_expense_rows,
    parse_detail_facts,
    read_csv_rows,
    write_detail_facts_csv,
    write_detail_facts_markdown,
)


class XoloExpenseDetailsTests(unittest.TestCase):
    def test_parses_usd_exchange_rate_as_eur_basis(self):
        row = {
            "xolo_id": "1751445",
            "xolo_url": "https://app.xolo.io/selfservice/expense/invoice/1751445/details?from=expense",
            "date": "2023-06-30",
            "recipient": "Synthetic Party 001",
            "number": "2023-06-30",
            "currency": "USD",
            "amount_original": "125.00",
            "subtotal_amount": "125.00",
        }
        text = "Expense Computer hardware & software: This expense is a depreciable asset. Currency USD Exchange rate 1.0819"

        facts = parse_detail_facts(row, text)

        self.assertEqual(facts.detail_currency, "USD")
        self.assertEqual(facts.exchange_rate, Decimal("1.0819"))
        self.assertEqual(facts.gross_eur, Decimal("115.54"))
        self.assertEqual(facts.vat_base_eur, Decimal("115.54"))
        self.assertTrue(facts.is_depreciable_asset)
        self.assertEqual(facts.confidence, "detail_page_exchange_rate")

    def test_parses_eur_without_exchange_rate(self):
        row = {
            "xolo_id": "3019975",
            "date": "2026-04-08",
            "recipient": "Synthetic Party 016",
            "number": "SYNTH-DOCUMENT-031",
            "currency": "EUR",
            "amount_original": "1994.00",
            "subtotal_amount": "1647.93",
        }
        text = "Expense Computer hardware & software: This expense is a depreciable asset. Currency EUR Exchange rate -"

        facts = parse_detail_facts(row, text)

        self.assertEqual(facts.exchange_rate, None)
        self.assertEqual(facts.gross_eur, Decimal("1994.00"))
        self.assertEqual(facts.vat_base_eur, Decimal("1647.93"))
        self.assertEqual(facts.confidence, "detail_page_eur")

    def test_currency_mismatch_is_not_converted_or_marked_high_confidence(self):
        row = {
            "xolo_id": "bad",
            "currency": "EUR",
            "amount_original": "125.00",
            "subtotal_amount": "125.00",
        }
        text = "Currency USD Exchange rate 1.0819"

        facts = parse_detail_facts(row, text)

        self.assertEqual(facts.detail_currency, "USD")
        self.assertIsNone(facts.gross_eur)
        self.assertIsNone(facts.vat_base_eur)
        self.assertEqual(facts.confidence, "currency_mismatch_review")

    def test_missing_exchange_rate_is_explicit(self):
        row = {
            "xolo_id": "missing",
            "currency": "USD",
            "amount_original": "125.00",
            "subtotal_amount": "125.00",
        }

        facts = parse_detail_facts(row, "Expense details without currency block")

        self.assertIsNone(facts.exchange_rate)
        self.assertIsNone(facts.gross_eur)
        self.assertEqual(facts.confidence, "missing_exchange_rate")

    def test_parses_european_decimal_rate_and_negative_amount(self):
        row = {
            "xolo_id": "refund",
            "currency": "USD",
            "amount_original": "-250.00",
            "subtotal_amount": "-250.00",
        }

        facts = parse_detail_facts(row, "Currency USD Exchange rate 1,1086")

        self.assertEqual(facts.gross_eur, Decimal("-225.51"))

    def test_depreciable_asset_defaults_to_false(self):
        row = {
            "xolo_id": "normal",
            "currency": "EUR",
            "amount_original": "10.00",
        }

        facts = parse_detail_facts(row, "Currency EUR Exchange rate - Professional expenses")

        self.assertFalse(facts.is_depreciable_asset)

    def test_enriches_raw_rows_with_detail_facts(self):
        raw_rows = [
            {
                "xolo_id": "1751445",
                "currency": "USD",
                "amount_original": "125.00",
            }
        ]
        fact_rows = [
            {
                "xolo_id": "1751445",
                "exchange_rate": "1.0819",
                "gross_eur": "115.54",
                "vat_base_eur": "115.54",
                "detail_currency": "USD",
                "is_depreciable_asset": "yes",
                "confidence": "detail_page_exchange_rate",
            }
        ]

        enriched = enrich_raw_expense_rows(raw_rows, fact_rows)

        self.assertEqual(enriched[0]["gross_eur"], "115.54")
        self.assertEqual(enriched[0]["xolo_exchange_rate"], "1.0819")
        self.assertEqual(enriched[0]["is_depreciable_asset"], "yes")

    def test_enrich_marks_rows_without_matching_detail(self):
        raw_rows = [{"xolo_id": "missing", "currency": "USD", "amount_original": "10.00"}]

        enriched = enrich_raw_expense_rows(raw_rows, [])

        self.assertEqual(enriched[0]["detail_confidence"], "missing_detail")
        self.assertEqual(enriched[0]["gross_eur"], "")

    def test_writes_detail_facts_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw.csv"
            raw.write_text("xolo_id,currency,amount_original\n1,USD,10.00\n", encoding="utf-8")
            rows = read_csv_rows(raw)
            facts = [parse_detail_facts(rows[0], "Currency USD Exchange rate 1.25")]
            out_csv = root / "facts.csv"
            out_md = root / "facts.md"

            write_detail_facts_csv(out_csv, facts)
            write_detail_facts_markdown(out_md, [fact.to_row() for fact in facts])

            with out_csv.open(newline="", encoding="utf-8") as handle:
                csv_rows = list(csv.DictReader(handle))
            self.assertEqual(csv_rows[0]["gross_eur"], "8.00")
            self.assertIn("Xolo Expense Detail Facts", out_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
