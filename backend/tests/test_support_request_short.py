from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.support_request_short import build_xolo_support_request_short


class SupportRequestShortTests(unittest.TestCase):
    def test_builds_source_export_request_from_intake_and_asset_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            intake = tmp_path / "intake.csv"
            asset_gap = tmp_path / "asset_gap.csv"
            first_gate = tmp_path / "first_gate.csv"
            asset_ui = tmp_path / "asset_ui.csv"
            dataexport_inventory = tmp_path / "dataexport_inventory.csv"
            dataexport_archives = tmp_path / "dataexport_archives.csv"
            self._write_intake(intake)
            self._write_asset_gap(asset_gap)
            self._write_first_gate(first_gate)
            self._write_asset_ui(asset_ui)
            self._write_dataexport_inventory(dataexport_inventory)
            self._write_dataexport_archives(dataexport_archives)

            markdown = build_xolo_support_request_short(
                intake,
                asset_gap,
                first_gate_csv=first_gate,
                asset_ui_evidence_csv=asset_ui,
                dataexport_inventory_csv=dataexport_inventory,
                dataexport_archives_csv=dataexport_archives,
                max_questions=2,
            )

        self.assertIn("Xolo Support Request - Modelo 130 Source Books", markdown)
        self.assertIn("Please answer with source exports", markdown)
        self.assertIn("First Chronological Blocker", markdown)
        self.assertIn("first_asset_treatment_gate", markdown)
        self.assertIn("`2T 2023` (`2023-Q2`)", markdown)
        self.assertIn("Local residual, routing only", markdown)
        self.assertIn("Visible local row to reconcile, routing only", markdown)
        self.assertIn("21,48", markdown)
        self.assertIn("GitHub 1751445", markdown)
        self.assertIn("2T 2023 source-book tie-out", markdown)
        self.assertIn("without assuming the local hypothesis", markdown)
        self.assertIn("Please answer this first blocker before interpreting later amortization patterns", markdown)
        self.assertIn("Locally ruled-out explanations", markdown)
        self.assertIn("github_direct_expense", markdown)
        self.assertNotIn("Did Xolo use 21.48 EUR from GitHub as partial deduction?", markdown)
        self.assertIn("libro registro de compras y gastos", markdown)
        self.assertIn("libro registro de bienes de inversión", markdown)
        self.assertIn("Machine-Import Field Spec", markdown)
        self.assertIn("fx_rate", markdown)
        self.assertIn("casilla02_ytd", markdown)
        self.assertNotIn("asset id", markdown.lower())
        self.assertIn("not be treated as confirmed Xolo accounting", markdown)
        self.assertIn("Data export evidence already checked", markdown)
        self.assertIn("`173` expense files", markdown)
        self.assertIn("`0` source-book/asset-schedule filename candidates", markdown)
        self.assertIn("`8` archives scanned", markdown)
        self.assertIn("another normal Data export is unlikely", markdown)
        self.assertIn("Appendix A - Local Asset/Register Split", markdown)
        self.assertIn("ordinary_amortization_too_small", markdown)
        self.assertIn("asset_schedule_confirmation_required", markdown)
        self.assertIn("Prioritized Reconciliation Prompts", markdown)
        self.assertIn("Appendix B - Local Arithmetic Queue", markdown)
        self.assertIn("Please let the books speak first", markdown)
        self.assertIn("Which adjustment explains the gap?", markdown)
        self.assertIn("Row: 2023-06-30 1751445 Synthetic Party 001.", markdown)
        self.assertNotIn("Included or excluded?", markdown)
        self.assertIn("confirmed_irpf_deductible_eur", markdown)
        self.assertIn("Appendix C - Xolo UI Asset Classification Evidence", markdown)
        self.assertIn("does not confirm the submitted amortization schedule", markdown)
        self.assertIn("2026-04-08 3019975 Synthetic Party 016 SYNTH-DOCUMENT-031", markdown)
        self.assertIn("ui_confirms_depreciable_asset", markdown)
        self.assertIn("local_asset_candidate_without_ui_banner", markdown)
        self.assertIn("direct-expensed, split/multiple treatment, capitalized, excluded", markdown)

    def test_cli_writes_short_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            intake = tmp_path / "intake.csv"
            asset_gap = tmp_path / "asset_gap.csv"
            first_gate = tmp_path / "first_gate.csv"
            asset_ui = tmp_path / "asset_ui.csv"
            dataexport_inventory = tmp_path / "dataexport_inventory.csv"
            dataexport_archives = tmp_path / "dataexport_archives.csv"
            out_md = tmp_path / "support.md"
            self._write_intake(intake)
            self._write_asset_gap(asset_gap)
            self._write_first_gate(first_gate)
            self._write_asset_ui(asset_ui)
            self._write_dataexport_inventory(dataexport_inventory)
            self._write_dataexport_archives(dataexport_archives)

            exit_code = main(
                [
                    "audit-support-request-short",
                    "--answer-intake",
                    str(intake),
                    "--asset-gap-matrix",
                    str(asset_gap),
                    "--first-gate",
                    str(first_gate),
                    "--asset-ui-evidence",
                    str(asset_ui),
                    "--dataexport-inventory",
                    str(dataexport_inventory),
                    "--dataexport-archives",
                    str(dataexport_archives),
                    "--max-questions",
                    "3",
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            content = out_md.read_text(encoding="utf-8")
            self.assertIn("Xolo Support Request", content)
            self.assertIn("First Chronological Blocker", content)
            self.assertIn("Included or excluded?", content)
            self.assertIn("source_book_line_reference", content)
            self.assertIn("Xolo UI Asset Classification Evidence", content)
            self.assertIn("Data export evidence already checked", content)

    def test_builds_without_first_gate_for_backward_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            intake = tmp_path / "intake.csv"
            asset_gap = tmp_path / "asset_gap.csv"
            self._write_intake(intake)
            self._write_asset_gap(asset_gap)

            markdown = build_xolo_support_request_short(intake, asset_gap, max_questions=2)

        self.assertIn("Xolo Support Request", markdown)
        self.assertNotIn("First chronological blocker", markdown)
        self.assertNotIn("First Chronological Blocker", markdown)
        self.assertIn("Prioritized Reconciliation Prompts", markdown)

    def test_ignores_first_gate_csv_without_gate_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            intake = tmp_path / "intake.csv"
            asset_gap = tmp_path / "asset_gap.csv"
            first_gate = tmp_path / "first_gate.csv"
            self._write_intake(intake)
            self._write_asset_gap(asset_gap)
            self._write_first_gate(first_gate, include_gate=False)

            markdown = build_xolo_support_request_short(intake, asset_gap, first_gate_csv=first_gate)

        self.assertNotIn("First chronological blocker", markdown)
        self.assertNotIn("First Chronological Blocker", markdown)

    def _write_intake(self, path: Path) -> None:
        fieldnames = [
            "queue_rank",
            "period",
            "audit_priority",
            "decision_priority",
            "classification",
            "row_label",
            "amount_eur",
            "xolo_id",
            "xolo_url",
            "question",
            "required_evidence",
            "answer_status",
            "xolo_answer",
            "confirmed_included",
            "confirmed_irpf_deductible_eur",
            "confirmed_amortization_eur",
            "confirmed_basis",
            "confirmed_source",
            "application_target",
            "application_notes",
        ]
        rows = [
            {
                "queue_rank": "1",
                "period": "2023-Q2",
                "audit_priority": "P0",
                "row_label": "",
                "amount_eur": "21.48",
                "question": "Which adjustment explains the gap?",
                "application_target": "register_review.new_adjustment_row",
            },
            {
                "queue_rank": "2",
                "period": "2023-Q2",
                "audit_priority": "P0",
                "row_label": "2023-06-30 1751445 Synthetic Party 001",
                "amount_eur": "115.54",
                "question": "Direct expense or asset?",
                "application_target": "register_review.confirmed_amortization_eur",
            },
            {
                "queue_rank": "3",
                "period": "2026-Q2",
                "audit_priority": "P2",
                "row_label": "2026-05-10 3099344 SYNTH-DOCUMENT-019 TGSS",
                "amount_eur": "189.01",
                "question": "Included or excluded?",
                "application_target": "register_review.confirmed_included",
            },
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fieldnames})

    def _write_asset_gap(self, path: Path) -> None:
        fieldnames = [
            "period",
            "required_amortization_or_catchup_delta",
            "annual_constrained_amortization_delta",
            "amortization_gap_signal",
            "next_action",
        ]
        rows = [
            {
                "period": "2024-Q3",
                "required_amortization_or_catchup_delta": "395.07",
                "annual_constrained_amortization_delta": "54.42",
                "amortization_gap_signal": "ordinary_amortization_too_small",
                "next_action": "Ask Xolo whether this quarter has catch-up.",
            },
            {
                "period": "2026-Q2",
                "required_amortization_or_catchup_delta": "44.82",
                "annual_constrained_amortization_delta": "235.29",
                "amortization_gap_signal": "asset_schedule_confirmation_required",
                "next_action": "Confirm the submitted asset schedule and row register.",
            },
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _write_first_gate(self, path: Path, include_gate: bool = True) -> None:
        fieldnames = [
            "section",
            "period",
            "key",
            "status",
            "amount_eur",
            "fit_signal",
            "evidence_status",
            "finding",
            "source_ref",
            "xolo_question",
        ]
        rows = []
        if include_gate:
            rows.append(
                {
                    "section": "gate",
                    "period": "2023-Q2",
                    "key": "first_asset_treatment_gate",
                    "status": "blocked_material_unexplained_adjustment",
                    "amount_eur": "115.54",
                    "evidence_status": "source-book treatment for active asset/direct-expense rows",
                    "finding": "Local raw non-asset rows do not reach the submitted expense delta.",
                    "xolo_question": "Confirm whether GitHub was direct-expensed, capitalized, or excluded.",
                }
            )
        rows.extend(
            [
                {
                    "section": "raw_context",
                    "period": "2023-Q2",
                    "amount_eur": "94.06",
                    "fit_signal": "21.48",
                    "finding": "GitHub 1751445",
                },
                {
                    "section": "hypothesis",
                    "period": "2023-Q2",
                    "key": "github_partial_asset_basis",
                    "status": "unresolved_required_evidence",
                    "amount_eur": "21.48",
                    "finding": "Partial asset basis might explain the gap.",
                    "xolo_question": "Did Xolo use 21.48 EUR from GitHub as partial deduction?",
                },
                {
                    "section": "hypothesis",
                    "period": "2023-Q2",
                    "key": "github_direct_expense",
                    "status": "ruled_out_local_hypothesis",
                    "amount_eur": "115.54",
                    "finding": "Too high versus the 21.48 EUR gap.",
                },
            ]
        )
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _write_asset_ui(self, path: Path) -> None:
        fieldnames = [
            "period",
            "date",
            "xolo_id",
            "recipient",
            "type",
            "number",
            "currency",
            "amount_original",
            "gross_eur",
            "vat_base_eur",
            "detail_confidence",
            "ui_depreciable_asset",
            "asset_like_reason",
            "status",
            "evidence",
            "next_action",
        ]
        rows = [
            {
                "period": "2026-Q2",
                "date": "2026-04-08",
                "xolo_id": "3019975",
                "recipient": "Synthetic Party 016",
                "type": "Computer hardware & software",
                "number": "SYNTH-DOCUMENT-031",
                "currency": "EUR",
                "amount_original": "1994.00",
                "gross_eur": "1994.00",
                "vat_base_eur": "1647.93",
                "detail_confidence": "detail_page_eur",
                "ui_depreciable_asset": "yes",
                "asset_like_reason": "xolo_detail_depreciable_asset_banner",
                "status": "ui_confirms_depreciable_asset",
                "evidence": "Authenticated Xolo expense detail page says this expense is a depreciable asset.",
                "next_action": "Request the submitted asset schedule line: amortizable base, rate/life, period amortization, and accumulated amortization.",
            },
            {
                "period": "2024-Q2",
                "date": "2024-05-07",
                "xolo_id": "2117398",
                "recipient": "Apple Retal Spain, s.L.U.",
                "type": "Multiple",
                "number": "SYNTH-DOCUMENT-026",
                "currency": "EUR",
                "amount_original": "642.95",
                "gross_eur": "642.95",
                "vat_base_eur": "531.36",
                "detail_confidence": "detail_page_eur",
                "ui_depreciable_asset": "no",
                "asset_like_reason": "known_asset_supplier_or_keyword:apple retal",
                "status": "local_asset_candidate_without_ui_banner",
                "evidence": "Local asset-like heuristic selected this row.",
                "next_action": "Ask Xolo whether this row was direct expense, split/multiple treatment, or capitalized in the investment-goods book.",
            },
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _write_dataexport_inventory(self, path: Path) -> None:
        fieldnames = ["kind", "key", "count", "paths"]
        rows = [
            {"kind": "top_level", "key": "EXPENSE", "count": "173"},
            {"kind": "top_level", "key": "INVOICE", "count": "49"},
            {"kind": "top_level", "key": "TAX_REPORT", "count": "30"},
            {"kind": "conclusion", "key": "candidate_source_book_or_asset_schedule", "count": "0"},
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fieldnames})

    def _write_dataexport_archives(self, path: Path) -> None:
        fieldnames = [
            "archive",
            "bytes",
            "company_files",
            "expense_files",
            "invoice_files",
            "tax_report_files",
            "candidate_source_book_or_asset_files",
        ]
        rows = [
            {
                "archive": f"dataexport_{index}.zip",
                "candidate_source_book_or_asset_files": "0",
            }
            for index in range(8)
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fieldnames})


if __name__ == "__main__":
    unittest.main()
