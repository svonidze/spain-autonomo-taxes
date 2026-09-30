from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.ledger_projection import (
    build_ledger_projection,
    write_ledger_projection_markdown,
)


class LedgerProjectionTests(unittest.TestCase):
    def test_projection_marks_matches_with_unconfirmed_rows(self):
        history = (
            "year,quarter,target_casilla_02,target_casilla_02_delta\n"
            "2025,4,10.00,10.00\n"
            "2026,1,100.00,100.00\n"
            "2026,2,150.00,50.00\n"
        )
        ledger = (
            "xolo_url,xolo_id,recipient,type,number,date,amount_original,currency,gross_eur,vat_base_eur,"
            "irpf_deductible_eur,inclusion_quarter,include_in_modelo130,source_document_path,confidence,notes\n"
            "https://xolo.test/1,1,Supplier,Professional,INV1,2026-01-01,60.00,EUR,60.00,,60.00,2026-Q1,yes,,xolo_ui,\n"
            ",,Xolo amortization,Asset amortization,AMORT-Q1,2026-03-31,40.00,EUR,40.00,,40.00,2026-Q1,yes,,amortization_pending_xolo_confirmation,pending schedule\n"
            "https://xolo.test/2,2,Supplier,Professional,INV2,2026-04-01,50.00,EUR,50.00,,50.00,2026-Q2,yes,,xolo_ui,\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            ledger_path = tmp_path / "ledger.csv"
            md_path = tmp_path / "projection.md"
            history_path.write_text(history, encoding="utf-8")
            ledger_path.write_text(ledger, encoding="utf-8")

            rows = build_ledger_projection(history_path, ledger_path)
            write_ledger_projection_markdown(md_path, rows)
            markdown = md_path.read_text(encoding="utf-8")

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(by_period["2025-Q4"]["status"], "no_ledger_rows_for_period")
        self.assertEqual(by_period["2026-Q1"]["status"], "matches_target_with_unconfirmed_rows")
        self.assertEqual(by_period["2026-Q1"]["pending_or_inferred_delta_eur"], "40.00")
        self.assertIn("AMORT-Q1", by_period["2026-Q1"]["pending_or_inferred_rows"])
        self.assertEqual(by_period["2026-Q2"]["status"], "matches_target_with_unconfirmed_rows")
        self.assertEqual(by_period["2026-Q2"]["ledger_delta"], "50.00")
        self.assertIn("matches_target_with_unconfirmed_rows", markdown)


if __name__ == "__main__":
    unittest.main()
