from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.p2_near_target_context import build_p2_near_target_context
from autonomo_taxes.quarter_acceptance import QUARTER_ACCEPTANCE_FIELDS
from autonomo_taxes.quarter_balance_bridge import QUARTER_BALANCE_BRIDGE_FIELDS


class P2NearTargetContextTests(unittest.TestCase):
    def test_builds_only_p2_unconfirmed_context_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_inputs(Path(tmp))

            rows = build_p2_near_target_context(
                paths["acceptance"],
                paths["bridge"],
                paths["source_findings"],
            )

        self.assertEqual([row["row_ref"] for row in rows], ["scenario_asset", "asset_pending", "RETA-2024-679.15", "secondary"])
        by_ref = {row["row_ref"]: row for row in rows}
        self.assertEqual(
            by_ref["scenario_asset"]["context_signal"],
            "primary_near_target_fork_requires_source_books",
        )
        self.assertEqual(
            by_ref["asset_pending"]["context_signal"],
            "asset_placeholder_requires_schedule",
        )
        self.assertEqual(
            by_ref["RETA-2024-679.15"]["context_signal"],
            "reta_treatment_requires_xolo_confirmation",
        )
        self.assertEqual(
            by_ref["secondary"]["context_signal"],
            "secondary_near_target_fork",
        )

    def test_cli_writes_p2_context_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _write_inputs(root)
            out_csv = root / "p2.csv"
            out_md = root / "p2.md"

            exit_code = main(
                [
                    "audit-p2-near-target-context",
                    "--quarter-acceptance",
                    str(paths["acceptance"]),
                    "--quarter-balance-bridge",
                    str(paths["bridge"]),
                    "--source-findings",
                    str(paths["source_findings"]),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertIn("primary_near_target_fork_requires_source_books", out_csv.read_text(encoding="utf-8"))
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Modelo 130 P2 Near-Target Context", markdown)
            self.assertIn("Confirmed closed from this context alone: `0`", markdown)


def _write_inputs(root: Path) -> dict[str, Path]:
    acceptance = root / "acceptance.csv"
    bridge = root / "bridge.csv"
    source_findings = root / "source.csv"
    _write_dicts(
        acceptance,
        QUARTER_ACCEPTANCE_FIELDS,
        [
            _acceptance_row("2023-Q3", "P1"),
            _acceptance_row("2024-Q2", "P2"),
            _acceptance_row("2026-Q2", "P2"),
        ],
    )
    _write_dicts(
        bridge,
        QUARTER_BALANCE_BRIDGE_FIELDS,
        [
            _bridge_row("2024-Q2"),
            _bridge_row("2026-Q2"),
        ],
    )
    _write_dicts(
        source_findings,
        ["period", "row_ref", "source_status", "source_document", "finding", "impact"],
        [
            _source_row("2023-Q3", "scenario_p1", "unconfirmed_arithmetic_hypothesis"),
            _source_row("2024-Q2", "scenario_asset", "unconfirmed_arithmetic_hypothesis"),
            _source_row("2024-Q2", "confirmed_row", "confirmed_invoice_total"),
            _source_row("2026-Q2", "asset_pending", "unconfirmed_asset_amortization_row"),
            _source_row("2026-Q2", "RETA-2024-679.15", "unconfirmed_xolo_compliance_accounting_instruction"),
            _source_row("2026-Q2", "secondary", "secondary_arithmetic_hypothesis"),
        ],
    )
    return {"acceptance": acceptance, "bridge": bridge, "source_findings": source_findings}


def _acceptance_row(period: str, priority: str) -> dict[str, str]:
    return {
        "period": period,
        "review_order": "001",
        "acceptance_status": "not_closed_near_target_requires_asset_schedule",
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


def _bridge_row(period: str) -> dict[str, str]:
    return {
        "period": period,
        "target_casilla_02_delta": "100.00",
        "raw_non_asset_delta": "90.00",
        "candidate_amortization_delta": "10.00",
        "nearest_excluded_or_netted_eur": "5.00",
        "candidate_model_after_local_adjustments": "100.00",
        "candidate_balance_to_target": "0.00",
        "annual_constraint_source": "fixture",
        "annual_constrained_amortization_delta": "10.00",
        "annual_constrained_model_after_local_adjustments": "100.00",
        "annual_constrained_balance_to_target": "0.12",
        "amortization_shift_eur": "0.00",
        "balance_signal": "near_target_pending_confirmation",
        "closure_status": "fixture",
        "required_xolo_evidence": "register",
        "equation": "fixture",
    }


def _source_row(period: str, row_ref: str, status: str) -> dict[str, str]:
    return {
        "period": period,
        "row_ref": row_ref,
        "source_status": status,
        "source_document": "runs/source.md",
        "finding": f"Finding for {row_ref}",
        "impact": f"Impact for {row_ref}",
    }


def _write_dicts(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
