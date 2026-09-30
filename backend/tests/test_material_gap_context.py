from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.material_gap_context import build_material_gap_context
from autonomo_taxes.material_gap_drilldown import MATERIAL_GAP_DRILLDOWN_FIELDS
from autonomo_taxes.quarter_acceptance import QUARTER_ACCEPTANCE_FIELDS
from autonomo_taxes.quarter_balance_bridge import QUARTER_BALANCE_BRIDGE_FIELDS


class MaterialGapContextTests(unittest.TestCase):
    def test_q2_gap_matches_excluded_asset_candidate_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_inputs(Path(tmp))

            rows = build_material_gap_context(
                paths["acceptance"],
                paths["bridge"],
                paths["xolo_ledger"],
                paths["material"],
            )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["period"], "2023-Q2")
        self.assertEqual(row["xolo_document_quarter_non_asset_gross_eur"], "94.06")
        self.assertEqual(row["xolo_document_quarter_asset_candidate_gross_eur"], "107.10")
        self.assertEqual(row["target_minus_xolo_non_asset_eur"], "21.48")
        self.assertEqual(row["bridge_raw_vs_xolo_non_asset_delta"], "0.00")
        self.assertEqual(row["context_signal"], "gap_matches_excluded_asset_candidate_context")
        self.assertIn("1751445", row["gap_sized_asset_candidate_rows"])
        self.assertEqual(row["gap_sized_asset_candidate_fit"], "1751445: gap 21.48 is 20.06% of 107.10")
        self.assertIn("21,48", row["next_xolo_question"])

    def test_non_eur_row_without_gross_eur_is_not_treated_as_eur(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_inputs(Path(tmp), github_gross_eur="")

            rows = build_material_gap_context(
                paths["acceptance"],
                paths["bridge"],
                paths["xolo_ledger"],
                paths["material"],
            )

        row = rows[0]
        self.assertEqual(row["xolo_document_quarter_asset_candidate_gross_eur"], "0.00")
        self.assertEqual(row["xolo_document_quarter_unconverted_row_count"], "1")
        self.assertIn("1751445", row["xolo_document_quarter_unconverted_rows"])
        self.assertIn("USD", row["xolo_document_quarter_unconverted_rows"])
        self.assertEqual(row["gap_sized_asset_candidate_rows"], "")
        self.assertEqual(row["context_signal"], "xolo_raw_has_unconverted_currency_rows_requires_fx")
        self.assertIn("booked EUR", row["next_xolo_question"])

    def test_non_eur_row_with_untrusted_gross_eur_is_not_treated_as_eur(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_inputs(
                Path(tmp),
                github_gross_eur="115.54",
                github_detail_confidence="currency_mismatch_review",
            )

            rows = build_material_gap_context(
                paths["acceptance"],
                paths["bridge"],
                paths["xolo_ledger"],
                paths["material"],
            )

        row = rows[0]
        self.assertEqual(row["xolo_document_quarter_asset_candidate_gross_eur"], "0.00")
        self.assertEqual(row["xolo_document_quarter_unconverted_row_count"], "1")
        self.assertEqual(row["context_signal"], "xolo_raw_has_unconverted_currency_rows_requires_fx")

    def test_target_match_to_asset_candidate_is_called_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_inputs(Path(tmp), github_gross_eur="115.54")

            rows = build_material_gap_context(
                paths["acceptance"],
                paths["bridge"],
                paths["xolo_ledger"],
                paths["material"],
            )

        row = rows[0]
        self.assertEqual(row["target_minus_xolo_asset_candidate_eur"], "0.00")
        self.assertEqual(row["context_signal"], "target_matches_asset_candidate_if_non_assets_excluded")
        self.assertIn("matches the asset/category rows", row["next_xolo_question"])
        self.assertIn("Social security", row["next_xolo_question"])

    def test_cli_writes_context_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _write_inputs(root)
            out_csv = root / "context.csv"
            out_md = root / "context.md"

            exit_code = main(
                [
                    "audit-material-gap-context",
                    "--quarter-acceptance",
                    str(paths["acceptance"]),
                    "--quarter-balance-bridge",
                    str(paths["bridge"]),
                    "--xolo-expense-ledger",
                    str(paths["xolo_ledger"]),
                    "--material-gap-drilldown",
                    str(paths["material"]),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertIn("gap_matches_excluded_asset_candidate_context", out_csv.read_text(encoding="utf-8"))
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Modelo 130 Material Gap Context", markdown)
            self.assertIn("Synthetic Party 001", markdown)


def _write_inputs(
    root: Path,
    github_gross_eur: str = "107.10",
    github_detail_confidence: str = "",
) -> dict[str, Path]:
    acceptance = root / "acceptance.csv"
    bridge = root / "bridge.csv"
    xolo_ledger = root / "xolo.csv"
    material = root / "material.csv"

    _write_dicts(
        acceptance,
        QUARTER_ACCEPTANCE_FIELDS,
        [
            {
                "period": "2023-Q2",
                "review_order": "001",
                "acceptance_status": "not_closed_material_gap_requires_xolo_register",
                "priority": "P0",
                "target_casilla_02_delta": "115.54",
                "annual_constrained_balance_to_target": "21.48",
                "balance_signal": "submitted_target_above_local_model",
                "closure_status": "blocked_material_unexplained_adjustment",
                "material_gap_focus": "yes",
                "material_hypotheses": "",
                "required_evidence": "submitted register",
                "next_action": "question",
                "packet_path": "packet",
            }
        ],
    )
    _write_dicts(
        bridge,
        QUARTER_BALANCE_BRIDGE_FIELDS,
        [
            {
                "period": "2023-Q2",
                "target_casilla_02_delta": "115.54",
                "raw_non_asset_delta": "94.06",
                "candidate_amortization_delta": "0.00",
                "nearest_excluded_or_netted_eur": "0.00",
                "candidate_model_after_local_adjustments": "94.06",
                "candidate_balance_to_target": "21.48",
                "annual_constraint_source": "fixture",
                "annual_constrained_amortization_delta": "0.00",
                "annual_constrained_model_after_local_adjustments": "94.06",
                "annual_constrained_balance_to_target": "21.48",
                "amortization_shift_eur": "0.00",
                "balance_signal": "submitted_target_above_local_model",
                "closure_status": "blocked_material_unexplained_adjustment",
                "required_xolo_evidence": "submitted register",
                "equation": "94.06 + 0.00 - 0.00 + 21.48 = 115.54",
            }
        ],
    )
    _write_dicts(
        xolo_ledger,
        [
            "xolo_id",
            "recipient",
            "type",
            "number",
            "date",
            "amount_original",
            "currency",
            "gross_eur",
            "detail_confidence",
            "notes",
        ],
        [
            _xolo_row("1890560", "Synthetic Party 011", "Social security & prof. fees", "177918930315", "2023-06-30", "85.71", "EUR", "85.71"),
            _xolo_row("1771412", "Synthetic Party 004", "Professional expenses", "2023-06-15", "2023-06-30", "8.35", "EUR", "8.35"),
            _xolo_row(
                "1751445",
                "Synthetic Party 001",
                "Computer hardware & software",
                "2023-06-30",
                "2023-06-30",
                "125.00",
                "USD",
                github_gross_eur,
                github_detail_confidence,
            ),
        ],
    )
    _write_dicts(
        material,
        MATERIAL_GAP_DRILLDOWN_FIELDS,
        [
            {
                "period": "2023-Q2",
                "hypothesis_id": "unexplained_register_adjustment",
                "hypothesis_status": "unresolved_required_evidence",
                "component": "Submitted-register adjustment",
                "amount_eur": "21.48",
                "effect_closes_gap_eur": "21.48",
                "counts_in_best_bridge": "yes",
                "hypothesis_total_eur": "21.48",
                "candidate_balance_to_target": "21.48",
                "residual_to_candidate_balance_eur": "0.00",
                "annual_constrained_balance_to_target": "21.48",
                "residual_to_annual_balance_eur": "0.00",
                "fit_signal": "near_exact_pending_confirmation",
                "source_ref": "source",
                "evidence_status": "missing",
                "notes": "No local row",
                "xolo_question": "Which row?",
            }
        ],
    )
    return {"acceptance": acceptance, "bridge": bridge, "xolo_ledger": xolo_ledger, "material": material}


def _xolo_row(
    xolo_id: str,
    recipient: str,
    category: str,
    number: str,
    date: str,
    original: str,
    currency: str,
    gross_eur: str,
    detail_confidence: str = "",
) -> dict[str, str]:
    return {
        "xolo_id": xolo_id,
        "recipient": recipient,
        "type": category,
        "number": number,
        "date": date,
        "amount_original": original,
        "currency": currency,
        "gross_eur": gross_eur,
        "detail_confidence": detail_confidence,
        "notes": "",
    }


def _write_dicts(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
