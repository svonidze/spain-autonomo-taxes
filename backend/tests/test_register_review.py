from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.register_review import (
    build_register_review_template,
    write_register_review_markdown,
)


class RegisterReviewTests(unittest.TestCase):
    def test_template_prioritizes_confirmation_rows_without_confirming_them(self):
        row_audit = (
            "year,quarter,period,row_kind,classification,xolo_id,date,recipient,type,number,xolo_status,"
            "currency,amount_original,gross_eur,base_eur,gross_minus_base_eur,"
            "candidate_model_minus_target_delta,nearest_subset_total_eur,nearest_subset_error_eur,notes,xolo_url\n"
            "2026,2,2026-Q2,xolo_expense,asset_amortization_candidate,3019975,2026-04-08,Apple Retail Spain,Computer hardware & software,SYNTH-DOCUMENT-031,UNPAID,"
            "EUR,1994.00,1994.00,1647.93,346.07,188.36,189.01,0.65,asset row,https://xolo.test/asset\n"
            "2026,2,2026-Q2,xolo_expense,nearest_exclusion_candidate,3099344,2026-05-10,TGSS,Multiple,SYNTH-DOCUMENT-019,UNPAID,"
            "EUR,189.01,189.01,189.01,0.00,188.36,189.01,0.65,nearest row,https://xolo.test/tgss\n"
            "2026,2,2026-Q2,xolo_expense,raw_non_asset_candidate_model,3141770,2026-06-29,Namecheap,Professional expenses,SYNTH-DOCUMENT-030,UNPAID,"
            "USD,75.98,65.10,65.10,0.00,188.36,189.01,0.65,fx row,https://xolo.test/namecheap\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audit_path = tmp_path / "row_audit.csv"
            md_path = tmp_path / "review.md"
            audit_path.write_text(row_audit, encoding="utf-8")

            rows = build_register_review_template(audit_path)
            write_register_review_markdown(md_path, rows)
            markdown = md_path.read_text(encoding="utf-8")

        by_number = {row["number"]: row for row in rows}
        self.assertEqual(by_number["SYNTH-DOCUMENT-031"]["priority"], "high")
        self.assertEqual(by_number["SYNTH-DOCUMENT-031"]["candidate_model_amount_eur"], "0.00")
        self.assertIn("Confirm acquisition basis", by_number["SYNTH-DOCUMENT-031"]["confirmation_question"])
        self.assertEqual(by_number["SYNTH-DOCUMENT-019"]["priority"], "high")
        self.assertIn("excluded, netted, reversed", by_number["SYNTH-DOCUMENT-019"]["confirmation_question"])
        self.assertEqual(by_number["SYNTH-DOCUMENT-030"]["priority"], "medium")
        self.assertIn("Confirm FX source", by_number["SYNTH-DOCUMENT-030"]["confirmation_question"])
        self.assertEqual(by_number["SYNTH-DOCUMENT-030"]["confirmed_included"], "")
        self.assertEqual(by_number["SYNTH-DOCUMENT-030"]["confirmed_irpf_deductible_eur"], "")
        self.assertIn("[3019975](https://xolo.test/asset)", markdown)


if __name__ == "__main__":
    unittest.main()
