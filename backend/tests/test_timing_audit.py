from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.quarter_closure import QUARTER_CLOSURE_FIELDS
from autonomo_taxes.timing_audit import build_timing_audit, write_timing_audit_markdown


class TimingAuditTests(unittest.TestCase):
    def test_year_near_zero_signal_and_timing_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            closure = tmp_path / "closure.csv"
            sources = tmp_path / "sources.csv"
            self._write_closure(
                closure,
                [
                    self._closure_row("2024-Q3", "335.43", "blocked_material_unexplained_adjustment"),
                    self._closure_row("2024-Q4", "-0.10", "pending_asset_schedule_confirmation"),
                    self._closure_row("2025-Q1", "-1.48", "pending_asset_schedule_confirmation"),
                    self._closure_row("2025-Q2", "-0.85", "pending_asset_schedule_confirmation"),
                    self._closure_row("2025-Q3", "0.12", "pending_asset_schedule_confirmation"),
                    self._closure_row("2025-Q4", "-0.12", "pending_asset_schedule_confirmation"),
                ],
            )
            self._write_sources(
                sources,
                [
                    self._source_row("2024-Q3", "scenario_q2_catchup_and_fiveplus_fx", "Q2 catch-up and corrected invoice"),
                    self._source_row("2025-Q4", "scenario_cursor_jetbrains_exclusion", "ordinary exclusion"),
                ],
            )

            rows = build_timing_audit(closure, sources)

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(
            by_period["2025-Q4"]["year_timing_signal"],
            "annual_net_near_zero_after_hypotheses",
        )
        self.assertEqual(by_period["2025-Q4"]["year_balance_total_eur"], "-2.33")
        self.assertEqual(
            by_period["2024-Q3"]["year_timing_signal"],
            "annual_positive_submitted_adjustment_remains",
        )
        self.assertIn("scenario_q2_catchup_and_fiveplus_fx", by_period["2024-Q3"]["timing_source_refs"])
        self.assertEqual(by_period["2025-Q4"]["timing_source_count"], "0")

    def test_markdown_and_cli_write_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            closure = tmp_path / "closure.csv"
            sources = tmp_path / "sources.csv"
            out_csv = tmp_path / "timing.csv"
            out_md = tmp_path / "timing.md"
            self._write_closure(
                closure,
                [
                    self._closure_row("2026-Q1", "-0.07", "pending_asset_schedule_confirmation"),
                    self._closure_row("2026-Q2", "0.65", "pending_asset_schedule_confirmation"),
                ],
            )
            self._write_sources(sources, [self._source_row("2026-Q2", "RETA-2024-679.15", "apremio principal surcharge")])

            exit_code = main(
                [
                    "audit-timing",
                    "--quarter-closure",
                    str(closure),
                    "--source-findings",
                    str(sources),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )
            markdown = out_md.read_text(encoding="utf-8")
            csv_exists = out_csv.exists()

        self.assertEqual(exit_code, 0)
        self.assertTrue(csv_exists)
        self.assertIn("Modelo 130 Timing And Carry-Forward Audit", markdown)
        self.assertIn("RETA-2024-679.15", markdown)

    def test_markdown_writer_summarizes_material_years(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timing.md"
            rows = [
                {
                    "year": "2024",
                    "period": "2024-Q3",
                    "closure_status": "blocked_material_unexplained_adjustment",
                    "balancing_adjustment_eur": "335.43",
                    "cumulative_year_balance_eur": "335.43",
                    "year_balance_total_eur": "335.43",
                    "period_timing_signal": "target_above_model_needs_addition_or_catchup",
                    "year_timing_signal": "annual_positive_submitted_adjustment_remains",
                    "timing_source_refs": "scenario_q2_catchup",
                    "timing_source_count": "1",
                    "next_local_action": "Review timing source refs.",
                }
            ]

            write_timing_audit_markdown(path, rows)

            self.assertIn("Annual material residual years", path.read_text(encoding="utf-8"))

    def _write_closure(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=QUARTER_CLOSURE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_sources(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["period", "row_ref", "source_status", "source_document", "finding", "impact"])
            writer.writeheader()
            writer.writerows(rows)

    def _closure_row(self, period: str, balance: str, status: str) -> dict[str, str]:
        row = {field: "" for field in QUARTER_CLOSURE_FIELDS}
        row.update(
            {
                "period": period,
                "closure_status": status,
                "target_casilla_02_delta": "100.00",
                "pre_plug_model_minus_target_eur": str(-float(balance)),
                "balancing_adjustment_eur": balance,
                "balancing_adjustment_pct_of_target": "1.00",
                "balancing_adjustment_materiality": "material" if "blocked" in status else "minor",
                "candidate_amortization_delta": "0.00",
                "excluded_asset_direct_eur": "0.00",
                "excluded_nearest_subset_eur": "0.00",
                "has_material_plug": "yes" if "blocked" in status else "no",
                "has_asset_decision": "no",
                "has_nearest_exclusion": "no",
                "has_minor_residual": "yes",
            }
        )
        return row

    def _source_row(self, period: str, row_ref: str, finding: str) -> dict[str, str]:
        return {
            "period": period,
            "row_ref": row_ref,
            "source_status": "unconfirmed_arithmetic_hypothesis",
            "source_document": "runs/example.md",
            "finding": finding,
            "impact": "",
        }


if __name__ == "__main__":
    unittest.main()
