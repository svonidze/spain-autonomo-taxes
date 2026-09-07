from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.revolut import (
    RevolutMatchCandidate,
    load_revolut_payments_csv,
    match_revolut_payments,
)


class RevolutTests(unittest.TestCase):
    def test_parses_representative_csv_variants_and_preserves_original_values(self):
        first_csv = (
            "Completed Date,Reference,Counterparty,Description,Amount,Fee,Currency,Amount in EUR,Fee in EUR,State\n"
            "2026-07-02,INV-2026-06,Client A,Invoice payment,1200.00,2.00,USD,1104.00,1.84,COMPLETED\n"
        )
        second_csv = (
            "Date,Beneficiary,Payment reference,Details,Paid out,Fee paid,Currency,Settled amount,Settled fee,Status\n"
            "30/06/2026,Agencia Tributaria,Q2 VAT,Tax payment,450.00,0.00,EUR,450.00,0.00,COMPLETED\n"
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first_path = tmp_path / "first.csv"
            second_path = tmp_path / "second.csv"
            first_path.write_text(first_csv, encoding="utf-8")
            second_path.write_text(second_csv, encoding="utf-8")

            first_payment = load_revolut_payments_csv(first_path)[0]
            second_payment = load_revolut_payments_csv(second_path)[0]

        self.assertEqual(first_payment.payment_date, date(2026, 7, 2))
        self.assertEqual(first_payment.reference, "INV-2026-06")
        self.assertEqual(first_payment.counterparty, "Client A")
        self.assertEqual(first_payment.amount_original, Decimal("1200.00"))
        self.assertEqual(first_payment.currency, "USD")
        self.assertEqual(first_payment.amount_eur, Decimal("1104.00"))
        self.assertEqual(first_payment.fee_original, Decimal("2.00"))
        self.assertEqual(first_payment.fee_eur, Decimal("1.84"))
        self.assertEqual(first_payment.source_row["Completed Date"], "2026-07-02")

        self.assertEqual(second_payment.payment_date, date(2026, 6, 30))
        self.assertEqual(second_payment.reference, "Q2 VAT")
        self.assertEqual(second_payment.counterparty, "Agencia Tributaria")
        self.assertEqual(second_payment.amount_original, Decimal("-450.00"))
        self.assertEqual(second_payment.currency, "EUR")
        self.assertEqual(second_payment.amount_eur, Decimal("-450.00"))

    def test_exact_match_preserves_tax_recognition_date(self):
        csv_content = (
            "Completed Date,Reference,Counterparty,Amount,Fee,Currency,Amount in EUR\n"
            "2026-07-02,INV-2026-06,Client A,1200.00,2.00,USD,1104.00\n"
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payments.csv"
            path.write_text(csv_content, encoding="utf-8")
            payment = load_revolut_payments_csv(path)[0]

        candidate = RevolutMatchCandidate(
            candidate_id="invoice-1",
            recognition_date=date(2026, 6, 30),
            payment_date=date(2026, 7, 2),
            reference="INV-2026-06",
            amount_original=Decimal("1200.00"),
            currency="USD",
            amount_eur=Decimal("1104.00"),
        )

        result = match_revolut_payments([payment], [candidate])[0]

        self.assertEqual(result.outcome, "exact")
        self.assertEqual(result.matched_candidate_ids, ("invoice-1",))
        self.assertEqual(result.payment_date, date(2026, 7, 2))
        self.assertEqual(result.recognition_date, date(2026, 6, 30))

    def test_amount_only_candidates_require_manual_review(self):
        csv_content = (
            "Completed Date,Counterparty,Amount,Currency,Amount in EUR\n"
            "2026-07-02,Client A,1200.00,USD,1104.00\n"
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payments.csv"
            path.write_text(csv_content, encoding="utf-8")
            payment = load_revolut_payments_csv(path)[0]

        candidates = [
            RevolutMatchCandidate(
                candidate_id="invoice-1",
                recognition_date=date(2026, 6, 30),
                payment_date=None,
                reference="",
                amount_original=Decimal("1200.00"),
                currency="USD",
                amount_eur=Decimal("1104.00"),
            ),
            RevolutMatchCandidate(
                candidate_id="invoice-2",
                recognition_date=date(2026, 6, 29),
                payment_date=None,
                reference="",
                amount_original=Decimal("1200.00"),
                currency="USD",
                amount_eur=Decimal("1104.00"),
            ),
        ]

        result = match_revolut_payments([payment], candidates)[0]

        self.assertEqual(result.outcome, "unmatched")
        self.assertEqual(result.matched_candidate_ids, ())
        self.assertIsNone(result.recognition_date)

    def test_match_is_unmatched_when_no_candidate_qualifies(self):
        csv_content = (
            "Completed Date,Reference,Amount,Currency,Amount in EUR\n"
            "2026-07-02,INV-2026-06,1200.00,USD,1104.00\n"
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payments.csv"
            path.write_text(csv_content, encoding="utf-8")
            payment = load_revolut_payments_csv(path)[0]

        candidate = RevolutMatchCandidate(
            candidate_id="invoice-1",
            recognition_date=date(2026, 6, 30),
            payment_date=date(2026, 7, 2),
            reference="INV-OTHER",
            amount_original=Decimal("1200.00"),
            currency="USD",
            amount_eur=Decimal("1104.00"),
        )

        result = match_revolut_payments([payment], [candidate])[0]

        self.assertEqual(result.outcome, "unmatched")
        self.assertEqual(result.matched_candidate_ids, ())

    def test_identical_csv_rows_have_distinct_ids_and_cannot_consume_one_invoice_twice(self):
        csv_content = (
            "Completed Date,Reference,Counterparty,Amount,Currency,Amount in EUR\n"
            "2026-07-02,INV-1,Client A,100.00,EUR,100.00\n"
            "2026-07-02,INV-1,Client A,100.00,EUR,100.00\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payments.csv"
            path.write_text(csv_content, encoding="utf-8")
            payments = load_revolut_payments_csv(path)

        candidate = RevolutMatchCandidate(
            candidate_id="invoice-1",
            recognition_date=date(2026, 6, 30),
            payment_date=None,
            reference="INV-1",
            amount_original=Decimal("100.00"),
            currency="EUR",
            amount_eur=Decimal("100.00"),
        )
        results = match_revolut_payments(payments, [candidate])

        self.assertNotEqual(payments[0].payment_id, payments[1].payment_id)
        self.assertEqual([result.outcome for result in results], ["exact", "unmatched"])

    def test_amount_only_match_outside_ninety_days_requires_manual_review(self):
        csv_content = (
            "Completed Date,Amount,Currency,Amount in EUR\n"
            "2026-07-02,100.00,EUR,100.00\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payments.csv"
            path.write_text(csv_content, encoding="utf-8")
            payment = load_revolut_payments_csv(path)[0]

        candidate = RevolutMatchCandidate(
            candidate_id="old-invoice",
            recognition_date=date(2025, 12, 1),
            payment_date=None,
            reference="",
            amount_original=Decimal("100.00"),
            currency="EUR",
            amount_eur=Decimal("100.00"),
        )
        result = match_revolut_payments([payment], [candidate])[0]
        self.assertEqual(result.outcome, "unmatched")


if __name__ == "__main__":
    unittest.main()
