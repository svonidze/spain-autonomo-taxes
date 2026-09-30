from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.material_gap_drilldown import MATERIAL_GAP_DRILLDOWN_FIELDS
from autonomo_taxes.quarter_acceptance import build_quarter_acceptance_matrix
from autonomo_taxes.quarter_balance_bridge import QUARTER_BALANCE_BRIDGE_FIELDS
from autonomo_taxes.quarter_closure import QUARTER_CLOSURE_FIELDS
from autonomo_taxes.source_book_reconcile import SOURCE_BOOK_RECONCILIATION_FIELDS


class QuarterAcceptanceTests(unittest.TestCase):
    def test_prioritizes_material_gaps_without_closing_near_fit_quarters(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_inputs(Path(tmp))

            rows = build_quarter_acceptance_matrix(
                paths["closure"],
                paths["bridge"],
                paths["material"],
                paths["packets"],
            )

        by_period = {row["period"]: row for row in rows}
        q2 = by_period["2023-Q2"]
        self.assertEqual(q2["priority"], "P0")
        self.assertEqual(q2["acceptance_status"], "not_closed_material_gap_requires_source_books")
        self.assertEqual(q2["material_gap_focus"], "yes")
        self.assertIn("Which source-book row", q2["next_action"])
        self.assertNotIn("submitted register", q2["required_evidence"])
        self.assertIn("Source-book adjustment", q2["material_hypotheses"])
        self.assertTrue(q2["packet_path"].endswith("2023-Q2.md"))

        q3 = by_period["2023-Q3"]
        self.assertEqual(q3["priority"], "P1")
        self.assertEqual(q3["acceptance_status"], "not_closed_material_status_near_fit_requires_source_books")
        self.assertEqual(q3["material_gap_focus"], "no")
        self.assertNotIn("ruled_out", q3["material_hypotheses"])

        q4 = by_period["2023-Q4"]
        self.assertEqual(q4["priority"], "P1")
        self.assertEqual(q4["acceptance_status"], "not_closed_row_exclusion_requires_source_books")

        q1 = by_period["2024-Q1"]
        self.assertEqual(q1["priority"], "P2")
        self.assertEqual(q1["acceptance_status"], "not_closed_near_target_requires_asset_schedule")

    def test_accepts_period_when_source_book_rows_and_tieout_reconcile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _write_inputs(root)
            reconciliation = root / "source_book_reconciliation.csv"
            _write_source_book_reconciliation(reconciliation)

            rows = build_quarter_acceptance_matrix(
                paths["closure"],
                paths["bridge"],
                paths["material"],
                paths["packets"],
                reconciliation,
            )

        by_period = {row["period"]: row for row in rows}
        q2 = by_period["2023-Q2"]
        self.assertEqual(q2["priority"], "accepted")
        self.assertEqual(q2["acceptance_status"], "accepted_from_source_books")
        self.assertIn("official register rows and any investment-goods amortization rows", q2["required_evidence"])
        self.assertIn("expenses=94,06", q2["required_evidence"])
        self.assertIn("amortization=21,48", q2["required_evidence"])
        self.assertIn("diff=0,00", q2["required_evidence"])
        self.assertIn("supersedes local material-gap hypotheses", q2["required_evidence"])
        self.assertIn("source rows=1 expense / 1 asset / 1 tie-out", q2["next_action"])

        q3 = by_period["2023-Q3"]
        self.assertEqual(q3["priority"], "P1")
        self.assertEqual(q3["acceptance_status"], "not_closed_material_status_near_fit_requires_source_books")

        q4 = by_period["2023-Q4"]
        self.assertEqual(q4["priority"], "accepted")
        self.assertEqual(q4["acceptance_status"], "accepted_from_source_books")

        q1 = by_period["2024-Q1"]
        self.assertEqual(q1["priority"], "P2")
        self.assertEqual(q1["acceptance_status"], "not_closed_near_target_requires_asset_schedule")

    def test_marks_annual_adjustment_acceptance_separately_from_row_level_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _write_inputs(root)
            reconciliation = root / "source_book_reconciliation.csv"
            _write_source_book_reconciliation_with_annual_adjustment(reconciliation)

            rows = build_quarter_acceptance_matrix(
                paths["closure"],
                paths["bridge"],
                paths["material"],
                paths["packets"],
                reconciliation,
            )

        q4 = {row["period"]: row for row in rows}["2023-Q4"]
        self.assertEqual(q4["priority"], "accepted")
        self.assertEqual(q4["acceptance_status"], "accepted_from_source_books_with_annual_adjustment")
        self.assertIn("filed annual Modelo 100 adjustment", q4["required_evidence"])
        self.assertIn("not row-level tie-out", q4["next_action"])

    def test_duplicate_source_book_reconciliation_period_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _write_inputs(root)
            reconciliation = root / "source_book_reconciliation.csv"
            _write_duplicate_source_book_reconciliation(reconciliation)

            with self.assertRaisesRegex(ValueError, "Duplicate source-book reconciliation period row"):
                build_quarter_acceptance_matrix(
                    paths["closure"],
                    paths["bridge"],
                    paths["material"],
                    paths["packets"],
                    reconciliation,
                )

    def test_cli_writes_acceptance_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _write_inputs(root)
            out_csv = root / "acceptance.csv"
            out_md = root / "acceptance.md"

            exit_code = main(
                [
                    "audit-quarter-acceptance",
                    "--quarter-closure",
                    str(paths["closure"]),
                    "--quarter-balance-bridge",
                    str(paths["bridge"]),
                    "--material-gap-drilldown",
                    str(paths["material"]),
                    "--packets-dir",
                    str(paths["packets"]),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertIn("not_closed_material_gap_requires_source_books", out_csv.read_text(encoding="utf-8"))
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Modelo 130 Quarter Acceptance Matrix", markdown)
            self.assertIn("Accepted/closed from source-book evidence: `0`", markdown)

    def test_cli_can_close_reconciled_source_book_periods(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _write_inputs(root)
            reconciliation = root / "source_book_reconciliation.csv"
            out_csv = root / "acceptance.csv"
            out_md = root / "acceptance.md"
            _write_source_book_reconciliation(reconciliation)

            exit_code = main(
                [
                    "audit-quarter-acceptance",
                    "--quarter-closure",
                    str(paths["closure"]),
                    "--quarter-balance-bridge",
                    str(paths["bridge"]),
                    "--material-gap-drilldown",
                    str(paths["material"]),
                    "--packets-dir",
                    str(paths["packets"]),
                    "--source-book-reconciliation",
                    str(reconciliation),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            csv_rows = list(csv.DictReader(out_csv.open("r", newline="", encoding="utf-8-sig")))
            markdown = out_md.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        by_period = {row["period"]: row for row in csv_rows}
        self.assertEqual(by_period["2023-Q2"]["acceptance_status"], "accepted_from_source_books")
        self.assertIn("Accepted/closed from source-book evidence: `2`", markdown)
        self.assertIn("accepted_from_source_books", markdown)


def _write_inputs(root: Path) -> dict[str, Path]:
    closure = root / "closure.csv"
    bridge = root / "bridge.csv"
    material = root / "material.csv"
    packets = root / "packets"
    packets.mkdir()

    closure_rows = [
        _closure_row("2023-Q2", "blocked_material_unexplained_adjustment", "115.54", "21.48"),
        _closure_row("2023-Q3", "blocked_material_unexplained_adjustment", "262.01", "12.84"),
        _closure_row("2023-Q4", "pending_row_exclusion_confirmation", "400.00", "18.25"),
        _closure_row("2024-Q1", "pending_asset_schedule_confirmation", "254.36", "-0.55"),
    ]
    _write_dicts(closure, QUARTER_CLOSURE_FIELDS, closure_rows)

    bridge_rows = [
        _bridge_row("2023-Q2", "115.54", "21.48", "submitted_target_above_local_model"),
        _bridge_row("2023-Q3", "262.01", "12.84", "minor_amount_but_material_by_context"),
        _bridge_row("2023-Q4", "400.00", "18.25", "minor_residual_pending_confirmation"),
        _bridge_row("2024-Q1", "254.36", "0.55", "near_target_pending_confirmation"),
    ]
    _write_dicts(bridge, QUARTER_BALANCE_BRIDGE_FIELDS, bridge_rows)

    material_rows = [
        {
            "period": "2023-Q2",
            "hypothesis_id": "unexplained_register_adjustment",
            "hypothesis_status": "unresolved_required_evidence",
            "component": "Submitted-register adjustment",
            "amount_eur": "21.48",
            "effect_closes_gap_eur": "21.48",
            "counts_in_best_bridge": "yes",
            "hypothesis_total_eur": "21.48",
            "candidate_balance_to_target": "21.48",
            "residual_to_candidate_balance_eur": "0.00",
            "annual_constrained_balance_to_target": "21.48",
            "residual_to_annual_balance_eur": "0.00",
            "fit_signal": "near_exact_pending_confirmation",
            "source_ref": "source",
            "evidence_status": "missing",
            "notes": "No local row",
            "xolo_question": "Which submitted row explains 21.48 EUR?",
        },
        {
            "period": "2023-Q3",
            "hypothesis_id": "ruled_out_candidate",
            "hypothesis_status": "ruled_out_local_hypothesis",
            "component": "Rejected idea",
            "amount_eur": "12.84",
            "effect_closes_gap_eur": "12.84",
            "counts_in_best_bridge": "no",
            "hypothesis_total_eur": "0.00",
            "candidate_balance_to_target": "12.84",
            "residual_to_candidate_balance_eur": "12.84",
            "annual_constrained_balance_to_target": "12.84",
            "residual_to_annual_balance_eur": "12.84",
            "fit_signal": "ruled_out",
            "source_ref": "source",
            "evidence_status": "rejected",
            "notes": "Ruled out locally",
            "xolo_question": "",
        },
    ]
    _write_dicts(material, MATERIAL_GAP_DRILLDOWN_FIELDS, material_rows)
    return {"closure": closure, "bridge": bridge, "material": material, "packets": packets}


def _write_source_book_reconciliation(path: Path) -> None:
    _write_dicts(
        path,
        SOURCE_BOOK_RECONCILIATION_FIELDS,
        [
            {
                "period": "2023-Q2",
                "status": "rows_and_tieout_match_target",
                "target_casilla_02_ytd": "115.54",
                "target_casilla_02_delta": "115.54",
                "imported_expense_delta": "94.06",
                "imported_amortization_delta": "21.48",
                "imported_total_delta": "115.54",
                "imported_minus_target_delta": "0.00",
                "tieout_casilla02_ytd": "115.54",
                "tieout_casilla02_ytd_minus_target": "0.00",
                "tieout_casilla02_delta": "115.54",
                "tieout_delta_minus_target": "0.00",
                "expense_row_count": "1",
                "asset_row_count": "1",
                "tieout_row_count": "1",
                "skipped_row_count": "0",
                "amount_parse_error_count": "0",
                "unassigned_row_count": "0",
                "source_files": "source.csv",
                "notes": "Imported source-book rows and tie-out rows are available for comparison.",
            },
            {
                "period": "2023-Q3",
                "status": "source_book_rows_mismatch",
                "target_casilla_02_ytd": "377.55",
                "target_casilla_02_delta": "262.01",
                "imported_expense_delta": "0.00",
                "imported_amortization_delta": "0.00",
                "imported_total_delta": "0.00",
                "imported_minus_target_delta": "-262.01",
                "tieout_casilla02_ytd": "",
                "tieout_casilla02_ytd_minus_target": "",
                "tieout_casilla02_delta": "",
                "tieout_delta_minus_target": "",
                "expense_row_count": "0",
                "asset_row_count": "0",
                "tieout_row_count": "0",
                "skipped_row_count": "0",
                "amount_parse_error_count": "0",
                "unassigned_row_count": "0",
                "source_files": "",
                "notes": "Fixture mismatch.",
            },
            {
                "period": "2023-Q4",
                "status": "rows_match_target_no_tieout",
                "target_casilla_02_ytd": "777.55",
                "target_casilla_02_delta": "400.00",
                "imported_expense_delta": "400.00",
                "imported_amortization_delta": "0.00",
                "imported_total_delta": "400.00",
                "imported_minus_target_delta": "0.00",
                "tieout_casilla02_ytd": "",
                "tieout_casilla02_ytd_minus_target": "",
                "tieout_casilla02_delta": "",
                "tieout_delta_minus_target": "",
                "expense_row_count": "1",
                "asset_row_count": "0",
                "tieout_row_count": "0",
                "skipped_row_count": "0",
                "amount_parse_error_count": "0",
                "unassigned_row_count": "0",
                "source_files": "source.csv",
                "notes": "Rows match target but no tie-out row was imported.",
            },
            {
                "period": "2024-Q1",
                "status": "rows_and_ytd_tieout_match_target_delta_missing",
                "target_casilla_02_ytd": "254.36",
                "target_casilla_02_delta": "254.36",
                "imported_expense_delta": "254.36",
                "imported_amortization_delta": "0.00",
                "imported_total_delta": "254.36",
                "imported_minus_target_delta": "0.00",
                "tieout_casilla02_ytd": "254.36",
                "tieout_casilla02_ytd_minus_target": "0.00",
                "tieout_casilla02_delta": "",
                "tieout_delta_minus_target": "",
                "expense_row_count": "1",
                "asset_row_count": "0",
                "tieout_row_count": "1",
                "skipped_row_count": "0",
                "amount_parse_error_count": "0",
                "unassigned_row_count": "0",
                "source_files": "source.csv",
                "notes": "Tie-out YTD present, but delta is missing.",
            },
        ],
    )


def _write_duplicate_source_book_reconciliation(path: Path) -> None:
    row = {
        "period": "2023-Q2",
        "status": "rows_and_tieout_match_target",
        "target_casilla_02_ytd": "115.54",
        "target_casilla_02_delta": "115.54",
        "imported_expense_delta": "94.06",
        "imported_amortization_delta": "21.48",
        "imported_total_delta": "115.54",
        "imported_minus_target_delta": "0.00",
        "tieout_casilla02_ytd": "115.54",
        "tieout_casilla02_ytd_minus_target": "0.00",
        "tieout_casilla02_delta": "115.54",
        "tieout_delta_minus_target": "0.00",
        "expense_row_count": "1",
        "asset_row_count": "1",
        "tieout_row_count": "1",
        "skipped_row_count": "0",
        "amount_parse_error_count": "0",
        "unassigned_row_count": "0",
        "source_files": "source.csv",
        "notes": "Duplicate fixture.",
    }
    _write_dicts(path, SOURCE_BOOK_RECONCILIATION_FIELDS, [row, row])


def _write_source_book_reconciliation_with_annual_adjustment(path: Path) -> None:
    _write_dicts(
        path,
        SOURCE_BOOK_RECONCILIATION_FIELDS,
        [
            {
                "period": "2023-Q4",
                "status": "rows_match_target_after_annual_adjustment_no_tieout",
                "target_casilla_02_ytd": "777.55",
                "target_casilla_02_delta": "400.00",
                "imported_expense_delta": "425.00",
                "imported_amortization_delta": "0.00",
                "imported_total_delta": "425.00",
                "imported_minus_target_delta": "25.00",
                "annual_adjustment_delta": "25.00",
                "adjusted_imported_total_delta": "400.00",
                "adjusted_minus_target_delta": "0.00",
                "expense_row_count": "2",
                "asset_row_count": "0",
                "tieout_row_count": "0",
                "skipped_row_count": "0",
                "amount_parse_error_count": "0",
                "unassigned_row_count": "0",
                "source_files": "source.csv",
                "notes": "Annual adjustment fixture.",
            }
        ],
    )


def _closure_row(period: str, status: str, target: str, balance: str) -> dict[str, str]:
    return {
        "period": period,
        "closure_status": status,
        "target_casilla_02_delta": target,
        "pre_plug_model_minus_target_eur": balance,
        "balancing_adjustment_eur": balance,
        "balancing_adjustment_pct_of_target": "1.00",
        "balancing_adjustment_materiality": "material",
        "candidate_amortization_delta": "0.00",
        "excluded_asset_direct_eur": "0.00",
        "excluded_nearest_subset_eur": "0.00",
        "has_material_plug": "yes" if "material" in status else "no",
        "has_asset_decision": "yes" if "asset" in status else "no",
        "has_nearest_exclusion": "yes" if "exclusion" in status else "no",
        "has_minor_residual": "no",
        "asset_rows": "",
        "nearest_exclusion_rows": "",
        "required_xolo_evidence": "submitted register; asset schedule",
        "next_question": f"Question for {period}",
    }


def _bridge_row(period: str, target: str, annual_balance: str, signal: str) -> dict[str, str]:
    return {
        "period": period,
        "target_casilla_02_delta": target,
        "raw_non_asset_delta": target,
        "candidate_amortization_delta": "0.00",
        "nearest_excluded_or_netted_eur": "0.00",
        "candidate_model_after_local_adjustments": target,
        "candidate_balance_to_target": annual_balance,
        "annual_constraint_source": "fixture",
        "annual_constrained_amortization_delta": "0.00",
        "annual_constrained_model_after_local_adjustments": target,
        "annual_constrained_balance_to_target": annual_balance,
        "amortization_shift_eur": "0.00",
        "balance_signal": signal,
        "closure_status": "",
        "required_xolo_evidence": "submitted register",
        "equation": "fixture",
    }


def _write_dicts(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
