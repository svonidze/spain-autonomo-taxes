from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

from autonomo_taxes.cli import main
from autonomo_taxes.xolo_dataexport_inventory import (
    build_xolo_dataexport_inventory,
    write_xolo_dataexport_inventory_markdown,
)


class XoloDataexportInventoryTests(unittest.TestCase):
    def test_documents_only_export_has_no_register_or_asset_schedule_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "dataexport.zip"
            _write_zip(
                zip_path,
                [
                    "EXPENSE/Xolo_Business_Spain_SL_00006.pdf",
                    "INVOICE/INV-2023-001.pdf",
                    "TAX_REPORT/M130 2T 2023 exampleei example.pdf",
                    "COMPANY/MOD 036 exampleei example.pdf",
                ],
            )

            rows = build_xolo_dataexport_inventory(zip_path)

        by_kind_key = {(row["kind"], row["key"]): row for row in rows}
        self.assertEqual(by_kind_key[("top_level", "EXPENSE")]["count"], "1")
        self.assertEqual(by_kind_key[("extension", ".pdf")]["count"], "4")
        self.assertEqual(by_kind_key[("keyword", "register")]["count"], "0")
        self.assertEqual(by_kind_key[("keyword", "asset")]["count"], "0")
        self.assertEqual(by_kind_key[("conclusion", "candidate_source_book_or_asset_schedule")]["count"], "0")
        with tempfile.TemporaryDirectory() as tmp:
            out_md = Path(tmp) / "inventory.md"
            write_xolo_dataexport_inventory_markdown(out_md, rows, zip_path)
            markdown = out_md.read_text(encoding="utf-8")
        self.assertIn("Filename-only scan", markdown)
        self.assertIn("does not inspect PDF/image contents", markdown)

    def test_register_and_amortization_keywords_are_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "dataexport.zip"
            _write_zip(
                zip_path,
                [
                    "docs/libro registro gastos 2024.csv",
                    "docs/asset amortization schedule.xlsx",
                ],
            )
            out_md = Path(tmp) / "inventory.md"

            rows = build_xolo_dataexport_inventory(zip_path)
            write_xolo_dataexport_inventory_markdown(out_md, rows, zip_path)
            markdown = out_md.read_text(encoding="utf-8")

            self.assertIn("Candidate source-book row-evidence or asset/amortization files found: `2`", markdown)
            self.assertIn("libro registro gastos 2024.csv", markdown)
            self.assertIn("asset amortization schedule.xlsx", markdown)

    def test_cli_writes_zip_inventory_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "dataexport.zip"
            _write_zip(zip_path, ["EXPENSE/xolo.pdf"])
            out_csv = Path(tmp) / "inventory.csv"
            out_md = Path(tmp) / "inventory.md"

            exit_code = main(
                [
                    "audit-xolo-dataexport-inventory",
                    "--zip",
                    str(zip_path),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            self.assertIn("Xolo Data Export Inventory", out_md.read_text(encoding="utf-8"))


def _write_zip(path: Path, names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "fixture")


if __name__ == "__main__":
    unittest.main()
