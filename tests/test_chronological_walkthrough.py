from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.asset_gap_matrix import ASSET_GAP_MATRIX_FIELDS
from autonomo_taxes.chronological_walkthrough import (
    build_chronological_walkthrough,
    write_chronological_walkthrough_markdown,
)
from autonomo_taxes.cli import main
from autonomo_taxes.quarter_closure import QUARTER_CLOSURE_FIELDS


class ChronologicalWalkthroughTests(unittest.TestCase):
    def test_walkthrough_marks_first_blocker_and_amortization_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            closure = tmp_path / "closure.csv"
            asset_gap = tmp_path / "asset_gap.csv"
            source_findings = tmp_path / "source_findings.csv"
            out_md = tmp_path / "walkthrough.md"
            _write_closure(
                closure,
                [
                    _closure_row("2024-Q3", "blocked_material_unexplained_adjustment", "4008.94"),
                    _closure_row("2023-Q2", "blocked_material_unexplained_adjustment", "115.54"),
                    _closure_row("2023-Q3", "blocked_material_unexplained_adjustment", "262.01"),
                    _closure_row("2026-Q2", "pending_asset_schedule_confirmation", "4843.29"),
                ],
            )
            _write_asset_gap(
                asset_gap,
                [
                    _asset_gap_row(
                        "2023-Q2",
                        "excluded_asset_or_register_adjustment_required",
                        required="21.48",
                        annual="0.00",
                        required_minus_annual="21.48",
                    ),
                    _asset_gap_row(
                        "2023-Q3",
                        "raw_non_asset_above_target",
                        required="-120.26",
                        annual="0.00",
                        required_minus_annual="-120.26",
                    ),
                    _asset_gap_row(
                        "2024-Q3",
                        "ordinary_amortization_too_small",
                        required="395.07",
                        annual="54.42",
                        required_minus_annual="340.65",
                    ),
                    _asset_gap_row(
                        "2026-Q2",
                        "asset_amortization_above_required_row_exclusions_needed",
                        required="44.82",
                        annual="235.29",
                        required_minus_annual="-190.47",
                    ),
                ],
            )
            _write_source_findings(
                source_findings,
                [
                    _source_row("2023-Q2", "scenario_q2_sensitivity"),
                    _source_row("2026-Q2", "XOLO-2026-Q2-AMORTIZATION-PENDING", "unconfirmed_asset_amortization_row"),
                ],
            )

            rows = build_chronological_walkthrough(closure, asset_gap, source_findings)
            write_chronological_walkthrough_markdown(out_md, rows)
            markdown = out_md.read_text(encoding="utf-8")

        by_period = {row["period"]: row for row in rows}
        self.assertEqual([row["period"] for row in rows], ["2023-Q2", "2023-Q3", "2024-Q3", "2026-Q2"])
        self.assertEqual(by_period["2023-Q2"]["first_unclosed_period"], "yes")
        self.assertEqual(by_period["2023-Q3"]["first_unclosed_period"], "no")
        self.assertEqual(by_period["2023-Q2"]["chronological_gate"], "first_asset_treatment_gate")
        self.assertEqual(by_period["2023-Q3"]["chronological_gate"], "row_set_before_amortization")
        self.assertEqual(by_period["2024-Q3"]["chronological_gate"], "catchup_or_reclassification_gate")
        self.assertEqual(by_period["2026-Q2"]["chronological_gate"], "asset_schedule_plus_row_set_gate")
        self.assertIn("adding amortization worsens the fit", by_period["2023-Q3"]["local_verdict"])
        self.assertIn("scenario_q2_sensitivity", by_period["2023-Q2"]["unresolved_source_refs"])
        self.assertIn("First unclosed chronological gate: `2023-Q2`", markdown)
        self.assertIn("First asset/direct-expense treatment gates: `2023-Q2`", markdown)
        self.assertIn("Ordinary amortization too small / catch-up pressure: `2024-Q3`", markdown)
        self.assertIn("Confirm source books for 2023-Q2", markdown)

    def test_cli_writes_walkthrough_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            closure = tmp_path / "closure.csv"
            asset_gap = tmp_path / "asset_gap.csv"
            out_csv = tmp_path / "walkthrough.csv"
            out_md = tmp_path / "walkthrough.md"
            _write_closure(closure, [_closure_row("2023-Q2", "blocked_material_unexplained_adjustment", "115.54")])
            _write_asset_gap(
                asset_gap,
                [
                    _asset_gap_row(
                        "2023-Q2",
                        "excluded_asset_or_register_adjustment_required",
                        required="21.48",
                        annual="0.00",
                        required_minus_annual="21.48",
                    )
                ],
            )

            exit_code = main(
                [
                    "audit-chronological-walkthrough",
                    "--quarter-closure",
                    str(closure),
                    "--asset-gap-matrix",
                    str(asset_gap),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            self.assertIn("Chronological Walkthrough", out_md.read_text(encoding="utf-8"))


def _write_closure(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUARTER_CLOSURE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_asset_gap(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=ASSET_GAP_MATRIX_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_source_findings(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["period", "row_ref", "source_status", "source_document", "finding", "impact"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _closure_row(period: str, status: str, target: str) -> dict[str, str]:
    row = {field: "" for field in QUARTER_CLOSURE_FIELDS}
    row.update(
        {
            "period": period,
            "closure_status": status,
            "target_casilla_02_delta": target,
            "pre_plug_model_minus_target_eur": "-21.48",
            "balancing_adjustment_eur": "21.48",
            "next_question": f"Next question for {period}",
        }
    )
    return row


def _asset_gap_row(
    period: str,
    signal: str,
    *,
    required: str,
    annual: str,
    required_minus_annual: str,
) -> dict[str, str]:
    row = {field: "" for field in ASSET_GAP_MATRIX_FIELDS}
    row.update(
        {
            "period": period,
            "target_casilla_02_delta": "100.00",
            "raw_non_asset_gross_delta": "78.52",
            "required_amortization_or_catchup_delta": required,
            "annual_constrained_amortization_delta": annual,
            "required_minus_annual_constrained_amortization": required_minus_annual,
            "active_asset_count": "2",
            "amortization_gap_signal": signal,
            "next_action": f"Confirm submitted register for {period}",
        }
    )
    return row


def _source_row(
    period: str,
    row_ref: str,
    status: str = "unconfirmed_arithmetic_hypothesis",
) -> dict[str, str]:
    return {
        "period": period,
        "row_ref": row_ref,
        "source_status": status,
        "source_document": "runs/example.md",
        "finding": "fixture",
        "impact": "fixture",
    }


if __name__ == "__main__":
    unittest.main()
