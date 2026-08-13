from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.source_book_import import SOURCE_BOOK_IMPORT_FIELDS
from autonomo_taxes.source_book_reconcile import build_source_book_reconciliation


class SourceBookReconcileTests(unittest.TestCase):
    def test_reconciles_expense_asset_and_tieout_rows_to_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history = tmp_path / "history.csv"
            source_rows = tmp_path / "source_rows.csv"
            _write_history(
                history,
                [
                    {"year": "2026", "quarter": "1", "target_casilla_02": "100.00", "target_casilla_02_delta": "100.00"},
                    {"year": "2026", "quarter": "2", "target_casilla_02": "250.00", "target_casilla_02_delta": "150.00"},
                ],
            )
            _write_source_rows(
                source_rows,
                [
                    _row("gastos_book", period="2026-Q1", irpf_deductible_eur="100.00"),
                    _row("modelo130_source_book_tieout", period="2026-Q1", casilla02_ytd="100.00"),
                    _row("gastos_book", period="2026-Q2", irpf_deductible_eur="120.00"),
                    _row(
                        "bienes_inversion_book",
                        amortization_period="2026-Q2",
                        amortization_amount_eur="30.00",
                    ),
                    _row("modelo130_source_book_tieout", period="2026-Q2", casilla02_ytd="250.00"),
                ],
            )

            rows = build_source_book_reconciliation(history, source_rows)

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(by_period["2026-Q1"]["status"], "rows_and_tieout_match_target")
        self.assertEqual(by_period["2026-Q1"]["tieout_casilla02_delta"], "100.00")
        self.assertEqual(by_period["2026-Q2"]["status"], "rows_and_tieout_match_target")
        self.assertEqual(by_period["2026-Q2"]["imported_expense_delta"], "120.00")
        self.assertEqual(by_period["2026-Q2"]["imported_amortization_delta"], "30.00")
        self.assertEqual(by_period["2026-Q2"]["imported_total_delta"], "150.00")
        self.assertEqual(by_period["2026-Q2"]["tieout_casilla02_delta"], "150.00")
        self.assertEqual(by_period["2026-Q2"]["tieout_delta_minus_target"], "0.00")

    def test_empty_import_keeps_quarter_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history = tmp_path / "history.csv"
            source_rows = tmp_path / "source_rows.csv"
            _write_history(
                history,
                [{"year": "2026", "quarter": "2", "target_casilla_02": "250.00", "target_casilla_02_delta": "150.00"}],
            )
            _write_source_rows(source_rows, [])

            rows = build_source_book_reconciliation(history, source_rows)

        self.assertEqual(rows[0]["status"], "no_imported_source_book_rows")
        self.assertEqual(rows[0]["notes"], "No imported source-book rows for this quarter.")

    def test_skipped_import_row_blocks_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history = tmp_path / "history.csv"
            source_rows = tmp_path / "source_rows.csv"
            _write_history(
                history,
                [{"year": "2026", "quarter": "2", "target_casilla_02": "250.00", "target_casilla_02_delta": "150.00"}],
            )
            _write_source_rows(
                source_rows,
                [_row("gastos_book", period="2026-Q2", import_status="skipped_unreadable_source_file")],
            )

            rows = build_source_book_reconciliation(history, source_rows)

        self.assertEqual(rows[0]["status"], "import_attention")
        self.assertEqual(rows[0]["skipped_row_count"], "1")

    def test_malformed_amount_marks_period_attention_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history = tmp_path / "history.csv"
            source_rows = tmp_path / "source_rows.csv"
            _write_history(
                history,
                [{"year": "2026", "quarter": "2", "target_casilla_02": "250.00", "target_casilla_02_delta": "150.00"}],
            )
            _write_source_rows(
                source_rows,
                [_row("gastos_book", period="2026-Q2", irpf_deductible_eur="N/A")],
            )

            rows = build_source_book_reconciliation(history, source_rows)

        self.assertEqual(rows[0]["status"], "import_attention")
        self.assertEqual(rows[0]["amount_parse_error_count"], "1")
        self.assertIn("Malformed amount cell", rows[0]["notes"])
        self.assertIn("irpf_deductible_eur='N/A'", rows[0]["notes"])

    def test_unassigned_imported_row_blocks_all_quarter_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history = tmp_path / "history.csv"
            source_rows = tmp_path / "source_rows.csv"
            _write_history(
                history,
                [{"year": "2026", "quarter": "2", "target_casilla_02": "150.00", "target_casilla_02_delta": "150.00"}],
            )
            _write_source_rows(
                source_rows,
                [
                    _row("gastos_book", period="2026-Q2", irpf_deductible_eur="150.00"),
                    _row("gastos_book", source_book_line_id="missing-period", irpf_deductible_eur="1.00"),
                ],
            )

            rows = build_source_book_reconciliation(history, source_rows)

        self.assertEqual(rows[0]["status"], "import_attention")
        self.assertEqual(rows[0]["unassigned_row_count"], "1")
        self.assertIn("missing-period", rows[0]["notes"])

    def test_unassigned_row_only_blocks_its_inferred_tax_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history = tmp_path / "history.csv"
            source_rows = tmp_path / "source_rows.csv"
            _write_history(
                history,
                [
                    {"year": "2025", "quarter": "4", "target_casilla_02": "100.00", "target_casilla_02_delta": "100.00"},
                    {"year": "2026", "quarter": "2", "target_casilla_02": "150.00", "target_casilla_02_delta": "150.00"},
                ],
            )
            _write_source_rows(
                source_rows,
                [
                    _row("gastos_book", source_scope="2025", period="2025-Q4", irpf_deductible_eur="100.00"),
                    _row("gastos_book", period="2026-Q2", irpf_deductible_eur="150.00"),
                    _row("gastos_book", source_book_line_id="unassigned-2026", irpf_deductible_eur="1.00"),
                ],
            )

            rows = build_source_book_reconciliation(history, source_rows)

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(by_period["2025-Q4"]["status"], "rows_match_target_no_tieout")
        self.assertEqual(by_period["2025-Q4"]["unassigned_row_count"], "0")
        self.assertEqual(by_period["2026-Q2"]["status"], "import_attention")

    def test_q4_annual_modelo100_adjustment_reconciles_filed_modelo130_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history = tmp_path / "history.csv"
            source_rows = tmp_path / "source_rows.csv"
            annual = tmp_path / "annual.csv"
            _write_history(
                history,
                [{"year": "2026", "quarter": "4", "target_casilla_02": "100.00", "target_casilla_02_delta": "100.00"}],
            )
            _write_source_rows(
                source_rows,
                [
                    _row("gastos_book", period="2026-Q4", irpf_deductible_eur="80.00", source_book_line_id="regular"),
                    _row("gastos_book", period="2026-Q4", irpf_deductible_eur="25.00", source_book_line_id="annual-only"),
                    _row("gastos_book", period="2026-Q4", irpf_deductible_eur="20.00", source_book_line_id="regular-2"),
                ],
            )
            _write_annual_comparison(
                annual,
                [{"year": "2026", "status": "filed_text_extract", "annual_minus_m130_q4": "25.00"}],
            )

            rows = build_source_book_reconciliation(history, source_rows, annual)

        self.assertEqual(rows[0]["status"], "rows_match_target_after_annual_adjustment_no_tieout")
        self.assertEqual(rows[0]["imported_total_delta"], "125.00")
        self.assertEqual(rows[0]["imported_minus_target_delta"], "25.00")
        self.assertEqual(rows[0]["annual_adjustment_delta"], "25.00")
        self.assertEqual(rows[0]["adjusted_imported_total_delta"], "100.00")
        self.assertEqual(rows[0]["adjusted_minus_target_delta"], "0.00")
        self.assertIn("annual_adjustment_delta=25.00", rows[0]["notes"])

    def test_annual_adjustment_requires_filed_extract_and_matching_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history = tmp_path / "history.csv"
            source_rows = tmp_path / "source_rows.csv"
            annual = tmp_path / "annual.csv"
            _write_history(
                history,
                [{"year": "2026", "quarter": "4", "target_casilla_02": "100.00", "target_casilla_02_delta": "100.00"}],
            )
            _write_source_rows(
                source_rows,
                [_row("gastos_book", period="2026-Q4", irpf_deductible_eur="125.00")],
            )
            _write_annual_comparison(
                annual,
                [
                    {"year": "2026", "status": "draft_text_extract", "annual_minus_m130_q4": "25.00"},
                    {"year": "2027", "status": "filed_text_extract", "annual_minus_m130_q4": "20.00"},
                ],
            )

            rows = build_source_book_reconciliation(history, source_rows, annual)

        self.assertEqual(rows[0]["status"], "source_book_rows_mismatch")
        self.assertEqual(rows[0]["annual_adjustment_delta"], "0.00")
        self.assertEqual(rows[0]["adjusted_minus_target_delta"], "25.00")

    def test_nonmatching_annual_adjustment_does_not_reconcile_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history = tmp_path / "history.csv"
            source_rows = tmp_path / "source_rows.csv"
            annual = tmp_path / "annual.csv"
            _write_history(
                history,
                [{"year": "2026", "quarter": "4", "target_casilla_02": "100.00", "target_casilla_02_delta": "100.00"}],
            )
            _write_source_rows(
                source_rows,
                [_row("gastos_book", period="2026-Q4", irpf_deductible_eur="125.00")],
            )
            _write_annual_comparison(
                annual,
                [{"year": "2026", "status": "filed_text_extract", "annual_minus_m130_q4": "20.00"}],
            )

            rows = build_source_book_reconciliation(history, source_rows, annual)

        self.assertEqual(rows[0]["status"], "source_book_rows_mismatch")
        self.assertEqual(rows[0]["annual_adjustment_delta"], "0.00")
        self.assertEqual(rows[0]["adjusted_minus_target_delta"], "25.00")

    def test_annual_airpods_row_without_quarterly_amortization_blocks_false_green_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history = tmp_path / "history.csv"
            source_rows = tmp_path / "source_rows.csv"
            _write_history(
                history,
                [{"year": "2024", "quarter": "2", "target_casilla_02": "399.56", "target_casilla_02_delta": "399.56"}],
            )
            _write_source_rows(
                source_rows,
                [
                    _row("gastos_book", source_scope="2024", period="2024-Q2", irpf_deductible_eur="399.56"),
                    _row(
                        "bienes_inversion_book",
                        source_scope="2024",
                        source_book_line_id="airpods-annual-2024",
                        date="07/05/2024",
                        asset_id="SYNTH-DOCUMENT-007",
                        amortizable_base_eur="478.51",
                        amortization_period="0A",
                        amortization_amount_eur="81",
                    ),
                ],
            )

            rows = build_source_book_reconciliation(history, source_rows)

        self.assertEqual(rows[0]["status"], "import_attention")
        self.assertEqual(rows[0]["imported_expense_delta"], "399.56")
        self.assertEqual(rows[0]["imported_amortization_delta"], "0.00")
        self.assertEqual(rows[0]["imported_minus_target_delta"], "0.00")
        self.assertEqual(rows[0]["unassigned_row_count"], "0")
        self.assertIn("SYNTH-DOCUMENT-007", rows[0]["notes"])
        self.assertIn("no quarterly amortization row matches this asset/year", rows[0]["notes"])

    def test_annual_airpods_row_cross_checks_against_quarterly_amortization_without_double_counting(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history = tmp_path / "history.csv"
            source_rows = tmp_path / "source_rows.csv"
            _write_history(
                history,
                [{"year": "2024", "quarter": "2", "target_casilla_02": "480.56", "target_casilla_02_delta": "480.56"}],
            )
            _write_source_rows(
                source_rows,
                [
                    _row("gastos_book", source_scope="2024", period="2024-Q2", irpf_deductible_eur="399.56"),
                    _row(
                        "bienes_inversion_book",
                        source_scope="2024",
                        source_book_line_id="airpods-q2-2024",
                        date="07/05/2024",
                        asset_id="SYNTH-DOCUMENT-007",
                        amortizable_base_eur="478.51",
                        amortization_period="2024-Q2",
                        amortization_amount_eur="81",
                    ),
                    _row(
                        "bienes_inversion_book",
                        source_scope="2024",
                        source_book_line_id="airpods-annual-2024",
                        date="07/05/2024",
                        asset_id="SYNTH-DOCUMENT-007",
                        amortizable_base_eur="478.51",
                        amortization_period="0A",
                        amortization_amount_eur="81",
                    ),
                ],
            )

            rows = build_source_book_reconciliation(history, source_rows)

        self.assertEqual(rows[0]["status"], "rows_match_target_no_tieout")
        self.assertEqual(rows[0]["imported_expense_delta"], "399.56")
        self.assertEqual(rows[0]["imported_amortization_delta"], "81.00")
        self.assertEqual(rows[0]["imported_total_delta"], "480.56")
        self.assertEqual(rows[0]["imported_minus_target_delta"], "0.00")
        self.assertEqual(rows[0]["notes"], "No Modelo 130 tie-out row imported for this period.")

    def test_cli_writes_reconciliation_report_without_tieout(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history = tmp_path / "history.csv"
            source_rows = tmp_path / "source_rows.csv"
            out_csv = tmp_path / "reconcile.csv"
            out_md = tmp_path / "reconcile.md"
            _write_history(
                history,
                [{"year": "2026", "quarter": "2", "target_casilla_02": "150.00", "target_casilla_02_delta": "150.00"}],
            )
            _write_source_rows(
                source_rows,
                [
                    _row("gastos_book", period="2026-Q2", irpf_deductible_eur="120.00"),
                    _row(
                        "bienes_inversion_book",
                        amortization_period="2026-Q2",
                        amortization_amount_eur="30.00",
                    ),
                ],
            )

            exit_code = main(
                [
                    "audit-source-book-reconcile",
                    "--history-audit",
                    str(history),
                    "--source-book-rows",
                    str(source_rows),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )
            rows = list(csv.DictReader(out_csv.open("r", newline="", encoding="utf-8-sig")))
            markdown = out_md.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(rows[0]["status"], "rows_match_target_no_tieout")
        self.assertIn("Xolo Source-Book Quarter Reconciliation", markdown)
        self.assertIn("rows_match_target_no_tieout", markdown)


def _write_history(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["year", "quarter", "target_casilla_02", "target_casilla_02_delta"])
        writer.writeheader()
        writer.writerows(rows)


def _write_source_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_BOOK_IMPORT_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in SOURCE_BOOK_IMPORT_FIELDS})


def _write_annual_comparison(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["year", "status", "annual_minus_m130_q4"])
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in writer.fieldnames})


def _row(source_book_type: str, **kwargs) -> dict[str, str]:
    row = {field: "" for field in SOURCE_BOOK_IMPORT_FIELDS}
    row.update(
        {
            "source_book_type": source_book_type,
            "source_scope": "2026",
            "source_file": "source.csv",
            "source_file_format": "csv",
            "source_row_number": "2",
            "source_book_line_id": "line-1",
            "import_status": "imported",
        }
    )
    row.update(kwargs)
    return row


if __name__ == "__main__":
    unittest.main()
