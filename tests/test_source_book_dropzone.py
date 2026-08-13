from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.source_book_dropzone import build_source_book_dropzone_rows


class SourceBookDropzoneTests(unittest.TestCase):
    def test_builds_expected_dropzone_rows_from_response_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            response_check = tmp_path / "response.csv"
            _write_response_check(response_check)

            rows = build_source_book_dropzone_rows(
                source_book_response_check_csv=response_check,
                response_root=tmp_path / "evidence" / "xolo-source-books",
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(len(rows), 4)
        self.assertEqual(
            by_key[("ingresos_book", "2023")]["suggested_filename"],
            "libro_registro_ingresos_2023.xlsx",
        )
        self.assertEqual(
            by_key[("gastos_book", "2023")]["suggested_filename"],
            "libro_registro_gastos_2023.xlsx",
        )
        self.assertIn("irpf_deductible_eur", by_key[("gastos_book", "2023")]["required_column_groups"])
        self.assertIn("book + gastos", by_key[("gastos_book", "2023")]["accepted_filename_pattern"])
        self.assertEqual(
            by_key[("bienes_inversion_book", "2023")]["suggested_filename"],
            "libro_registro_bienes_inversion_2023.xlsx",
        )
        self.assertEqual(
            by_key[("provisiones_suplidos_book", "2023")]["suggested_filename"],
            "libro_registro_provisiones_suplidos_2023.xlsx",
        )
        self.assertIn("real extension", by_key[("provisiones_suplidos_book", "2023")]["next_action"])

    def test_found_deliverables_are_counted_and_marked_for_content_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            response_check = tmp_path / "response.csv"
            out_md = tmp_path / "dropzone.md"
            _write_response_check(response_check, compras_status="found")

            rows = build_source_book_dropzone_rows(
                source_book_response_check_csv=response_check,
                response_root=tmp_path / "evidence" / "xolo-source-books",
            )
            from autonomo_taxes.source_book_dropzone import write_source_book_dropzone_markdown

            write_source_book_dropzone_markdown(out_md, rows, response_root=tmp_path / "evidence" / "xolo-source-books")
            markdown = out_md.read_text(encoding="utf-8")

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "found")
        self.assertIn("Run content check", by_key[("gastos_book", "2023")]["next_action"])
        self.assertIn("Already matched by filename: `1`", markdown)
        self.assertIn("Run content check; filename matched", markdown)

    def test_cli_writes_dropzone_reports_and_readme(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            response_check = tmp_path / "response.csv"
            out_csv = tmp_path / "dropzone.csv"
            out_md = tmp_path / "dropzone.md"
            readme = tmp_path / "evidence" / "xolo-source-books" / "README.md"
            _write_response_check(response_check)

            exit_code = main(
                [
                    "audit-source-book-dropzone",
                    "--response-root",
                    str(tmp_path / "evidence" / "xolo-source-books"),
                    "--runs-root",
                    str(tmp_path / "private-runs"),
                    "--source-book-response-check",
                    str(response_check),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                    "--dropzone-readme",
                    str(readme),
                ]
            )

            rows = list(csv.DictReader(out_csv.open("r", newline="", encoding="utf-8")))
            markdown = out_md.read_text(encoding="utf-8")
            readme_markdown = readme.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(rows), 4)
        self.assertIn("Xolo Source-Book Dropzone Guide", markdown)
        self.assertIn("After Files Arrive", readme_markdown)
        self.assertIn("audit-source-book-refresh", readme_markdown)
        self.assertIn("audit-source-book-reconcile", readme_markdown)
        self.assertIn("audit-quarter-acceptance", readme_markdown)
        self.assertIn("--source-book-reconciliation", readme_markdown)
        self.assertIn("audit-goal-status", readme_markdown)
        self.assertIn("accepted_from_source_books", readme_markdown)
        self.assertIn("A quarter is not closed merely because a file exists", readme_markdown)
        self.assertIn(str((tmp_path / "evidence" / "xolo-source-books").resolve()), readme_markdown)
        self.assertIn(str((tmp_path / "private-runs").resolve()), readme_markdown)


def _write_response_check(path: Path, *, compras_status: str = "missing") -> None:
    fieldnames = ["check", "scope", "status", "matched_count", "matched_files", "required_for", "next_action"]
    rows = [
        {
            "check": "ingresos_book",
            "scope": "2023",
            "status": "missing",
            "required_for": "2023-Q2, 2023-Q3, 2023-Q4",
        },
        {
            "check": "gastos_book",
            "scope": "2023",
            "status": compras_status,
            "matched_count": "1" if compras_status == "found" else "0",
            "matched_files": "libro registro compras gastos 2023.xlsx" if compras_status == "found" else "",
            "required_for": "2023-Q2, 2023-Q3, 2023-Q4",
        },
        {
            "check": "bienes_inversion_book",
            "scope": "2023",
            "status": "missing",
            "required_for": "2023-Q2, 2023-Q3, 2023-Q4",
        },
        {
            "check": "provisiones_suplidos_book",
            "scope": "2023",
            "status": "missing",
            "required_for": "2023-Q2, 2023-Q3, 2023-Q4",
        },
        {
            "check": "package_verdict",
            "scope": "2023-Q2..2023-Q4",
            "status": "missing_required_deliverables",
            "required_for": "all quarters in quarter acceptance matrix",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


if __name__ == "__main__":
    unittest.main()
