from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.closure_request import build_xolo_closure_request
from autonomo_taxes.quarter_closure import QUARTER_CLOSURE_FIELDS


class ClosureRequestTests(unittest.TestCase):
    def test_request_prioritizes_material_gaps_and_asset_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "closure.csv"
            root_cause = Path(tmp) / "root_cause.csv"
            inventory = Path(tmp) / "inventory.csv"
            local_attention = Path(tmp) / "local_attention.csv"
            material_gap = Path(tmp) / "material_gap.csv"
            material_context = Path(tmp) / "material_context.csv"
            p1_context = Path(tmp) / "p1_context.csv"
            p2_context = Path(tmp) / "p2_context.csv"
            asset_gap = Path(tmp) / "asset_gap.csv"
            self._write_closure(
                path,
                [
                    self._row(
                        "2023-Q2",
                        "blocked_material_unexplained_adjustment",
                        balance="21.48",
                        asset_rows="2023-06-30 GitHub 116.86",
                    ),
                    self._row(
                        "2026-Q2",
                        "pending_asset_schedule_confirmation",
                        balance="0.65",
                        asset_rows="2026-04-08 SYNTH-DOCUMENT-031 Apple 1994.00",
                        nearest_rows="2026-05-10 SYNTH-DOCUMENT-019 189.01",
                    ),
                ],
            )
            self._write_root_cause(
                root_cause,
                [
                    {
                        "period": "2023-Q2",
                        "closure_status": "blocked_material_unexplained_adjustment",
                        "annual_constrained_residual": "-21.48",
                        "annual_professional_base_diff": "-0.57",
                        "eliminated_causes": "missing VAT-bearing EUR expense rows; annual professional gross-gap row-exclusion inference",
                        "remaining_causes": "catch-up, reclassification, or missing non-VAT/FX rows",
                    }
                ],
            )
            self._write_inventory(inventory)
            self._write_local_attention(local_attention)
            self._write_material_gap(material_gap)
            self._write_material_context(material_context)
            self._write_p1_context(p1_context)
            self._write_p2_context(p2_context)
            self._write_asset_gap(asset_gap)

            markdown = build_xolo_closure_request(
                path,
                root_cause_narrowing_csv=root_cause,
                evidence_inventory_csv=inventory,
                local_attention_bridge_csv=local_attention,
                material_gap_drilldown_csv=material_gap,
                material_gap_context_csv=material_context,
                p1_near_fit_context_csv=p1_context,
                p2_near_target_context_csv=p2_context,
                asset_gap_matrix_csv=asset_gap,
            )

        self.assertIn("Draft-only artifact. Do not send automatically.", markdown)
        self.assertIn("Local Root-Cause Gates Already Checked", markdown)
        self.assertIn("annual professional gross-gap row-exclusion inference", markdown)
        self.assertIn("2023-Q2", markdown)
        self.assertIn("21,48", markdown)
        self.assertIn("SYNTH-DOCUMENT-031", markdown)
        self.assertIn("SYNTH-DOCUMENT-019", markdown)
        self.assertIn("libro registro de compras y gastos", markdown)
        self.assertIn("libro registro de bienes de inversión", markdown)
        self.assertIn("Standard quarterly or annual summaries", markdown)
        self.assertIn("Evidence inventory found 13 Modelo 130 reports, 0 candidate source-book row-evidence files", markdown)
        self.assertIn("Local Archive Attention Candidates", markdown)
        self.assertIn("amazon invoice 1.pdf", markdown)
        self.assertIn("Material Gap Drilldown", markdown)
        self.assertIn("q2_catchup_and_fiveplus_fx", markdown)
        self.assertIn("P0 Raw Xolo Context", markdown)
        self.assertIn("gap_matches_excluded_asset_candidate_context", markdown)
        self.assertIn("20.06% of 107.10", markdown)
        self.assertIn("GitHub 1751445", markdown)
        self.assertIn("P1 Near-Fit Context", markdown)
        self.assertIn("LINQPad exact basis", markdown)
        self.assertIn("P2 Near-Target Context", markdown)
        self.assertIn("scenario_q2_2026_asset_exclusion_fork", markdown)
        self.assertIn("Asset/Register Split From Local Matrix", markdown)
        self.assertIn("source exports rather than a narrative explanation", markdown)
        self.assertIn("ordinary_amortization_too_small", markdown)
        self.assertIn("annual_constrained_amortization_near_required", markdown)
        self.assertIn("asset_schedule_confirmation_required", markdown)
        self.assertNotIn("asset id", markdown.lower())
        self.assertNotIn("This ruled-out row should not appear.", markdown)

    def test_request_can_include_unconfirmed_source_finding_highlights(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            closure = tmp_path / "closure.csv"
            source_findings = tmp_path / "source_findings.csv"
            self._write_closure(
                closure,
                [
                    self._row(
                        "2026-Q2",
                        "pending_asset_schedule_confirmation",
                        balance="0.65",
                        asset_rows="2026-04-08 SYNTH-DOCUMENT-031 Apple 1994.00",
                    )
                ],
            )
            self._write_source_findings(
                source_findings,
                [
                    {
                        "period": "2026-Q2",
                        "row_ref": "scenario_q2_2026_xolo_ledger_match",
                        "source_status": "unconfirmed_arithmetic_hypothesis",
                        "source_document": "runs/2026-Q2/modelo130_report.md",
                        "finding": "Reviewed ledger matches the target via a pending amortization row.",
                        "impact": "Do not treat the target-fitting row as confirmed Xolo accounting.",
                    },
                    {
                        "period": "2026-Q2",
                        "row_ref": "SYNTH-DOCUMENT-005",
                        "source_status": "confirmed_invoice_total",
                        "source_document": "EXPENSE/xolo.pdf",
                        "finding": "Confirmed invoice total.",
                        "impact": "Not a highlight.",
                    },
                ],
            )

            markdown = build_xolo_closure_request(closure, source_findings)

        self.assertIn("Source-Finding Highlights To Preserve", markdown)
        self.assertIn("scenario_q2_2026_xolo_ledger_match", markdown)
        self.assertIn("target-fitting row", markdown)
        self.assertNotIn("Confirmed invoice total.", markdown)

    def test_request_reads_legacy_inventory_category_without_emitting_legacy_wording(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            closure = tmp_path / "closure.csv"
            inventory = tmp_path / "inventory.csv"
            self._write_closure(
                closure,
                [self._row("2026-Q2", "pending_asset_schedule_confirmation")],
            )
            with inventory.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["category", "period", "count", "paths"])
                writer.writeheader()
                writer.writerows(
                    [
                        {"category": "modelo130_report", "period": "2026-Q2", "count": "1", "paths": "M130.pdf"},
                        {
                            "category": "candidate_submitted_register",
                            "period": "",
                            "count": "2",
                            "paths": "legacy.csv",
                        },
                        {"category": "candidate_asset_schedule", "period": "", "count": "0", "paths": ""},
                    ]
                )

            markdown = build_xolo_closure_request(closure, evidence_inventory_csv=inventory)

        self.assertIn("1 Modelo 130 reports, 2 candidate source-book row-evidence files", markdown)
        self.assertNotIn("candidate_submitted_register", markdown)

    def test_request_can_include_concrete_row_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            closure = tmp_path / "closure.csv"
            row_decisions = tmp_path / "row_decisions.csv"
            self._write_closure(closure, [self._row("2026-Q2", "pending_asset_schedule_confirmation")])
            self._write_row_decisions(
                row_decisions,
                [
                    {
                        "period": "2026-Q2",
                        "priority": "high",
                        "decision_kind": "confirm asset amortization schedule treatment",
                        "classification": "asset_amortization_candidate",
                        "date": "2026-04-08",
                        "number": "SYNTH-DOCUMENT-031",
                        "recipient": "Synthetic Party 016",
                        "amount_eur": "1994.00",
                        "question": "What asset basis, rate, start date, and amortization amount did Xolo use?",
                        "root_cause_context": "annual professional gross gap is VAT-shaped",
                    }
                ],
            )

            markdown = build_xolo_closure_request(closure, row_decisions_csv=row_decisions)

        self.assertIn("Concrete Row-Level Decisions", markdown)
        self.assertIn("SYNTH-DOCUMENT-031", markdown)
        self.assertIn("1.994,00", markdown)
        self.assertIn("asset basis", markdown)
        self.assertIn("annual professional gross gap is VAT-shaped", markdown)

    def test_cli_writes_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            closure = tmp_path / "closure.csv"
            root_cause = tmp_path / "root_cause.csv"
            inventory = tmp_path / "inventory.csv"
            local_attention = tmp_path / "local_attention.csv"
            material_gap = tmp_path / "material_gap.csv"
            material_context = tmp_path / "material_context.csv"
            p1_context = tmp_path / "p1_context.csv"
            p2_context = tmp_path / "p2_context.csv"
            asset_gap = tmp_path / "asset_gap.csv"
            out = tmp_path / "request.md"
            self._write_closure(closure, [self._row("2023-Q2", "blocked_material_unexplained_adjustment")])
            self._write_root_cause(
                root_cause,
                [
                    {
                        "period": "2023-Q2",
                        "closure_status": "blocked_material_unexplained_adjustment",
                        "annual_constrained_residual": "-21.48",
                        "annual_professional_base_diff": "-0.57",
                        "eliminated_causes": "missing VAT-bearing EUR expense rows",
                        "remaining_causes": "submitted-register adjustment",
                    }
                ],
            )

            row_decisions = tmp_path / "row_decisions.csv"
            self._write_row_decisions(row_decisions, [])
            self._write_inventory(inventory)
            self._write_local_attention(local_attention)
            self._write_material_gap(material_gap)
            self._write_material_context(material_context)
            self._write_p1_context(p1_context)
            self._write_p2_context(p2_context)
            self._write_asset_gap(asset_gap)

            exit_code = main(
                [
                    "audit-closure-request",
                    "--quarter-closure",
                    str(closure),
                    "--row-decisions",
                    str(row_decisions),
                    "--root-cause-narrowing",
                    str(root_cause),
                    "--evidence-inventory",
                    str(inventory),
                    "--local-attention-bridge",
                    str(local_attention),
                    "--material-gap-drilldown",
                    str(material_gap),
                    "--material-gap-context",
                    str(material_context),
                    "--p1-near-fit-context",
                    str(p1_context),
                    "--p2-near-target-context",
                    str(p2_context),
                    "--asset-gap-matrix",
                    str(asset_gap),
                    "--out-md",
                    str(out),
                ]
            )
            exists = out.exists()
            content = out.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertTrue(exists)
        self.assertIn("Local Root-Cause Gates Already Checked", content)
        self.assertIn("Evidence inventory", content)
        self.assertIn("Local Archive Attention Candidates", content)
        self.assertIn("Material Gap Drilldown", content)
        self.assertIn("P0 Raw Xolo Context", content)
        self.assertIn("P1 Near-Fit Context", content)
        self.assertIn("P2 Near-Target Context", content)
        self.assertIn("Asset/Register Split From Local Matrix", content)

    def _write_closure(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=QUARTER_CLOSURE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_row_decisions(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "period",
                    "priority",
                    "decision_kind",
                    "classification",
                    "date",
                    "number",
                    "recipient",
                    "amount_eur",
                    "question",
                    "root_cause_context",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

    def _write_source_findings(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "period",
                    "row_ref",
                    "source_status",
                    "source_document",
                    "finding",
                    "impact",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

    def _write_root_cause(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "period",
                    "closure_status",
                    "annual_constrained_residual",
                    "annual_professional_base_diff",
                    "eliminated_causes",
                    "remaining_causes",
                ],
            )
            writer.writeheader()
            writer.writerows(rows)

    def _write_inventory(self, path: Path) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["category", "period", "count", "paths"])
            writer.writeheader()
            writer.writerows(
                [
                    {
                        "category": "modelo130_report",
                        "period": "2023-Q2; 2026-Q2",
                        "count": "13",
                        "paths": "TAX_REPORT/M130 2T 2023 exampleei example.pdf",
                    },
                    {
                        "category": "candidate_source_book_row_evidence",
                        "period": "",
                        "count": "0",
                        "paths": "",
                    },
                    {
                        "category": "candidate_asset_schedule",
                        "period": "",
                        "count": "0",
                        "paths": "",
                    },
                ]
            )

    def _write_local_attention(self, path: Path) -> None:
        fieldnames = [
            "period",
            "quarter_balance_eur",
            "balance_direction",
            "attention_status",
            "date",
            "local_document",
            "known_local_amount_eur",
            "matched_xolo_numbers",
            "matched_xolo_amounts",
            "known_effect_eur",
            "gap_fit_signal",
            "priority",
            "xolo_question",
            "notes",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
                {
                    "period": "2024-Q3",
                    "quarter_balance_eur": "335.43",
                    "balance_direction": "target_above_model",
                    "attention_status": "amount_mismatch_potential_xolo_raw",
                    "date": "2024-07-04",
                    "local_document": "amazon invoice 1.pdf",
                    "known_local_amount_eur": "212.14",
                    "matched_xolo_numbers": "SYNTH-DOCUMENT-047",
                    "matched_xolo_amounts": "256.69",
                    "known_effect_eur": "-44.55",
                    "gap_fit_signal": "known_effect_moves_away_from_gap",
                    "priority": "high",
                    "xolo_question": "Should Amazon be local or Xolo raw amount?",
                    "notes": "fixture",
                }
            )

    def _write_asset_gap(self, path: Path) -> None:
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
                "period": "2024-Q3",
                "required_amortization_or_catchup_delta": "395.07",
                "annual_constrained_amortization_delta": "54.42",
                "required_minus_annual_constrained_amortization": "340.65",
                "active_asset_count": "7",
                "amortization_gap_signal": "ordinary_amortization_too_small",
                "next_action": "Ask Xolo whether this quarter has catch-up, direct asset deduction, or row reclassification.",
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
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
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
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
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
                    "source_ref": "EXPENSE/direct_debit_20240813_143930.png",
                    "evidence_status": "confirmed_direct_debit",
                    "notes": "fixture",
                    "xolo_question": "Was June TGSS caught up in Q3?",
                }
            )
            writer.writerow(
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
                    "source_ref": "EXPENSE/github.pdf",
                    "evidence_status": "confirmed_invoice_total",
                    "notes": "fixture",
                    "xolo_question": "This ruled-out row should not appear.",
                }
            )

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

    def _row(
        self,
        period: str,
        status: str,
        *,
        balance: str = "21.48",
        asset_rows: str = "",
        nearest_rows: str = "",
    ) -> dict[str, str]:
        row = {field: "" for field in QUARTER_CLOSURE_FIELDS}
        row.update(
            {
                "period": period,
                "closure_status": status,
                "target_casilla_02_delta": "100.00",
                "pre_plug_model_minus_target_eur": "-21.48",
                "balancing_adjustment_eur": balance,
                "balancing_adjustment_pct_of_target": "21.48",
                "balancing_adjustment_materiality": "material" if "material" in status else "minor",
                "candidate_amortization_delta": "10.00" if asset_rows else "0.00",
                "excluded_asset_direct_eur": "100.00" if asset_rows else "0.00",
                "excluded_nearest_subset_eur": "50.00" if nearest_rows else "0.00",
                "has_material_plug": "yes" if "material" in status else "no",
                "has_asset_decision": "yes" if asset_rows else "no",
                "has_nearest_exclusion": "yes" if nearest_rows else "no",
                "has_minor_residual": "no",
                "asset_rows": asset_rows,
                "nearest_exclusion_rows": nearest_rows,
                "required_xolo_evidence": "submitted register",
                "next_question": f"Explain {period}",
            }
        )
        return row


if __name__ == "__main__":
    unittest.main()
