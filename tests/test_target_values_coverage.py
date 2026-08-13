from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.target_values_coverage import build_target_values_coverage


class TargetValuesCoverageTests(unittest.TestCase):
    def test_all_filed_periods_with_history_and_submitted_xolo_compare_are_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_inputs(Path(tmp), comparison_status="matched")

            rows = build_target_values_coverage(**paths)

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(by_period["2023-Q2"]["status"], "matched")
        self.assertIn("01=1000.00", by_period["2023-Q2"]["target_values"])
        self.assertEqual(by_period["target_values_verdict"]["status"], "complete")

    def test_missing_history_field_fails_closed_before_compare_evidence_is_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_inputs(Path(tmp), omit_history_field="target_casilla_13")

            rows = build_target_values_coverage(**paths)

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(by_period["2023-Q2"]["status"], "missing_history_targets")
        self.assertEqual(by_period["2023-Q2"]["history_status"], "missing_fields")
        self.assertIn("target_casilla_13", by_period["2023-Q2"]["evidence"])
        self.assertEqual(by_period["target_values_verdict"]["status"], "attention_required")

    def test_xolo_compare_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_inputs(Path(tmp), comparison_status="amount_mismatch")

            rows = build_target_values_coverage(**paths)

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(by_period["2023-Q2"]["status"], "xolo_compare_not_matched")
        self.assertEqual(by_period["2023-Q2"]["xolo_compare_status"], "amount_mismatch")
        self.assertEqual(by_period["target_values_verdict"]["status"], "attention_required")

    def test_cli_writes_target_values_coverage_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_inputs(tmp_path)
            out_csv = tmp_path / "target_values.csv"
            out_md = tmp_path / "target_values.md"

            exit_code = main(
                [
                    "audit-target-values-coverage",
                    "--tax-report-sequence",
                    str(paths["tax_report_sequence_csv"]),
                    "--history-audit",
                    str(paths["history_audit_csv"]),
                    "--xolo-calculation-compare",
                    str(paths["xolo_calculation_compare_csv"]),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Modelo 130 Target Values Coverage", markdown)
            self.assertIn("complete", markdown)


def _write_inputs(
    tmp_path: Path,
    *,
    comparison_status: str = "matched",
    omit_history_field: str | None = None,
) -> dict[str, Path]:
    tax_report_sequence = tmp_path / "tax_report_sequence.csv"
    _write_csv(
        tax_report_sequence,
        ["period", "expected_label", "status", "matched_count", "matched_files", "evidence", "next_action"],
        [
            {
                "period": "2023-Q2",
                "status": "found",
                "matched_count": "1",
                "matched_files": "M130 2T 2023 exampleei example.pdf",
            },
            {
                "period": "sequence_verdict",
                "status": "complete",
                "matched_count": "1",
                "evidence": "Checked 1 expected Modelo 130 quarters.",
            },
        ],
    )

    history_fields = [
        "year",
        "quarter",
        "report",
        "target_casilla_01",
        "target_casilla_02",
        "target_casilla_02_delta",
        "target_casilla_07",
        "target_casilla_13",
        "target_casilla_19",
    ]
    history = {
        "year": "2023",
        "quarter": "2",
        "report": "M130 2T 2023 exampleei example.pdf",
        "target_casilla_01": "1000.00",
        "target_casilla_02": "100.00",
        "target_casilla_02_delta": "100.00",
        "target_casilla_07": "180.00",
        "target_casilla_13": "0.00",
        "target_casilla_19": "180.00",
    }
    if omit_history_field:
        history[omit_history_field] = ""
    history_audit = tmp_path / "history_audit.csv"
    _write_csv(history_audit, history_fields, [history])

    xolo_calculation_compare = tmp_path / "xolo_calculation_compare.csv"
    _write_csv(
        xolo_calculation_compare,
        [
            "period",
            "xolo_status",
            "comparison_status",
            "xolo_filename",
            "income_ytd_diff",
            "expenses_ytd_diff",
            "payable_diff",
        ],
        [
            {
                "period": "2023-Q2",
                "xolo_status": "submitted",
                "comparison_status": comparison_status,
                "xolo_filename": "M130 2T 2023 exampleei example.pdf",
                "income_ytd_diff": "0.00",
                "expenses_ytd_diff": "0.00",
                "payable_diff": "0.00",
            }
        ],
    )
    return {
        "tax_report_sequence_csv": tax_report_sequence,
        "history_audit_csv": history_audit,
        "xolo_calculation_compare_csv": xolo_calculation_compare,
    }


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


if __name__ == "__main__":
    unittest.main()
