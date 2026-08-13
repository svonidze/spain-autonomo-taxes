from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.register_answer_status import build_register_answer_status


class RegisterAnswerStatusTests(unittest.TestCase):
    def test_classifies_answer_intake_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            intake = Path(tmp) / "intake.csv"
            self._write_intake(intake)

            rows = build_register_answer_status(intake)
            by_rank = {row["queue_rank"]: row for row in rows}

            self.assertEqual(by_rank["1"]["readiness_status"], "open")
            self.assertEqual(by_rank["2"]["readiness_status"], "free_text_only_unstructured")
            self.assertEqual(by_rank["3"]["readiness_status"], "structured_incomplete")
            self.assertIn("confirmed_source", by_rank["3"]["missing_fields"])
            self.assertIn("confirmed_irpf_deductible_eur_or_confirmed_amortization_eur", by_rank["3"]["missing_fields"])
            self.assertEqual(by_rank["4"]["readiness_status"], "ready_to_apply")
            self.assertEqual(by_rank["5"]["readiness_status"], "ready_to_apply")

    def test_cli_writes_status_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            intake = tmp_path / "intake.csv"
            out_csv = tmp_path / "status.csv"
            out_md = tmp_path / "status.md"
            self._write_intake(intake)

            exit_code = main(
                [
                    "audit-register-answer-status",
                    "--answer-intake",
                    str(intake),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            with out_csv.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 5)
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Register Answer Status", markdown)
            self.assertIn("ready_to_apply", markdown)
            self.assertIn("free_text_only_unstructured", markdown)

    def _write_intake(self, path: Path) -> None:
        fieldnames = [
            "queue_rank",
            "period",
            "audit_priority",
            "classification",
            "xolo_id",
            "row_label",
            "application_target",
            "answer_status",
            "xolo_answer",
            "confirmed_included",
            "confirmed_irpf_deductible_eur",
            "confirmed_amortization_eur",
            "confirmed_basis",
            "confirmed_source",
        ]
        rows = [
            {
                "queue_rank": "1",
                "period": "2023-Q2",
                "audit_priority": "P0",
                "classification": "missing_catch_up_or_reclassification",
                "application_target": "register_review.new_adjustment_row",
                "answer_status": "open",
            },
            {
                "queue_rank": "2",
                "period": "2023-Q2",
                "audit_priority": "P0",
                "classification": "asset_amortization_candidate",
                "application_target": "register_review.confirmed_amortization_eur",
                "answer_status": "answered",
                "xolo_answer": "It was amortized.",
            },
            {
                "queue_rank": "3",
                "period": "2023-Q2",
                "audit_priority": "P0",
                "classification": "asset_amortization_candidate",
                "application_target": "register_review.confirmed_amortization_eur",
                "answer_status": "answered",
                "confirmed_included": "yes",
                "confirmed_basis": "amortized",
            },
            {
                "queue_rank": "4",
                "period": "2026-Q2",
                "audit_priority": "P2",
                "classification": "nearest_exclusion_candidate",
                "application_target": "register_review.confirmed_included",
                "answer_status": "confirmed",
                "confirmed_included": "no",
                "confirmed_basis": "excluded",
                "confirmed_source": "Xolo register",
            },
            {
                "queue_rank": "5",
                "period": "2024-Q3",
                "audit_priority": "P0",
                "classification": "missing_catch_up_or_reclassification",
                "application_target": "register_review.new_adjustment_row",
                "answer_status": "confirmed",
                "confirmed_included": "yes",
                "confirmed_irpf_deductible_eur": "335.43",
                "confirmed_basis": "submitted adjustment",
                "confirmed_source": "Xolo register",
            },
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fieldnames})


if __name__ == "__main__":
    unittest.main()
