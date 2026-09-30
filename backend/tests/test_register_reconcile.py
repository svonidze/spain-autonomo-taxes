from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.register_reconcile import (
    build_register_reconciliation,
    write_register_reconciliation_markdown,
)


class RegisterReconcileTests(unittest.TestCase):
    def test_matches_target_from_confirmed_amounts_and_exclusions(self):
        history = (
            "year,quarter,target_casilla_02_delta\n"
            "2026,2,100.00\n"
        )
        review = self._review_csv(
            [
                "2026-Q2,high,closed,xolo_expense,raw,1,2026-04-01,Supplier,Professional,INV1,EUR,100.00,100.00,100.00,0.00,100.00,h,q,yes,70.00,,gross,Xolo PDF,n,https://xolo.test/1",
                "2026-Q2,high,closed,xolo_expense,asset,2,2026-04-02,Apple,Hardware,ASSET,EUR,500.00,500.00,413.22,86.78,0.00,h,q,yes,,30.00,asset schedule,Xolo PDF,n,https://xolo.test/2",
                "2026-Q2,high,closed,xolo_expense,nearest,3,2026-04-03,TGSS,Multiple,EXCL,EUR,10.00,10.00,10.00,0.00,10.00,h,q,no,,,,Xolo PDF,n,https://xolo.test/3",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            review_path = tmp_path / "review.csv"
            md_path = tmp_path / "reconcile.md"
            history_path.write_text(history, encoding="utf-8")
            review_path.write_text(review, encoding="utf-8")

            rows = build_register_reconciliation(history_path, review_path)
            write_register_reconciliation_markdown(md_path, rows)
            markdown = md_path.read_text(encoding="utf-8")

        self.assertEqual(rows[0]["status"], "matches_target")
        self.assertEqual(rows[0]["confirmed_register_delta"], "100.00")
        self.assertEqual(rows[0]["confirmed_zero_row_count"], "1")
        self.assertEqual(rows[0]["open_total"], "0")
        self.assertIn("matches_target", markdown)

    def test_included_without_amount_blocks_reconciliation(self):
        history = (
            "year,quarter,target_casilla_02_delta\n"
            "2026,2,100.00\n"
        )
        review = self._review_csv(
            [
                "2026-Q2,high,open,xolo_expense,raw,1,2026-04-01,Supplier,Professional,INV1,EUR,100.00,100.00,100.00,0.00,100.00,h,q,yes,,,,,n,https://xolo.test/1",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            review_path = tmp_path / "review.csv"
            history_path.write_text(history, encoding="utf-8")
            review_path.write_text(review, encoding="utf-8")

            rows = build_register_reconciliation(history_path, review_path)

        self.assertEqual(rows[0]["status"], "included_rows_missing_amounts")
        self.assertIn("INV1", rows[0]["rows_needing_amount"])
        self.assertEqual(rows[0]["open_high"], "1")

    def _review_csv(self, rows: list[str]) -> str:
        header = (
            "period,priority,review_status,row_kind,source_classification,xolo_id,date,recipient,type,number,"
            "currency,amount_original,gross_eur,base_eur,gross_minus_base_eur,candidate_model_amount_eur,"
            "hypothesis,confirmation_question,confirmed_included,confirmed_irpf_deductible_eur,"
            "confirmed_amortization_eur,confirmed_basis,confirmed_source,notes,xolo_url\n"
        )
        return header + "\n".join(rows) + "\n"


if __name__ == "__main__":
    unittest.main()
