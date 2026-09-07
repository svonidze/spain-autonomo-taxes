from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.xolo_api_coverage import build_xolo_api_coverage


class XoloApiCoverageTests(unittest.TestCase):
    def test_complete_snapshot_records_counts_hashes_and_quarters(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_json, raw_csv = self._write_snapshot(Path(tmp), records_total=2)

            coverage = build_xolo_api_coverage(raw_json, raw_csv)

            summary = {row["metric"]: row for row in coverage.summary}
            self.assertEqual(coverage.status, "complete_raw_snapshot")
            self.assertEqual(summary["json_pages"]["value"], "2")
            self.assertEqual(summary["json_rows"]["value"], "2")
            self.assertEqual(summary["csv_rows"]["value"], "2")
            self.assertEqual(summary["expected_total_match"]["value"], "yes")
            self.assertEqual(summary["covers_activity_start"]["value"], "yes")
            self.assertEqual(summary["duplicate_xolo_ids"]["value"], "0")
            self.assertEqual([row["period"] for row in coverage.quarters], ["2023-Q2", "2026-Q3"])

    def test_attention_required_when_csv_and_json_counts_diverge(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_json, raw_csv = self._write_snapshot(Path(tmp), records_total=3)

            coverage = build_xolo_api_coverage(raw_json, raw_csv)

            self.assertEqual(coverage.status, "attention_required")
            self.assertIn("JSON page rows 2 do not match recordsFiltered/recordsTotal 3", coverage.warnings)

    def test_cli_writes_summary_quarter_counts_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw_json, raw_csv = self._write_snapshot(Path(tmp), records_total=2)
            out_csv = Path(tmp) / "coverage.csv"
            out_quarter_csv = Path(tmp) / "quarters.csv"
            out_md = Path(tmp) / "coverage.md"

            exit_code = main(
                [
                    "audit-xolo-api-coverage",
                    "--raw-json",
                    str(raw_json),
                    "--raw-csv",
                    str(raw_csv),
                    "--out-csv",
                    str(out_csv),
                    "--out-quarter-csv",
                    str(out_quarter_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            with out_csv.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["metric"], "status")
            self.assertEqual(rows[0]["value"], "complete_raw_snapshot")
            with out_quarter_csv.open(newline="", encoding="utf-8") as handle:
                quarters = list(csv.DictReader(handle))
            self.assertEqual(quarters[0]["period"], "2023-Q2")
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Xolo API Raw Export Coverage", markdown)
            self.assertIn("It does not prove Xolo's source books", markdown)

    def _write_snapshot(self, root: Path, records_total: int) -> tuple[Path, Path]:
        raw_json = root / "xolo.json"
        raw_json.write_text(
            json.dumps(
                {
                    "pages": [
                        {
                            "draw": 1,
                            "recordsTotal": records_total,
                            "recordsFiltered": records_total,
                            "data": [{"id": 1}],
                        },
                        {
                            "draw": 2,
                            "recordsTotal": records_total,
                            "recordsFiltered": records_total,
                            "data": [{"id": 2}],
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        raw_csv = root / "xolo.csv"
        with raw_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["xolo_id", "date", "recipient"])
            writer.writeheader()
            writer.writerow({"xolo_id": "1", "date": "2023-05-31", "recipient": "Xolo"})
            writer.writerow({"xolo_id": "2", "date": "2026-07-01", "recipient": "Xolo"})
        return raw_json, raw_csv


if __name__ == "__main__":
    unittest.main()
