from pathlib import Path
import tempfile
import unittest
import zipfile

from autonomo_taxes.cli import main
from autonomo_taxes.evidence_inventory import (
    build_evidence_inventory,
    write_evidence_inventory_csv,
    write_evidence_inventory_markdown,
)


class EvidenceInventoryTests(unittest.TestCase):
    def test_inventory_classifies_reports_and_candidate_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "TAX_REPORT" / "M130 2T 2023 exampleei example.pdf")
            _touch(root / "TAX_REPORT" / "MOD 303 2T 2023 exampleei example.pdf")
            _touch(root / "TAX_REPORT" / "M100 0A 2023 exampleei example.pdf")
            _touch(root / "TAX_REPORT" / "MOD 390 2023 exampleei example.pdf")
            _touch(root / "EXPENSE" / "xolo.pdf")
            _touch(root / "INVOICE" / "invoice.pdf")
            _touch(root / "docs" / "libro registro gastos.csv")
            _touch(root / "docs" / "asset amortization schedule.xlsx")
            _touch(root / "docs" / "Justificante_Registro_Electronico.pdf")

            rows = build_evidence_inventory(root)

        by_category = {row["category"]: row for row in rows}
        self.assertEqual(by_category["modelo130_report"]["period"], "2023-Q2")
        self.assertEqual(by_category["modelo303_report"]["period"], "2023-Q2")
        self.assertEqual(by_category["modelo100_report"]["period"], "2023")
        self.assertEqual(by_category["modelo390_report"]["period"], "2023")
        self.assertEqual(by_category["candidate_source_book_row_evidence"]["count"], "1")
        self.assertEqual(by_category["candidate_asset_schedule"]["count"], "1")
        self.assertNotIn("Justificante_Registro_Electronico.pdf", by_category["candidate_source_book_row_evidence"]["paths"])
        self.assertIn("Justificante_Registro_Electronico.pdf", by_category["source_book_keyword_false_positive"]["paths"])

    def test_inventory_records_zero_candidate_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "TAX_REPORT" / "M130 2T 2023 exampleei example.pdf")

            rows = build_evidence_inventory(root)

        by_category = {row["category"]: row for row in rows}
        self.assertEqual(by_category["candidate_source_book_row_evidence"]["count"], "0")
        self.assertEqual(by_category["candidate_asset_schedule"]["count"], "0")

    def test_inventory_scans_zip_members_without_counting_register_receipts_as_source_books(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_zip(
                root / "appeal.zip",
                [
                    "docs/Justificante_Registro_Electronico.pdf",
                    "docs/libro registro compras gastos 2026.xlsx",
                    "docs/bienes inversion amortizacion.xlsx",
                ],
            )

            rows = build_evidence_inventory(root)

        by_category = {row["category"]: row for row in rows}
        self.assertEqual(by_category["archive_file"]["count"], "1")
        self.assertEqual(by_category["scan_zip_members"]["count"], "3")
        self.assertEqual(by_category["candidate_source_book_row_evidence"]["count"], "1")
        self.assertEqual(by_category["candidate_asset_schedule"]["count"], "1")
        self.assertIn(
            "appeal.zip!/docs/Justificante_Registro_Electronico.pdf",
            by_category["archive_source_book_keyword_false_positive"]["paths"],
        )

    def test_inventory_recognizes_additional_tax_forms_dynamically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _touch(root / "TAX_REPORT" / "modelo_349_2026_q2.pdf")
            _touch(root / "TAX_REPORT" / "MOD-347-2025.pdf")
            _touch(root / "TAX_REPORT" / "M111_2026_1T.pdf")
            _touch(root / "TAX_REPORT" / "M721 2024.pdf")

            rows = build_evidence_inventory(root)

        by_category = {row["category"]: row for row in rows}
        self.assertEqual(by_category["modelo349_report"]["period"], "2026-Q2")
        self.assertEqual(by_category["modelo347_report"]["period"], "2025")
        self.assertEqual(by_category["modelo111_report"]["period"], "2026-Q1")
        self.assertEqual(by_category["modelo721_report"]["period"], "2024")

    def test_cli_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "xolo"
            _touch(root / "TAX_REPORT" / "M130 2T 2023 exampleei example.pdf")
            out_csv = Path(tmp) / "inventory.csv"
            out_md = Path(tmp) / "inventory.md"

            exit_code = main(
                [
                    "audit-evidence-inventory",
                    "--xolo-root",
                    str(root),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Modelo 130 Evidence Inventory", markdown)
        self.assertIn("Candidate source-book row-evidence files found: `0`", markdown)
        self.assertIn("ZIP member inspection is filename-only", markdown)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fixture", encoding="utf-8")


def _write_zip(path: Path, names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "fixture")


if __name__ == "__main__":
    unittest.main()
