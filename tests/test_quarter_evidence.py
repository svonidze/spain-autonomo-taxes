from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.quarter_evidence import (
    build_quarter_evidence,
    write_quarter_evidence_markdown,
)


class QuarterEvidenceTests(unittest.TestCase):
    def test_builds_period_package_with_rows_questions_and_status(self):
        history = (
            "year,quarter,report,target_casilla_01,target_casilla_02,target_casilla_02_delta,"
            "target_casilla_19,derived_income_usd_fx\n"
            "2026,2,M130.pdf,36770.89,10280.23,4843.29,2639.12,0.90\n"
        )
        candidate = (
            "year,quarter,period,scenario,target_casilla_02_delta,raw_non_asset_gross_delta,"
            "candidate_amortization_delta,candidate_model_delta,candidate_model_minus_target_delta,"
            "candidate_amortization_ytd,ytd_residual_after_candidate,nearest_excluded_subset_eur,"
            "nearest_excluded_subset_error_eur,nearest_excluded_subset_rows,interpretation\n"
            "2026,2,2026-Q2,test,4843.29,4796.36,235.29,5031.65,188.36,"
            "374.23,188.36,189.01,0.65,2026-05-10 SYNTH-DOCUMENT-019 189.01,row exclusions\n"
        )
        raw = (
            "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,"
            "gross_amount,match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,irpf_percentage,status\n"
            ",1,Namecheap,Professional expenses,00007,2026-06-04,,$100.00,100.00,USD,100.00,100.00,100.00,0,,0,0,UNPAID\n"
            ',2,Apple Retail Spain,Computer hardware & software,SYNTH-DOCUMENT-031,2026-04-08,,"EUR 1994.00",1994.00,EUR,1994.00,1994.00,1647.93,346.07,21,0,0,UNPAID\n'
        )
        questions = (
            "status,priority,period,topic,question,why_it_matters,related_rows,evidence,owner,answer\n"
            "open,high,2023-Q2..2026-Q2,asset amortization schedule,q,w,r,e,Xolo,\n"
            "open,high,2026-Q2,TGSS debt row inclusion conflict,q,w,r,e,Xolo,\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            candidate_path = tmp_path / "candidate.csv"
            raw_path = tmp_path / "raw.csv"
            questions_path = tmp_path / "questions.csv"
            md_path = tmp_path / "evidence.md"
            history_path.write_text(history, encoding="utf-8")
            candidate_path.write_text(candidate, encoding="utf-8")
            raw_path.write_text(raw, encoding="utf-8")
            questions_path.write_text(questions, encoding="utf-8")

            rows = build_quarter_evidence(history_path, candidate_path, raw_path, questions_path)
            write_quarter_evidence_markdown(md_path, rows)
            markdown = md_path.read_text(encoding="utf-8")

        self.assertEqual(rows[0]["status"], "needs_exclusion_or_basis_reduction")
        self.assertIn("00007 Namecheap 90.00", rows[0]["quarter_non_asset_rows"])
        self.assertIn("SYNTH-DOCUMENT-031 Apple Retail Spain 1994.00 base 1647.93", rows[0]["quarter_asset_candidate_rows"])
        self.assertIn("high:asset amortization schedule", rows[0]["open_question_topics"])
        self.assertIn("high:TGSS debt row inclusion conflict", rows[0]["open_question_topics"])
        self.assertIn("Get Xolo row-level exclusions", rows[0]["recommended_next_evidence"])
        self.assertIn("## 2026-Q2", markdown)
        self.assertIn("2026-05-10 SYNTH-DOCUMENT-019 189.01", markdown)


if __name__ == "__main__":
    unittest.main()
