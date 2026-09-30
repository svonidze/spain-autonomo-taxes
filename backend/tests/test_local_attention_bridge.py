from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.local_attention_bridge import build_local_attention_bridge
from autonomo_taxes.local_archive_audit import LOCAL_ARCHIVE_AUDIT_FIELDS
from autonomo_taxes.quarter_closure import QUARTER_CLOSURE_FIELDS


class LocalAttentionBridgeTests(unittest.TestCase):
    def test_bridge_classifies_amount_mismatch_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "archive.csv"
            closure = tmp_path / "closure.csv"
            self._write_archive(
                archive,
                [
                    self._archive_row(
                        period="2024-Q3",
                        status="amount_mismatch_potential_xolo_raw",
                        amount_eur="212.14",
                        deductible_eur="212.14",
                        matched_amounts="256.69",
                        document="amazon invoice 1.pdf",
                    )
                ],
            )
            self._write_closure(closure, [self._closure_row("2024-Q3", "335.43")])

            rows = build_local_attention_bridge(archive, closure)

        self.assertEqual(rows[0]["known_effect_eur"], "-44.55")
        self.assertEqual(rows[0]["gap_fit_signal"], "known_effect_moves_away_from_gap")
        self.assertEqual(rows[0]["priority"], "high")
        self.assertIn("local parsed amount", rows[0]["xolo_question"])

    def test_bridge_flags_ambiguous_duplicate_or_correction(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "archive.csv"
            closure = tmp_path / "closure.csv"
            self._write_archive(
                archive,
                [
                    self._archive_row(
                        period="2024-Q3",
                        status="ambiguous_xolo_raw_matches",
                        matched_numbers="SYNTH-DOCUMENT-016; SYNTH-DOCUMENT-016",
                        matched_amounts="250.00; 500.00",
                        document="Five Plus Games LLC Invoice - SYNTH-DOCUMENT-016, EUR.pdf",
                    )
                ],
            )
            self._write_closure(closure, [self._closure_row("2024-Q3", "335.43")])

            rows = build_local_attention_bridge(archive, closure)

        self.assertEqual(rows[0]["gap_fit_signal"], "duplicate_or_correction_treatment_can_change_gap")
        self.assertEqual(rows[0]["priority"], "high")
        self.assertIn("duplicate/corrected rows", rows[0]["xolo_question"])

    def test_cli_writes_bridge_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "archive.csv"
            closure = tmp_path / "closure.csv"
            out_csv = tmp_path / "bridge.csv"
            out_md = tmp_path / "bridge.md"
            self._write_archive(
                archive,
                [
                    self._archive_row(
                        period="2023-Q4",
                        status="local_manual_review_without_xolo_raw",
                        document="SYNTH-DOCUMENT-035.pdf",
                    )
                ],
            )
            self._write_closure(closure, [self._closure_row("2023-Q4", "18.25")])

            exit_code = main(
                [
                    "audit-local-attention",
                    "--local-archive-audit",
                    str(archive),
                    "--quarter-closure",
                    str(closure),
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
        self.assertIn("Modelo 130 Local Attention Bridge", markdown)
        self.assertIn("amount_unknown_needs_document_parse_or_visual_review", markdown)

    def _write_archive(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=LOCAL_ARCHIVE_AUDIT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _write_closure(self, path: Path, rows: list[dict[str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=QUARTER_CLOSURE_FIELDS)
            writer.writeheader()
            writer.writerows(rows)

    def _archive_row(
        self,
        *,
        period: str,
        status: str,
        document: str,
        amount_eur: str = "",
        deductible_eur: str = "",
        matched_numbers: str = "",
        matched_amounts: str = "",
    ) -> dict[str, str]:
        row = {field: "" for field in LOCAL_ARCHIVE_AUDIT_FIELDS}
        row.update(
            {
                "period": period,
                "status": status,
                "date": "2024-07-04",
                "local_document": document,
                "amount_eur": amount_eur,
                "deductible_eur": deductible_eur,
                "matched_xolo_numbers": matched_numbers,
                "matched_xolo_amounts": matched_amounts,
                "notes": "fixture",
            }
        )
        return row

    def _closure_row(self, period: str, balance: str) -> dict[str, str]:
        row = {field: "" for field in QUARTER_CLOSURE_FIELDS}
        row.update(
            {
                "period": period,
                "closure_status": "blocked_material_unexplained_adjustment",
                "target_casilla_02_delta": "100.00",
                "pre_plug_model_minus_target_eur": str(-float(balance)),
                "balancing_adjustment_eur": balance,
                "balancing_adjustment_pct_of_target": "1.00",
                "balancing_adjustment_materiality": "material",
            }
        )
        return row


if __name__ == "__main__":
    unittest.main()
