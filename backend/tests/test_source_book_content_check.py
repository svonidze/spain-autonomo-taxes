from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest
import zipfile

from autonomo_taxes.cli import main
from autonomo_taxes.source_book_content_check import build_source_book_content_check


class SourceBookContentCheckTests(unittest.TestCase):
    def test_missing_response_files_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            response = tmp_path / "response_check.csv"
            _write_response_check(response, matched_files="")

            rows = build_source_book_content_check(
                response_root=tmp_path,
                source_book_response_check_csv=response,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "missing_response_file")
        self.assertIn("irpf_deductible_eur", by_key[("gastos_book", "2023")]["missing_required_groups"])
        self.assertEqual(by_key[("content_verdict", "2023-Q2..2023-Q2")]["status"], "content_attention")

    def test_machine_readable_csv_with_required_headers_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            response = tmp_path / "response_check.csv"
            _write_csv(
                tmp_path / "libro compras gastos 2023.csv",
                [
                    "Date",
                    "Supplier",
                    "Invoice Number",
                    "Concept",
                    "IRPF deductible EUR",
                ],
                [["2023-04-01", "Xolo", "INV-1", "Services", "10.00"]],
            )
            _write_response_check(response, matched_files="libro compras gastos 2023.csv")

            rows = build_source_book_content_check(
                response_root=tmp_path,
                source_book_response_check_csv=response,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "content_ready")
        self.assertEqual(by_key[("content_verdict", "2023-Q2..2023-Q2")]["status"], "ready_for_import")

    def test_machine_readable_xlsx_with_required_headers_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            response = tmp_path / "response_check.csv"
            _write_minimal_xlsx(
                tmp_path / "libro compras gastos 2023.xlsx",
                [
                    "Date",
                    "Supplier",
                    "Invoice Number",
                    "Concept",
                    "IRPF deductible EUR",
                ],
                ["2023-04-01", "Xolo", "INV-1", "Services", "10.00"],
            )
            _write_response_check(response, matched_files="libro compras gastos 2023.xlsx")

            rows = build_source_book_content_check(
                response_root=tmp_path,
                source_book_response_check_csv=response,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "content_ready")
        self.assertEqual(by_key[("content_verdict", "2023-Q2..2023-Q2")]["status"], "ready_for_import")

    def test_xlsx_uses_content_ready_sheet_when_first_sheet_is_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            response = tmp_path / "response_check.csv"
            _write_multi_sheet_xlsx(
                tmp_path / "libro compras gastos 2023.xlsx",
                [
                    (
                        ["Report", "Generated"],
                        ["Libro registro de compras y gastos", "2026-07-09"],
                    ),
                    (
                        [
                            "Date",
                            "Supplier",
                            "Invoice Number",
                            "Concept",
                            "IRPF deductible EUR",
                        ],
                        ["2023-04-01", "Xolo", "INV-1", "Services", "10.00"],
                    ),
                ],
            )
            _write_response_check(response, matched_files="libro compras gastos 2023.xlsx")

            rows = build_source_book_content_check(
                response_root=tmp_path,
                source_book_response_check_csv=response,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "content_ready")
        self.assertIn("xlsx:sheet2", by_key[("gastos_book", "2023")]["evidence"])

    def test_xlsx_with_no_valid_sheet_stays_content_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            response = tmp_path / "response_check.csv"
            _write_multi_sheet_xlsx(
                tmp_path / "libro compras gastos 2023.xlsx",
                [
                    (["Report", "Generated"], ["Libro registro de compras y gastos", "2026-07-09"]),
                    (["Date", "Supplier", "Invoice Number"], ["2023-04-01", "Xolo", "INV-1"]),
                ],
            )
            _write_response_check(response, matched_files="libro compras gastos 2023.xlsx")

            rows = build_source_book_content_check(
                response_root=tmp_path,
                source_book_response_check_csv=response,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "content_incomplete")
        self.assertIn("concept", by_key[("gastos_book", "2023")]["missing_required_groups"])
        self.assertEqual(by_key[("content_verdict", "2023-Q2..2023-Q2")]["status"], "content_attention")

    def test_incomplete_headers_identify_missing_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            response = tmp_path / "response_check.csv"
            _write_csv(
                tmp_path / "libro compras gastos 2023.csv",
                ["Date", "Supplier", "Invoice Number", "Concept"],
                [["2023-04-01", "Xolo", "INV-1", "Services"]],
            )
            _write_response_check(response, matched_files="libro compras gastos 2023.csv")

            rows = build_source_book_content_check(
                response_root=tmp_path,
                source_book_response_check_csv=response,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "content_incomplete")
        self.assertEqual(by_key[("gastos_book", "2023")]["missing_required_groups"], "irpf_deductible_eur")
        self.assertEqual(by_key[("content_verdict", "2023-Q2..2023-Q2")]["status"], "content_attention")

    def test_single_token_aliases_do_not_match_inside_unrelated_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            response = tmp_path / "response_check.csv"
            _write_csv(
                tmp_path / "libro compras gastos 2023.csv",
                [
                    "Candidate Source",
                    "Supplier",
                    "Invoice Number",
                    "Concept",
                    "IRPF deductible EUR",
                ],
                [["local candidate", "Xolo", "INV-1", "Services", "10.00"]],
            )
            _write_response_check(response, matched_files="libro compras gastos 2023.csv")

            rows = build_source_book_content_check(
                response_root=tmp_path,
                source_book_response_check_csv=response,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("gastos_book", "2023")]["status"], "content_incomplete")
        self.assertIn("date", by_key[("gastos_book", "2023")]["missing_required_groups"])

    def test_investment_goods_and_provisions_required_headers_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            response = tmp_path / "response_check.csv"
            _write_csv(
                tmp_path / "asset schedule.csv",
                [
                    "Description",
                    "Supplier",
                    "Acquisition Date",
                    "Acquisition Value",
                    "Amortization Method",
                    "Annual Amortization",
                    "Accumulated Amortization",
                ],
                [["Laptop", "Apple", "2023-04-01", "100.00", "linear", "25%", "25.00"]],
            )
            _write_csv(
                tmp_path / "provisiones.csv",
                ["Date", "Counterparty", "Concept", "Amount"],
                [["2023-04-01", "Client", "No movements", "0.00"]],
            )
            _write_response_check(
                response,
                rows=[
                    {
                        "check": "bienes_inversion_book",
                        "scope": "2023",
                        "status": "found",
                        "matched_count": "1",
                        "matched_files": "asset schedule.csv",
                        "required_for": "2023-Q2",
                    },
                    {
                        "check": "provisiones_suplidos_book",
                        "scope": "2023",
                        "status": "found",
                        "matched_count": "1",
                        "matched_files": "provisiones.csv",
                        "required_for": "2023-Q2",
                    },
                ],
            )

            rows = build_source_book_content_check(
                response_root=tmp_path,
                source_book_response_check_csv=response,
            )

        by_key = {(row["check"], row["scope"]): row for row in rows}
        self.assertEqual(by_key[("bienes_inversion_book", "2023")]["status"], "content_ready")
        self.assertEqual(by_key[("provisiones_suplidos_book", "2023")]["status"], "content_ready")
        self.assertEqual(by_key[("content_verdict", "2023-Q2..2023-Q2")]["status"], "ready_for_import")

    def test_cli_writes_source_book_content_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            response = tmp_path / "response_check.csv"
            out_csv = tmp_path / "content.csv"
            out_md = tmp_path / "content.md"
            _write_response_check(response, matched_files="")

            exit_code = main(
                [
                    "audit-source-book-content-check",
                    "--response-root",
                    str(tmp_path),
                    "--source-book-response-check",
                    str(response),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Xolo Source-Book Content Check", markdown)
            self.assertIn("content_attention", markdown)


def _write_response_check(path: Path, matched_files: str = "", rows: list[dict[str, str]] | None = None) -> None:
    if rows is None:
        rows = [
            {
                "check": "gastos_book",
                "scope": "2023",
                "status": "found" if matched_files else "missing",
                "matched_count": "1" if matched_files else "0",
                "matched_files": matched_files,
                "required_for": "2023-Q2",
            }
        ]
    rows = [
        *rows,
        {
            "check": "package_verdict",
            "scope": "2023-Q2..2023-Q2",
            "status": "ready_for_intake" if all(row.get("status") == "found" for row in rows) else "missing_required_deliverables",
            "matched_count": str(sum(1 for row in rows if row.get("status") == "found")),
            "required_for": "all quarters in quarter acceptance matrix",
        },
    ]
    _write_dicts(
        path,
        ["check", "scope", "status", "matched_count", "matched_files", "required_for", "next_action"],
        rows,
    )


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def _write_dicts(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _write_minimal_xlsx(path: Path, headers: list[str], row: list[str]) -> None:
    _write_multi_sheet_xlsx(path, [(headers, row)])


def _write_multi_sheet_xlsx(path: Path, sheets: list[tuple[list[str], list[str]]]) -> None:
    strings = [value for headers, row in sheets for value in headers + row]
    shared = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(strings)}" uniqueCount="{len(strings)}">'
        + "".join(f"<si><t>{value}</t></si>" for value in strings)
        + "</sst>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("xl/sharedStrings.xml", shared)
        index = 0
        for sheet_index, (headers, row) in enumerate(sheets, start=1):
            cells = []
            for row_number, values in enumerate((headers, row), start=1):
                row_cells = []
                for col_number, _ in enumerate(values, start=1):
                    row_cells.append(f'<c r="{_column_letter(col_number)}{row_number}" t="s"><v>{index}</v></c>')
                    index += 1
                cells.append(f'<row r="{row_number}">' + "".join(row_cells) + "</row>")
            sheet = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                "<sheetData>"
                + "".join(cells)
                + "</sheetData></worksheet>"
            )
            archive.writestr(f"xl/worksheets/sheet{sheet_index}.xml", sheet)


def _column_letter(index: int) -> str:
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


if __name__ == "__main__":
    unittest.main()
