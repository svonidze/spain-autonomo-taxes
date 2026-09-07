from __future__ import annotations
from autonomo_test_support.paths import REPO_ROOT

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


SCRIPT_PATH = REPO_ROOT / "scripts" / "audit_xolo_export_archives.py"
SPEC = importlib.util.spec_from_file_location("audit_xolo_export_archives", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
archive_audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = archive_audit
SPEC.loader.exec_module(archive_audit)


class AuditXoloExportArchivesTests(unittest.TestCase):
    def test_build_rows_summarizes_archive_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path = root / "dataexport_test.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("COMPANY/company.pdf", "company")
                archive.writestr("EXPENSE/libro_registro_gastos.pdf", "register")
                archive.writestr("INVOICE/invoice.pdf", "invoice")
                archive.writestr("TAX_REPORT/MOD 130.pdf", "tax")

            rows = archive_audit.build_rows(root)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["archive"], "dataexport_test.zip")
        self.assertEqual(rows[0]["company_files"], "1")
        self.assertEqual(rows[0]["expense_files"], "1")
        self.assertEqual(rows[0]["candidate_source_book_or_asset_files"], "1")
        self.assertEqual(rows[0]["keyword_registro"], "1")

    def test_markdown_includes_filename_only_caveat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "compare.md"

            archive_audit.write_markdown(out, [], root)

            text = out.read_text(encoding="utf-8")
        self.assertIn("filename-only scan", text)
        self.assertIn("does not inspect PDF or image contents", text)


if __name__ == "__main__":
    unittest.main()
