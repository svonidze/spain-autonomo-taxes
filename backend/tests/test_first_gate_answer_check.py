from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.first_gate_answer_check import build_first_gate_answer_check
from autonomo_taxes.register_answer_intake import REGISTER_ANSWER_INTAKE_FIELDS


class FirstGateAnswerCheckTests(unittest.TestCase):
    def test_current_open_answers_keep_first_gate_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first_gate = tmp_path / "first_gate.csv"
            answer_intake = tmp_path / "answer_intake.csv"
            _write_first_gate(first_gate)
            _write_answer_intake(answer_intake, filled=False)

            rows = build_first_gate_answer_check(first_gate, answer_intake)

        by_check = {row["check"]: row for row in rows}
        self.assertEqual(by_check["p0_answer_readiness"]["status"], "none_ready")
        self.assertEqual(by_check["casilla02_tieout"]["status"], "missing")
        self.assertEqual(by_check["residual_explanation"]["status"], "missing")
        self.assertEqual(by_check["asset_treatment"]["status"], "missing")
        self.assertEqual(by_check["closure_verdict"]["status"], "blocked_no_xolo_answer")

    def test_structured_xolo_answer_makes_first_gate_ready_for_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first_gate = tmp_path / "first_gate.csv"
            answer_intake = tmp_path / "answer_intake.csv"
            _write_first_gate(first_gate)
            _write_answer_intake(answer_intake, filled=True)

            rows = build_first_gate_answer_check(first_gate, answer_intake)

        by_check = {row["check"]: row for row in rows}
        self.assertEqual(by_check["p0_answer_readiness"]["status"], "all_ready")
        self.assertEqual(by_check["casilla02_tieout"]["status"], "matches_target")
        self.assertEqual(by_check["residual_explanation"]["status"], "matches_residual")
        self.assertEqual(by_check["asset_treatment"]["status"], "ready")
        self.assertEqual(by_check["closure_verdict"]["status"], "ready_for_rebuild")

    def test_non_zero_residual_requires_adjustment_answer_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first_gate = tmp_path / "first_gate.csv"
            answer_intake = tmp_path / "answer_intake.csv"
            _write_first_gate(first_gate)
            asset_only = _asset_row(filled=True)
            asset_only["casilla02_ytd"] = "115.54"
            _write_csv(answer_intake, REGISTER_ANSWER_INTAKE_FIELDS, [asset_only])

            rows = build_first_gate_answer_check(first_gate, answer_intake)

        by_check = {row["check"]: row for row in rows}
        self.assertEqual(by_check["p0_answer_readiness"]["status"], "all_ready")
        self.assertEqual(by_check["casilla02_tieout"]["status"], "matches_target")
        self.assertEqual(by_check["residual_explanation"]["status"], "missing")
        self.assertEqual(by_check["closure_verdict"]["status"], "structured_but_unresolved")

    def test_conflicting_ready_tieout_values_block_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first_gate = tmp_path / "first_gate.csv"
            answer_intake = tmp_path / "answer_intake.csv"
            _write_first_gate(first_gate)
            adjustment = _adjustment_row(filled=True)
            asset = _asset_row(filled=True)
            asset["casilla02_ytd"] = "99.99"
            _write_csv(answer_intake, REGISTER_ANSWER_INTAKE_FIELDS, [adjustment, asset])

            rows = build_first_gate_answer_check(first_gate, answer_intake)

        by_check = {row["check"]: row for row in rows}
        self.assertEqual(by_check["casilla02_tieout"]["status"], "conflict")
        self.assertEqual(by_check["residual_explanation"]["status"], "matches_residual")
        self.assertEqual(by_check["closure_verdict"]["status"], "structured_but_unresolved")

    def test_open_tieout_value_does_not_count_as_source_book_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first_gate = tmp_path / "first_gate.csv"
            answer_intake = tmp_path / "answer_intake.csv"
            _write_first_gate(first_gate)
            open_adjustment = _adjustment_row(filled=False)
            open_adjustment["casilla02_ytd"] = "115.54"
            _write_csv(answer_intake, REGISTER_ANSWER_INTAKE_FIELDS, [open_adjustment])

            rows = build_first_gate_answer_check(first_gate, answer_intake)

        by_check = {row["check"]: row for row in rows}
        self.assertEqual(by_check["p0_answer_readiness"]["status"], "none_ready")
        self.assertEqual(by_check["casilla02_tieout"]["status"], "missing")
        self.assertEqual(by_check["closure_verdict"]["status"], "blocked_no_xolo_answer")

    def test_missing_raw_context_blocks_residual_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first_gate = tmp_path / "first_gate.csv"
            answer_intake = tmp_path / "answer_intake.csv"
            _write_first_gate(first_gate, include_context=False)
            _write_answer_intake(answer_intake, filled=True)

            rows = build_first_gate_answer_check(first_gate, answer_intake)

        by_check = {row["check"]: row for row in rows}
        self.assertEqual(by_check["residual_explanation"]["status"], "missing_context")
        self.assertEqual(by_check["closure_verdict"]["status"], "structured_but_unresolved")

    def test_cli_writes_first_gate_answer_check_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            first_gate = tmp_path / "first_gate.csv"
            answer_intake = tmp_path / "answer_intake.csv"
            out_csv = tmp_path / "answer_check.csv"
            out_md = tmp_path / "answer_check.md"
            _write_first_gate(first_gate)
            _write_answer_intake(answer_intake, filled=False)

            exit_code = main(
                [
                    "audit-first-gate-answer-check",
                    "--first-gate",
                    str(first_gate),
                    "--answer-intake",
                    str(answer_intake),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Modelo 130 First Gate Answer Check", markdown)
            self.assertIn("blocked_no_xolo_answer", markdown)


def _write_first_gate(path: Path, *, include_context: bool = True) -> None:
    fieldnames = [
        "section",
        "period",
        "key",
        "status",
        "amount_eur",
        "fit_signal",
        "evidence_status",
        "finding",
        "source_ref",
        "xolo_question",
    ]
    rows = [
        {
            "section": "gate",
            "period": "2023-Q2",
            "key": "first_asset_treatment_gate",
            "status": "blocked_material_unexplained_adjustment",
            "amount_eur": "115.54",
        },
    ]
    if include_context:
        rows.append(
            {
                "section": "raw_context",
                "period": "2023-Q2",
                "amount_eur": "94.06",
                "fit_signal": "21.48",
            }
        )
    _write_csv(path, fieldnames, rows)


def _write_answer_intake(path: Path, *, filled: bool) -> None:
    rows = [_adjustment_row(filled), _asset_row(filled)]
    _write_csv(path, REGISTER_ANSWER_INTAKE_FIELDS, rows)


def _adjustment_row(filled: bool) -> dict[str, str]:
    row = _base_answer_row(
        queue_rank="1",
        classification="missing_catch_up_or_reclassification",
        amount_eur="21.48",
        application_target="register_review.new_adjustment_row",
    )
    if filled:
        row.update(
            {
                "answer_status": "confirmed",
                "confirmed_included": "yes",
                "confirmed_irpf_deductible_eur": "21.48",
                "confirmed_basis": "source_book_adjustment",
                "confirmed_source": "Xolo source-book export",
                "source_book_type": "compras_gastos",
                "source_book_line_id": "CG-2023-2T-99",
                "casilla02_ytd": "115.54",
            }
        )
    return row


def _asset_row(filled: bool) -> dict[str, str]:
    row = _base_answer_row(
        queue_rank="2",
        classification="asset_amortization_candidate",
        amount_eur="115.54",
        application_target="register_review.confirmed_amortization_eur",
    )
    row.update(
        {
            "row_label": "2023-06-30 1751445 2023-06-30 Synthetic Party 001",
            "xolo_id": "1751445",
        }
    )
    if filled:
        row.update(
            {
                "answer_status": "confirmed",
                "confirmed_included": "no",
                "confirmed_basis": "excluded_from_2T_source_books",
                "confirmed_source": "Xolo source-book export",
                "source_book_type": "bienes_inversion",
                "source_book_line_id": "BI-2023-GITHUB",
                "reason_code": "excluded",
            }
        )
    return row


def _base_answer_row(
    *,
    queue_rank: str,
    classification: str,
    amount_eur: str,
    application_target: str,
) -> dict[str, str]:
    row = {field: "" for field in REGISTER_ANSWER_INTAKE_FIELDS}
    row.update(
        {
            "queue_rank": queue_rank,
            "period": "2023-Q2",
            "audit_priority": "P0",
            "decision_priority": "high",
            "classification": classification,
            "amount_eur": amount_eur,
            "question": "fixture",
            "required_evidence": "source-book export",
            "answer_status": "open",
            "application_target": application_target,
        }
    )
    return row


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


if __name__ == "__main__":
    unittest.main()
