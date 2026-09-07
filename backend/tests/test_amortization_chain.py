from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.amortization_chain import build_amortization_chain
from autonomo_taxes.cli import main
from autonomo_taxes.quarter_closure import QUARTER_CLOSURE_FIELDS


class AmortizationChainTests(unittest.TestCase):
    def test_builds_chain_signals_and_year_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_inputs(Path(tmp))

            rows, years = build_amortization_chain(
                paths["candidate"],
                paths["annual"],
                paths["closure"],
                paths["assets"],
                paths["modelo100"],
            )

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(
            by_period["2023-Q2"]["amortization_chain_signal"],
            "m100_zero_asset_like_row_reclassification_question",
        )
        self.assertEqual(
            by_period["2024-Q1"]["amortization_chain_signal"],
            "annual_amortization_not_main_gap",
        )
        self.assertEqual(
            by_period["2026-Q1"]["amortization_chain_signal"],
            "current_year_unconstrained_asset_schedule_required",
        )
        by_year = {row["year"]: row for row in years}
        self.assertEqual(by_year["2023"]["year_signal"], "annual_zero_amortization_consistent_with_candidate_filter")
        self.assertEqual(by_year["2024"]["year_signal"], "annual_constraint_matches_modelo100_0208")
        self.assertEqual(by_year["2026"]["year_signal"], "no_modelo100_available")

    def test_cli_writes_chain_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _write_inputs(root)
            out_csv = root / "chain.csv"
            out_years = root / "years.csv"
            out_md = root / "chain.md"

            exit_code = main(
                [
                    "audit-amortization-chain",
                    "--candidate-quarter-reconciliation",
                    str(paths["candidate"]),
                    "--annual-constrained-assets",
                    str(paths["annual"]),
                    "--quarter-closure",
                    str(paths["closure"]),
                    "--asset-candidates",
                    str(paths["assets"]),
                    "--modelo100-summary",
                    str(paths["modelo100"]),
                    "--out-csv",
                    str(out_csv),
                    "--out-years-csv",
                    str(out_years),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertIn("current_year_unconstrained_asset_schedule_required", out_csv.read_text(encoding="utf-8"))
            self.assertIn("annual_constraint_matches_modelo100_0208", out_years.read_text(encoding="utf-8"))
            self.assertIn("Modelo 130 Amortization Chain Audit", out_md.read_text(encoding="utf-8"))


def _write_inputs(root: Path) -> dict[str, Path]:
    candidate = root / "candidate.csv"
    annual = root / "annual.csv"
    closure = root / "closure.csv"
    assets = root / "assets.csv"
    modelo100 = root / "modelo100.csv"
    _write_dicts(
        candidate,
        [
            "year",
            "quarter",
            "period",
            "scenario",
            "target_casilla_02_delta",
            "raw_non_asset_gross_delta",
            "candidate_amortization_delta",
            "candidate_model_delta",
            "candidate_model_minus_target_delta",
            "candidate_amortization_ytd",
            "ytd_residual_after_candidate",
            "nearest_excluded_subset_eur",
            "nearest_excluded_subset_error_eur",
            "nearest_excluded_subset_rows",
            "interpretation",
        ],
        [
            _candidate_row("2023", "2", "2023-Q2", "0.00", "0.00"),
            _candidate_row("2024", "1", "2024-Q1", "20.00", "20.00"),
            _candidate_row("2026", "1", "2026-Q1", "30.00", "30.00"),
        ],
    )
    _write_dicts(
        annual,
        [
            "year",
            "quarter",
            "period",
            "annual_constraint_source",
            "target_casilla_02_delta",
            "raw_non_asset_gross_delta",
            "candidate_amortization_delta",
            "annual_constrained_amortization_delta",
            "amortization_delta_shift",
            "candidate_model_minus_target_delta",
            "annual_constrained_model_delta",
            "annual_constrained_model_minus_target_delta",
            "interpretation",
        ],
        [
            _annual_row("2023", "2", "2023-Q2", "m100_0208_filed_visual_extract", "0.00", "0.00", "-21.48"),
            _annual_row("2024", "1", "2024-Q1", "m100_0208_filed_text_extract", "20.00", "-0.50", "100.00"),
            _annual_row("2026", "1", "2026-Q1", "no_m100_0208_available_unconstrained", "30.00", "0.00", "30.00"),
        ],
    )
    _write_dicts(
        closure,
        QUARTER_CLOSURE_FIELDS,
        [
            _closure_row("2023-Q2", asset_rows="2023-06-30 GitHub 116.86"),
            _closure_row("2024-Q1"),
            _closure_row("2026-Q1"),
        ],
    )
    _write_dicts(
        assets,
        [
            "date",
            "purchase_period",
            "recipient",
            "type",
            "number",
            "currency",
            "amount_original",
            "subtotal_amount",
            "gross_basis_eur",
            "base_basis_eur",
            "annual_rate",
            "basis_source",
        ],
        [
            {
                "date": "2023-06-30",
                "purchase_period": "2023-Q2",
                "recipient": "GitHub",
                "type": "Computer hardware & software",
                "number": "1751445",
                "currency": "USD",
                "amount_original": "125.00",
                "subtotal_amount": "125.00",
                "gross_basis_eur": "116.86",
                "base_basis_eur": "116.86",
                "annual_rate": "0.26",
                "basis_source": "fixture",
            }
        ],
    )
    _write_dicts(
        modelo100,
        [
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
        ],
        [
            _modelo100_row("2023", "filed_visual_extract", "0.00"),
            _modelo100_row("2024", "filed_text_extract", "20.00"),
        ],
    )
    return {
        "candidate": candidate,
        "annual": annual,
        "closure": closure,
        "assets": assets,
        "modelo100": modelo100,
    }


def _candidate_row(year: str, quarter: str, period: str, amortization: str, amortization_ytd: str) -> dict[str, str]:
    return {
        "year": year,
        "quarter": quarter,
        "period": period,
        "scenario": "fixture",
        "target_casilla_02_delta": "100.00",
        "raw_non_asset_gross_delta": "80.00",
        "candidate_amortization_delta": amortization,
        "candidate_model_delta": "100.00",
        "candidate_model_minus_target_delta": "0.00",
        "candidate_amortization_ytd": amortization_ytd,
        "ytd_residual_after_candidate": "0.00",
        "nearest_excluded_subset_eur": "0.00",
        "nearest_excluded_subset_error_eur": "0.00",
        "nearest_excluded_subset_rows": "",
        "interpretation": "fixture",
    }


def _annual_row(
    year: str,
    quarter: str,
    period: str,
    source: str,
    constrained_amortization: str,
    shift: str,
    residual: str,
) -> dict[str, str]:
    return {
        "year": year,
        "quarter": quarter,
        "period": period,
        "annual_constraint_source": source,
        "target_casilla_02_delta": "100.00",
        "raw_non_asset_gross_delta": "80.00",
        "candidate_amortization_delta": constrained_amortization,
        "annual_constrained_amortization_delta": constrained_amortization,
        "amortization_delta_shift": shift,
        "candidate_model_minus_target_delta": residual,
        "annual_constrained_model_delta": "100.00",
        "annual_constrained_model_minus_target_delta": residual,
        "interpretation": "fixture",
    }


def _closure_row(period: str, *, asset_rows: str = "") -> dict[str, str]:
    row = {field: "" for field in QUARTER_CLOSURE_FIELDS}
    row.update(
        {
            "period": period,
            "closure_status": "pending_asset_schedule_confirmation",
            "target_casilla_02_delta": "100.00",
            "has_asset_decision": "yes" if asset_rows else "no",
            "asset_rows": asset_rows,
            "required_xolo_evidence": "submitted register; asset schedule",
        }
    )
    return row


def _modelo100_row(year: str, status: str, amortization: str) -> dict[str, str]:
    return {
        "year": year,
        "source_report": f"M100 {year}.pdf",
        "status": status,
        "income_0171": "0.00",
        "total_income_0180": "0.00",
        "social_security_0186": "0.00",
        "professional_services_0199": "0.00",
        "other_external_services_0202": "0.00",
        "amortization_0208": amortization,
        "deductible_expenses_0218": "0.00",
        "difficult_expenses_0222": "0.00",
        "total_deductible_0223": "0.00",
        "notes": "fixture",
    }


def _write_dicts(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
