from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.chronological_walkthrough import CHRONOLOGICAL_WALKTHROUGH_FIELDS
from autonomo_taxes.cli import main
from autonomo_taxes.first_gate_report import build_first_gate_report, write_first_gate_markdown
from autonomo_taxes.material_gap_drilldown import MATERIAL_GAP_DRILLDOWN_FIELDS


class FirstGateReportTests(unittest.TestCase):
    def test_report_focuses_first_unclosed_period_and_preserves_verdicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            chronological = tmp_path / "chronological.csv"
            drilldown = tmp_path / "drilldown.csv"
            context = tmp_path / "context.csv"
            source_findings = tmp_path / "source_findings.csv"
            out_md = tmp_path / "first_gate.md"
            _write_chronological(
                chronological,
                [
                    _chronological_row("2023-Q2", "yes", "first_asset_treatment_gate"),
                    _chronological_row("2023-Q3", "no", "row_set_before_amortization"),
                ],
            )
            _write_drilldown(
                drilldown,
                [
                    _hypothesis_row(
                        "2023-Q2",
                        "github_partial_asset_basis",
                        "unresolved_required_evidence",
                        "21.48",
                        "near_exact_pending_confirmation",
                    ),
                    _hypothesis_row(
                        "2023-Q2",
                        "github_direct_expense",
                        "ruled_out_local_hypothesis",
                        "115.54",
                        "ruled_out",
                    ),
                    _hypothesis_row(
                        "2023-Q3",
                        "q3_other",
                        "unresolved_required_evidence",
                        "12.84",
                        "near_exact_pending_confirmation",
                    ),
                ],
            )
            _write_context(context)
            _write_source_findings(source_findings)

            rows = build_first_gate_report(chronological, drilldown, context, source_findings)
            write_first_gate_markdown(out_md, rows)
            markdown = out_md.read_text(encoding="utf-8")

        by_section = {}
        for row in rows:
            by_section.setdefault(row["section"], []).append(row)
        self.assertEqual(by_section["gate"][0]["period"], "2023-Q2")
        self.assertEqual(len(by_section["hypothesis"]), 2)
        self.assertIn("github_partial_asset_basis", markdown)
        self.assertIn("github_direct_expense", markdown)
        self.assertIn("Locally ruled-out explanations: `github_direct_expense`", markdown)
        self.assertIn("provide the source-book tie-out", markdown)
        self.assertIn("GitHub 1751445", markdown)
        self.assertNotIn("q3_other", markdown)

    def test_cli_writes_first_gate_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            chronological = tmp_path / "chronological.csv"
            drilldown = tmp_path / "drilldown.csv"
            out_csv = tmp_path / "first_gate.csv"
            out_md = tmp_path / "first_gate.md"
            _write_chronological(chronological, [_chronological_row("2023-Q2", "yes", "first_asset_treatment_gate")])
            _write_drilldown(
                drilldown,
                [
                    _hypothesis_row(
                        "2023-Q2",
                        "unexplained_source_book_adjustment",
                        "unresolved_required_evidence",
                        "21.48",
                        "near_exact_pending_confirmation",
                    )
                ],
            )

            exit_code = main(
                [
                    "audit-first-gate",
                    "--chronological-walkthrough",
                    str(chronological),
                    "--material-gap-drilldown",
                    str(drilldown),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            self.assertIn("First Gate Report", out_md.read_text(encoding="utf-8"))

    def test_no_unclosed_gate_writes_closed_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            chronological = tmp_path / "chronological.csv"
            drilldown = tmp_path / "drilldown.csv"
            out_md = tmp_path / "first_gate.md"
            _write_chronological(chronological, [_chronological_row("2023-Q2", "no", "")])
            _write_drilldown(drilldown, [])

            rows = build_first_gate_report(chronological, drilldown)
            write_first_gate_markdown(out_md, rows)

            self.assertEqual(rows, [])
            self.assertIn("No unclosed chronological gate was found", out_md.read_text(encoding="utf-8"))


def _write_chronological(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CHRONOLOGICAL_WALKTHROUGH_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_drilldown(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MATERIAL_GAP_DRILLDOWN_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_context(path: Path) -> None:
    fieldnames = [
        "period",
        "context_signal",
        "bridge_raw_non_asset_delta",
        "target_minus_xolo_non_asset_eur",
        "gap_sized_asset_candidate_rows",
        "gap_sized_asset_candidate_fit",
        "asset_candidate_rows",
        "next_xolo_question",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "period": "2023-Q2",
                "context_signal": "target_matches_asset_candidate_if_non_assets_excluded",
                "bridge_raw_non_asset_delta": "94.06",
                "target_minus_xolo_non_asset_eur": "21.48",
                "gap_sized_asset_candidate_rows": "GitHub 1751445",
                "gap_sized_asset_candidate_fit": "1751445: gap 21.48 is 18.59% of 115.54",
                "asset_candidate_rows": "GitHub 1751445",
                "next_xolo_question": "Did Xolo use GitHub 1751445?",
            }
        )


def _write_source_findings(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["period", "row_ref", "source_status", "source_document", "finding", "impact"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "period": "2023-Q2",
                "row_ref": "scenario_q2_sensitivity",
                "source_status": "unconfirmed_arithmetic_hypothesis",
                "source_document": "runs/modelo130_2023_q2_sensitivity.md",
                "finding": "fixture",
                "impact": "Keep 2023-Q2 open until Xolo provides source books.",
            }
        )


def _chronological_row(period: str, first: str, gate: str) -> dict[str, str]:
    row = {field: "" for field in CHRONOLOGICAL_WALKTHROUGH_FIELDS}
    row.update(
        {
            "period": period,
            "first_unclosed_period": first,
            "chronological_gate": gate,
            "closure_status": "blocked_material_unexplained_adjustment",
            "target_casilla_02_delta": "115.54",
            "asset_gap_signal": "excluded_asset_or_register_adjustment_required",
            "blocking_evidence": "source-book treatment for active asset/direct-expense rows",
            "local_verdict": "Active asset/direct-expense row must explain the first gap.",
            "next_action": "Confirm source-book treatment.",
        }
    )
    return row


def _hypothesis_row(
    period: str,
    hypothesis_id: str,
    status: str,
    amount: str,
    fit_signal: str,
) -> dict[str, str]:
    row = {field: "" for field in MATERIAL_GAP_DRILLDOWN_FIELDS}
    row.update(
        {
            "period": period,
            "hypothesis_id": hypothesis_id,
            "hypothesis_status": status,
            "component": hypothesis_id,
            "effect_closes_gap_eur": amount,
            "fit_signal": fit_signal,
            "evidence_status": "fixture_evidence",
            "notes": f"Finding for {hypothesis_id}",
            "source_ref": "runs/source.md",
            "xolo_question": f"Question for {hypothesis_id}",
        }
    )
    return row


if __name__ == "__main__":
    unittest.main()
