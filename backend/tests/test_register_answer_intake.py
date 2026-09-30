from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.register_answer_intake import build_register_answer_intake


class RegisterAnswerIntakeTests(unittest.TestCase):
    def test_builds_fillable_intake_from_blocking_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = Path(tmp) / "queue.csv"
            self._write_queue(queue)

            rows = build_register_answer_intake(queue)

            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[0]["answer_status"], "open")
            self.assertEqual(rows[0]["application_target"], "register_review.new_adjustment_row")
            self.assertEqual(rows[1]["xolo_id"], "1751445")
            self.assertEqual(rows[1]["application_target"], "register_review.confirmed_amortization_eur")
            self.assertEqual(rows[1]["source_book_type"], "")
            self.assertEqual(rows[1]["fx_rate"], "")
            self.assertEqual(rows[1]["casilla02_ytd"], "")
            self.assertIn("direct expense", rows[1]["application_notes"])
            self.assertEqual(rows[2]["application_target"], "register_review.confirmed_included")
            self.assertEqual(rows[3]["application_target"], "register_review.residual_adjustment")

    def test_cli_writes_csv_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = Path(tmp) / "queue.csv"
            out_csv = Path(tmp) / "intake.csv"
            out_md = Path(tmp) / "intake.md"
            self._write_queue(queue)

            exit_code = main(
                [
                    "audit-register-answer-intake",
                    "--register-blocking-queue",
                    str(queue),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            with out_csv.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["queue_rank"], "1")
            self.assertEqual(rows[1]["confirmed_amortization_eur"], "")
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Xolo Register Answer Intake Template", markdown)
            self.assertIn("Application targets", markdown)
            self.assertIn("source_book_line_id", markdown)
            self.assertIn("casilla02_ytd", markdown)
            self.assertIn("synthetic adjustment", markdown)

    def _write_queue(self, path: Path) -> None:
        fieldnames = [
            "queue_rank",
            "period",
            "audit_priority",
            "acceptance_status",
            "register_status",
            "open_high",
            "open_total",
            "amortization_chain_signal",
            "decision_priority",
            "decision_kind",
            "classification",
            "row_label",
            "amount_eur",
            "question",
            "required_evidence",
            "xolo_url",
        ]
        rows = [
            {
                "queue_rank": "1",
                "period": "2023-Q2",
                "audit_priority": "P0",
                "decision_priority": "high",
                "classification": "missing_catch_up_or_reclassification",
                "row_label": "",
                "amount_eur": "21.48",
                "question": "Which adjustment explains the gap?",
                "required_evidence": "submitted register",
                "xolo_url": "",
            },
            {
                "queue_rank": "2",
                "period": "2023-Q2",
                "audit_priority": "P0",
                "decision_priority": "medium",
                "classification": "asset_amortization_candidate",
                "row_label": "2023-06-30 1751445 Synthetic Party 001",
                "amount_eur": "116.86",
                "question": "Direct expense or asset?",
                "required_evidence": "asset schedule",
                "xolo_url": "https://app.xolo.io/selfservice/expense/invoice/1751445/details?from=expense",
            },
            {
                "queue_rank": "3",
                "period": "2026-Q2",
                "audit_priority": "P2",
                "decision_priority": "high",
                "classification": "nearest_exclusion_candidate",
                "row_label": "2026-05-10 3099344 SYNTH-DOCUMENT-019 TGSS",
                "amount_eur": "189.01",
                "question": "Included or excluded?",
                "required_evidence": "submitted register",
                "xolo_url": "https://app.xolo.io/selfservice/expense/invoice/3099344/details?from=expense",
            },
            {
                "queue_rank": "4",
                "period": "2023-Q3",
                "audit_priority": "P1",
                "decision_priority": "high",
                "classification": "unresolved_after_nearest_subset",
                "row_label": "",
                "amount_eur": "12.84",
                "question": "Which residual explains this?",
                "required_evidence": "submitted register",
                "xolo_url": "",
            },
        ]
        normalized = []
        for row in rows:
            normalized.append({field: row.get(field, "") for field in fieldnames})
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(normalized)


if __name__ == "__main__":
    unittest.main()
