from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.material_gap_drilldown import MATERIAL_GAP_DRILLDOWN_FIELDS
from autonomo_taxes.quarter_balance_bridge import QUARTER_BALANCE_BRIDGE_FIELDS
from autonomo_taxes.quarter_closure import QUARTER_CLOSURE_FIELDS
from autonomo_taxes.tax_report_sequence import TAX_REPORT_SEQUENCE_FIELDS
from autonomo_taxes.target_values_coverage import TARGET_VALUES_COVERAGE_FIELDS


class SourceBookRefreshTests(unittest.TestCase):
    def test_cli_refresh_runs_intake_through_complete_goal_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            response_root = root / "xolo-response"
            response_root.mkdir()
            runs = root / "runs"
            runs.mkdir()
            _write_gate_inputs(runs)
            _write_source_book_response(response_root)
            out_csv = runs / "refresh.csv"
            out_md = runs / "refresh.md"

            exit_code = main(
                [
                    "audit-source-book-refresh",
                    "--response-root",
                    str(response_root),
                    "--runs-root",
                    str(runs),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            refresh_rows = _read_dicts(out_csv)
            acceptance_rows = _read_dicts(runs / "modelo130_quarter_acceptance.csv")
            reconciliation_rows = _read_dicts(runs / "xolo_source_book_reconciliation.csv")
            goal_rows = _read_dicts(runs / "modelo130_goal_status.csv")
            imported_rows = _read_dicts(runs / "xolo_source_book_rows.csv")
            markdown = out_md.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual([row["step"] for row in refresh_rows], [
            "source_book_response_check",
            "source_book_content_check",
            "source_book_import",
            "source_book_reconciliation",
            "quarter_acceptance",
            "goal_status",
        ])
        by_step = {row["step"]: row for row in refresh_rows}
        self.assertEqual(by_step["source_book_response_check"]["status"], "ready_for_intake")
        self.assertEqual(by_step["source_book_content_check"]["status"], "ready_for_import")
        self.assertEqual(by_step["source_book_import"]["status"], "imported")
        self.assertEqual(by_step["source_book_reconciliation"]["status"], "complete")
        self.assertEqual(by_step["quarter_acceptance"]["status"], "complete")
        self.assertEqual(by_step["goal_status"]["status"], "complete")
        self.assertIn("does not infer accounting treatment from target fitting", markdown)
        self.assertEqual({row["acceptance_status"] for row in acceptance_rows}, {"accepted_from_source_books"})
        self.assertEqual({row["status"] for row in reconciliation_rows}, {"rows_match_target_no_tieout"})
        self.assertEqual(next(row for row in goal_rows if row["gate"] == "goal_verdict")["status"], "complete")
        self.assertEqual(len(imported_rows), 6)

    def test_cli_refresh_keeps_goal_open_when_xolo_files_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            response_root = root / "xolo-response"
            response_root.mkdir()
            runs = root / "runs"
            runs.mkdir()
            _write_gate_inputs(runs)
            out_csv = runs / "refresh.csv"
            out_md = runs / "refresh.md"

            exit_code = main(
                [
                    "audit-source-book-refresh",
                    "--response-root",
                    str(response_root),
                    "--runs-root",
                    str(runs),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            refresh_rows = _read_dicts(out_csv)
            goal_rows = _read_dicts(runs / "modelo130_goal_status.csv")

        self.assertEqual(exit_code, 0)
        by_step = {row["step"]: row for row in refresh_rows}
        self.assertEqual(by_step["source_book_response_check"]["status"], "missing_required_deliverables")
        self.assertEqual(by_step["goal_status"]["status"], "not_complete_waiting_for_xolo_books")
        self.assertEqual(
            next(row for row in goal_rows if row["gate"] == "goal_verdict")["status"],
            "not_complete_waiting_for_xolo_books",
        )

    def test_cli_refresh_writes_default_summary_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            response_root = root / "xolo-response"
            response_root.mkdir()
            runs = root / "runs"
            runs.mkdir()
            _write_gate_inputs(runs)

            exit_code = main(
                [
                    "audit-source-book-refresh",
                    "--response-root",
                    str(response_root),
                    "--runs-root",
                    str(runs),
                ]
            )

            refresh_rows = _read_dicts(runs / "xolo_source_book_refresh.csv")
            refresh_markdown_exists = (runs / "xolo_source_book_refresh.md").exists()

        self.assertEqual(exit_code, 0)
        self.assertTrue(refresh_markdown_exists)
        self.assertEqual(
            next(row for row in refresh_rows if row["step"] == "goal_status")["status"],
            "not_complete_waiting_for_xolo_books",
        )


def _write_gate_inputs(runs: Path) -> None:
    periods = ["2023-Q2", "2024-Q1"]
    _write_dicts(
        runs / "modelo130_quarter_acceptance.csv",
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
    _write_dicts(
        runs / "modelo130_history_audit.csv",
        [
            "year",
            "quarter",
            "target_casilla_01",
            "target_casilla_02",
            "target_casilla_02_delta",
            "target_casilla_07",
            "target_casilla_13",
            "target_casilla_19",
            "report",
        ],
        [
            {
                "year": "2023",
                "quarter": "2",
                "target_casilla_01": "1000.00",
                "target_casilla_02": "115.54",
                "target_casilla_02_delta": "115.54",
                "target_casilla_07": "176.89",
                "target_casilla_13": "100.00",
                "target_casilla_19": "76.89",
                "report": "M130 2T 2023 exampleei example.pdf",
            },
            {
                "year": "2024",
                "quarter": "1",
                "target_casilla_01": "2000.00",
                "target_casilla_02": "254.36",
                "target_casilla_02_delta": "254.36",
                "target_casilla_07": "349.13",
                "target_casilla_13": "200.00",
                "target_casilla_19": "149.13",
                "report": "M130 1T 2024 exampleei example.pdf",
            },
        ],
    )
    _write_dicts(
        runs / "modelo130_quarter_closure.csv",
        QUARTER_CLOSURE_FIELDS,
        [
            _closure_row("2023-Q2", "115.54"),
            _closure_row("2024-Q1", "254.36"),
        ],
    )
    _write_dicts(
        runs / "modelo130_quarter_balance_bridge.csv",
        QUARTER_BALANCE_BRIDGE_FIELDS,
        [
            _bridge_row("2023-Q2", "115.54"),
            _bridge_row("2024-Q1", "254.36"),
        ],
    )
    _write_dicts(runs / "modelo130_material_gap_drilldown.csv", MATERIAL_GAP_DRILLDOWN_FIELDS, [])
    (runs / "modelo130_quarter_packets").mkdir()
    _write_dicts(
        runs / "modelo130_tax_report_sequence.csv",
        TAX_REPORT_SEQUENCE_FIELDS,
        [
            _sequence_row("2023-Q2", "M130 2T 2023 exampleei example.pdf"),
            _sequence_row("2024-Q1", "M130 1T 2024 exampleei example.pdf"),
            {
                "period": "sequence_verdict",
                "status": "complete",
                "matched_count": "2",
                "evidence": "Checked 2 expected Modelo 130 quarters.",
            },
        ],
    )
    _write_dicts(
        runs / "modelo130_target_values_coverage.csv",
        TARGET_VALUES_COVERAGE_FIELDS,
        [
            _target_row("2023-Q2", "M130 2T 2023 exampleei example.pdf"),
            _target_row("2024-Q1", "M130 1T 2024 exampleei example.pdf"),
            {
                "period": "target_values_verdict",
                "status": "complete",
                "evidence": "2/2 periods matched.",
            },
        ],
    )
    _write_dicts(
        runs / "modelo130_first_gate_answer_check.csv",
        ["check", "period", "status", "expected", "actual", "evidence", "next_action"],
        [
            {
                "check": "closure_verdict",
                "period": "2023-Q2",
                "status": "ready_for_rebuild",
                "expected": "ready_for_rebuild",
                "actual": "ready_for_rebuild",
                "evidence": "Fixture has structured source-book evidence.",
                "next_action": "Continue to source-book refresh.",
            }
        ],
    )


def _write_source_book_response(response_root: Path) -> None:
    _write_csv(
        response_root / "Libro registro ingresos 2023-2024.csv",
        ["Date", "Customer", "Invoice Number", "Concept", "Income EUR"],
        [
            ["2023-06-30", "Client", "I-2023", "Services", "1000.00"],
            ["2024-01-04", "Client", "I-2024", "Services", "2000.00"],
        ],
    )
    _write_csv(
        response_root / "Libro registro gastos 2023-2024.csv",
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
        response_root / "Libro registro provisiones suplidos 2023-2024.csv",
        ["Date", "Counterparty", "Concept", "Amount"],
        [],
    )


def _closure_row(period: str, target: str) -> dict[str, str]:
    return {
        "period": period,
        "closure_status": "blocked_material_unexplained_adjustment",
        "target_casilla_02_delta": target,
        "pre_plug_model_minus_target_eur": target,
        "balancing_adjustment_eur": target,
        "balancing_adjustment_pct_of_target": "1.00",
        "balancing_adjustment_materiality": "material",
        "candidate_amortization_delta": "0.00",
        "excluded_asset_direct_eur": "0.00",
        "excluded_nearest_subset_eur": "0.00",
        "has_material_plug": "yes",
        "has_asset_decision": "no",
        "has_nearest_exclusion": "no",
        "has_minor_residual": "no",
        "asset_rows": "",
        "nearest_exclusion_rows": "",
        "required_xolo_evidence": "source-book export with deductible EUR amount per row; asset schedule",
        "next_question": f"Confirm source-book rows for {period}.",
    }


def _bridge_row(period: str, target: str) -> dict[str, str]:
    return {
        "period": period,
        "target_casilla_02_delta": target,
        "raw_non_asset_delta": "0.00",
        "candidate_amortization_delta": "0.00",
        "nearest_excluded_or_netted_eur": "0.00",
        "candidate_model_after_local_adjustments": "0.00",
        "candidate_balance_to_target": target,
        "annual_constraint_source": "fixture",
        "annual_constrained_amortization_delta": "0.00",
        "annual_constrained_model_after_local_adjustments": "0.00",
        "annual_constrained_balance_to_target": target,
        "amortization_shift_eur": "0.00",
        "balance_signal": "material_source_book_gap",
        "closure_status": "blocked_material_unexplained_adjustment",
        "required_xolo_evidence": "source-book export with deductible EUR amount per row; asset schedule",
        "equation": "fixture",
    }


def _sequence_row(period: str, filename: str) -> dict[str, str]:
    return {
        "period": period,
        "expected_label": period,
        "status": "found",
        "matched_count": "1",
        "matched_files": filename,
        "evidence": "fixture",
        "next_action": "fixture next",
    }


def _target_row(period: str, filename: str) -> dict[str, str]:
    return {
        "period": period,
        "status": "matched",
        "report": filename,
        "history_status": "complete",
        "xolo_compare_status": "matched",
        "target_values": "fixture",
        "evidence": "fixture",
        "next_action": "fixture next",
    }


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
