from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.hypothesis_ledger import HYPOTHESIS_SUMMARY_FIELDS
from autonomo_taxes.quarter_closure import build_quarter_closure_checklist, write_quarter_closure_markdown
from autonomo_taxes.row_audit import ROW_AUDIT_FIELDS


class QuarterClosureTests(unittest.TestCase):
    def test_material_plug_takes_priority_over_asset_and_nearest_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary = tmp_path / "summary.csv"
            audit = tmp_path / "rows.csv"
            self._write_summary(
                summary,
                [
                    self._summary(
                        "2024-Q1",
                        balance="-6.40",
                        materiality="material",
                        amort="20.89",
                        asset="419.30",
                        nearest="219.76",
                    )
                ],
            )
            self._write_audit(
                audit,
                [
                    self._row("2024-Q1", "asset_amortization_candidate", "ASSET", "419.30"),
                    self._row("2024-Q1", "nearest_exclusion_candidate", "EXCL", "86.66"),
                ],
            )

            rows = build_quarter_closure_checklist(summary, audit)

        self.assertEqual(rows[0]["closure_status"], "blocked_material_unexplained_adjustment")
        self.assertEqual(rows[0]["has_material_plug"], "yes")
        self.assertEqual(rows[0]["has_asset_decision"], "yes")
        self.assertEqual(rows[0]["has_nearest_exclusion"], "yes")
        self.assertIn("material balancing amount", rows[0]["next_question"])

    def test_asset_schedule_status_when_no_material_plug(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary = tmp_path / "summary.csv"
            audit = tmp_path / "rows.csv"
            md = tmp_path / "closure.md"
            self._write_summary(
                summary,
                [
                    self._summary(
                        "2026-Q2",
                        balance="0.65",
                        materiality="minor",
                        amort="235.29",
                        asset="1994.00",
                        nearest="189.01",
                    )
                ],
            )
            self._write_audit(
                audit,
                [
                    self._row("2026-Q2", "asset_amortization_candidate", "SYNTH-DOCUMENT-031", "1994.00"),
                    self._row("2026-Q2", "nearest_exclusion_candidate", "SYNTH-DOCUMENT-019", "189.01"),
                ],
            )

            rows = build_quarter_closure_checklist(summary, audit)
            write_quarter_closure_markdown(md, rows)
            markdown = md.read_text(encoding="utf-8")

        self.assertEqual(rows[0]["closure_status"], "pending_asset_schedule_confirmation")
        self.assertIn("SYNTH-DOCUMENT-031", rows[0]["asset_rows"])
        self.assertIn("SYNTH-DOCUMENT-019", rows[0]["nearest_exclusion_rows"])
        self.assertIn("asset amortization schedule", rows[0]["required_xolo_evidence"])
        self.assertIn("Modelo 130 Quarter Closure Checklist", markdown)

    def test_cli_writes_closure_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary = tmp_path / "summary.csv"
            audit = tmp_path / "rows.csv"
            out_csv = tmp_path / "closure.csv"
            out_md = tmp_path / "closure.md"
            self._write_summary(summary, [self._summary("2025-Q4", balance="-0.12", materiality="minor")])
            self._write_audit(audit, [self._row("2025-Q4", "raw_non_asset_candidate_model", "INV", "10.00")])

            exit_code = main(
                [
                    "audit-quarter-closure",
                    "--hypothesis-summary",
                    str(summary),
                    "--row-audit",
                    str(audit),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )
            csv_exists = out_csv.exists()
            md_exists = out_md.exists()

        self.assertEqual(exit_code, 0)
        self.assertTrue(csv_exists)
        self.assertTrue(md_exists)

    def _write_summary(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=HYPOTHESIS_SUMMARY_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_audit(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=ROW_AUDIT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _summary(
        self,
        period: str,
        *,
        balance: str,
        materiality: str,
        amort: str = "0.00",
        asset: str = "0.00",
        nearest: str = "0.00",
    ) -> dict[str, str]:
        row = {field: "" for field in HYPOTHESIS_SUMMARY_FIELDS}
        row.update(
            {
                "period": period,
                "target_casilla_02_delta": "100.00",
                "included_raw_gross_eur": "90.00",
                "excluded_nearest_subset_eur": nearest,
                "excluded_asset_direct_eur": asset,
                "candidate_amortization_delta": amort,
                "pre_plug_model_minus_target_eur": "0.00",
                "balancing_adjustment_eur": balance,
                "balancing_adjustment_pct_of_target": "6.40",
                "balancing_adjustment_materiality": materiality,
                "hypothesis_ledger_delta": "100.00",
                "hypothesis_minus_target_delta": "0.00",
                "included_raw_rows": "1",
                "excluded_nearest_rows": "0",
                "excluded_asset_rows": "0",
                "ignored_non_xolo_rows": "0",
                "status": "fixture",
                "open_confirmation": "fixture",
            }
        )
        return row

    def _row(self, period: str, classification: str, number: str, gross: str) -> dict[str, str]:
        row = {field: "" for field in ROW_AUDIT_FIELDS}
        row.update(
            {
                "period": period,
                "row_kind": "xolo_expense",
                "classification": classification,
                "date": f"{period[:4]}-01-01",
                "recipient": "Supplier",
                "type": "Professional expenses",
                "number": number,
                "currency": "EUR",
                "amount_original": gross,
                "gross_eur": gross,
                "base_eur": gross,
            }
        )
        return row


if __name__ == "__main__":
    unittest.main()
