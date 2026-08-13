from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.tax_report_sequence import build_tax_report_sequence


class TaxReportSequenceTests(unittest.TestCase):
    def test_complete_sequence_finds_one_modelo130_pdf_per_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "M130 2T 2023 exampleei example.pdf")
            _touch(root / "MOD 130 3T 2023 exampleei example.pdf")
            _touch(root / "MOD 130 4T 2023 exampleei example.pdf")
            _touch(root / "MOD 303 2T 2023 exampleei example.pdf")

            rows = build_tax_report_sequence(
                tax_report_dir=root,
                start_year=2023,
                start_quarter=2,
                end_year=2023,
                end_quarter=4,
            )

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(by_period["2023-Q2"]["status"], "found")
        self.assertIn("M130 2T 2023", by_period["2023-Q2"]["matched_files"])
        self.assertEqual(by_period["2023-Q3"]["status"], "found")
        self.assertEqual(by_period["2023-Q4"]["status"], "found")
        self.assertEqual(by_period["sequence_verdict"]["status"], "complete")

    def test_missing_and_duplicate_periods_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "MOD 130 2T 2023 exampleei example.pdf")
            _touch(root / "M130 2T 2023 duplicate.pdf")

            rows = build_tax_report_sequence(
                tax_report_dir=root,
                start_year=2023,
                start_quarter=2,
                end_year=2023,
                end_quarter=3,
            )

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(by_period["2023-Q2"]["status"], "duplicate")
        self.assertEqual(by_period["2023-Q3"]["status"], "missing")
        self.assertEqual(by_period["sequence_verdict"]["status"], "missing_reports")

    def test_only_pdf_reports_count_and_spelled_modelo_name_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "M130 2T 2023 exampleei example.txt")
            _touch(root / "Modelo 130 3T 2023 exampleei example.pdf")

            rows = build_tax_report_sequence(
                tax_report_dir=root,
                start_year=2023,
                start_quarter=2,
                end_year=2023,
                end_quarter=3,
            )

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(by_period["2023-Q2"]["status"], "missing")
        self.assertEqual(by_period["2023-Q3"]["status"], "found")
        self.assertIn("Modelo 130 3T 2023", by_period["2023-Q3"]["matched_files"])

    def test_cli_writes_tax_report_sequence_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tax"
            root.mkdir()
            out_csv = Path(tmp) / "sequence.csv"
            out_md = Path(tmp) / "sequence.md"
            _touch(root / "M130 2T 2023 exampleei example.pdf")

            exit_code = main(
                [
                    "audit-tax-report-sequence",
                    "--tax-report-dir",
                    str(root),
                    "--start-year",
                    "2023",
                    "--start-quarter",
                    "2",
                    "--end-year",
                    "2023",
                    "--end-quarter",
                    "2",
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Modelo 130 Tax Report Sequence", markdown)
            self.assertIn("complete", markdown)


def _touch(path: Path) -> None:
    path.write_text("fixture", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
