from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main


class SourceBookPipelineE2ETests(unittest.TestCase):
    def test_full_history_books_with_amortization_reconcile_two_quarters(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            response_root = tmp_path / "xolo-response"
            response_root.mkdir()
            runs = tmp_path / "runs"
            runs.mkdir()

            acceptance = runs / "quarter_acceptance.csv"
            history = runs / "history.csv"
            response_check = runs / "response_check.csv"
            response_check_md = runs / "response_check.md"
            content_check = runs / "content_check.csv"
            content_check_md = runs / "content_check.md"
            imported_rows = runs / "source_book_rows.csv"
            imported_rows_md = runs / "source_book_rows.md"
            reconciliation = runs / "source_book_reconciliation.csv"
            reconciliation_md = runs / "source_book_reconciliation.md"

            _write_acceptance(acceptance, ["2023-Q2", "2024-Q1"])
            _write_history(
                history,
                [
                    {"year": "2023", "quarter": "2", "target_casilla_02": "115.54", "target_casilla_02_delta": "115.54"},
                    {"year": "2024", "quarter": "1", "target_casilla_02": "254.36", "target_casilla_02_delta": "254.36"},
                ],
            )
            _write_csv(
                response_root / "Libro registro ingresos 2023-2026.csv",
                ["Date", "Customer", "Invoice Number", "Concept", "Income EUR"],
                [
                    ["2023-06-30", "Client", "I-2023", "Services", "1000.00"],
                    ["2024-01-04", "Client", "I-2024", "Services", "2000.00"],
                ],
            )
            _write_csv(
                response_root / "Libro registro gastos 2023-2026.csv",
                ["Date", "Supplier", "Invoice Number", "Concept", "IRPF deductible EUR"],
                [
                    ["2023-06-30", "Synthetic Party 001", "2023-06-30", "Services", "94.06"],
                    ["2024-01-04", "MEDIA MARKT SATURN S.A.", "SYNTH-DOCUMENT-041", "Hardware", "200.00"],
                ],
            )
            _write_csv(
                response_root / "Complete asset amortization schedule.csv",
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
                    ["GitHub", "Synthetic Party 001", "2023-06-30", "115.54", "2023-Q2", "21.48", "linear", "21.48"],
                    ["MediaMarkt", "MEDIA MARKT SATURN S.A.", "2024-01-04", "346.53", "2024-Q1", "54.36", "linear", "54.36"],
                ],
            )
            _write_csv(
                response_root / "Libro registro provisiones suplidos 2023-2026.csv",
                ["Date", "Counterparty", "Concept", "Amount"],
                [],
            )

            self.assertEqual(
                main(
                    [
                        "audit-source-book-response-check",
                        "--response-root",
                        str(response_root),
                        "--quarter-acceptance",
                        str(acceptance),
                        "--out-csv",
                        str(response_check),
                        "--out-md",
                        str(response_check_md),
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(
                    [
                        "audit-source-book-content-check",
                        "--response-root",
                        str(response_root),
                        "--source-book-response-check",
                        str(response_check),
                        "--out-csv",
                        str(content_check),
                        "--out-md",
                        str(content_check_md),
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(
                    [
                        "audit-source-book-import",
                        "--response-root",
                        str(response_root),
                        "--source-book-content-check",
                        str(content_check),
                        "--out-csv",
                        str(imported_rows),
                        "--out-md",
                        str(imported_rows_md),
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(
                    [
                        "audit-source-book-reconcile",
                        "--history-audit",
                        str(history),
                        "--source-book-rows",
                        str(imported_rows),
                        "--out-csv",
                        str(reconciliation),
                        "--out-md",
                        str(reconciliation_md),
                    ]
                ),
                0,
            )

            response_rows = _read_dicts(response_check)
            content_rows = _read_dicts(content_check)
            source_rows = _read_dicts(imported_rows)
            reconciliation_rows = _read_dicts(reconciliation)

        response_verdict = next(row for row in response_rows if row["check"] == "package_verdict")
        content_verdict = next(row for row in content_rows if row["check"] == "content_verdict")
        self.assertEqual(response_verdict["status"], "ready_for_intake")
        self.assertEqual(content_verdict["status"], "ready_for_import")
        self.assertEqual(len(source_rows), 6)
        self.assertEqual(
            sorted((row["source_book_type"], row["source_scope"]) for row in source_rows),
            [
                ("bienes_inversion_book", "2023; 2024"),
                ("bienes_inversion_book", "2023; 2024"),
                ("gastos_book", "2023; 2024"),
                ("gastos_book", "2023; 2024"),
                ("ingresos_book", "2023; 2024"),
                ("ingresos_book", "2023; 2024"),
            ],
        )
        by_period = {row["period"]: row for row in reconciliation_rows}
        self.assertEqual(by_period["2023-Q2"]["status"], "rows_match_target_no_tieout")
        self.assertEqual(by_period["2023-Q2"]["imported_expense_delta"], "94.06")
        self.assertEqual(by_period["2023-Q2"]["imported_amortization_delta"], "21.48")
        self.assertEqual(by_period["2024-Q1"]["status"], "rows_match_target_no_tieout")
        self.assertEqual(by_period["2024-Q1"]["imported_expense_delta"], "200.00")
        self.assertEqual(by_period["2024-Q1"]["imported_amortization_delta"], "54.36")

    def test_annual_airpods_asset_marker_keeps_matching_quarter_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            response_root = tmp_path / "xolo-response"
            response_root.mkdir()
            runs = tmp_path / "runs"
            runs.mkdir()

            acceptance = runs / "quarter_acceptance.csv"
            history = runs / "history.csv"
            response_check = runs / "response_check.csv"
            response_check_md = runs / "response_check.md"
            content_check = runs / "content_check.csv"
            content_check_md = runs / "content_check.md"
            imported_rows = runs / "source_book_rows.csv"
            imported_rows_md = runs / "source_book_rows.md"
            reconciliation = runs / "source_book_reconciliation.csv"
            reconciliation_md = runs / "source_book_reconciliation.md"

            _write_acceptance(acceptance, ["2024-Q2"])
            _write_history(
                history,
                [{"year": "2024", "quarter": "2", "target_casilla_02": "399.56", "target_casilla_02_delta": "399.56"}],
            )
            _write_csv(
                response_root / "Libro registro ingresos 2024.csv",
                ["Date", "Customer", "Invoice Number", "Concept", "Income EUR"],
                [["2024-05-07", "Client", "I-2024-Q2", "Services", "1000.00"]],
            )
            _write_csv(
                response_root / "Libro registro gastos 2024.csv",
                ["Date", "Supplier", "Invoice Number", "Concept", "IRPF deductible EUR"],
                [["2024-05-07", "Synthetic Party 016", "AP-2024-Q2", "AirPods purchase", "399.56"]],
            )
            _write_csv(
                response_root / "Complete asset amortization schedule.csv",
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
                [["SYNTH-DOCUMENT-007", "Apple Retal Spain, s.L.U.", "07/05/2024", "478.51", "0A", "81", "linear", "81"]],
            )
            _write_csv(
                response_root / "Libro registro provisiones suplidos 2024.csv",
                ["Date", "Counterparty", "Concept", "Amount"],
                [],
            )

            self.assertEqual(
                main(
                    [
                        "audit-source-book-response-check",
                        "--response-root",
                        str(response_root),
                        "--quarter-acceptance",
                        str(acceptance),
                        "--out-csv",
                        str(response_check),
                        "--out-md",
                        str(response_check_md),
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(
                    [
                        "audit-source-book-content-check",
                        "--response-root",
                        str(response_root),
                        "--source-book-response-check",
                        str(response_check),
                        "--out-csv",
                        str(content_check),
                        "--out-md",
                        str(content_check_md),
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(
                    [
                        "audit-source-book-import",
                        "--response-root",
                        str(response_root),
                        "--source-book-content-check",
                        str(content_check),
                        "--out-csv",
                        str(imported_rows),
                        "--out-md",
                        str(imported_rows_md),
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(
                    [
                        "audit-source-book-reconcile",
                        "--history-audit",
                        str(history),
                        "--source-book-rows",
                        str(imported_rows),
                        "--out-csv",
                        str(reconciliation),
                        "--out-md",
                        str(reconciliation_md),
                    ]
                ),
                0,
            )

            source_rows = _read_dicts(imported_rows)
            reconciliation_rows = _read_dicts(reconciliation)

        asset_row = next(row for row in source_rows if row["source_book_type"] == "bienes_inversion_book")
        self.assertEqual(asset_row["amortization_period"], "0A")
        self.assertEqual(asset_row["asset_id"], "SYNTH-DOCUMENT-007")
        row = reconciliation_rows[0]
        self.assertEqual(row["status"], "import_attention")
        self.assertEqual(row["imported_expense_delta"], "399.56")
        self.assertEqual(row["imported_amortization_delta"], "0.00")
        self.assertEqual(row["imported_minus_target_delta"], "0.00")
        self.assertIn("no quarterly amortization row matches this asset/year", row["notes"])


def _write_acceptance(path: Path, periods: list[str]) -> None:
    _write_dicts(
        path,
        ["period", "acceptance_status", "priority"],
        [
            {
                "period": period,
                "acceptance_status": "not_closed_material_gap_requires_source_books",
                "priority": "P0",
            }
            for period in periods
        ],
    )


def _write_history(path: Path, rows: list[dict[str, str]]) -> None:
    _write_dicts(path, ["year", "quarter", "target_casilla_02", "target_casilla_02_delta"], rows)


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


def _read_dicts(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


if __name__ == "__main__":
    unittest.main()
