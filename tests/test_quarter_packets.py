from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.quarter_closure import QUARTER_CLOSURE_FIELDS
from autonomo_taxes.quarter_packets import build_quarter_packets, write_quarter_packets
from autonomo_taxes.row_audit import ROW_AUDIT_FIELDS


class QuarterPacketsTests(unittest.TestCase):
    def test_packet_contains_submitted_values_and_row_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history = tmp_path / "history.csv"
            closure = tmp_path / "closure.csv"
            audit = tmp_path / "row_audit.csv"
            source_findings = tmp_path / "source_findings.csv"
            root_cause = tmp_path / "root_cause.csv"
            row_decisions = tmp_path / "row_decisions.csv"
            balance_bridge = tmp_path / "balance_bridge.csv"
            self._write_history(history)
            self._write_closure(closure)
            self._write_audit(audit)
            self._write_source_findings(source_findings)
            self._write_root_cause(root_cause)
            self._write_row_decisions(row_decisions)
            self._write_balance_bridge(balance_bridge)

            packets = build_quarter_packets(
                history,
                closure,
                audit,
                source_findings,
                root_cause,
                row_decisions,
                balance_bridge,
            )

        self.assertEqual(len(packets), 1)
        markdown = str(packets[0]["markdown"])
        self.assertIn("Modelo 130 2023-Q2 Verification Packet", markdown)
        self.assertIn("Casilla 02 quarter delta: `115,54`", markdown)
        self.assertIn("blocked_material_unexplained_adjustment", markdown)
        self.assertIn("Local Source Checks", markdown)
        self.assertIn("Root-Cause Gates", markdown)
        self.assertIn("Balance Bridge", markdown)
        self.assertIn("94.06 + 0.00 - 0.00 + 21.48 = 115.54", markdown)
        self.assertIn("Open Row Decisions", markdown)
        self.assertIn("annual professional gross gap is VAT-shaped", markdown)
        self.assertIn("confirmed_direct_debit", markdown)
        self.assertIn("Synthetic Party 001", markdown)
        self.assertIn("Acceptance Criteria", markdown)

    def test_cli_writes_index_and_packet_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history = tmp_path / "history.csv"
            closure = tmp_path / "closure.csv"
            audit = tmp_path / "row_audit.csv"
            source_findings = tmp_path / "source_findings.csv"
            root_cause = tmp_path / "root_cause.csv"
            row_decisions = tmp_path / "row_decisions.csv"
            balance_bridge = tmp_path / "balance_bridge.csv"
            out_dir = tmp_path / "packets"
            self._write_history(history)
            self._write_closure(closure)
            self._write_audit(audit)
            self._write_source_findings(source_findings)
            self._write_root_cause(root_cause)
            self._write_row_decisions(row_decisions)
            self._write_balance_bridge(balance_bridge)

            exit_code = main(
                [
                    "audit-quarter-packets",
                    "--history-audit",
                    str(history),
                    "--quarter-closure",
                    str(closure),
                    "--row-audit",
                    str(audit),
                    "--source-findings",
                    str(source_findings),
                    "--root-cause-narrowing",
                    str(root_cause),
                    "--row-decisions",
                    str(row_decisions),
                    "--quarter-balance-bridge",
                    str(balance_bridge),
                    "--out-dir",
                    str(out_dir),
                ]
            )
            index_exists = (out_dir / "index.md").exists()
            packet_exists = (out_dir / "2023-Q2.md").exists()

        self.assertEqual(exit_code, 0)
        self.assertTrue(index_exists)
        self.assertTrue(packet_exists)

    def _write_history(self, path: Path) -> None:
        fieldnames = [
            "year",
            "quarter",
            "report",
            "target_casilla_01",
            "target_casilla_02",
            "target_casilla_02_delta",
            "target_casilla_19",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
                {
                    "year": "2023",
                    "quarter": "2",
                    "report": "M130 2T 2023 exampleei example.pdf",
                    "target_casilla_01": "5757.20",
                    "target_casilla_02": "115.54",
                    "target_casilla_02_delta": "115.54",
                    "target_casilla_19": "1028.33",
                }
            )

    def _write_closure(self, path: Path) -> None:
        row = {field: "" for field in QUARTER_CLOSURE_FIELDS}
        row.update(
            {
                "period": "2023-Q2",
                "closure_status": "blocked_material_unexplained_adjustment",
                "target_casilla_02_delta": "115.54",
                "pre_plug_model_minus_target_eur": "-21.48",
                "balancing_adjustment_eur": "21.48",
                "balancing_adjustment_pct_of_target": "18.59",
                "balancing_adjustment_materiality": "material",
                "candidate_amortization_delta": "0.00",
                "excluded_asset_direct_eur": "116.86",
                "excluded_nearest_subset_eur": "0.00",
                "required_xolo_evidence": "submitted register; asset schedule",
                "next_question": "Explain 2023-Q2 material adjustment.",
            }
        )
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=QUARTER_CLOSURE_FIELDS)
            writer.writeheader()
            writer.writerow(row)

    def _write_audit(self, path: Path) -> None:
        rows = [
            self._audit_row("asset_amortization_candidate", "2023-06-30", "Synthetic Party 001", "2023-06-30", "116.86"),
            self._audit_row("raw_non_asset_context_for_catch_up", "2023-06-30", "TGSS", "177918930315", "85.71"),
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=ROW_AUDIT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_source_findings(self, path: Path) -> None:
        fieldnames = ["period", "row_ref", "source_status", "source_document", "finding", "impact"]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
                {
                    "period": "2023-Q2",
                    "row_ref": "177918930315",
                    "source_status": "confirmed_direct_debit",
                    "source_document": "EXPENSE/direct_debit_20231115_124855.png",
                    "finding": "Openbank direct debit date 177918930315 period 06/2023 amount 85.71 EUR.",
                    "impact": "Confirms the TGSS row but does not explain the 21.48 EUR gap.",
                }
            )

    def _write_root_cause(self, path: Path) -> None:
        fieldnames = [
            "period",
            "annual_constrained_residual",
            "modelo303_vat_status",
            "annual_professional_base_diff",
            "eliminated_causes",
            "remaining_causes",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
                {
                    "period": "2023-Q2",
                    "annual_constrained_residual": "-21.48",
                    "modelo303_vat_status": "matched",
                    "annual_professional_base_diff": "-0.57",
                    "eliminated_causes": "annual professional gross-gap row-exclusion inference",
                    "remaining_causes": "annual asset direct-expense or reclassification treatment",
                }
            )

    def _write_row_decisions(self, path: Path) -> None:
        fieldnames = [
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
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
                {
                    "period": "2023-Q2",
                    "priority": "high",
                    "decision_kind": "identify missing catch-up or reclassification amount",
                    "classification": "missing_catch_up_or_reclassification",
                    "date": "",
                    "number": "",
                    "recipient": "",
                    "amount_eur": "21.48",
                    "question": "Which register row explains the material gap?",
                    "root_cause_context": "annual professional gross gap is VAT-shaped",
                }
            )

    def _write_balance_bridge(self, path: Path) -> None:
        fieldnames = [
            "period",
            "target_casilla_02_delta",
            "raw_non_asset_delta",
            "candidate_amortization_delta",
            "nearest_excluded_or_netted_eur",
            "candidate_model_after_local_adjustments",
            "candidate_balance_to_target",
            "annual_constraint_source",
            "annual_constrained_amortization_delta",
            "annual_constrained_model_after_local_adjustments",
            "annual_constrained_balance_to_target",
            "amortization_shift_eur",
            "balance_signal",
            "closure_status",
            "required_xolo_evidence",
            "equation",
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(
                {
                    "period": "2023-Q2",
                    "target_casilla_02_delta": "115.54",
                    "raw_non_asset_delta": "94.06",
                    "candidate_amortization_delta": "0.00",
                    "nearest_excluded_or_netted_eur": "0.00",
                    "candidate_model_after_local_adjustments": "94.06",
                    "candidate_balance_to_target": "21.48",
                    "annual_constraint_source": "m100_0208_filed_visual_extract",
                    "annual_constrained_amortization_delta": "0.00",
                    "annual_constrained_model_after_local_adjustments": "94.06",
                    "annual_constrained_balance_to_target": "21.48",
                    "amortization_shift_eur": "0.00",
                    "balance_signal": "submitted_target_above_local_model",
                    "closure_status": "blocked_material_unexplained_adjustment",
                    "required_xolo_evidence": "submitted register; asset schedule",
                    "equation": "94.06 + 0.00 - 0.00 + 21.48 = 115.54",
                }
            )

    def _audit_row(self, classification: str, date: str, recipient: str, number: str, amount: str) -> dict[str, str]:
        row = {field: "" for field in ROW_AUDIT_FIELDS}
        row.update(
            {
                "period": "2023-Q2",
                "row_kind": "xolo_expense",
                "classification": classification,
                "date": date,
                "recipient": recipient,
                "number": number,
                "gross_eur": amount,
                "base_eur": amount,
                "notes": "fixture",
            }
        )
        return row


if __name__ == "__main__":
    unittest.main()
