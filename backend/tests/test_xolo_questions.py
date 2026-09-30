from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.xolo_questions import build_xolo_questions


class XoloQuestionsTests(unittest.TestCase):
    def test_questions_include_q2_2026_tgss_conflict(self):
        candidate = self._candidate_csv(
            [
                {
                    "year": "2026",
                    "quarter": "2",
                    "period": "2026-Q2",
                    "target_casilla_02_delta": "4843.29",
                    "raw_non_asset_gross_delta": "4796.36",
                    "candidate_amortization_delta": "235.29",
                    "candidate_model_minus_target_delta": "188.36",
                    "nearest_excluded_subset_eur": "189.01",
                    "nearest_excluded_subset_error_eur": "0.65",
                    "nearest_excluded_subset_rows": "2026-05-10 SYNTH-DOCUMENT-019 189.01",
                }
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidate.csv"
            path.write_text(candidate, encoding="utf-8")

            questions = build_xolo_questions(path)

        conflict = [row for row in questions if row["topic"] == "TGSS debt row inclusion conflict"]
        self.assertEqual(len(conflict), 1)
        self.assertEqual(conflict[0]["priority"], "high")
        self.assertIn("SYNTH-DOCUMENT-019", conflict[0]["question"])

    def test_questions_include_negative_catchup_quarter(self):
        candidate = self._candidate_csv(
            [
                {
                    "year": "2024",
                    "quarter": "3",
                    "period": "2024-Q3",
                    "target_casilla_02_delta": "4008.94",
                    "raw_non_asset_gross_delta": "3618.20",
                    "candidate_amortization_delta": "55.31",
                    "candidate_model_minus_target_delta": "-335.43",
                    "nearest_excluded_subset_eur": "0.00",
                    "nearest_excluded_subset_error_eur": "0.00",
                    "nearest_excluded_subset_rows": "",
                }
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidate.csv"
            path.write_text(candidate, encoding="utf-8")

            questions = build_xolo_questions(path)

        catchup = [row for row in questions if row["period"] == "2024-Q3" and row["topic"] == "catch-up or reclassification"]
        self.assertEqual(len(catchup), 1)
        self.assertEqual(catchup[0]["priority"], "high")
        self.assertIn("335,43", catchup[0]["question"])

    def _candidate_csv(self, rows: list[dict[str, str]]) -> str:
        header = [
            "year",
            "quarter",
            "period",
            "scenario",
            "target_casilla_02_delta",
            "raw_non_asset_gross_delta",
            "candidate_amortization_delta",
            "candidate_model_delta",
            "candidate_model_minus_target_delta",
            "candidate_amortization_ytd",
            "ytd_residual_after_candidate",
            "nearest_excluded_subset_eur",
            "nearest_excluded_subset_error_eur",
            "nearest_excluded_subset_rows",
            "interpretation",
        ]
        lines = [",".join(header)]
        for row in rows:
            lines.append(",".join(row.get(field, "") for field in header))
        return "\n".join(lines) + "\n"


if __name__ == "__main__":
    unittest.main()
