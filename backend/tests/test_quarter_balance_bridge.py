from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.quarter_balance_bridge import (
    build_quarter_balance_bridge,
    write_quarter_balance_bridge_csv,
    write_quarter_balance_bridge_markdown,
)


class QuarterBalanceBridgeTests(unittest.TestCase):
    def test_rewrites_quarter_as_raw_amortization_exclusions_and_balance(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_inputs(Path(tmp))

            rows = build_quarter_balance_bridge(
                paths["candidate"],
                paths["closure"],
                paths["annual"],
            )

        by_period = {row["period"]: row for row in rows}
        q3 = by_period["2023-Q3"]
        self.assertEqual(q3["raw_non_asset_delta"], "382.27")
        self.assertEqual(q3["annual_constrained_amortization_delta"], "0.00")
        self.assertEqual(q3["nearest_excluded_or_netted_eur"], "133.10")
        self.assertEqual(q3["annual_constrained_model_after_local_adjustments"], "249.17")
        self.assertEqual(q3["annual_constrained_balance_to_target"], "12.84")
        self.assertEqual(q3["balance_signal"], "minor_amount_but_material_by_context")
        self.assertEqual(q3["equation"], "382.27 + 0.00 - 133.10 + 12.84 = 262.01")

    def test_uses_annual_constrained_amortization_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_inputs(Path(tmp))

            rows = build_quarter_balance_bridge(
                paths["candidate"],
                paths["closure"],
                paths["annual"],
            )

        q1 = {row["period"]: row for row in rows}["2024-Q1"]
        self.assertEqual(q1["candidate_amortization_delta"], "20.89")
        self.assertEqual(q1["annual_constrained_amortization_delta"], "20.55")
        self.assertEqual(q1["amortization_shift_eur"], "-0.34")
        self.assertEqual(q1["candidate_balance_to_target"], "-6.40")
        self.assertEqual(q1["annual_constrained_balance_to_target"], "-6.06")

    def test_cli_writes_csv_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _write_inputs(root)
            out_csv = root / "out.csv"
            out_md = root / "out.md"

            exit_code = main(
                [
                    "audit-quarter-balance-bridge",
                    "--candidate-quarter-reconciliation",
                    str(paths["candidate"]),
                    "--quarter-closure",
                    str(paths["closure"]),
                    "--annual-constrained-assets",
                    str(paths["annual"]),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertIn("2024-Q1", out_csv.read_text(encoding="utf-8"))
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Modelo 130 Quarter Balance Bridge", markdown)
            self.assertIn("459.63 + 20.55 - 219.76 + -6.06 = 254.36", markdown)


def _write_inputs(root: Path) -> dict[str, Path]:
    candidate = root / "candidate.csv"
    candidate.write_text(
        "\n".join(
            [
                "year,quarter,period,scenario,target_casilla_02_delta,raw_non_asset_gross_delta,candidate_amortization_delta,candidate_model_delta,candidate_model_minus_target_delta,candidate_amortization_ytd,ytd_residual_after_candidate,nearest_excluded_subset_eur,nearest_excluded_subset_error_eur,nearest_excluded_subset_rows,interpretation",
                "2023,3,2023-Q3,test,262.01,382.27,0.00,382.27,120.26,0.00,-120.26,133.10,12.84,SYNTH-DOCUMENT-043; 00010,candidate model above target",
                "2024,1,2024-Q1,test,254.36,459.63,20.89,480.52,226.16,20.89,-226.16,219.76,6.40,SYNTH-DOCUMENT-024; TGSS; 00004,candidate model above target",
            ]
        ),
        encoding="utf-8",
    )
    closure = root / "closure.csv"
    closure.write_text(
        "\n".join(
            [
                "period,closure_status,target_casilla_02_delta,pre_plug_model_minus_target_eur,balancing_adjustment_eur,balancing_adjustment_pct_of_target,balancing_adjustment_materiality,candidate_amortization_delta,excluded_asset_direct_eur,excluded_nearest_subset_eur,has_material_plug,has_asset_decision,has_nearest_exclusion,has_minor_residual,asset_rows,nearest_exclusion_rows,required_xolo_evidence,next_question",
                "2023-Q3,blocked_material_unexplained_adjustment,262.01,-12.84,12.84,4.90,material,0.00,80.69,133.10,yes,yes,yes,no,asset row,nearest rows,submitted register; asset treatment,question",
                "2024-Q1,blocked_material_unexplained_adjustment,254.36,6.40,-6.40,2.52,material,20.89,419.30,219.76,yes,yes,yes,no,asset row,nearest rows,submitted register; asset schedule,question",
            ]
        ),
        encoding="utf-8",
    )
    annual = root / "annual.csv"
    annual.write_text(
        "\n".join(
            [
                "year,quarter,period,annual_constraint_source,target_casilla_02_delta,raw_non_asset_gross_delta,candidate_amortization_delta,annual_constrained_amortization_delta,amortization_delta_shift,candidate_model_minus_target_delta,annual_constrained_model_delta,annual_constrained_model_minus_target_delta,interpretation",
                "2023,3,2023-Q3,m100_0208_filed_visual_extract,262.01,382.27,0.00,0.00,0.00,120.26,382.27,120.26,look for exclusions",
                "2024,1,2024-Q1,m100_0208_filed_text_extract,254.36,459.63,20.89,20.55,-0.34,226.16,480.18,225.82,look for exclusions",
            ]
        ),
        encoding="utf-8",
    )
    return {"candidate": candidate, "closure": closure, "annual": annual}


if __name__ == "__main__":
    unittest.main()
