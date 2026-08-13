from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

from autonomo_taxes.source_book_xlsx import read_xlsx_tables


class SourceBookXlsxTests(unittest.TestCase):
    def test_reads_two_row_headers_and_normalizes_noisy_numeric_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.xlsx"
            _write_minimal_xlsx(
                path,
                """
                <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <sheetData>
                    <row r="1">
                      <c r="A1" t="inlineStr"><is><t>Autoliquidacion</t></is></c>
                      <c r="B1" t="inlineStr"><is><t>Autoliquidacion</t></is></c>
                      <c r="C1" t="inlineStr"><is><t>Total Factura</t></is></c>
                      <c r="D1" t="inlineStr"><is><t>Plain Decimal</t></is></c>
                    </row>
                    <row r="2">
                      <c r="A2" t="inlineStr"><is><t>Periodo</t></is></c>
                      <c r="B2" t="inlineStr"><is><t>Ejercicio</t></is></c>
                      <c r="C2" t="inlineStr"><is><t>Total Factura</t></is></c>
                      <c r="D2" t="inlineStr"><is><t>Plain Decimal</t></is></c>
                    </row>
                    <row r="3">
                      <c r="A3" t="inlineStr"><is><t>2T</t></is></c>
                      <c r="B3" t="inlineStr"><is><t>2026</t></is></c>
                      <c r="C3"><v>71.650000000000006</v></c>
                      <c r="D3"><v>1.5</v></c>
                    </row>
                  </sheetData>
                </worksheet>
                """,
            )

            tables = read_xlsx_tables(path)

        table = next(table for table in tables if table.file_format == "xlsx:sheet1:r1-r2")
        self.assertEqual(
            table.headers,
            [
                "Autoliquidacion Periodo",
                "Autoliquidacion Ejercicio",
                "Total Factura",
                "Plain Decimal",
            ],
        )
        self.assertEqual(table.rows, [["2T", "2026", "71.65", "1.5"]])


def _write_minimal_xlsx(path: Path, sheet_xml: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)


if __name__ == "__main__":
    unittest.main()
