from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.source_book_response_check import build_source_book_response_check


class SourceBookResponseCheckTests(unittest.TestCase):
    def test_submitted_tax_report_pdf_does_not_count_as_source_book_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            acceptance = tmp_path / "quarter_acceptance.csv"
            response_root = tmp_path / "response"
            response_root.mkdir()
            _write_acceptance(acceptance, ["2023-Q2", "2023-Q3"])
            (response_root / "M130 2T 2023 exampleei example.pdf").write_text("tax report", encoding="utf-8")

            rows = build_source_book_response_check(
                response_root=response_root,
                quarter_acceptance_csv=acceptance,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("ingresos_book", "2023")]["status"], "missing")
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "missing")
        self.assertEqual(by_key[("bienes_inversion_book", "2023")]["status"], "missing")
        self.assertEqual(by_key[("provisiones_suplidos_book", "2023")]["status"], "missing")
        self.assertEqual(by_key[("package_verdict", "2023-Q2..2023-Q3")]["status"], "missing_required_deliverables")

    def test_filing_receipts_do_not_count_as_required_source_books(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            acceptance = tmp_path / "quarter_acceptance.csv"
            response_root = tmp_path / "response"
            response_root.mkdir()
            _write_acceptance(acceptance, ["2026-Q2"])
            _touch(response_root / "Modelo 303 2T 2026 justificante de presentacion.pdf")
            _touch(response_root / "NRC payment receipt 2T 2026.pdf")

            rows = build_source_book_response_check(
                response_root=response_root,
                quarter_acceptance_csv=acceptance,
            )

        deliverables = [row for row in rows if row["check"] != "package_verdict"]
        self.assertEqual(len(deliverables), 4)
        self.assertTrue(all(row["status"] == "missing" for row in deliverables))
        self.assertEqual(rows[-1]["status"], "missing_required_deliverables")

    def test_full_response_package_is_ready_for_intake(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            acceptance = tmp_path / "quarter_acceptance.csv"
            response_root = tmp_path / "response"
            response_root.mkdir()
            _write_acceptance(acceptance, ["2023-Q2", "2023-Q3", "2024-Q1"])
            _touch(response_root / "Libro registro ingresos 2023-2026.xlsx")
            _touch(response_root / "Libro registro compras gastos 2023-2026.xlsx")
            _touch(response_root / "Complete asset amortization schedule.xlsx")
            _touch(response_root / "Libro registro provisiones suplidos 2023-2026.xlsx")

            rows = build_source_book_response_check(
                response_root=response_root,
                quarter_acceptance_csv=acceptance,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("ingresos_book", "2023")]["status"], "found")
        self.assertEqual(by_key[("ingresos_book", "2024")]["status"], "found")
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "found")
        self.assertEqual(by_key[("gastos_book", "2024")]["status"], "found")
        self.assertEqual(by_key[("bienes_inversion_book", "2023")]["status"], "found")
        self.assertEqual(by_key[("bienes_inversion_book", "2024")]["status"], "found")
        self.assertEqual(by_key[("provisiones_suplidos_book", "2023")]["status"], "found")
        self.assertEqual(by_key[("provisiones_suplidos_book", "2024")]["status"], "found")
        self.assertEqual(by_key[("package_verdict", "2023-Q2..2024-Q1")]["status"], "ready_for_intake")

    def test_unscoped_spanish_book_names_cover_all_years(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            acceptance = tmp_path / "quarter_acceptance.csv"
            response_root = tmp_path / "response"
            response_root.mkdir()
            _write_acceptance(acceptance, ["2023-Q2", "2024-Q1"])
            _touch(response_root / "Libro registro ingresos.xlsx")
            _touch(response_root / "Libro registro facturas recibidas.xlsx")
            _touch(response_root / "Asset schedule.xlsx")
            _touch(response_root / "Libro registro provisiones suplidos.xlsx")

            rows = build_source_book_response_check(
                response_root=response_root,
                quarter_acceptance_csv=acceptance,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("ingresos_book", "2023")]["status"], "found")
        self.assertEqual(by_key[("ingresos_book", "2024")]["status"], "found")
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "found")
        self.assertEqual(by_key[("gastos_book", "2024")]["status"], "found")
        self.assertEqual(by_key[("bienes_inversion_book", "2023")]["status"], "found")
        self.assertEqual(by_key[("bienes_inversion_book", "2024")]["status"], "found")
        self.assertEqual(by_key[("provisiones_suplidos_book", "2023")]["status"], "found")
        self.assertEqual(by_key[("provisiones_suplidos_book", "2024")]["status"], "found")
        self.assertEqual(by_key[("package_verdict", "2023-Q2..2024-Q1")]["status"], "ready_for_intake")

    def test_year_scoped_book_does_not_cover_other_years(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            acceptance = tmp_path / "quarter_acceptance.csv"
            response_root = tmp_path / "response"
            response_root.mkdir()
            _write_acceptance(acceptance, ["2023-Q2", "2024-Q1"])
            _touch(response_root / "Libro registro facturas recibidas 2024.xlsx")

            rows = build_source_book_response_check(
                response_root=response_root,
                quarter_acceptance_csv=acceptance,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "missing")
        self.assertEqual(by_key[("gastos_book", "2024")]["status"], "found")

    def test_glued_year_scope_does_not_cover_other_years(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            acceptance = tmp_path / "quarter_acceptance.csv"
            response_root = tmp_path / "response"
            response_root.mkdir()
            _write_acceptance(acceptance, ["2023-Q2", "2024-Q1"])
            _touch(response_root / "Libro registro compras2024.xlsx")

            rows = build_source_book_response_check(
                response_root=response_root,
                quarter_acceptance_csv=acceptance,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "missing")
        self.assertEqual(by_key[("gastos_book", "2024")]["status"], "found")

    def test_all_year_tokens_are_not_substrings_inside_other_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            acceptance = tmp_path / "quarter_acceptance.csv"
            response_root = tmp_path / "response"
            response_root.mkdir()
            _write_acceptance(acceptance, ["2023-Q2", "2024-Q1"])
            _touch(response_root / "Libro metodo gastos 2024.xlsx")

            rows = build_source_book_response_check(
                response_root=response_root,
                quarter_acceptance_csv=acceptance,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "missing")
        self.assertEqual(by_key[("gastos_book", "2024")]["status"], "found")

    def test_book_token_does_not_match_inside_supplier_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            acceptance = tmp_path / "quarter_acceptance.csv"
            response_root = tmp_path / "response"
            response_root.mkdir()
            _write_acceptance(acceptance, ["2023-Q2"])
            _touch(response_root / "Facebook expenses.xlsx")

            rows = build_source_book_response_check(
                response_root=response_root,
                quarter_acceptance_csv=acceptance,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "missing")

    def test_bad_acceptance_schema_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            acceptance = tmp_path / "quarter_acceptance.csv"
            response_root = tmp_path / "response"
            response_root.mkdir()
            with acceptance.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["quarter", "acceptance_status"])
                writer.writeheader()
                writer.writerow({"quarter": "2023-Q2", "acceptance_status": "not_closed"})

            rows = build_source_book_response_check(
                response_root=response_root,
                quarter_acceptance_csv=acceptance,
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["check"], "package_verdict")
        self.assertEqual(rows[0]["status"], "invalid_quarter_acceptance")
        self.assertIn("Regenerate quarter acceptance", rows[0]["next_action"])

    def test_cli_writes_source_book_response_check_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            acceptance = tmp_path / "quarter_acceptance.csv"
            response_root = tmp_path / "response"
            out_csv = tmp_path / "response_check.csv"
            out_md = tmp_path / "response_check.md"
            response_root.mkdir()
            _write_acceptance(acceptance, ["2023-Q2"])

            exit_code = main(
                [
                    "audit-source-book-response-check",
                    "--response-root",
                    str(response_root),
                    "--quarter-acceptance",
                    str(acceptance),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Xolo Official Register Response Package Check", markdown)
            self.assertIn("missing_required_deliverables", markdown)


def _write_acceptance(path: Path, periods: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["period", "acceptance_status", "priority"])
        writer.writeheader()
        for period in periods:
            writer.writerow(
                {
                    "period": period,
                    "acceptance_status": "not_closed_material_gap_requires_source_books",
                    "priority": "P0",
                }
            )


def _touch(path: Path) -> None:
    path.write_text("fixture", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
