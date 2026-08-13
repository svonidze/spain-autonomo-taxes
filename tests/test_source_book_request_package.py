from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.source_book_request_package import build_source_book_request_package


class SourceBookRequestPackageTests(unittest.TestCase):
    def test_builds_safe_markdown_package_and_excludes_spreadsheets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            message = tmp_path / "message.md"
            support = tmp_path / "support.md"
            target_csv = tmp_path / "target_fit.csv"
            out_dir = tmp_path / "package"
            message.write_text("message", encoding="utf-8")
            support.write_text("support", encoding="utf-8")
            target_csv.write_text("period,target,diff\n2023-Q2,115.54,21.48\n", encoding="utf-8")

            rows = build_source_book_request_package(
                message_md=message,
                attachments=[support, target_csv],
                out_dir=out_dir,
            )

            by_source = {Path(row["source_path"]).name: row for row in rows}
            self.assertEqual(by_source["message.md"]["status"], "included")
            self.assertEqual(by_source["support.md"]["status"], "included")
            self.assertEqual(by_source["target_fit.csv"]["status"], "excluded_disallowed_suffix")
            self.assertTrue((out_dir / "message_to_xolo.md").exists())
            self.assertTrue((out_dir / "attachments" / "support.md").exists())
            self.assertFalse((out_dir / "attachments" / "target_fit.csv").exists())
            manifest = (out_dir / "MANIFEST.md").read_text(encoding="utf-8")
        self.assertIn("excludes spreadsheet/data attachment formats", manifest)
        self.assertIn("official accounting/register offboarding records", manifest)
        self.assertIn("IVA books, filing receipts, and standard bookkeeping summaries are requested as supporting records", manifest)
        self.assertIn("Allowed markdown/text attachments still require human content review", manifest)
        self.assertIn("Excluded for safety: `1`", manifest)
        self.assertIn("Missing or broken inputs: `0`", manifest)
        self.assertIn("Do not attach CSV/JSON/XLSX target-fitting outputs", manifest)

    def test_missing_required_message_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                build_source_book_request_package(
                    message_md=tmp_path / "missing.md",
                    attachments=[],
                    out_dir=tmp_path / "package",
                )

    def test_spreadsheet_message_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            message = tmp_path / "message.csv"
            message.write_text("period,target,diff\n2023-Q2,115.54,21.48\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                build_source_book_request_package(
                    message_md=message,
                    attachments=[],
                    out_dir=tmp_path / "package",
                )

    def test_rebuild_removes_stale_attachment_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            message = tmp_path / "message.md"
            first = tmp_path / "first.md"
            second = tmp_path / "second.md"
            out_dir = tmp_path / "package"
            message.write_text("message", encoding="utf-8")
            first.write_text("first", encoding="utf-8")
            second.write_text("second", encoding="utf-8")

            build_source_book_request_package(message_md=message, attachments=[first, second], out_dir=out_dir)
            build_source_book_request_package(message_md=message, attachments=[first], out_dir=out_dir)

            self.assertTrue((out_dir / "attachments" / "first.md").exists())
            self.assertFalse((out_dir / "attachments" / "second.md").exists())

    def test_cli_writes_manifest_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            message = tmp_path / "message.md"
            support = tmp_path / "support.md"
            out_dir = tmp_path / "package"
            message.write_text("message", encoding="utf-8")
            support.write_text("support", encoding="utf-8")

            exit_code = main(
                [
                    "audit-source-book-request-package",
                    "--message",
                    str(message),
                    "--attachment",
                    str(support),
                    "--out-dir",
                    str(out_dir),
                ]
            )

            rows = list(csv.DictReader((out_dir / "MANIFEST.csv").open("r", newline="", encoding="utf-8")))

            self.assertEqual(exit_code, 0)
            self.assertEqual(len(rows), 2)
            self.assertTrue((out_dir / "MANIFEST.md").exists())
            self.assertEqual({row["status"] for row in rows}, {"included"})


if __name__ == "__main__":
    unittest.main()
