from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.p1_near_fit_context import build_p1_near_fit_context
from autonomo_taxes.quarter_acceptance import QUARTER_ACCEPTANCE_FIELDS
from autonomo_taxes.quarter_balance_bridge import QUARTER_BALANCE_BRIDGE_FIELDS


class P1NearFitContextTests(unittest.TestCase):
    def test_builds_only_p1_context_and_classifies_exact_and_near_fits(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_inputs(Path(tmp))

            rows = build_p1_near_fit_context(
                paths["acceptance"],
                paths["bridge"],
                paths["hypotheses"],
            )

        self.assertEqual([row["period"] for row in rows], ["2023-Q3", "2024-Q1"])
        q3 = rows[0]
        self.assertEqual(q3["context_signal"], "exact_arithmetic_fit_requires_xolo_confirmation")
        self.assertEqual(q3["diff_to_target_eur"], "0.00")
        self.assertIn("LINQPad", q3["components"])

        q1 = rows[1]
        self.assertEqual(q1["context_signal"], "near_fit_requires_xolo_confirmation")
        self.assertEqual(q1["diff_to_target_eur"], "0.17")
        self.assertIn("MediaMarkt", q1["xolo_question"])

    def test_cli_writes_p1_context_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _write_inputs(root)
            out_csv = root / "p1.csv"
            out_md = root / "p1.md"

            exit_code = main(
                [
                    "audit-p1-near-fit-context",
                    "--quarter-acceptance",
                    str(paths["acceptance"]),
                    "--quarter-balance-bridge",
                    str(paths["bridge"]),
                    "--hypotheses",
                    str(paths["hypotheses"]),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertIn("exact_arithmetic_fit_requires_xolo_confirmation", out_csv.read_text(encoding="utf-8"))
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Modelo 130 P1 Near-Fit Context", markdown)
            self.assertIn("Confirmed closed from this context alone: `0`", markdown)


def _write_inputs(root: Path) -> dict[str, Path]:
    acceptance = root / "acceptance.csv"
    bridge = root / "bridge.csv"
    hypotheses = root / "hypotheses.csv"
    _write_dicts(
        acceptance,
        QUARTER_ACCEPTANCE_FIELDS,
        [
            _acceptance_row("2023-Q2", "P0"),
            _acceptance_row("2023-Q3", "P1"),
            _acceptance_row("2024-Q1", "P1"),
        ],
    )
    _write_dicts(
        bridge,
        QUARTER_BALANCE_BRIDGE_FIELDS,
        [
            _bridge_row("2023-Q2", "115.54", "21.48"),
            _bridge_row("2023-Q3", "262.01", "12.84"),
            _bridge_row("2024-Q1", "254.36", "-6.06"),
        ],
    )
    _write_dicts(
        hypotheses,
        [
            "period",
            "hypothesis_id",
            "hypothesis_status",
            "target_delta_eur",
            "model_amount_eur",
            "diff_to_target_eur",
            "fit_signal",
            "components",
            "source_ref",
            "evidence_status",
            "notes",
            "xolo_question",
        ],
        [
            _hypothesis_row("2023-Q2", "p0_should_be_filtered", "115.54", "115.54", "0.00", "GitHub", "P0 question"),
            _hypothesis_row("2023-Q3", "linqpad_exact", "262.01", "262.01", "0.00", "LINQPad exact basis", "LINQPad question"),
            _hypothesis_row("2024-Q1", "mediamarkt_near_fit", "254.36", "254.53", "0.17", "MediaMarkt near fit", "MediaMarkt question"),
        ],
    )
    return {"acceptance": acceptance, "bridge": bridge, "hypotheses": hypotheses}


def _acceptance_row(period: str, priority: str) -> dict[str, str]:
    return {
        "period": period,
        "review_order": "001",
        "acceptance_status": "not_closed_material_status_near_fit_requires_register",
        "priority": priority,
        "target_casilla_02_delta": "0.00",
        "annual_constrained_balance_to_target": "0.00",
        "balance_signal": "fixture",
        "closure_status": "fixture",
        "material_gap_focus": "no",
        "material_hypotheses": "",
        "required_evidence": "register",
        "next_action": "question",
        "packet_path": "packet",
    }


def _bridge_row(period: str, target: str, balance: str) -> dict[str, str]:
    return {
        "period": period,
        "target_casilla_02_delta": target,
        "raw_non_asset_delta": target,
        "candidate_amortization_delta": "0.00",
        "nearest_excluded_or_netted_eur": "0.00",
        "candidate_model_after_local_adjustments": target,
        "candidate_balance_to_target": balance,
        "annual_constraint_source": "fixture",
        "annual_constrained_amortization_delta": "0.00",
        "annual_constrained_model_after_local_adjustments": target,
        "annual_constrained_balance_to_target": balance,
        "amortization_shift_eur": "0.00",
        "balance_signal": "fixture",
        "closure_status": "fixture",
        "required_xolo_evidence": "register",
        "equation": "fixture",
    }


def _hypothesis_row(
    period: str,
    hypothesis_id: str,
    target: str,
    model: str,
    diff: str,
    components: str,
    question: str,
) -> dict[str, str]:
    return {
        "period": period,
        "hypothesis_id": hypothesis_id,
        "hypothesis_status": "unconfirmed_register_basis",
        "target_delta_eur": target,
        "model_amount_eur": model,
        "diff_to_target_eur": diff,
        "fit_signal": "fixture",
        "components": components,
        "source_ref": "runs/fixture.md",
        "evidence_status": "local_sensitivity_only",
        "notes": "fixture",
        "xolo_question": question,
    }


def _write_dicts(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
