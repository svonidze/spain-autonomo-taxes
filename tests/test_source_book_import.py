from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest
import zipfile

from autonomo_taxes.cli import main
from autonomo_taxes.source_book_import import build_source_book_import


class SourceBookImportTests(unittest.TestCase):
    def test_imports_content_ready_gastos_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "libro gastos 2023.csv"
            content_check = tmp_path / "content.csv"
            _write_csv(
                source,
                [
                    "Date",
                    "Supplier",
                    "Invoice Number",
                    "Concept",
                    "IRPF deductible EUR",
                    "Gross EUR",
                    "VAT Treatment",
                    "Payment Status",
                    "Fecha Contabilización",
                ],
                [["2023-04-01", "Xolo", "INV-1", "Services", "10.00", "12.10", "gross", "paid", "2023-04-02"]],
            )
            _write_content_check(
                content_check,
                check="gastos_book",
                scope="2023",
                status="content_ready",
                content_ready_files=str(source),
            )

            rows = build_source_book_import(response_root=tmp_path, source_book_content_check_csv=content_check)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source_book_type"], "gastos_book")
        self.assertEqual(row["source_scope"], "2023")
        self.assertEqual(row["period"], "2023-Q2")
        self.assertEqual(row["date"], "2023-04-01")
        self.assertEqual(row["booking_date"], "2023-04-02")
        self.assertEqual(row["supplier"], "Xolo")
        self.assertEqual(row["document_number"], "INV-1")
        self.assertEqual(row["irpf_deductible_eur"], "10.00")
        self.assertEqual(row["gross_eur"], "12.10")
        self.assertEqual(row["vat_treatment"], "gross")
        self.assertEqual(row["reason_code"], "Services")
        self.assertEqual(row["import_status"], "imported")

    def test_imports_full_history_file_only_once_when_matched_to_multiple_years(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "libro gastos 2023-2026.csv"
            content_check = tmp_path / "content.csv"
            _write_csv(
                source,
                [
                    "Date",
                    "Supplier",
                    "Invoice Number",
                    "Concept",
                    "IRPF deductible EUR",
                ],
                [
                    ["2023-04-01", "Xolo", "INV-2023", "Services", "10.00"],
                    ["2024-01-01", "Xolo", "INV-2024", "Services", "20.00"],
                ],
            )
            _write_content_check_rows(
                content_check,
                [
                    {
                        "check": "gastos_book",
                        "scope": "2024",
                        "status": "content_ready",
                        "content_ready_files": str(source),
                    },
                    {
                        "check": "gastos_book",
                        "scope": "2023",
                        "status": "content_ready",
                        "content_ready_files": str(source),
                    },
                ],
            )

            rows = build_source_book_import(response_root=tmp_path, source_book_content_check_csv=content_check)

        self.assertEqual(len(rows), 2)
        self.assertEqual({row["document_number"] for row in rows}, {"INV-2023", "INV-2024"})
        self.assertEqual({row["source_scope"] for row in rows}, {"2023; 2024"})

    def test_spanish_date_format_derives_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "libro gastos 2023.csv"
            content_check = tmp_path / "content.csv"
            _write_csv(
                source,
                ["Date", "Supplier", "Invoice Number", "Concept", "IRPF deductible EUR"],
                [["15/06/2023", "Xolo", "INV-1", "Services", "10.00"]],
            )
            _write_content_check(
                content_check,
                check="gastos_book",
                scope="2023",
                status="content_ready",
                content_ready_files=str(source),
            )

            rows = build_source_book_import(response_root=tmp_path, source_book_content_check_csv=content_check)

        self.assertEqual(rows[0]["period"], "2023-Q2")

    def test_imports_aeat_tax_fields_from_official_expense_book(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "libro gastos 2026.csv"
            content_check = tmp_path / "content.csv"
            _write_csv(
                source,
                [
                    "Autoliquidacion Ejercicio",
                    "Autoliquidacion Periodo",
                    "Gasto Deducible",
                    "Fecha Expedicion",
                    "Identificacion Factura del Expedidor Serie Numero",
                    "NIF Expedidor Codigo Pais",
                    "NIF Expedidor Identificacion",
                    "Nombre Expedidor",
                    "Tipo de Factura",
                    "Clave de Operacion",
                    "Inversion del Sujeto Pasivo",
                    "Total Factura",
                    "Base Imponible",
                    "Tipo de IVA",
                    "Cuota IVA Soportado",
                    "Cuota Deducible",
                    "Tipo Retencion del IRPF",
                    "Importe Retenido del IRPF",
                ],
                [[
                    "2026",
                    "2T",
                    "113.62",
                    "04/06/2026",
                    "00007",
                    "US",
                    "9999999",
                    "Namecheap, Inc",
                    "F1",
                    "13",
                    "S",
                    "113.62",
                    "113.62",
                    "0%",
                    "0",
                    "23.86",
                    "",
                    "",
                ]],
            )
            _write_content_check(
                content_check,
                check="gastos_book",
                scope="2026",
                status="content_ready",
                content_ready_files=str(source),
            )

            rows = build_source_book_import(
                response_root=tmp_path,
                source_book_content_check_csv=content_check,
            )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["period"], "2026-Q2")
        self.assertEqual(row["counterparty_country_code"], "US")
        self.assertEqual(row["counterparty_tax_id"], "9999999")
        self.assertEqual(row["invoice_type"], "F1")
        self.assertEqual(row["operation_key"], "13")
        self.assertEqual(row["reverse_charge"], "S")
        self.assertEqual(row["invoice_total_eur"], "113.62")
        self.assertEqual(row["taxable_base_eur"], "113.62")
        self.assertEqual(row["vat_rate_percent"], "0%")
        self.assertEqual(row["vat_eur"], "0")
        self.assertEqual(row["deductible_vat_eur"], "23.86")

    def test_header_only_empty_provisions_book_imports_no_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "libro provisiones suplidos 2023.csv"
            content_check = tmp_path / "content.csv"
            _write_csv(source, ["Date", "Counterparty", "Concept", "Amount"], [])
            _write_content_check(
                content_check,
                check="provisiones_suplidos_book",
                scope="2023",
                status="content_ready",
                content_ready_files=str(source),
            )

            rows = build_source_book_import(response_root=tmp_path, source_book_content_check_csv=content_check)

        self.assertEqual(rows, [])

    def test_same_file_imports_once_per_source_book_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "combined source book.csv"
            content_check = tmp_path / "content.csv"
            _write_csv(
                source,
                [
                    "Date",
                    "Supplier",
                    "Invoice Number",
                    "Concept",
                    "IRPF deductible EUR",
                    "Period",
                    "Casilla 01",
                    "Casilla 02",
                    "Casilla 07",
                    "Casilla 19",
                ],
                [["2023-04-01", "Xolo", "INV-1", "Services", "10.00", "2023-Q2", "100.00", "10.00", "18.00", "8.00"]],
            )
            _write_content_check_rows(
                content_check,
                [
                    {
                        "check": "gastos_book",
                        "scope": "2023",
                        "status": "content_ready",
                        "content_ready_files": str(source),
                    },
                    {
                        "check": "modelo130_source_book_tieout",
                        "scope": "2023-Q2",
                        "status": "content_ready",
                        "content_ready_files": str(source),
                    },
                ],
            )

            rows = build_source_book_import(response_root=tmp_path, source_book_content_check_csv=content_check)

        self.assertEqual(len(rows), 2)
        self.assertEqual([row["source_book_type"] for row in rows], ["gastos_book", "modelo130_source_book_tieout"])
        self.assertEqual(rows[0]["irpf_deductible_eur"], "10.00")
        self.assertEqual(rows[1]["casilla02_ytd"], "10.00")

    def test_missing_full_history_file_yields_one_skipped_row_for_combined_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content_check = tmp_path / "content.csv"
            _write_content_check_rows(
                content_check,
                [
                    {
                        "check": "gastos_book",
                        "scope": "2023",
                        "status": "content_ready",
                        "content_ready_files": "missing full history.csv",
                    },
                    {
                        "check": "gastos_book",
                        "scope": "2024",
                        "status": "content_ready",
                        "content_ready_files": "missing full history.csv",
                    },
                ],
            )

            rows = build_source_book_import(response_root=tmp_path, source_book_content_check_csv=content_check)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_scope"], "2023; 2024")
        self.assertEqual(rows[0]["import_status"], "skipped_unreadable_source_file")
        self.assertIn("FileNotFoundError", rows[0]["notes"])

    def test_imports_asset_schedule_from_best_xlsx_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "asset schedule.xlsx"
            content_check = tmp_path / "content.csv"
            _write_multi_sheet_xlsx(
                source,
                [
                    (["Report", "Generated"], ["Asset schedule", "2026-07-09"]),
                    (
                        [
                            "Description",
                            "Supplier",
                            "Acquisition Date",
                            "Acquisition Value",
                            "Quarter",
                            "Amortization Amount EUR",
                            "Amortization Method",
                            "Accumulated Amortization",
                        ],
                        ["MacBook", "Apple", "2026-04-08", "1647.93", "2026-Q2", "94.81", "linear", "94.81"],
                    ),
                ],
            )
            _write_content_check(
                content_check,
                check="bienes_inversion_book",
                scope="2026",
                status="content_ready",
                content_ready_files=str(source),
            )

            rows = build_source_book_import(response_root=tmp_path, source_book_content_check_csv=content_check)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source_file_format"], "xlsx:sheet2:r1")
        self.assertEqual(row["asset_id"], "MacBook")
        self.assertEqual(row["date"], "2026-04-08")
        self.assertEqual(row["amortizable_base_eur"], "1647.93")
        self.assertEqual(row["amortization_period"], "2026-Q2")
        self.assertEqual(row["amortization_amount_eur"], "94.81")
        self.assertEqual(row["rate_or_life"], "94.81")

    def test_imports_real_airpods_annual_asset_marker_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "asset schedule 2024.csv"
            content_check = tmp_path / "content.csv"
            _write_csv(
                source,
                [
                    "Description",
                    "Supplier",
                    "Acquisition Date",
                    "Acquisition Value",
                    "Quarter",
                    "Amortization Amount EUR",
                    "Amortization Method",
                    "Accumulated Amortization",
                ],
                [
                    [
                        "SYNTH-DOCUMENT-007",
                        "Apple Retal Spain, s.L.U.",
                        "07/05/2024",
                        "478.51",
                        "0A",
                        "81",
                        "linear",
                        "81",
                    ]
                ],
            )
            _write_content_check(
                content_check,
                check="bienes_inversion_book",
                scope="2024",
                status="content_ready",
                content_ready_files=str(source),
            )

            rows = build_source_book_import(response_root=tmp_path, source_book_content_check_csv=content_check)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source_scope"], "2024")
        self.assertEqual(row["asset_id"], "SYNTH-DOCUMENT-007")
        self.assertEqual(row["date"], "07/05/2024")
        self.assertEqual(row["amortizable_base_eur"], "478.51")
        self.assertEqual(row["amortization_period"], "0A")
        self.assertEqual(row["amortization_amount_eur"], "81")
        self.assertEqual(row["import_status"], "imported")

    def test_cli_writes_rows_and_skips_non_ready_deliverables(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "tieout.csv"
            content_check = tmp_path / "content.csv"
            out_csv = tmp_path / "rows.csv"
            out_md = tmp_path / "rows.md"
            _write_csv(
                source,
                ["Period", "Casilla 01", "Casilla 02", "Casilla 07", "Casilla 19"],
                [["2023-Q2", "1000.00", "100.00", "180.00", "80.00"]],
            )
            _write_content_check_rows(
                content_check,
                [
                    {
                        "check": "modelo130_source_book_tieout",
                        "scope": "2023-Q2",
                        "status": "content_ready",
                        "content_ready_files": "tieout.csv",
                    },
                    {
                        "check": "gastos_book",
                        "scope": "2023",
                        "status": "content_incomplete",
                        "content_ready_files": "",
                    },
                ],
            )

            exit_code = main(
                [
                    "audit-source-book-import",
                    "--response-root",
                    str(tmp_path),
                    "--source-book-content-check",
                    str(content_check),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            rows = list(csv.DictReader(out_csv.open("r", newline="", encoding="utf-8-sig")))
            markdown = out_md.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_book_type"], "modelo130_source_book_tieout")
        self.assertEqual(rows[0]["period"], "2023-Q2")
        self.assertEqual(rows[0]["casilla02_ytd"], "100.00")
        self.assertIn("Xolo Source-Book Imported Rows", markdown)
        self.assertIn("Imported rows: `1`", markdown)

    def test_missing_content_ready_file_becomes_skipped_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            content_check = tmp_path / "content.csv"
            _write_content_check(
                content_check,
                check="gastos_book",
                scope="2023",
                status="content_ready",
                content_ready_files="moved_or_deleted.csv",
            )

            rows = build_source_book_import(response_root=tmp_path, source_book_content_check_csv=content_check)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["import_status"], "skipped_unreadable_source_file")
        self.assertEqual(rows[0]["source_book_line_id"], "moved_or_deleted.csv:unreadable")
        self.assertIn("FileNotFoundError", rows[0]["notes"])

    def test_header_only_content_ready_file_becomes_skipped_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "header_only.csv"
            content_check = tmp_path / "content.csv"
            _write_csv(
                source,
                ["Date", "Supplier", "Invoice Number", "Concept", "IRPF deductible EUR"],
                [],
            )
            _write_content_check(
                content_check,
                check="gastos_book",
                scope="2023",
                status="content_ready",
                content_ready_files=str(source),
            )

            rows = build_source_book_import(response_root=tmp_path, source_book_content_check_csv=content_check)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["import_status"], "skipped_empty_source_file")
        self.assertEqual(rows[0]["source_book_line_id"], "header_only.csv:empty")


def _write_content_check(
    path: Path,
    *,
    check: str,
    scope: str,
    status: str,
    content_ready_files: str,
) -> None:
    _write_content_check_rows(
        path,
        [
            {
                "check": check,
                "scope": scope,
                "status": status,
                "content_ready_files": content_ready_files,
            }
        ],
    )


def _write_content_check_rows(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "check",
        "scope",
        "status",
        "matched_count",
        "content_ready_count",
        "matched_files",
        "content_ready_files",
        "required_for",
        "missing_required_groups",
        "observed_headers",
        "evidence",
        "next_action",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


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
