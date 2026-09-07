from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.history import load_raw_xolo_expenses
from autonomo_taxes.xolo_api_export import (
    import_xolo_expense_api_json,
    normalize_xolo_api_row,
    write_raw_xolo_expense_csv,
)


class XoloApiExportTests(unittest.TestCase):
    def test_imports_saved_datatables_pages_to_raw_csv_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.json"
            path.write_text(
                json.dumps(
                    {
                        "draw": 1,
                        "recordsTotal": 2,
                        "recordsFiltered": 2,
                        "data": [
                            {
                                "id": 3137426,
                                "party": '<a href="/selfservice/expense/invoice/3137426/details?from=expense">Synthetic Party 015</a>',
                                "categoryText": "Professional expenses",
                                "number": "177919077234",
                                "dateString": "2026-07-01",
                                "paymentDateString": "2026-07-02",
                                "amount": "€71,39",
                                "subTotalAmount": "59,00",
                                "vatAmount": "12,39",
                                "status": "<span>PAID</span>",
                            },
                            {
                                "party": '<a href="https://app.xolo.io/selfservice/expense/invoice/3141770/details?from=expense">Synthetic Party 002</a>',
                                "categoryText": "Professional expenses",
                                "number": "SYNTH-DOCUMENT-030",
                                "date": "2026-06-29",
                                "amountString": "$75,98",
                                "amount": "$75,98",
                                "status": "UNPAID",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            pages, rows = import_xolo_expense_api_json([path])

            self.assertEqual(len(pages), 1)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["xolo_id"], "3137426")
            self.assertEqual(rows[0]["recipient"], "Synthetic Party 015")
            self.assertEqual(rows[0]["currency"], "EUR")
            self.assertEqual(rows[0]["subtotal_amount"], "59,00")
            self.assertEqual(rows[1]["xolo_id"], "3141770")
            self.assertEqual(rows[1]["currency"], "USD")

            out = Path(tmp) / "xolo_raw.csv"
            write_raw_xolo_expense_csv(out, rows)
            with out.open(newline="", encoding="utf-8") as handle:
                csv_rows = list(csv.DictReader(handle))
            self.assertEqual(csv_rows[0]["number"], "177919077234")
            self.assertEqual(csv_rows[1]["amount_original"], "$75,98")

            loaded = load_raw_xolo_expenses(out)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0].recipient, "Synthetic Party 015")
            self.assertEqual(loaded[0].currency, "EUR")
            self.assertEqual(loaded[1].currency, "USD")

    def test_rejects_login_html_saved_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "login.html"
            path.write_text("<html><title>Logged out | Xolo</title></html>", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "not the copied curl command or login HTML"):
                import_xolo_expense_api_json([path])

    def test_normalizes_live_numeric_amount_shape(self):
        row = normalize_xolo_api_row(
            {
                "id": 3137426,
                "party": "Synthetic Party 015",
                "categoryText": "Professional expenses",
                "number": "177919077234",
                "date": "2026-06-30T21:00:00.000+00:00",
                "dateString": "2026-07-01",
                "amount": 71.39,
                "amountString": "€71,39",
                "matchAmount": 71.39,
                "currency": "EUR",
                "subTotalAmount": 59,
                "vatAmount": 12.39,
                "vatPercentages": "21",
                "status": "UNPAID",
            }
        )

        self.assertEqual(row["xolo_id"], "3137426")
        self.assertEqual(row["xolo_url"], "https://app.xolo.io/selfservice/expense/invoice/3137426/details?from=expense")
        self.assertEqual(row["date"], "2026-07-01")
        self.assertEqual(row["amount_text"], "€71,39")
        self.assertEqual(row["amount_original"], "71.39")
        self.assertEqual(row["currency"], "EUR")
        self.assertEqual(row["gross_amount"], "71.39")
        self.assertEqual(row["match_amount"], "71.39")
        self.assertEqual(row["subtotal_amount"], "59")
        self.assertEqual(row["vat_amount"], "12.39")
        self.assertEqual(row["status"], "UNPAID")


if __name__ == "__main__":
    unittest.main()
