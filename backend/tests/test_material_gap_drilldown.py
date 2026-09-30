from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.material_gap_drilldown import build_material_gap_drilldown
from autonomo_taxes.quarter_balance_bridge import QUARTER_BALANCE_BRIDGE_FIELDS


class MaterialGapDrilldownTests(unittest.TestCase):
    def test_strong_q3_hypothesis_sums_against_candidate_and_annual_balances(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bridge = _write_bridge(root)
            hypotheses = _write_hypotheses(root)

            rows = build_material_gap_drilldown(bridge, hypotheses)

        q3_rows = [
            row
            for row in rows
            if row["period"] == "2024-Q3" and row["hypothesis_id"] == "q2_catchup_and_fiveplus_fx"
        ]
        self.assertEqual(len(q3_rows), 3)
        self.assertEqual(q3_rows[0]["hypothesis_total_eur"], "335.15")
        self.assertEqual(q3_rows[0]["residual_to_candidate_balance_eur"], "0.28")
        self.assertEqual(q3_rows[0]["residual_to_annual_balance_eur"], "1.17")
        self.assertEqual(q3_rows[0]["fit_signal"], "near_exact_pending_confirmation")

    def test_unresolved_q2_gap_remains_required_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bridge = _write_bridge(root)
            hypotheses = _write_hypotheses(root)

            rows = build_material_gap_drilldown(bridge, hypotheses)

        q2 = next(row for row in rows if row["period"] == "2023-Q2")
        self.assertEqual(q2["hypothesis_status"], "unresolved_required_evidence")
        self.assertEqual(q2["hypothesis_total_eur"], "21.48")
        self.assertEqual(q2["residual_to_candidate_balance_eur"], "0.00")
        self.assertEqual(q2["fit_signal"], "near_exact_pending_confirmation")
        self.assertIn("Which submitted", q2["xolo_question"])

    def test_cli_writes_drilldown_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bridge = _write_bridge(root)
            hypotheses = _write_hypotheses(root)
            out_csv = root / "out.csv"
            out_md = root / "out.md"

            exit_code = main(
                [
                    "audit-material-gaps",
                    "--quarter-balance-bridge",
                    str(bridge),
                    "--hypotheses",
                    str(hypotheses),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertIn("q2_catchup_and_fiveplus_fx", out_csv.read_text(encoding="utf-8"))
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Modelo 130 Material Gap Drilldown", markdown)
            self.assertIn("2024-Q3", markdown)

    def test_rejects_malformed_hypothesis_rows_with_extra_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bridge = _write_bridge(root)
            hypotheses = root / "bad.csv"
            hypotheses.write_text(
                "\n".join(
                    [
                        "period,hypothesis_id,hypothesis_status,component,amount_eur,effect_closes_gap_eur,counts_in_best_bridge,source_ref,evidence_status,notes,xolo_question",
                        "2023-Q2,bad,unresolved_required_evidence,Component,1.00,1.00,yes,source,with,comma,evidence,notes,question",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "extra fields"):
                build_material_gap_drilldown(bridge, hypotheses)


def _write_bridge(root: Path) -> Path:
    path = root / "bridge.csv"
    rows = [
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
            "required_xolo_evidence": "submitted register",
            "equation": "94.06 + 0.00 - 0.00 + 21.48 = 115.54",
        },
        {
            "period": "2024-Q3",
            "target_casilla_02_delta": "4008.94",
            "raw_non_asset_delta": "3618.20",
            "candidate_amortization_delta": "55.31",
            "nearest_excluded_or_netted_eur": "0.00",
            "candidate_model_after_local_adjustments": "3673.51",
            "candidate_balance_to_target": "335.43",
            "annual_constraint_source": "m100_0208_filed_text_extract",
            "annual_constrained_amortization_delta": "54.42",
            "annual_constrained_model_after_local_adjustments": "3672.62",
            "annual_constrained_balance_to_target": "336.32",
            "amortization_shift_eur": "-0.89",
            "balance_signal": "submitted_target_above_local_model",
            "closure_status": "blocked_material_unexplained_adjustment",
            "required_xolo_evidence": "submitted register; asset schedule",
            "equation": "3618.20 + 54.42 - 0.00 + 336.32 = 4008.94",
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        import csv

        writer = csv.DictWriter(handle, fieldnames=QUARTER_BALANCE_BRIDGE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_hypotheses(root: Path) -> Path:
    path = root / "hypotheses.csv"
    path.write_text(
        "\n".join(
            [
                "period,hypothesis_id,hypothesis_status,component,amount_eur,effect_closes_gap_eur,counts_in_best_bridge,source_ref,evidence_status,notes,xolo_question",
                "2023-Q2,unexplained_register_adjustment,unresolved_required_evidence,Submitted-register adjustment,21.48,21.48,yes,source,no_local_source,No local row,Which submitted row?",
                "2024-Q3,q2_catchup_and_fiveplus_fx,strong_candidate_pending_confirmation,Q2 June TGSS,297.67,297.67,yes,source,confirmed,Component,Question 1",
                "2024-Q3,q2_catchup_and_fiveplus_fx,strong_candidate_pending_confirmation,Q2 bus base,17.30,17.30,yes,source,confirmed,Component,Question 2",
                "2024-Q3,q2_catchup_and_fiveplus_fx,strong_candidate_pending_confirmation,Five Plus basis,20.18,20.18,yes,source,confirmed,Component,Question 3",
            ]
        ),
        encoding="utf-8",
    )
    return path


if __name__ == "__main__":
    unittest.main()
