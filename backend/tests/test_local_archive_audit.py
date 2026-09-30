from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.history import RawXoloExpense
from autonomo_taxes.local_archive_audit import (
    audit_local_entries,
    write_local_archive_audit_markdown,
)
from autonomo_taxes.parsers import LedgerEntry


class LocalArchiveAuditTests(unittest.TestCase):
    def test_matches_by_number_in_local_filename(self):
        local = [
            self._entry(
                document="EXPENSE/Xolo_SYNTH-DOCUMENT-043.pdf",
                description="SYNTH-DOCUMENT-043",
                amount=Decimal("66.55"),
            )
        ]
        raw = [self._raw(number="SYNTH-DOCUMENT-043", amount=Decimal("66.55"))]

        rows = audit_local_entries(local, raw)

        self.assertEqual(rows[0]["status"], "matched_to_xolo_raw")
        self.assertEqual(rows[0]["matched_xolo_numbers"], "SYNTH-DOCUMENT-043")
        self.assertIn("number_in_local_document", rows[0]["match_reasons"])

    def test_classifies_local_parsed_without_xolo_raw(self):
        rows = audit_local_entries(
            [
                self._entry(
                    document="EXPENSE/transaction_statement_64897da7.pdf",
                    description="Revolut Premium",
                    amount=Decimal("7.99"),
                    entry_date=date(2023, 6, 14),
                )
            ],
            [],
        )

        self.assertEqual(rows[0]["period"], "2023-Q2")
        self.assertEqual(rows[0]["status"], "local_parsed_without_xolo_raw")
        self.assertIn("Not matched to the raw Xolo expense API snapshot", rows[0]["notes"])

    def test_short_numeric_date_like_number_does_not_match_inside_invoice_number(self):
        local = [self._entry(document="EXPENSE/Xolo_SYNTH-DOCUMENT-045.pdf", description="SYNTH-DOCUMENT-045")]
        raw = [self._raw(number="SYNTH-DOCUMENT-006", amount=Decimal("55.00"), row_date=date(2025, 7, 31))]

        rows = audit_local_entries(local, raw)

        self.assertEqual(rows[0]["status"], "local_parsed_without_xolo_raw")
        self.assertEqual(rows[0]["matched_xolo_numbers"], "")

    def test_same_date_counterparty_amount_mismatch_is_not_local_only(self):
        local = [
            self._entry(
                document="EXPENSE/synthetic marketplace invoice.pdf",
                description="Synthetic marketplace invoice",
                amount=Decimal("212.14"),
                entry_date=date(2024, 7, 4),
                counterparty="Synthetic Marketplace",
            )
        ]
        raw = [
            self._raw(
                number="SYNTH-DOCUMENT-047",
                amount=Decimal("256.69"),
                row_date=date(2024, 7, 4),
                recipient="Synthetic Marketplace",
            )
        ]

        rows = audit_local_entries(local, raw)

        self.assertEqual(rows[0]["status"], "amount_mismatch_potential_xolo_raw")
        self.assertEqual(rows[0]["matched_xolo_numbers"], "SYNTH-DOCUMENT-047")

    def test_classifies_manual_review_without_date_amount(self):
        rows = audit_local_entries(
            [
                LedgerEntry(
                    kind="manual_review",
                    date=None,
                    document="EXPENSE/scan.png",
                    counterparty="Unknown",
                    description="scan.png",
                    amount_original=None,
                    currency="",
                    amount_eur=None,
                    deductible_eur=None,
                    category="manual",
                    confidence="low",
                    review_required=True,
                    notes="image needs OCR",
                )
            ],
            [],
        )

        self.assertEqual(rows[0]["period"], "undated")
        self.assertEqual(rows[0]["status"], "local_manual_review_without_date_amount")

    def test_ambiguous_same_date_amount_matches_are_not_collapsed(self):
        local = [self._entry(amount=Decimal("104.33"), entry_date=date(2026, 5, 10))]
        raw = [
            self._raw(number="A", amount=Decimal("104.33"), row_date=date(2026, 5, 10)),
            self._raw(number="B", amount=Decimal("104.33"), row_date=date(2026, 5, 10)),
        ]

        rows = audit_local_entries(local, raw)

        self.assertEqual(rows[0]["status"], "ambiguous_xolo_raw_matches")
        self.assertEqual(rows[0]["matched_xolo_numbers"], "A; B")

    def test_markdown_focuses_2023_2024_attention_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.md"
            rows = [
                {
                    "period": "2023-Q2",
                    "status": "local_parsed_without_xolo_raw",
                    "date": "2023-06-14",
                    "local_document": "EXPENSE/revolut.pdf",
                    "counterparty": "Revolut",
                    "description": "Premium",
                    "parser_kind": "expense",
                    "category": "bank_fee",
                    "amount_original": "7.99",
                    "currency": "EUR",
                    "amount_eur": "7.99",
                    "deductible_eur": "7.99",
                    "confidence": "fixture",
                    "review_required": "no",
                    "matched_xolo_ids": "",
                    "matched_xolo_numbers": "",
                    "matched_xolo_recipients": "",
                    "matched_xolo_amounts": "",
                    "match_reasons": "",
                    "notes": "candidate",
                }
            ]

            write_local_archive_audit_markdown(path, rows)
            text = path.read_text(encoding="utf-8")

        self.assertIn("2023/2024 local attention candidates: `1`", text)
        self.assertIn("EXPENSE/revolut.pdf", text)

    def _entry(
        self,
        *,
        document: str = "EXPENSE/local.pdf",
        description: str = "local",
        amount: Decimal = Decimal("10.00"),
        entry_date: date = date(2023, 7, 1),
        counterparty: str = "Xolo Business Spain",
    ) -> LedgerEntry:
        return LedgerEntry(
            kind="expense",
            date=entry_date,
            document=document,
            counterparty=counterparty,
            description=description,
            amount_original=amount,
            currency="EUR",
            amount_eur=amount,
            deductible_eur=amount,
            category="fixture",
            confidence="fixture",
            review_required=False,
            notes="",
        )

    def _raw(
        self,
        *,
        number: str,
        amount: Decimal,
        row_date: date = date(2023, 7, 1),
        recipient: str = "Synthetic Party 015",
    ) -> RawXoloExpense:
        return RawXoloExpense(
            date=row_date,
            recipient=recipient,
            expense_type="Professional expenses",
            number=number,
            amount_original=amount,
            currency="EUR",
            subtotal_amount=amount,
            xolo_url=f"https://xolo.test/{number}",
            xolo_id=number,
            status="PAID",
        )


if __name__ == "__main__":
    unittest.main()
