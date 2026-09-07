from pathlib import Path
import csv
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.quarter_acceptance import QUARTER_ACCEPTANCE_FIELDS
from autonomo_taxes.register_blocking_queue import build_register_blocking_queue
from autonomo_taxes.register_reconcile import REGISTER_RECONCILIATION_FIELDS


class RegisterBlockingQueueTests(unittest.TestCase):
    def test_builds_priority_sorted_external_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _write_inputs(Path(tmp))

            rows = build_register_blocking_queue(
                paths["acceptance"],
                paths["reconciliation"],
                paths["decisions"],
                paths["amortization"],
            )

        self.assertEqual([row["period"] for row in rows], ["2023-Q2", "2026-Q2"])
        self.assertEqual(rows[0]["audit_priority"], "P0")
        self.assertIn("Annual Modelo 100 line 0208 is zero", rows[0]["question"])
        self.assertIn("current-year asset schedule", rows[1]["question"])
        self.assertEqual(rows[0]["queue_rank"], "1")
        self.assertEqual(rows[1]["queue_rank"], "2")

    def test_cli_writes_queue_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = _write_inputs(root)
            out_csv = root / "queue.csv"
            out_md = root / "queue.md"

            exit_code = main(
                [
                    "audit-register-blocking-queue",
                    "--quarter-acceptance",
                    str(paths["acceptance"]),
                    "--register-reconciliation",
                    str(paths["reconciliation"]),
                    "--row-decisions",
                    str(paths["decisions"]),
                    "--amortization-chain",
                    str(paths["amortization"]),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertIn("m100_zero_asset_like_row_reclassification_question", out_csv.read_text(encoding="utf-8"))
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Modelo 130 Register Blocking Queue", markdown)
            self.assertIn("First Questions", markdown)


def _write_inputs(root: Path) -> dict[str, Path]:
    acceptance = root / "acceptance.csv"
    reconciliation = root / "reconciliation.csv"
    decisions = root / "decisions.csv"
    amortization = root / "amortization.csv"
    _write_dicts(
        acceptance,
        QUARTER_ACCEPTANCE_FIELDS,
        [
            _acceptance_row("2023-Q2", "P0"),
            _acceptance_row("2026-Q2", "P2"),
        ],
    )
    _write_dicts(
        reconciliation,
        REGISTER_RECONCILIATION_FIELDS,
        [
            _reconciliation_row("2023-Q2", "2", "6"),
            _reconciliation_row("2026-Q2", "2", "8"),
        ],
    )
    _write_dicts(
        decisions,
        [
            "period",
            "priority",
            "decision_kind",
            "classification",
            "date",
            "xolo_id",
            "number",
            "recipient",
            "amount_eur",
            "candidate_model_minus_target_delta",
            "nearest_subset_total_eur",
            "nearest_subset_error_eur",
            "root_cause_remaining",
            "root_cause_context",
            "question",
            "xolo_url",
        ],
        [
            _decision_row(
                "2026-Q2",
                "high",
                "confirm asset amortization schedule treatment",
                "asset_amortization_candidate",
                "2026-04-08",
                "3019975",
                "SYNTH-DOCUMENT-031",
                "Apple Retail Spain",
                "1994.00",
            ),
            _decision_row(
                "2023-Q2",
                "medium",
                "confirm direct expense versus capitalized asset",
                "asset_amortization_candidate",
                "2023-06-30",
                "1751445",
                "1751445",
                "GitHub",
                "116.86",
            ),
        ],
    )
    _write_dicts(
        amortization,
        [
            "period",
            "amortization_chain_signal",
        ],
        [
            {
                "period": "2023-Q2",
                "amortization_chain_signal": "m100_zero_asset_like_row_reclassification_question",
            },
            {
                "period": "2026-Q2",
                "amortization_chain_signal": "current_year_unconstrained_asset_schedule_required",
            },
        ],
    )
    return {
        "acceptance": acceptance,
        "reconciliation": reconciliation,
        "decisions": decisions,
        "amortization": amortization,
    }


def _acceptance_row(period: str, priority: str) -> dict[str, str]:
    row = {field: "" for field in QUARTER_ACCEPTANCE_FIELDS}
    row.update(
        {
            "period": period,
            "priority": priority,
            "acceptance_status": "not_closed_fixture",
            "required_evidence": "submitted register; asset schedule",
        }
    )
    return row


def _reconciliation_row(period: str, open_high: str, open_total: str) -> dict[str, str]:
    row = {field: "" for field in REGISTER_RECONCILIATION_FIELDS}
    row.update(
        {
            "period": period,
            "status": "partial_review_open_rows",
            "open_high": open_high,
            "open_total": open_total,
        }
    )
    return row


def _decision_row(
    period: str,
    priority: str,
    decision_kind: str,
    classification: str,
    date: str,
    xolo_id: str,
    number: str,
    recipient: str,
    amount: str,
) -> dict[str, str]:
    return {
        "period": period,
        "priority": priority,
        "decision_kind": decision_kind,
        "classification": classification,
        "date": date,
        "xolo_id": xolo_id,
        "number": number,
        "recipient": recipient,
        "amount_eur": amount,
        "candidate_model_minus_target_delta": "0.00",
        "nearest_subset_total_eur": "0.00",
        "nearest_subset_error_eur": "0.00",
        "root_cause_remaining": "",
        "root_cause_context": "",
        "question": f"Question for {period}",
        "xolo_url": f"https://example.test/{xolo_id}",
    }


def _write_dicts(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
