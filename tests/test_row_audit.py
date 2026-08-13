from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.row_audit import build_row_audit, write_row_audit_markdown


class RowAuditTests(unittest.TestCase):
    def test_classifies_asset_and_nearest_exclusion_rows(self):
        history = (
            "year,quarter,report,derived_income_usd_fx\n"
            "2026,2,M130.pdf,0.90\n"
        )
        candidate = (
            "year,quarter,period,target_casilla_02_delta,raw_non_asset_gross_delta,"
            "candidate_amortization_delta,candidate_model_delta,candidate_model_minus_target_delta,"
            "candidate_amortization_ytd,ytd_residual_after_candidate,nearest_excluded_subset_eur,"
            "nearest_excluded_subset_error_eur,nearest_excluded_subset_rows,interpretation\n"
            "2026,2,2026-Q2,100.00,200.00,22.75,222.75,122.75,22.75,122.75,122.50,0.25,row,row exclusions\n"
        )
        raw = (
            "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,"
            "gross_amount,match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,irpf_percentage,status\n"
            "https://xolo.test/1,1,Supplier,Professional expenses,INV1,2026-04-01,,EUR 122.50,122.50,EUR,122.50,122.50,100.00,22.50,21,0,0,UNPAID\n"
            "https://xolo.test/2,2,Apple Retail Spain,Computer hardware & software,SYNTH-DOCUMENT-031,2026-04-08,,EUR 1994.00,1994.00,EUR,1994.00,1994.00,1647.93,346.07,21,0,0,UNPAID\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            candidate_path = tmp_path / "candidate.csv"
            raw_path = tmp_path / "raw.csv"
            md_path = tmp_path / "rows.md"
            history_path.write_text(history, encoding="utf-8")
            candidate_path.write_text(candidate, encoding="utf-8")
            raw_path.write_text(raw, encoding="utf-8")

            rows = build_row_audit(history_path, candidate_path, raw_path)
            write_row_audit_markdown(md_path, rows)
            markdown = md_path.read_text(encoding="utf-8")

        by_number = {row["number"]: row for row in rows if row["row_kind"] == "xolo_expense"}
        self.assertEqual(by_number["INV1"]["classification"], "nearest_exclusion_candidate")
        self.assertEqual(by_number["INV1"]["xolo_id"], "1")
        self.assertEqual(by_number["INV1"]["gross_minus_base_eur"], "22.50")
        self.assertIn("VAT-basis confirmation", by_number["INV1"]["notes"])
        self.assertEqual(by_number["SYNTH-DOCUMENT-031"]["classification"], "asset_amortization_candidate")
        self.assertIn("SYNTH-DOCUMENT-031", markdown)

    def test_negative_gap_creates_synthetic_catch_up_row(self):
        history = (
            "year,quarter,report,derived_income_usd_fx\n"
            "2024,3,M130.pdf,\n"
        )
        candidate = (
            "year,quarter,period,target_casilla_02_delta,raw_non_asset_gross_delta,"
            "candidate_amortization_delta,candidate_model_delta,candidate_model_minus_target_delta,"
            "candidate_amortization_ytd,ytd_residual_after_candidate,nearest_excluded_subset_eur,"
            "nearest_excluded_subset_error_eur,nearest_excluded_subset_rows,interpretation\n"
            "2024,3,2024-Q3,400.00,300.00,20.00,320.00,-80.00,20.00,0.00,0.00,0.00,,catch-up\n"
        )
        raw = (
            "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,"
            "gross_amount,match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,irpf_percentage,status\n"
            "https://xolo.test/1,1,Supplier,Professional expenses,INV1,2024-07-01,,EUR 300.00,300.00,EUR,300.00,300.00,300.00,0,,0,0,UNPAID\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            candidate_path = tmp_path / "candidate.csv"
            raw_path = tmp_path / "raw.csv"
            history_path.write_text(history, encoding="utf-8")
            candidate_path.write_text(candidate, encoding="utf-8")
            raw_path.write_text(raw, encoding="utf-8")

            rows = build_row_audit(history_path, candidate_path, raw_path)

        synthetic = [row for row in rows if row["row_kind"] == "synthetic_gap"]
        self.assertEqual(len(synthetic), 1)
        self.assertEqual(synthetic[0]["classification"], "missing_catch_up_or_reclassification")
        self.assertEqual(synthetic[0]["gross_eur"], "80.00")


if __name__ == "__main__":
    unittest.main()
