from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.annual_constrained_assets import build_annual_constrained_asset_reconciliation
from autonomo_taxes.cli import main


MODEL100_FIELDS = [
    "year",
    "source_report",
    "status",
    "income_0171",
    "total_income_0180",
    "social_security_0186",
    "professional_services_0199",
    "other_external_services_0202",
    "amortization_0208",
    "deductible_expenses_0218",
    "difficult_expenses_0222",
    "total_deductible_0223",
    "notes",
]

CANDIDATE_FIELDS = [
    "year",
    "quarter",
    "period",
    "scenario",
    "target_casilla_02_delta",
    "raw_non_asset_gross_delta",
    "candidate_amortization_delta",
    "candidate_model_delta",
    "candidate_model_minus_target_delta",
]


class AnnualConstrainedAssetTests(unittest.TestCase):
    def test_constrains_candidate_quarter_amortization_to_m100_0208(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            candidates = tmp_path / "candidate.csv"
            summaries = tmp_path / "m100.csv"
            self._write_candidates(
                candidates,
                [
                    self._candidate("2024", "1", "100.00", "30.00", "130.00", "30.00"),
                    self._candidate("2024", "2", "150.00", "70.00", "220.00", "70.00"),
                    self._candidate("2026", "1", "50.00", "25.00", "75.00", "25.00"),
                ],
            )
            self._write_summaries(summaries, [self._summary("2024", "80.00", "filed_text_extract")])

            rows = build_annual_constrained_asset_reconciliation(candidates, summaries)

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(by_period["2024-Q1"]["annual_constrained_amortization_delta"], "24.00")
        self.assertEqual(by_period["2024-Q2"]["annual_constrained_amortization_delta"], "56.00")
        self.assertEqual(by_period["2024-Q2"]["annual_constraint_source"], "m100_0208_filed_text_extract")
        self.assertEqual(by_period["2026-Q1"]["annual_constrained_amortization_delta"], "25.00")
        self.assertEqual(by_period["2026-Q1"]["annual_constraint_source"], "no_m100_0208_available_unconstrained")

    def test_cli_writes_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            candidates = tmp_path / "candidate.csv"
            summaries = tmp_path / "m100.csv"
            out_csv = tmp_path / "out.csv"
            out_md = tmp_path / "out.md"
            self._write_candidates(candidates, [self._candidate("2024", "1", "100.00", "10.00", "110.00", "10.00")])
            self._write_summaries(summaries, [self._summary("2024", "8.00", "filed_text_extract")])

            exit_code = main(
                [
                    "audit-annual-constrained-assets",
                    "--candidate-quarter-reconciliation",
                    str(candidates),
                    "--modelo100-summary",
                    str(summaries),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            self.assertIn("Annual-Constrained Asset Reconciliation", out_md.read_text(encoding="utf-8"))

    def _write_candidates(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CANDIDATE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_summaries(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=MODEL100_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _candidate(
        self,
        year: str,
        quarter: str,
        target: str,
        amortization: str,
        model: str,
        model_minus_target: str,
    ) -> dict[str, str]:
        return {
            "year": year,
            "quarter": quarter,
            "period": f"{year}-Q{quarter}",
            "scenario": "fixture",
            "target_casilla_02_delta": target,
            "raw_non_asset_gross_delta": "100.00",
            "candidate_amortization_delta": amortization,
            "candidate_model_delta": model,
            "candidate_model_minus_target_delta": model_minus_target,
        }

    def _summary(self, year: str, amortization: str, status: str) -> dict[str, str]:
        row = {field: "0.00" for field in MODEL100_FIELDS}
        row.update(
            {
                "year": year,
                "source_report": "fixture.pdf",
                "status": status,
                "amortization_0208": amortization,
                "notes": "fixture",
            }
        )
        return row


if __name__ == "__main__":
    unittest.main()
