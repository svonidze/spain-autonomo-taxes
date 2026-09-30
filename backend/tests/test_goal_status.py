from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.goal_status import build_goal_status


class GoalStatusTests(unittest.TestCase):
    def test_current_open_gates_report_waiting_for_xolo_books(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_gate_files(tmp_path, complete=False)

            rows = build_goal_status(**paths)

        by_gate = {row["gate"]: row for row in rows}
        self.assertEqual(by_gate["filed_pdf_sequence"]["status"], "complete")
        self.assertEqual(by_gate["target_values_coverage"]["status"], "complete")
        self.assertEqual(by_gate["scope_alignment"]["status"], "complete")
        self.assertEqual(by_gate["quarter_acceptance"]["status"], "not_closed")
        self.assertEqual(by_gate["first_gate_answer_check"]["status"], "blocked_no_xolo_answer")
        self.assertEqual(by_gate["source_book_response_package"]["status"], "missing_required_deliverables")
        self.assertEqual(by_gate["source_book_content_check"]["status"], "content_attention")
        self.assertEqual(by_gate["source_book_reconciliation"]["status"], "not_reconciled")
        self.assertEqual(by_gate["goal_verdict"]["status"], "not_complete_waiting_for_xolo_books")

    def test_all_green_gates_report_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_gate_files(tmp_path, complete=True)

            rows = build_goal_status(**paths)

        by_gate = {row["gate"]: row for row in rows}
        self.assertEqual(by_gate["target_values_coverage"]["status"], "complete")
        self.assertEqual(by_gate["quarter_acceptance"]["status"], "complete")
        self.assertEqual(by_gate["scope_alignment"]["status"], "complete")
        self.assertEqual(by_gate["first_gate_answer_check"]["status"], "ready_for_rebuild")
        self.assertEqual(by_gate["source_book_response_package"]["status"], "ready_for_intake")
        self.assertEqual(by_gate["source_book_content_check"]["status"], "ready_for_import")
        self.assertEqual(by_gate["source_book_reconciliation"]["status"], "complete")
        self.assertEqual(by_gate["goal_verdict"]["status"], "complete")

    def test_complete_source_books_supersede_stale_first_gate_answer_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_gate_files(tmp_path, complete=True)
            _write_csv(
                paths["first_gate_answer_check_csv"],
                ["check", "period", "status", "expected", "actual", "evidence", "next_action"],
                [
                    {
                        "check": "closure_verdict",
                        "period": "2023-Q2",
                        "status": "blocked_no_xolo_answer",
                        "expected": "ready_for_rebuild",
                        "evidence": "stale pre-source-book gate",
                    }
                ],
            )

            rows = build_goal_status(**paths)

        by_gate = {row["gate"]: row for row in rows}
        self.assertEqual(by_gate["quarter_acceptance"]["status"], "complete")
        self.assertEqual(by_gate["source_book_reconciliation"]["status"], "complete")
        self.assertEqual(by_gate["first_gate_answer_check"]["status"], "superseded_by_source_books")
        self.assertEqual(by_gate["goal_verdict"]["status"], "complete")

    def test_scope_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_gate_files(tmp_path, complete=True)
            _write_csv(
                paths["quarter_acceptance_csv"],
                ["period", "acceptance_status", "priority", "next_action"],
                [
                    {
                        "period": "2023-Q2",
                        "acceptance_status": "accepted_from_source_books",
                    },
                    {
                        "period": "2023-Q3",
                        "acceptance_status": "accepted_from_source_books",
                    },
                ],
            )

            rows = build_goal_status(**paths)

        by_gate = {row["gate"]: row for row in rows}
        self.assertEqual(by_gate["scope_alignment"]["status"], "scope_mismatch")
        self.assertEqual(by_gate["goal_verdict"]["status"], "not_complete_scope_mismatch")

    def test_target_values_attention_blocks_goal_even_if_verdict_row_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_gate_files(tmp_path, complete=True)
            _write_csv(
                paths["target_values_coverage_csv"],
                [
                    "period",
                    "status",
                    "report",
                    "history_status",
                    "xolo_compare_status",
                    "target_values",
                    "evidence",
                    "next_action",
                ],
                [
                    {
                        "period": "2023-Q2",
                        "status": "xolo_compare_not_matched",
                        "report": "M130 2T 2023 exampleei example.pdf",
                        "history_status": "complete",
                        "xolo_compare_status": "amount_mismatch",
                        "target_values": "01=1.00; 02=0.00; 19=0.20",
                    },
                    {
                        "period": "target_values_verdict",
                        "status": "complete",
                        "evidence": "stale contradictory fixture",
                        "next_action": "Fix target values coverage.",
                    },
                ],
            )

            rows = build_goal_status(**paths)

        by_gate = {row["gate"]: row for row in rows}
        self.assertEqual(by_gate["scope_alignment"]["status"], "complete")
        self.assertEqual(by_gate["target_values_coverage"]["status"], "attention_required")
        self.assertEqual(by_gate["target_values_coverage"]["actual"], "0/1 matched")
        self.assertEqual(by_gate["goal_verdict"]["status"], "not_complete_target_values_attention")

    def test_source_book_content_attention_blocks_goal_after_response_files_are_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_gate_files(tmp_path, complete=True)
            _write_csv(
                paths["source_book_content_check_csv"],
                [
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
                ],
                [
                    {
                        "check": "compras_gastos_book",
                        "scope": "2023",
                        "status": "content_incomplete",
                        "matched_count": "1",
                        "content_ready_count": "0",
                        "missing_required_groups": "irpf_deductible_eur",
                        "required_for": "2023-Q2",
                    },
                    {
                        "check": "content_verdict",
                        "scope": "2023-Q2..2023-Q2",
                        "status": "content_attention",
                        "matched_count": "1",
                        "content_ready_count": "0",
                        "evidence": "0/1 deliverables content-ready.",
                        "next_action": "Resolve source-book content before import.",
                    },
                ],
            )

            rows = build_goal_status(**paths)

        by_gate = {row["gate"]: row for row in rows}
        self.assertEqual(by_gate["scope_alignment"]["status"], "complete")
        self.assertEqual(by_gate["source_book_response_package"]["status"], "ready_for_intake")
        self.assertEqual(by_gate["source_book_content_check"]["status"], "content_attention")
        self.assertEqual(by_gate["goal_verdict"]["status"], "not_complete_source_book_content_attention")

    def test_source_book_reconciliation_blocks_goal_after_content_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_gate_files(tmp_path, complete=True)
            _write_csv(
                paths["source_book_reconciliation_csv"],
                [
                    "period",
                    "status",
                    "target_casilla_02_ytd",
                    "target_casilla_02_delta",
                    "imported_total_delta",
                    "imported_minus_target_delta",
                    "tieout_casilla02_delta",
                    "tieout_delta_minus_target",
                    "notes",
                ],
                [
                    {
                        "period": "2023-Q2",
                        "status": "no_imported_source_book_rows",
                        "target_casilla_02_ytd": "115.54",
                        "target_casilla_02_delta": "115.54",
                        "imported_total_delta": "0.00",
                        "imported_minus_target_delta": "-115.54",
                    }
                ],
            )

            rows = build_goal_status(**paths)

        by_gate = {row["gate"]: row for row in rows}
        self.assertEqual(by_gate["scope_alignment"]["status"], "complete")
        self.assertEqual(by_gate["source_book_response_package"]["status"], "ready_for_intake")
        self.assertEqual(by_gate["source_book_content_check"]["status"], "ready_for_import")
        self.assertEqual(by_gate["source_book_reconciliation"]["status"], "not_reconciled")
        self.assertEqual(by_gate["goal_verdict"]["status"], "not_complete_source_book_reconciliation")

    def test_reconciliation_short_scope_fails_closed_even_when_rows_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_gate_files(tmp_path, complete=True)
            _write_two_period_complete_scope(paths)

            rows = build_goal_status(**paths)

        by_gate = {row["gate"]: row for row in rows}
        self.assertEqual(by_gate["source_book_reconciliation"]["status"], "complete")
        self.assertEqual(by_gate["scope_alignment"]["status"], "scope_mismatch")
        self.assertEqual(by_gate["goal_verdict"]["status"], "not_complete_scope_mismatch")

    def test_empty_source_book_reconciliation_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_gate_files(tmp_path, complete=True)
            _write_csv(
                paths["source_book_reconciliation_csv"],
                [
                    "period",
                    "status",
                    "target_casilla_02_ytd",
                    "target_casilla_02_delta",
                    "imported_total_delta",
                    "imported_minus_target_delta",
                    "tieout_casilla02_delta",
                    "tieout_delta_minus_target",
                    "notes",
                ],
                [],
            )

            rows = build_goal_status(**paths)

        by_gate = {row["gate"]: row for row in rows}
        self.assertEqual(by_gate["source_book_reconciliation"]["status"], "not_reconciled")
        self.assertNotEqual(by_gate["goal_verdict"]["status"], "complete")

    def test_unknown_acceptance_status_does_not_count_as_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_gate_files(tmp_path, complete=True)
            _write_csv(
                paths["quarter_acceptance_csv"],
                ["period", "acceptance_status", "priority", "next_action"],
                [{"period": "2023-Q2", "acceptance_status": ""}],
            )

            rows = build_goal_status(**paths)

        by_gate = {row["gate"]: row for row in rows}
        self.assertEqual(by_gate["quarter_acceptance"]["status"], "not_closed")
        self.assertNotEqual(by_gate["goal_verdict"]["status"], "complete")

    def test_cli_writes_goal_status_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_gate_files(tmp_path, complete=False)
            out_csv = tmp_path / "goal.csv"
            out_md = tmp_path / "goal.md"

            exit_code = main(
                [
                    "audit-goal-status",
                    "--tax-report-sequence",
                    str(paths["tax_report_sequence_csv"]),
                    "--target-values-coverage",
                    str(paths["target_values_coverage_csv"]),
                    "--quarter-acceptance",
                    str(paths["quarter_acceptance_csv"]),
                    "--first-gate-answer-check",
                    str(paths["first_gate_answer_check_csv"]),
                    "--source-book-response-check",
                    str(paths["source_book_response_check_csv"]),
                    "--source-book-content-check",
                    str(paths["source_book_content_check_csv"]),
                    "--source-book-reconciliation",
                    str(paths["source_book_reconciliation_csv"]),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Modelo 130 Goal Status", markdown)
            self.assertIn("not_complete_waiting_for_xolo_books", markdown)


def _write_gate_files(tmp_path: Path, *, complete: bool) -> dict[str, Path]:
    tax_report_sequence = tmp_path / "tax_report_sequence.csv"
    _write_csv(
        tax_report_sequence,
        ["period", "expected_label", "status", "matched_count", "matched_files", "evidence", "next_action"],
        [
            {
                "period": "2023-Q2",
                "status": "found",
                "matched_count": "1",
                "matched_files": "M130 2T 2023 exampleei example.pdf",
            },
            {
                "period": "sequence_verdict",
                "status": "complete",
                "matched_count": "1",
                "evidence": "Checked 1 expected Modelo 130 quarters.",
            },
        ],
    )

    target_values_coverage = tmp_path / "target_values_coverage.csv"
    _write_csv(
        target_values_coverage,
        [
            "period",
            "status",
            "report",
            "history_status",
            "xolo_compare_status",
            "target_values",
            "evidence",
            "next_action",
        ],
        [
            {
                "period": "2023-Q2",
                "status": "matched",
                "report": "M130 2T 2023 exampleei example.pdf",
                "history_status": "complete",
                "xolo_compare_status": "matched",
                "target_values": "01=1.00; 02=0.00; 19=0.20",
            },
            {
                "period": "target_values_verdict",
                "status": "complete",
                "evidence": "1/1 periods matched.",
            },
        ],
    )

    quarter_acceptance = tmp_path / "quarter_acceptance.csv"
    _write_csv(
        quarter_acceptance,
        ["period", "acceptance_status", "priority", "next_action"],
        [
            {
                "period": "2023-Q2",
                "acceptance_status": "accepted_from_source_books"
                if complete
                else "not_closed_material_gap_requires_source_books",
                "priority": "" if complete else "P0",
            }
        ],
    )

    first_gate_answer_check = tmp_path / "first_gate_answer_check.csv"
    _write_csv(
        first_gate_answer_check,
        ["check", "period", "status", "expected", "actual", "evidence", "next_action"],
        [
            {
                "check": "closure_verdict",
                "period": "2023-Q2",
                "status": "ready_for_rebuild" if complete else "blocked_no_xolo_answer",
                "expected": "ready_for_rebuild",
                "evidence": "fixture",
                "next_action": "fixture next",
            }
        ],
    )

    source_book_response_check = tmp_path / "source_book_response_check.csv"
    _write_csv(
        source_book_response_check,
        ["check", "scope", "status", "matched_count", "matched_files", "required_for", "next_action"],
        [
            {
                "check": "package_verdict",
                "scope": "2023-Q2..2023-Q2",
                "status": "ready_for_intake" if complete else "missing_required_deliverables",
                "matched_count": "3" if complete else "0",
                "required_for": "all quarters in quarter acceptance matrix",
                "next_action": "fixture next",
            }
        ],
    )

    source_book_content_check = tmp_path / "source_book_content_check.csv"
    _write_csv(
        source_book_content_check,
        [
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
        ],
        [
            {
                "check": "compras_gastos_book",
                "scope": "2023",
                "status": "content_ready" if complete else "missing_response_file",
                "matched_count": "1" if complete else "0",
                "content_ready_count": "1" if complete else "0",
                "required_for": "2023-Q2",
            },
            {
                "check": "content_verdict",
                "scope": "2023-Q2..2023-Q2",
                "status": "ready_for_import" if complete else "content_attention",
                "matched_count": "1",
                "content_ready_count": "1" if complete else "0",
                "evidence": "fixture",
                "next_action": "fixture next",
            },
        ],
    )

    source_book_reconciliation = tmp_path / "source_book_reconciliation.csv"
    _write_csv(
        source_book_reconciliation,
        [
            "period",
            "status",
            "target_casilla_02_ytd",
            "target_casilla_02_delta",
            "imported_total_delta",
            "imported_minus_target_delta",
            "tieout_casilla02_delta",
            "tieout_delta_minus_target",
            "notes",
        ],
        [
            {
                "period": "2023-Q2",
                "status": "rows_and_tieout_match_target" if complete else "no_imported_source_book_rows",
                "target_casilla_02_ytd": "115.54",
                "target_casilla_02_delta": "115.54",
                "imported_total_delta": "115.54" if complete else "0.00",
                "imported_minus_target_delta": "0.00" if complete else "-115.54",
                "tieout_casilla02_delta": "115.54" if complete else "",
                "tieout_delta_minus_target": "0.00" if complete else "",
            }
        ],
    )

    return {
        "tax_report_sequence_csv": tax_report_sequence,
        "target_values_coverage_csv": target_values_coverage,
        "quarter_acceptance_csv": quarter_acceptance,
        "first_gate_answer_check_csv": first_gate_answer_check,
        "source_book_response_check_csv": source_book_response_check,
        "source_book_content_check_csv": source_book_content_check,
        "source_book_reconciliation_csv": source_book_reconciliation,
    }


def _write_two_period_complete_scope(paths: dict[str, Path]) -> None:
    _write_csv(
        paths["tax_report_sequence_csv"],
        ["period", "expected_label", "status", "matched_count", "matched_files", "evidence", "next_action"],
        [
            {
                "period": "2023-Q2",
                "status": "found",
                "matched_count": "1",
                "matched_files": "M130 2T 2023 exampleei example.pdf",
            },
            {
                "period": "2023-Q3",
                "status": "found",
                "matched_count": "1",
                "matched_files": "M130 3T 2023 exampleei example.pdf",
            },
            {
                "period": "sequence_verdict",
                "status": "complete",
                "matched_count": "2",
                "evidence": "Checked 2 expected Modelo 130 quarters.",
            },
        ],
    )
    _write_csv(
        paths["target_values_coverage_csv"],
        [
            "period",
            "status",
            "report",
            "history_status",
            "xolo_compare_status",
            "target_values",
            "evidence",
            "next_action",
        ],
        [
            {
                "period": "2023-Q2",
                "status": "matched",
                "report": "M130 2T 2023 exampleei example.pdf",
                "history_status": "complete",
                "xolo_compare_status": "matched",
                "target_values": "01=1.00; 02=115.54; 19=0.20",
            },
            {
                "period": "2023-Q3",
                "status": "matched",
                "report": "M130 3T 2023 exampleei example.pdf",
                "history_status": "complete",
                "xolo_compare_status": "matched",
                "target_values": "01=1.00; 02=377.55; 19=0.20",
            },
            {
                "period": "target_values_verdict",
                "status": "complete",
                "evidence": "2/2 periods matched.",
            },
        ],
    )
    _write_csv(
        paths["quarter_acceptance_csv"],
        ["period", "acceptance_status", "priority", "next_action"],
        [
            {"period": "2023-Q2", "acceptance_status": "accepted_from_source_books"},
            {"period": "2023-Q3", "acceptance_status": "accepted_from_source_books"},
        ],
    )
    _write_csv(
        paths["source_book_response_check_csv"],
        ["check", "scope", "status", "matched_count", "matched_files", "required_for", "next_action"],
        [
            {
                "check": "package_verdict",
                "scope": "2023-Q2..2023-Q3",
                "status": "ready_for_intake",
                "matched_count": "3",
                "required_for": "all quarters in quarter acceptance matrix",
                "next_action": "fixture next",
            }
        ],
    )
    _write_csv(
        paths["source_book_content_check_csv"],
        [
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
        ],
        [
            {
                "check": "content_verdict",
                "scope": "2023-Q2..2023-Q3",
                "status": "ready_for_import",
                "matched_count": "3",
                "content_ready_count": "3",
                "evidence": "fixture",
                "next_action": "fixture next",
            },
        ],
    )


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


if __name__ == "__main__":
    unittest.main()
