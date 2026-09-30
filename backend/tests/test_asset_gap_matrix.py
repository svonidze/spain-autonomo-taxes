from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.asset_gap_matrix import (
    build_asset_gap_matrix,
    write_asset_gap_matrix_markdown,
)
from autonomo_taxes.cli import main


class AssetGapMatrixTests(unittest.TestCase):
    def test_classifies_raw_non_asset_above_target_as_row_treatment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate, annual, intake = _write_fixture(root)

            rows = build_asset_gap_matrix(candidate, annual, intake)

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(by_period["2024-Q4"]["required_amortization_or_catchup_delta"], "-100.00")
        self.assertEqual(by_period["2024-Q4"]["amortization_gap_signal"], "raw_non_asset_above_target")
        self.assertIn("exclusions", by_period["2024-Q4"]["next_action"])

    def test_classifies_positive_gap_larger_than_schedule_as_catchup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate, annual, intake = _write_fixture(root)

            rows = build_asset_gap_matrix(candidate, annual, intake)

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(by_period["2024-Q3"]["required_amortization_or_catchup_delta"], "400.00")
        self.assertEqual(by_period["2024-Q3"]["required_minus_annual_constrained_amortization"], "345.00")
        self.assertEqual(by_period["2024-Q3"]["amortization_gap_signal"], "ordinary_amortization_too_small")
        self.assertEqual(by_period["2024-Q3"]["required_as_pct_of_active_asset_base"], "40.00%")

    def test_writes_markdown_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate, annual, intake = _write_fixture(root)
            rows = build_asset_gap_matrix(candidate, annual, intake)
            out_md = root / "gap.md"

            write_asset_gap_matrix_markdown(out_md, rows)

            text = out_md.read_text(encoding="utf-8")
        self.assertIn("Asset Gap Matrix", text)
        self.assertIn("ordinary_amortization_too_small", text)

    def test_cli_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate, annual, intake = _write_fixture(root)
            out_csv = root / "gap.csv"
            out_md = root / "gap.md"

            exit_code = main(
                [
                    "audit-asset-gap-matrix",
                    "--candidate-quarter-reconciliation",
                    str(candidate),
                    "--annual-constrained-assets",
                    str(annual),
                    "--asset-schedule-intake",
                    str(intake),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            self.assertIn("Asset Gap Matrix", out_md.read_text(encoding="utf-8"))


def _write_fixture(root: Path) -> tuple[Path, Path, Path]:
    candidate = root / "candidate.csv"
    candidate.write_text(
        "\n".join(
            [
                "year,quarter,period,target_casilla_02_delta,raw_non_asset_gross_delta,candidate_amortization_delta,candidate_model_minus_target_delta",
                "2024,3,2024-Q3,1000.00,600.00,50.00,-350.00",
                "2024,4,2024-Q4,1000.00,1100.00,55.00,155.00",
            ]
        ),
        encoding="utf-8",
    )
    annual = root / "annual.csv"
    annual.write_text(
        "\n".join(
            [
                "period,annual_constrained_amortization_delta,annual_constrained_model_minus_target_delta",
                "2024-Q3,55.00,-345.00",
                "2024-Q4,55.00,155.00",
            ]
        ),
        encoding="utf-8",
    )
    intake = root / "intake.csv"
    intake.write_text(
        "\n".join(
            [
                "period,asset_date,recipient,number,base_basis_eur,candidate_included",
                "2024-Q3,2024-01-04,Computer Shop,ASSET-1,1000.00,yes",
                "2024-Q4,2024-01-04,Computer Shop,ASSET-1,1000.00,yes",
            ]
        ),
        encoding="utf-8",
    )
    return candidate, annual, intake


if __name__ == "__main__":
    unittest.main()
