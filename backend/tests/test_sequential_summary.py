from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.quarter_closure import QUARTER_CLOSURE_FIELDS
from autonomo_taxes.sequential_summary import build_sequential_summary


class SequentialSummaryTests(unittest.TestCase):
    def test_summary_highlights_coverage_and_unresolved_forks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            closure = tmp_path / "closure.csv"
            source_findings = tmp_path / "source_findings.csv"
            modelo303 = tmp_path / "modelo303.csv"
            annual_categories = tmp_path / "annual_categories.csv"
            material_gap = tmp_path / "material_gap.csv"
            material_context = tmp_path / "material_context.csv"
            p1_context = tmp_path / "p1_context.csv"
            p2_context = tmp_path / "p2_context.csv"
            xolo_api_coverage = tmp_path / "xolo_api_coverage.csv"
            asset_gap = tmp_path / "asset_gap.csv"
            self._write_closure(
                closure,
                [
                    self._closure_row("2023-Q2", "blocked_material_unexplained_adjustment", balance="21.48"),
                    self._closure_row("2024-Q3", "blocked_material_unexplained_adjustment", balance="340.65"),
                    self._closure_row("2026-Q2", "pending_asset_schedule_confirmation", balance="0.65"),
                ],
            )
            self._write_source_findings(
                source_findings,
                [
                    self._source_row("2023-Q2", "scenario_q2", "runs/q2.md"),
                    self._source_row("2026-Q2", "XOLO-2026-Q2-AMORTIZATION-PENDING", "runs/q2-2026.md", status="unconfirmed_asset_amortization_row"),
                ],
            )
            self._write_modelo303(modelo303, [self._modelo303_row("2023-Q2"), self._modelo303_row("2026-Q1")])
            self._write_annual_categories(
                annual_categories,
                [
                    self._annual_category_row(
                        "2023",
                        "-171.51",
                        "-0.57",
                        "annual return appears to direct-expense/reclassify software or hardware instead of amortizing it",
                    ),
                    self._annual_category_row("2026", "-209.28", "-9.06"),
                ],
            )
            self._write_material_gap(material_gap)
            self._write_material_context(material_context)
            self._write_p1_context(p1_context)
            self._write_p2_context(p2_context)
            self._write_xolo_api_coverage(xolo_api_coverage)
            self._write_asset_gap(asset_gap)

            markdown = build_sequential_summary(
                closure,
                source_findings,
                modelo303,
                annual_categories,
                material_gap,
                material_context,
                p1_context,
                p2_context,
                xolo_api_coverage,
                asset_gap,
            )

        self.assertIn("Sequential Audit Summary", markdown)
        self.assertIn("Periods in closure checklist: 3", markdown)
        self.assertIn("Unconfirmed scenario/amortization rows without source artifact: 0", markdown)
        self.assertIn("Modelo 303 VAT cross-check matched rows: 2/2", markdown)
        self.assertIn("Annual Modelo 100 category VAT-base comparison covered periods: 2/3", markdown)
        self.assertIn("Material-gap drilldown periods: 2", markdown)
        self.assertIn("P0 raw Xolo context rows: 1", markdown)
        self.assertIn("Raw Xolo API export coverage: complete_raw_snapshot; rows 171; expected total match yes; activity start coverage yes.", markdown)
        self.assertIn("Raw Xolo expense API snapshot coverage is complete for this audit", markdown)
        self.assertIn("remaining Modelo 130 blocker is not raw expense-list pagination coverage", markdown)
        self.assertIn("Asset gap matrix covered periods: 3/3", markdown)
        self.assertIn("Asset Gap Matrix", markdown)
        self.assertIn("ordinary_amortization_too_small", markdown)
        self.assertIn("annual_constrained_amortization_near_required", markdown)
        self.assertIn("asset_schedule_confirmation_required", markdown)
        self.assertIn("evidence request, not a tuning exercise", markdown)
        self.assertIn("VAT-shaped professional gross gaps cover `2` quarters", markdown)
        self.assertIn("Material Gap Drilldown", markdown)
        self.assertIn("P0 Raw Xolo Context", markdown)
        self.assertIn("gap_matches_excluded_asset_candidate_context", markdown)
        self.assertIn("20.06% of 107.10", markdown)
        self.assertIn("P1 Near-Fit Context", markdown)
        self.assertIn("LINQPad", markdown)
        self.assertIn("P2 Near-Target Context", markdown)
        self.assertIn("scenario_q2_2026_asset_exclusion_fork", markdown)
        self.assertIn("q2_catchup_and_fiveplus_fx", markdown)
        self.assertIn("asset direct-expense/reclassification signal appears in `2023`", markdown)
        self.assertIn("first external confirmation should focus on `2023-Q2`, `2024-Q3`", markdown)
        self.assertIn("2026-Q2", markdown)
        self.assertIn("scenario_q2", markdown)
        self.assertIn("RETA principal-only", markdown)

    def test_cli_writes_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            closure = tmp_path / "closure.csv"
            source_findings = tmp_path / "source_findings.csv"
            annual_categories = tmp_path / "annual_categories.csv"
            material_gap = tmp_path / "material_gap.csv"
            material_context = tmp_path / "material_context.csv"
            p1_context = tmp_path / "p1_context.csv"
            p2_context = tmp_path / "p2_context.csv"
            xolo_api_coverage = tmp_path / "xolo_api_coverage.csv"
            asset_gap = tmp_path / "asset_gap.csv"
            out = tmp_path / "summary.md"
            self._write_closure(closure, [self._closure_row("2023-Q2", "blocked_material_unexplained_adjustment")])
            self._write_source_findings(source_findings, [self._source_row("2023-Q2", "scenario_q2", "runs/q2.md")])
            self._write_annual_categories(annual_categories, [self._annual_category_row("2023", "-171.51", "-0.57")])
            self._write_material_gap(material_gap)
            self._write_material_context(material_context)
            self._write_p1_context(p1_context)
            self._write_p2_context(p2_context)
            self._write_xolo_api_coverage(xolo_api_coverage)
            self._write_asset_gap(asset_gap, periods=["2023-Q2"])

            exit_code = main(
                [
                    "audit-sequential-summary",
                    "--quarter-closure",
                    str(closure),
                    "--source-findings",
                    str(source_findings),
                    "--annual-categories",
                    str(annual_categories),
                    "--material-gap-drilldown",
                    str(material_gap),
                    "--material-gap-context",
                    str(material_context),
                    "--p1-near-fit-context",
                    str(p1_context),
                    "--p2-near-target-context",
                    str(p2_context),
                    "--xolo-api-coverage",
                    str(xolo_api_coverage),
                    "--asset-gap-matrix",
                    str(asset_gap),
                    "--out-md",
                    str(out),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out.exists())
            content = out.read_text(encoding="utf-8")
            self.assertIn("Sequential Audit Summary", content)
            self.assertIn("Material Gap Drilldown", content)
            self.assertIn("P0 Raw Xolo Context", content)
            self.assertIn("P1 Near-Fit Context", content)
            self.assertIn("P2 Near-Target Context", content)
            self.assertIn("Raw Xolo API export coverage: complete_raw_snapshot", content)
            self.assertIn("Asset gap matrix covered periods: 1/1", content)

    def _write_closure(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=QUARTER_CLOSURE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_source_findings(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["period", "row_ref", "source_status", "source_document", "finding", "impact"],
            )
            writer.writeheader()
            writer.writerows(rows)

    def _write_modelo303(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["period", "crosscheck_status"])
            writer.writeheader()
            writer.writerows(rows)

    def _write_xolo_api_coverage(self, path: Path) -> None:
        rows = [
            {"metric": "status", "value": "complete_raw_snapshot", "status": "ok", "notes": ""},
            {"metric": "csv_rows", "value": "171", "status": "ok", "notes": ""},
            {"metric": "json_pages", "value": "2", "status": "ok", "notes": ""},
            {"metric": "expected_total_match", "value": "yes", "status": "ok", "notes": ""},
            {"metric": "covers_activity_start", "value": "yes", "status": "ok", "notes": ""},
            {"metric": "earliest_expense_date", "value": "2023-05-31", "status": "ok", "notes": ""},
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["metric", "value", "status", "notes"])
            writer.writeheader()
            writer.writerows(rows)

    def _write_asset_gap(self, path: Path, periods: list[str] | None = None) -> None:
        fieldnames = [
            "period",
            "required_amortization_or_catchup_delta",
            "annual_constrained_amortization_delta",
            "required_minus_annual_constrained_amortization",
            "active_asset_count",
            "amortization_gap_signal",
            "next_action",
        ]
        rows = [
            {
                "period": "2023-Q2",
                "required_amortization_or_catchup_delta": "21.48",
                "annual_constrained_amortization_delta": "0.00",
                "required_minus_annual_constrained_amortization": "21.48",
                "active_asset_count": "1",
                "amortization_gap_signal": "excluded_asset_or_register_adjustment_required",
                "next_action": "Confirm whether the active asset row was direct-expensed, capitalized, or excluded.",
            },
            {
                "period": "2024-Q3",
                "required_amortization_or_catchup_delta": "395.07",
                "annual_constrained_amortization_delta": "54.42",
                "required_minus_annual_constrained_amortization": "340.65",
                "active_asset_count": "7",
                "amortization_gap_signal": "ordinary_amortization_too_small",
                "next_action": "Ask Xolo for catch-up, direct asset deduction, or row reclassification.",
            },
            {
                "period": "2026-Q2",
                "required_amortization_or_catchup_delta": "235.94",
                "annual_constrained_amortization_delta": "235.29",
                "required_minus_annual_constrained_amortization": "0.65",
                "active_asset_count": "12",
                "amortization_gap_signal": "annual_constrained_amortization_near_required",
                "next_action": "Confirm per-asset basis, rate, start date, quarter amount, and YTD amount.",
            },
            {
                "period": "2024-Q3",
                "required_amortization_or_catchup_delta": "12.34",
                "annual_constrained_amortization_delta": "10.00",
                "required_minus_annual_constrained_amortization": "2.34",
                "active_asset_count": "7",
                "amortization_gap_signal": "asset_schedule_confirmation_required",
                "next_action": "Confirm the submitted asset schedule and row register.",
            },
        ]
        if periods is not None:
            rows = [row for row in rows if row["period"] in set(periods)]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _write_annual_categories(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["year", "status", "professional_gross_diff", "professional_base_diff", "category_signal"],
            )
            writer.writeheader()
            writer.writerows(rows)

    def _write_material_gap(self, path: Path) -> None:
        fieldnames = [
            "period",
            "hypothesis_id",
            "hypothesis_status",
            "component",
            "amount_eur",
            "effect_closes_gap_eur",
            "counts_in_best_bridge",
            "hypothesis_total_eur",
            "candidate_balance_to_target",
            "residual_to_candidate_balance_eur",
            "annual_constrained_balance_to_target",
            "residual_to_annual_balance_eur",
            "fit_signal",
            "source_ref",
            "evidence_status",
            "notes",
            "xolo_question",
        ]
        rows = [
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
                "source_ref": "runs/q2.md",
                "evidence_status": "no_local_source",
                "notes": "fixture",
                "xolo_question": "question",
            },
            {
                "period": "2024-Q3",
                "hypothesis_id": "q2_catchup_and_fiveplus_fx",
                "hypothesis_status": "strong_candidate_pending_confirmation",
                "component": "Q2 June TGSS carried into Q3",
                "amount_eur": "297.67",
                "effect_closes_gap_eur": "297.67",
                "counts_in_best_bridge": "yes",
                "hypothesis_total_eur": "335.43",
                "candidate_balance_to_target": "335.43",
                "residual_to_candidate_balance_eur": "0.00",
                "annual_constrained_balance_to_target": "336.32",
                "residual_to_annual_balance_eur": "0.89",
                "fit_signal": "near_exact_pending_confirmation",
                "source_ref": "runs/q3.md",
                "evidence_status": "confirmed_direct_debit",
                "notes": "fixture",
                "xolo_question": "question",
            },
            {
                "period": "2023-Q2",
                "hypothesis_id": "github_direct_expense",
                "hypothesis_status": "ruled_out_local_hypothesis",
                "component": "GitHub direct deduction",
                "amount_eur": "116.86",
                "effect_closes_gap_eur": "116.86",
                "counts_in_best_bridge": "no",
                "hypothesis_total_eur": "0.00",
                "candidate_balance_to_target": "21.48",
                "residual_to_candidate_balance_eur": "21.48",
                "annual_constrained_balance_to_target": "21.48",
                "residual_to_annual_balance_eur": "21.48",
                "fit_signal": "ruled_out",
                "source_ref": "runs/q2.md",
                "evidence_status": "confirmed_invoice_total",
                "notes": "fixture",
                "xolo_question": "question",
            },
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _write_material_context(self, path: Path) -> None:
        fieldnames = [
            "period",
            "priority",
            "target_casilla_02_delta",
            "annual_constrained_balance_to_target",
            "xolo_document_quarter_row_count",
            "xolo_document_quarter_gross_eur",
            "xolo_document_quarter_non_asset_gross_eur",
            "xolo_document_quarter_asset_candidate_gross_eur",
            "bridge_raw_non_asset_delta",
            "bridge_raw_vs_xolo_non_asset_delta",
            "target_minus_xolo_non_asset_eur",
            "asset_candidate_rows",
            "gap_sized_asset_candidate_rows",
            "gap_sized_asset_candidate_fit",
            "actionable_hypotheses",
            "context_signal",
            "next_xolo_question",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
                {
                    "period": "2023-Q2",
                    "priority": "P0",
                    "target_casilla_02_delta": "115.54",
                    "annual_constrained_balance_to_target": "21.48",
                    "xolo_document_quarter_row_count": "5",
                    "xolo_document_quarter_gross_eur": "201.16",
                    "xolo_document_quarter_non_asset_gross_eur": "94.06",
                    "xolo_document_quarter_asset_candidate_gross_eur": "107.10",
                    "bridge_raw_non_asset_delta": "94.06",
                    "bridge_raw_vs_xolo_non_asset_delta": "0.00",
                    "target_minus_xolo_non_asset_eur": "21.48",
                    "asset_candidate_rows": "GitHub 1751445",
                    "gap_sized_asset_candidate_rows": "GitHub 1751445",
                    "gap_sized_asset_candidate_fit": "1751445: gap 21.48 is 20.06% of 107.10",
                    "actionable_hypotheses": "github_partial_asset_basis",
                    "context_signal": "gap_matches_excluded_asset_candidate_context",
                    "next_xolo_question": "Did Xolo use GitHub 1751445?",
                }
            )

    def _write_p1_context(self, path: Path) -> None:
        fieldnames = [
            "period",
            "priority",
            "acceptance_status",
            "hypothesis_id",
            "hypothesis_status",
            "target_delta_eur",
            "model_amount_eur",
            "diff_to_target_eur",
            "annual_constrained_balance_to_target",
            "fit_signal",
            "components",
            "source_ref",
            "evidence_status",
            "notes",
            "xolo_question",
            "context_signal",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
                {
                    "period": "2023-Q3",
                    "priority": "P1",
                    "acceptance_status": "not_closed_material_status_near_fit_requires_register",
                    "hypothesis_id": "exclude_all_xolo_include_linqpad_exact_basis",
                    "hypothesis_status": "unconfirmed_register_and_fx_basis",
                    "target_delta_eur": "262.01",
                    "model_amount_eur": "262.01",
                    "diff_to_target_eur": "0.00",
                    "annual_constrained_balance_to_target": "12.84",
                    "fit_signal": "exact_arithmetic_requires_register_basis",
                    "components": "LINQPad exact basis",
                    "source_ref": "runs/modelo130_2023_q3_sensitivity.md",
                    "evidence_status": "local_sensitivity_only",
                    "notes": "fixture",
                    "xolo_question": "Confirm LINQPad basis",
                    "context_signal": "exact_arithmetic_fit_requires_xolo_confirmation",
                }
            )

    def _write_p2_context(self, path: Path) -> None:
        fieldnames = [
            "period",
            "priority",
            "acceptance_status",
            "row_ref",
            "source_status",
            "target_casilla_02_delta",
            "annual_constrained_balance_to_target",
            "annual_constrained_amortization_delta",
            "nearest_excluded_or_netted_eur",
            "finding",
            "impact",
            "context_signal",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
                {
                    "period": "2026-Q2",
                    "priority": "P2",
                    "acceptance_status": "not_closed_near_target_requires_asset_schedule",
                    "row_ref": "scenario_q2_2026_asset_exclusion_fork",
                    "source_status": "unconfirmed_arithmetic_hypothesis",
                    "target_casilla_02_delta": "4843.29",
                    "annual_constrained_balance_to_target": "0.65",
                    "annual_constrained_amortization_delta": "235.29",
                    "nearest_excluded_or_netted_eur": "189.01",
                    "finding": "Asset schedule plus apremio exclusion nearly fits.",
                    "impact": "Requires Xolo submitted register and asset schedule.",
                    "context_signal": "q2_2026_original_gap_fork",
                }
            )

    def _closure_row(self, period: str, status: str, *, balance: str = "21.48") -> dict[str, str]:
        row = {field: "" for field in QUARTER_CLOSURE_FIELDS}
        row.update(
            {
                "period": period,
                "closure_status": status,
                "target_casilla_02_delta": "100.00",
                "balancing_adjustment_eur": balance,
                "candidate_amortization_delta": "10.00",
                "excluded_asset_direct_eur": "0.00",
                "excluded_nearest_subset_eur": "0.00",
            }
        )
        return row

    def _source_row(
        self,
        period: str,
        row_ref: str,
        source_document: str,
        *,
        status: str = "unconfirmed_arithmetic_hypothesis",
    ) -> dict[str, str]:
        return {
            "period": period,
            "row_ref": row_ref,
            "source_status": status,
            "source_document": source_document,
            "finding": f"Finding for {row_ref}.",
            "impact": "Needs Xolo confirmation.",
        }

    def _modelo303_row(self, period: str, status: str = "matched") -> dict[str, str]:
        return {"period": period, "crosscheck_status": status}

    def _annual_category_row(
        self,
        year: str,
        professional_gross_diff: str,
        professional_base_diff: str,
        signal: str = "annual categories are near raw category totals",
    ) -> dict[str, str]:
        return {
            "year": year,
            "status": "filed",
            "professional_gross_diff": professional_gross_diff,
            "professional_base_diff": professional_base_diff,
            "category_signal": signal,
        }


if __name__ == "__main__":
    unittest.main()
