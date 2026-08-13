from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.quarter_closure import QUARTER_CLOSURE_FIELDS
from autonomo_taxes.root_cause_narrowing import build_root_cause_narrowing


ANNUAL_FIELDS = [
    "period",
    "annual_constraint_source",
    "annual_constrained_amortization_delta",
    "amortization_delta_shift",
    "annual_constrained_model_minus_target_delta",
]

M303_FIELDS = ["period", "crosscheck_status"]
ANNUAL_CATEGORY_FIELDS = [
    "year",
    "status",
    "professional_gross_diff",
    "professional_base_diff",
    "category_signal",
]


class RootCauseNarrowingTests(unittest.TestCase):
    def test_narrows_eliminated_and_remaining_causes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            closure = tmp_path / "closure.csv"
            annual = tmp_path / "annual.csv"
            annual_categories = tmp_path / "annual_categories.csv"
            modelo303 = tmp_path / "modelo303.csv"
            self._write_closure(
                closure,
                [
                    self._closure_row("2023-Q2", "blocked_material_unexplained_adjustment", has_asset="yes"),
                    self._closure_row("2024-Q4", "pending_asset_schedule_confirmation", has_asset="yes"),
                    self._closure_row("2026-Q2", "pending_asset_schedule_confirmation", has_asset="yes"),
                ],
            )
            self._write_annual(
                annual,
                [
                    self._annual_row("2023-Q2", "m100_0208_filed_visual_extract", "0.00", "0.00", "-21.48"),
                    self._annual_row("2024-Q4", "m100_0208_filed_text_extract", "54.44", "-0.88", "649.13"),
                    self._annual_row("2026-Q2", "no_m100_0208_available_unconstrained", "235.29", "0.00", "188.36"),
                ],
            )
            self._write_annual_categories(
                annual_categories,
                [
                    self._annual_category_row(
                        "2023",
                        "-171.51",
                        "-0.57",
                        "annual return appears to direct-expense/reclassify software or hardware instead of amortizing it",
                    ),
                    self._annual_category_row(
                        "2024",
                        "-171.58",
                        "58.06",
                        "M100 professional services exceed raw VAT-base rows; confirm category reclassification or catch-up",
                    ),
                ],
            )
            self._write_modelo303(modelo303, [self._m303_row("2023-Q2", "matched"), self._m303_row("2024-Q4", "matched")])

            rows = build_root_cause_narrowing(closure, annual, modelo303, annual_categories)

        by_period = {row["period"]: row for row in rows}
        self.assertIn("annual professional gross-gap row-exclusion inference", by_period["2023-Q2"]["eliminated_causes"])
        self.assertIn("annual asset direct-expense or reclassification treatment", by_period["2023-Q2"]["remaining_causes"])
        self.assertIn("asset/direct-expense treatment confirmation", by_period["2023-Q2"]["remaining_causes"])
        self.assertIn("missing VAT-bearing EUR expense rows", by_period["2024-Q4"]["eliminated_causes"])
        self.assertIn("material annual amortization total mismatch", by_period["2024-Q4"]["eliminated_causes"])
        self.assertIn("excluded/netted rows", by_period["2024-Q4"]["remaining_causes"])
        self.assertIn("annual professional category base difference", by_period["2024-Q4"]["remaining_causes"])
        self.assertIn("future Modelo 100 annual amortization line 0208", by_period["2026-Q2"]["next_evidence"])

    def test_cli_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            closure = tmp_path / "closure.csv"
            annual = tmp_path / "annual.csv"
            annual_categories = tmp_path / "annual_categories.csv"
            modelo303 = tmp_path / "modelo303.csv"
            out_csv = tmp_path / "narrow.csv"
            out_md = tmp_path / "narrow.md"
            self._write_closure(closure, [self._closure_row("2024-Q4", "pending_asset_schedule_confirmation")])
            self._write_annual(annual, [self._annual_row("2024-Q4", "m100_0208_filed_text_extract", "0.00", "0.00", "0.10")])
            self._write_annual_categories(annual_categories, [self._annual_category_row("2024", "-171.58", "58.06")])
            self._write_modelo303(modelo303, [self._m303_row("2024-Q4", "matched")])

            exit_code = main(
                [
                    "audit-root-cause-narrowing",
                    "--quarter-closure",
                    str(closure),
                    "--annual-constrained-assets",
                    str(annual),
                    "--annual-categories",
                    str(annual_categories),
                    "--modelo303-vat-crosscheck",
                    str(modelo303),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Modelo 130 Root-Cause Narrowing", markdown)
            self.assertIn("Annual Modelo 100 category VAT-base comparison", markdown)
            self.assertIn("Annual professional gross gaps are VAT-shaped rather than row-exclusion evidence for `0` quarters", markdown)
            self.assertIn("Annual professional VAT-base differences remain material for `1` quarters", markdown)

    def _write_closure(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=QUARTER_CLOSURE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_annual(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=ANNUAL_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_modelo303(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=M303_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_annual_categories(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=ANNUAL_CATEGORY_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _closure_row(self, period: str, status: str, *, has_asset: str = "no") -> dict[str, str]:
        row = {field: "" for field in QUARTER_CLOSURE_FIELDS}
        row.update(
            {
                "period": period,
                "closure_status": status,
                "target_casilla_02_delta": "100.00",
                "pre_plug_model_minus_target_eur": "25.00",
                "balancing_adjustment_eur": "25.00",
                "has_asset_decision": has_asset,
                "has_nearest_exclusion": "no",
            }
        )
        return row

    def _annual_row(self, period: str, source: str, amortization: str, shift: str, residual: str) -> dict[str, str]:
        return {
            "period": period,
            "annual_constraint_source": source,
            "annual_constrained_amortization_delta": amortization,
            "amortization_delta_shift": shift,
            "annual_constrained_model_minus_target_delta": residual,
        }

    def _m303_row(self, period: str, status: str) -> dict[str, str]:
        return {"period": period, "crosscheck_status": status}

    def _annual_category_row(
        self,
        year: str,
        professional_gross_diff: str,
        professional_base_diff: str,
        signal: str = "annual categories are near raw category totals",
    ) -> dict[str, str]:
        return {
            "year": year,
            "status": "filed",
            "professional_gross_diff": professional_gross_diff,
            "professional_base_diff": professional_base_diff,
            "category_signal": signal,
        }


if __name__ == "__main__":
    unittest.main()
