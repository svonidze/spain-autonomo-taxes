from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.row_audit import ROW_AUDIT_FIELDS
from autonomo_taxes.row_decision_report import build_row_decision_report


ROOT_FIELDS = [
    "period",
    "remaining_causes",
    "annual_constraint_source",
    "eliminated_causes",
    "annual_professional_base_diff",
]


class RowDecisionReportTests(unittest.TestCase):
    def test_builds_concrete_questions_for_open_row_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            row_audit = tmp_path / "row_audit.csv"
            root = tmp_path / "root.csv"
            self._write_row_audit(
                row_audit,
                [
                    self._row("2024-Q4", "nearest_exclusion_candidate", "INV", "Xolo", "59.29", subset_error="0.10"),
                    self._row("2023-Q2", "asset_amortization_candidate", "GITHUB", "GitHub", "116.86"),
                    self._row("2023-Q2", "missing_catch_up_or_reclassification", "", "", "21.48"),
                    self._row("2023-Q3", "unresolved_after_nearest_subset", "", "", "-12.84"),
                    self._row("2024-Q4", "raw_non_asset_candidate_model", "KEEP", "Supplier", "10.00"),
                ],
            )
            self._write_root(
                root,
                [
                    self._root("2024-Q4", "asset amortization schedule confirmation", "m100_0208_filed_text_extract"),
                    self._root(
                        "2023-Q2",
                        "asset/direct-expense treatment confirmation; annual asset direct-expense or reclassification treatment",
                        "m100_0208_filed_visual_extract",
                        eliminated="annual professional gross-gap row-exclusion inference",
                        professional_base_diff="-0.57",
                    ),
                    self._root("2023-Q3", "excluded/netted rows", "m100_0208_filed_visual_extract", professional_base_diff="-0.57"),
                ],
            )

            rows = build_row_decision_report(row_audit, root)

        self.assertEqual(len(rows), 4)
        by_number = {row["number"]: row for row in rows}
        self.assertEqual(by_number["INV"]["priority"], "high")
        self.assertIn("included in Modelo 130", by_number["INV"]["question"])
        self.assertEqual(by_number["GITHUB"]["decision_kind"], "confirm direct expense versus capitalized asset")
        self.assertIn("VAT-shaped", by_number["GITHUB"]["root_cause_context"])
        self.assertIn("direct expense or reclassification", by_number["GITHUB"]["root_cause_context"])
        missing = next(row for row in rows if row["classification"] == "missing_catch_up_or_reclassification")
        self.assertEqual(missing["priority"], "high")
        self.assertIn("catch-up", missing["question"])
        residual = next(row for row in rows if row["classification"] == "unresolved_after_nearest_subset")
        self.assertEqual(residual["amount_eur"], "12.84")
        self.assertIn("overshoots", residual["question"])

    def test_cli_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            row_audit = tmp_path / "row_audit.csv"
            root = tmp_path / "root.csv"
            out_csv = tmp_path / "decisions.csv"
            out_md = tmp_path / "decisions.md"
            self._write_row_audit(row_audit, [self._row("2024-Q4", "nearest_exclusion_candidate", "INV", "Xolo", "59.29")])
            self._write_root(root, [self._root("2024-Q4", "excluded/netted rows", "m100_0208_filed_text_extract")])

            exit_code = main(
                [
                    "audit-row-decisions",
                    "--row-audit",
                    str(row_audit),
                    "--root-cause-narrowing",
                    str(root),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            self.assertIn("Modelo 130 Row Decision Report", out_md.read_text(encoding="utf-8"))

    def _write_row_audit(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=ROW_AUDIT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_root(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=ROOT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _row(
        self,
        period: str,
        classification: str,
        number: str,
        recipient: str,
        amount: str,
        *,
        subset_error: str = "0.00",
    ) -> dict[str, str]:
        row = {field: "" for field in ROW_AUDIT_FIELDS}
        row.update(
            {
                "year": period[:4],
                "quarter": period[-1],
                "period": period,
                "row_kind": "synthetic_gap" if not number else "xolo_expense",
                "classification": classification,
                "date": "2024-12-01" if number else "",
                "recipient": recipient,
                "number": number,
                "currency": "EUR",
                "amount_original": amount,
                "gross_eur": amount,
                "base_eur": amount,
                "candidate_model_minus_target_delta": amount,
                "nearest_subset_total_eur": amount,
                "nearest_subset_error_eur": subset_error,
                "notes": "fixture",
            }
        )
        return row

    def _root(
        self,
        period: str,
        remaining: str,
        source: str,
        *,
        eliminated: str = "",
        professional_base_diff: str = "",
    ) -> dict[str, str]:
        return {
            "period": period,
            "remaining_causes": remaining,
            "annual_constraint_source": source,
            "eliminated_causes": eliminated,
            "annual_professional_base_diff": professional_base_diff,
        }


if __name__ == "__main__":
    unittest.main()
