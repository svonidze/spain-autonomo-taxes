from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.register_answer_import import import_register_answer_sheet_json
from autonomo_taxes.register_answer_intake import REGISTER_ANSWER_INTAKE_FIELDS


class RegisterAnswerImportTests(unittest.TestCase):
    def test_imports_sheet_json_to_answer_intake_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            sheet_json = Path(tmp) / "sheet.json"
            self._write_sheet_json(
                sheet_json,
                [
                    self._row("1", xolo_answer="Confirmed", confirmed_included="yes"),
                    self._short_row("2"),
                    ["" for _ in REGISTER_ANSWER_INTAKE_FIELDS],
                ],
            )

            rows = import_register_answer_sheet_json(sheet_json)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["queue_rank"], "1")
        self.assertEqual(rows[0]["xolo_answer"], "Confirmed")
        self.assertEqual(rows[0]["confirmed_included"], "yes")
        self.assertEqual(rows[1]["queue_rank"], "2")
        self.assertEqual(rows[1]["confirmed_source"], "")

    def test_rejects_missing_required_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            sheet_json = Path(tmp) / "sheet.json"
            sheet_json.write_text(json.dumps([["queue_rank"], ["1"]]), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing required"):
                import_register_answer_sheet_json(sheet_json)

    def test_rejects_duplicate_queue_ranks(self):
        with tempfile.TemporaryDirectory() as tmp:
            sheet_json = Path(tmp) / "sheet.json"
            self._write_sheet_json(sheet_json, [self._row("1"), self._row("1")])

            with self.assertRaisesRegex(ValueError, "duplicate queue_rank"):
                import_register_answer_sheet_json(sheet_json)

    def test_cli_writes_imported_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sheet_json = tmp_path / "sheet.json"
            out_csv = tmp_path / "imported.csv"
            self._write_sheet_json(sheet_json, [self._row("1", confirmed_basis="gross")])

            exit_code = main(
                [
                    "audit-register-answer-import",
                    "--sheet-json",
                    str(sheet_json),
                    "--out-csv",
                    str(out_csv),
                ]
            )

            self.assertEqual(exit_code, 0)
            with out_csv.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["queue_rank"], "1")
            self.assertEqual(rows[0]["confirmed_basis"], "gross")

    def _write_sheet_json(self, path: Path, rows: list[list[str]]) -> None:
        values = [REGISTER_ANSWER_INTAKE_FIELDS, *rows]
        path.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")

    def _row(self, rank: str, **overrides: str) -> list[str]:
        row = {field: "" for field in REGISTER_ANSWER_INTAKE_FIELDS}
        row.update(
            {
                "queue_rank": rank,
                "period": "2023-Q2",
                "audit_priority": "P0",
                "classification": "asset_amortization_candidate",
                "answer_status": "open",
                "application_target": "register_review.confirmed_amortization_eur",
            }
        )
        row.update(overrides)
        return [row[field] for field in REGISTER_ANSWER_INTAKE_FIELDS]

    def _short_row(self, rank: str) -> list[str]:
        full = self._row(rank)
        return full[: REGISTER_ANSWER_INTAKE_FIELDS.index("confirmed_source")]


if __name__ == "__main__":
    unittest.main()
