from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.drive_archive_index import (
    build_document_index,
    build_source_books_index,
    build_xolo_correspondence_index,
    load_drive_map,
)


class DriveArchiveIndexTests(unittest.TestCase):
    def test_document_index_covers_existing_export_source_books_and_audit_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xolo_root = root / "Xolo export"
            source_books = root / "source_books"
            archive = root / "Xolo evidence archive"
            runs = root / "runs"
            (xolo_root / "TAX_REPORT").mkdir(parents=True)
            (xolo_root / "EXPENSE").mkdir(parents=True)
            (xolo_root / ".omc" / "state").mkdir(parents=True)
            source_books.mkdir()
            (archive / "expense_evidence" / "2026-Q2").mkdir(parents=True)
            (archive / "tax_reports" / "2026").mkdir(parents=True)
            runs.mkdir()
            tax_pdf = xolo_root / "TAX_REPORT" / "MOD 130 2T 2026 exampleei example.pdf"
            expense_pdf = xolo_root / "EXPENSE" / "Xolo_INV.pdf"
            runtime_file = xolo_root / ".omc" / "state" / "runtime.json"
            source_book = source_books / "Libros_contables_exampleei_example_2026.xlsx"
            audit_csv = runs / "xolo_source_book_rows.csv"
            tax_pdf.write_bytes(b"pdf")
            expense_pdf.write_bytes(b"expense")
            runtime_file.write_text("{}", encoding="utf-8")
            source_book.write_bytes(b"xlsx")
            recovered_expense = archive / "expense_evidence" / "2026-Q2" / "invoice-REC-1.pdf"
            recovered_report = archive / "tax_reports" / "2026" / "MOD 303 2T 2026 exampleei example.pdf"
            recovered_expense.write_bytes(b"recovered-expense")
            recovered_report.write_bytes(b"recovered-report")
            audit_csv.write_text("a,b\n1,2\n", encoding="utf-8")
            drive_map = _drive_map(
                root / "drive_map.csv",
                [
                    {
                        "local_source_path": str(source_book),
                        "drive_relative_path": "Xolo evidence archive/source_books/Libros_contables_exampleei_example_2026.xlsx",
                        "drive_url": "https://drive/source-book",
                        "drive_id": "source-book-id",
                    },
                    {
                        "local_source_path": str(audit_csv),
                        "drive_relative_path": "Xolo evidence archive/audit_outputs/modelo130/xolo_source_book_rows.csv",
                        "drive_url": "https://drive/audit",
                        "drive_id": "audit-id",
                    },
                ],
            )

            rows = build_document_index(
                xolo_root=xolo_root,
                source_books_root=source_books,
                runs_root=runs,
                archive_root=archive,
                drive_map=load_drive_map(drive_map),
                xolo_export_folder_url="https://drive/xolo-export",
                archive_folder_url="https://drive/archive",
                indexed_at="2026-07-13",
            )

        by_name = {row["original_name"]: row for row in rows}
        self.assertNotIn(runtime_file.name, by_name)
        self.assertEqual(by_name[tax_pdf.name]["period"], "2026-Q2")
        self.assertEqual(by_name[tax_pdf.name]["drive_url"], "https://drive/xolo-export")
        self.assertEqual(by_name[source_book.name]["status"], "saved_to_drive")
        self.assertEqual(by_name[source_book.name]["drive_url"], "https://drive/source-book")
        self.assertEqual(by_name[audit_csv.name]["category"], "audit_output")
        self.assertEqual(by_name[audit_csv.name]["drive_url"], "https://drive/audit")
        self.assertEqual(by_name[recovered_expense.name]["category"], "expense_evidence")
        self.assertEqual(by_name[recovered_expense.name]["period"], "2026-Q2")
        self.assertEqual(by_name[recovered_expense.name]["status"], "saved_to_drive")
        self.assertEqual(by_name[recovered_report.name]["category"], "tax_report")
        self.assertEqual(by_name[recovered_report.name]["period"], "2026-Q2")

    def test_source_books_index_uses_content_check_import_counts_and_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            book = root / "Libros_contables_exampleei_example_2026.xlsx"
            book.write_bytes(b"book")
            content = root / "content.csv"
            imported = root / "rows.csv"
            reconciliation = root / "reconciliation.csv"
            _write_csv(
                content,
                [
                    "check",
                    "scope",
                    "status",
                    "content_ready_files",
                    "required_for",
                    "evidence",
                ],
                [
                    [
                        "gastos_book",
                        "2026",
                        "content_ready",
                        str(book),
                        "2026-Q1, 2026-Q2",
                        "content-ready",
                    ]
                ],
            )
            _write_csv(
                imported,
                ["source_book_type", "source_file"],
                [["gastos_book", str(book)], ["gastos_book", str(book)]],
            )
            _write_csv(
                reconciliation,
                ["period", "status"],
                [["2026-Q1", "rows_match_target_no_tieout"], ["2026-Q2", "rows_match_target_no_tieout"]],
            )
            drive_map = _drive_map(
                root / "drive_map.csv",
                [
                    {
                        "local_source_path": str(book),
                        "drive_relative_path": "Xolo evidence archive/source_books/Libros_contables_exampleei_example_2026.xlsx",
                        "drive_url": "https://drive/book",
                        "drive_id": "book-id",
                    }
                ],
            )

            rows = build_source_books_index(
                content_check_csv=content,
                imported_rows_csv=imported,
                reconciliation_csv=reconciliation,
                drive_map=load_drive_map(drive_map),
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["year"], "2026")
        self.assertEqual(rows[0]["scope_start"], "2026-Q1")
        self.assertEqual(rows[0]["scope_end"], "2026-Q2")
        self.assertEqual(rows[0]["imported_rows"], "2")
        self.assertEqual(rows[0]["drive_url"], "https://drive/book")
        self.assertEqual(rows[0]["reconciliation_status"], "rows_match_target_no_tieout")

    def test_correspondence_index_records_request_package_and_received_books(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            package = runs / "xolo_source_book_request_package"
            books = root / "books"
            package.mkdir(parents=True)
            books.mkdir()
            message = package / "message_to_xolo.md"
            book = books / "Libros_contables_exampleei_example_2025.xlsx"
            message.write_text("request", encoding="utf-8")
            book.write_bytes(b"book")

            rows = build_xolo_correspondence_index(
                runs_root=runs,
                source_books_root=books,
                drive_map={},
                indexed_at="2026-07-13",
            )

        self.assertEqual([row["direction"] for row in rows], ["outbound", "inbound"])
        self.assertEqual(rows[0]["status"], "prepared_request")
        self.assertEqual(rows[1]["status"], "received_pending_drive_url")

    def test_cli_writes_archive_index_csvs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xolo_root = root / "Xolo export"
            books = root / "books"
            runs = root / "runs"
            out = root / "out"
            (xolo_root / "TAX_REPORT").mkdir(parents=True)
            books.mkdir()
            runs.mkdir()
            (xolo_root / "TAX_REPORT" / "M130 2T 2023 exampleei example.pdf").write_bytes(b"pdf")
            book = books / "Libros_contables_exampleei_example_2023.xlsx"
            book.write_bytes(b"book")
            _write_csv(
                runs / "xolo_source_book_content_check.csv",
                ["check", "scope", "status", "content_ready_files", "required_for", "evidence"],
                [["ingresos_book", "2023", "content_ready", str(book), "2023-Q2", "ready"]],
            )
            _write_csv(
                runs / "xolo_source_book_rows.csv",
                ["source_book_type", "source_file"],
                [["ingresos_book", str(book)]],
            )
            _write_csv(
                runs / "xolo_source_book_reconciliation.csv",
                ["period", "status"],
                [["2023-Q2", "rows_match_target_no_tieout"]],
            )

            exit_code = main(
                [
                    "build-drive-archive-index",
                    "--xolo-root",
                    str(xolo_root),
                    "--source-books-root",
                    str(books),
                    "--runs-root",
                    str(runs),
                    "--out-dir",
                    str(out),
                    "--indexed-at",
                    "2026-07-13",
                ]
            )
            document_index_exists = (out / "document_index.csv").exists()
            source_books_exists = (out / "source_books.csv").exists()
            correspondence_exists = (out / "xolo_correspondence.csv").exists()

        self.assertEqual(exit_code, 0)
        self.assertTrue(document_index_exists)
        self.assertTrue(source_books_exists)
        self.assertTrue(correspondence_exists)


def _drive_map(path: Path, rows: list[dict[str, str]]) -> Path:
    _write_csv(path, ["local_source_path", "drive_relative_path", "drive_url", "drive_id"], [[row.get(field, "") for field in ["local_source_path", "drive_relative_path", "drive_url", "drive_id"]] for row in rows])
    return path


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
