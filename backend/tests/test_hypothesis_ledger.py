from pathlib import Path
import csv
from decimal import Decimal
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.hypothesis_ledger import build_hypothesis_ledger, write_hypothesis_markdown
from autonomo_taxes.ledger_projection import build_ledger_projection
from autonomo_taxes.row_audit import ROW_AUDIT_FIELDS
from autonomo_taxes.xolo_ledger import load_xolo_expense_ledger, write_xolo_expense_ledger_csv


class HypothesisLedgerTests(unittest.TestCase):
    def test_builds_matching_ledger_with_nearest_exclusion_and_residual(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            row_audit = tmp_path / "row_audit.csv"
            candidate = tmp_path / "candidate.csv"
            ledger_path = tmp_path / "hypothesis_ledger.csv"
            projection_history = tmp_path / "history.csv"
            md_path = tmp_path / "hypothesis.md"
            self._write_row_audit(
                row_audit,
                [
                    self._row("nearest_exclusion_candidate", "INV-EXCL", "122.50"),
                    self._row("raw_non_asset_candidate_model", "INV-INCL", "77.50"),
                    self._row("asset_amortization_candidate", "ASSET", "1994.00", expense_type="Computer hardware & software"),
                ],
            )
            candidate.write_text(
                "year,quarter,period,target_casilla_02_delta,candidate_amortization_delta\n"
                "2026,2,2026-Q2,100.00,22.75\n",
                encoding="utf-8",
            )
            projection_history.write_text(
                "year,quarter,target_casilla_02,target_casilla_02_delta\n"
                "2026,2,100.00,100.00\n",
                encoding="utf-8",
            )

            result = build_hypothesis_ledger(row_audit, candidate)
            write_xolo_expense_ledger_csv(ledger_path, result.ledger_rows)
            write_hypothesis_markdown(md_path, result)
            projection = build_ledger_projection(projection_history, ledger_path)
            ledger_rows = load_xolo_expense_ledger(ledger_path)
            markdown = md_path.read_text(encoding="utf-8")

        by_number = {row.number: row for row in ledger_rows}
        self.assertFalse(by_number["INV-EXCL"].include_in_modelo130)
        self.assertFalse(by_number["ASSET"].include_in_modelo130)
        self.assertTrue(by_number["INV-INCL"].include_in_modelo130)
        self.assertEqual(by_number["HYP-2026-Q2-AMORT"].irpf_deductible_eur, Decimal("22.75"))
        self.assertEqual(by_number["HYP-2026-Q2-BALANCE"].irpf_deductible_eur, Decimal("-0.25"))
        self.assertEqual(result.summary_rows[0]["hypothesis_ledger_delta"], "100.00")
        self.assertEqual(result.summary_rows[0]["pre_plug_model_minus_target_eur"], "0.25")
        self.assertEqual(result.summary_rows[0]["balancing_adjustment_materiality"], "minor")
        self.assertEqual(result.summary_rows[0]["status"], "matches_target_with_minor_inferred_balancing_adjustment")
        self.assertEqual(projection[0]["status"], "matches_target_with_unconfirmed_rows")
        self.assertEqual(projection[0]["ledger_delta"], "100.00")
        self.assertEqual(projection[0]["pending_or_inferred_delta_eur"], "22.50")
        self.assertIn("not proof of Xolo's source books", markdown)
        self.assertIn("post-plug diff is zero by construction", markdown)

    def test_negative_gap_creates_positive_balancing_adjustment(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            row_audit = tmp_path / "row_audit.csv"
            candidate = tmp_path / "candidate.csv"
            self._write_row_audit(
                row_audit,
                [
                    self._row(
                        "raw_non_asset_context_for_catch_up",
                        "INV-RAW",
                        "300.00",
                        year="2024",
                        quarter="3",
                        period="2024-Q3",
                        date="2024-07-01",
                    )
                ],
            )
            candidate.write_text(
                "year,quarter,period,target_casilla_02_delta,candidate_amortization_delta\n"
                "2024,3,2024-Q3,400.00,20.00\n",
                encoding="utf-8",
            )

            result = build_hypothesis_ledger(row_audit, candidate)

        by_number = {row["number"]: row for row in result.ledger_rows}
        self.assertEqual(by_number["HYP-2024-Q3-BALANCE"]["irpf_deductible_eur"], "80.00")
        self.assertEqual(result.summary_rows[0]["included_raw_gross_eur"], "300.00")
        self.assertEqual(result.summary_rows[0]["candidate_amortization_delta"], "20.00")
        self.assertEqual(result.summary_rows[0]["hypothesis_minus_target_delta"], "0.00")
        self.assertEqual(result.summary_rows[0]["balancing_adjustment_materiality"], "material")
        self.assertEqual(result.summary_rows[0]["status"], "matches_target_only_via_material_unexplained_plug")

    def test_exact_match_has_no_balancing_adjustment_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            row_audit = tmp_path / "row_audit.csv"
            candidate = tmp_path / "candidate.csv"
            self._write_row_audit(row_audit, [self._row("raw_non_asset_candidate_model", "INV", "10.00")])
            candidate.write_text(
                "year,quarter,period,target_casilla_02_delta,candidate_amortization_delta\n"
                "2026,2,2026-Q2,10.00,0.00\n",
                encoding="utf-8",
            )

            result = build_hypothesis_ledger(row_audit, candidate)

        self.assertEqual(result.summary_rows[0]["balancing_adjustment_eur"], "0.00")
        self.assertEqual(result.summary_rows[0]["balancing_adjustment_materiality"], "none")
        self.assertEqual(result.summary_rows[0]["status"], "matches_target_without_balancing_adjustment")

    def test_unexpected_row_kind_is_not_silently_folded_into_plug(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            row_audit = tmp_path / "row_audit.csv"
            candidate = tmp_path / "candidate.csv"
            unexpected = self._row("raw_non_asset_candidate_model", "INV", "10.00")
            unexpected["row_kind"] = "new_future_kind"
            self._write_row_audit(row_audit, [unexpected])
            candidate.write_text(
                "year,quarter,period,target_casilla_02_delta,candidate_amortization_delta\n"
                "2026,2,2026-Q2,10.00,0.00\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Unexpected row_kind"):
                build_hypothesis_ledger(row_audit, candidate)

    def test_cli_writes_hypothesis_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            row_audit = tmp_path / "row_audit.csv"
            candidate = tmp_path / "candidate.csv"
            ledger = tmp_path / "ledger.csv"
            summary = tmp_path / "summary.csv"
            markdown = tmp_path / "ledger.md"
            self._write_row_audit(
                row_audit,
                [
                    self._row(
                        "raw_non_asset_candidate_model",
                        "INV",
                        "10.00",
                        year="2026",
                        quarter="1",
                        period="2026-Q1",
                        date="2026-01-01",
                    )
                ],
            )
            candidate.write_text(
                "year,quarter,period,target_casilla_02_delta,candidate_amortization_delta\n"
                "2026,1,2026-Q1,10.00,0.00\n",
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "audit-hypothesis-ledger",
                    "--row-audit",
                    str(row_audit),
                    "--candidate-quarter-reconciliation",
                    str(candidate),
                    "--out-ledger-csv",
                    str(ledger),
                    "--out-summary-csv",
                    str(summary),
                    "--out-md",
                    str(markdown),
                ]
            )
            ledger_exists = ledger.exists()
            summary_exists = summary.exists()
            markdown_exists = markdown.exists()

        self.assertEqual(exit_code, 0)
        self.assertTrue(ledger_exists)
        self.assertTrue(summary_exists)
        self.assertTrue(markdown_exists)

    def _write_row_audit(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=ROW_AUDIT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _row(
        self,
        classification: str,
        number: str,
        gross: str,
        *,
        expense_type: str = "Professional expenses",
        year: str = "2026",
        quarter: str = "2",
        period: str = "2026-Q2",
        date: str = "2026-04-01",
    ) -> dict[str, str]:
        row = {field: "" for field in ROW_AUDIT_FIELDS}
        row.update(
            {
                "year": year,
                "quarter": quarter,
                "period": period,
                "row_kind": "xolo_expense",
                "classification": classification,
                "xolo_id": number,
                "date": date,
                "recipient": "Supplier",
                "type": expense_type,
                "number": number,
                "currency": "EUR",
                "amount_original": gross,
                "gross_eur": gross,
                "base_eur": gross,
                "notes": "fixture",
                "xolo_url": f"https://xolo.test/{number}",
            }
        )
        return row


if __name__ == "__main__":
    unittest.main()
