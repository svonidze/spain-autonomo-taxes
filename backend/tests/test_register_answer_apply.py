from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.register_answer_apply import apply_register_answers
from autonomo_taxes.register_review import REGISTER_REVIEW_FIELDS


class RegisterAnswerApplyTests(unittest.TestCase):
    def test_applies_structured_answers_and_adds_adjustment_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            review = tmp_path / "review.csv"
            intake = tmp_path / "intake.csv"
            self._write_review(review)
            self._write_intake(intake)

            result = apply_register_answers(review, intake)

            by_xolo = {row["xolo_id"]: row for row in result.review_rows if row["xolo_id"]}
            self.assertEqual(by_xolo["1751445"]["review_status"], "confirmed")
            self.assertEqual(by_xolo["1751445"]["confirmed_amortization_eur"], "21.48")
            self.assertEqual(by_xolo["1751445"]["confirmed_basis"], "amortized")
            self.assertIn("answer_intake_rank=2", by_xolo["1751445"]["notes"])
            self.assertEqual(by_xolo["3099344"]["confirmed_included"], "no")
            self.assertEqual(by_xolo["3099344"]["review_status"], "answered")

            adjustments = [row for row in result.review_rows if row["row_kind"] == "xolo_submitted_adjustment"]
            self.assertEqual(len(adjustments), 1)
            self.assertEqual(adjustments[0]["number"], "answer-intake-1")
            self.assertEqual(adjustments[0]["confirmed_irpf_deductible_eur"], "21.48")

            statuses = [row["apply_status"] for row in result.report_rows]
            self.assertEqual(statuses, ["added_adjustment_row", "applied_to_existing_row", "applied_to_existing_row", "skipped_open_or_unstructured"])

    def test_cli_writes_applied_review_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            review = tmp_path / "review.csv"
            intake = tmp_path / "intake.csv"
            out_review = tmp_path / "applied.csv"
            out_report = tmp_path / "report.csv"
            out_md = tmp_path / "report.md"
            self._write_review(review)
            self._write_intake(intake)

            exit_code = main(
                [
                    "audit-register-answer-apply",
                    "--register-review",
                    str(review),
                    "--answer-intake",
                    str(intake),
                    "--out-review-csv",
                    str(out_review),
                    "--out-report-csv",
                    str(out_report),
                    "--out-report-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            with out_review.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(any(row["number"] == "answer-intake-1" for row in rows))
            report = out_md.read_text(encoding="utf-8")
            self.assertIn("Register Answer Apply Report", report)
            self.assertIn("applied_to_existing_row", report)
            self.assertIn("added_adjustment_row", report)

    def _write_review(self, path: Path) -> None:
        rows = [
            self._review_row(
                period="2023-Q2",
                priority="high",
                source_classification="asset_amortization_candidate",
                xolo_id="1751445",
                date="2023-06-30",
                recipient="Synthetic Party 001",
                number="2023-06-30",
                gross_eur="116.86",
                xolo_url="https://app.xolo.io/selfservice/expense/invoice/1751445/details?from=expense",
            ),
            self._review_row(
                period="2026-Q2",
                priority="high",
                source_classification="nearest_exclusion_candidate",
                xolo_id="3099344",
                date="2026-05-10",
                recipient="Synthetic Party 011",
                number="SYNTH-DOCUMENT-019",
                gross_eur="189.01",
                xolo_url="https://app.xolo.io/selfservice/expense/invoice/3099344/details?from=expense",
            ),
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=REGISTER_REVIEW_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_intake(self, path: Path) -> None:
        fieldnames = [
            "queue_rank",
            "period",
            "audit_priority",
            "decision_priority",
            "classification",
            "row_label",
            "amount_eur",
            "xolo_id",
            "xolo_url",
            "question",
            "required_evidence",
            "answer_status",
            "xolo_answer",
            "confirmed_included",
            "confirmed_irpf_deductible_eur",
            "confirmed_amortization_eur",
            "confirmed_basis",
            "confirmed_source",
            "application_target",
            "application_notes",
        ]
        rows = [
            {
                "queue_rank": "1",
                "period": "2023-Q2",
                "audit_priority": "P0",
                "decision_priority": "high",
                "classification": "missing_catch_up_or_reclassification",
                "amount_eur": "21.48",
                "answer_status": "confirmed",
                "confirmed_included": "yes",
                "confirmed_irpf_deductible_eur": "21.48",
                "confirmed_basis": "submitted adjustment",
                "confirmed_source": "Xolo register",
                "application_target": "register_review.new_adjustment_row",
                "application_notes": "Create adjustment row.",
            },
            {
                "queue_rank": "2",
                "period": "2023-Q2",
                "audit_priority": "P0",
                "decision_priority": "medium",
                "classification": "asset_amortization_candidate",
                "row_label": "2023-06-30 1751445 2023-06-30 Synthetic Party 001",
                "amount_eur": "116.86",
                "xolo_id": "1751445",
                "xolo_url": "https://app.xolo.io/selfservice/expense/invoice/1751445/details?from=expense",
                "answer_status": "confirmed",
                "confirmed_included": "yes",
                "confirmed_amortization_eur": "21.48",
                "confirmed_basis": "amortized",
                "confirmed_source": "Xolo asset schedule",
                "application_target": "register_review.confirmed_amortization_eur",
                "application_notes": "Asset schedule row.",
            },
            {
                "queue_rank": "47",
                "period": "2026-Q2",
                "audit_priority": "P2",
                "decision_priority": "high",
                "classification": "nearest_exclusion_candidate",
                "row_label": "2026-05-10 3099344 SYNTH-DOCUMENT-019 TGSS",
                "amount_eur": "189.01",
                "xolo_id": "3099344",
                "xolo_url": "https://app.xolo.io/selfservice/expense/invoice/3099344/details?from=expense",
                "answer_status": "answered",
                "confirmed_included": "no",
                "confirmed_basis": "excluded",
                "confirmed_source": "Xolo register",
                "application_target": "register_review.confirmed_included",
                "application_notes": "Exclusion row.",
            },
            {
                "queue_rank": "99",
                "period": "2026-Q2",
                "audit_priority": "P2",
                "decision_priority": "high",
                "classification": "nearest_exclusion_candidate",
                "xolo_id": "999",
                "answer_status": "open",
                "xolo_answer": "free text only",
                "application_target": "register_review.confirmed_included",
            },
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fieldnames})

    def _review_row(self, **overrides: str) -> dict[str, str]:
        row = {field: "" for field in REGISTER_REVIEW_FIELDS}
        row.update(
            {
                "review_status": "open",
                "row_kind": "xolo_expense",
                "currency": "EUR",
                "amount_original": overrides.get("gross_eur", ""),
                "base_eur": overrides.get("gross_eur", ""),
                "gross_minus_base_eur": "0.00",
                "candidate_model_amount_eur": overrides.get("gross_eur", ""),
                "hypothesis": "fixture",
                "confirmation_question": "fixture",
            }
        )
        row.update(overrides)
        return row


if __name__ == "__main__":
    unittest.main()
