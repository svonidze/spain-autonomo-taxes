from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.source_book_waitlist import build_source_book_waitlist


class SourceBookWaitlistTests(unittest.TestCase):
    def test_waitlist_maps_year_and_quarter_deliverables_to_open_periods(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_inputs(root)

            rows = build_source_book_waitlist(
                quarter_acceptance_csv=root / "acceptance.csv",
                source_book_response_check_csv=root / "response.csv",
                source_book_reconciliation_csv=root / "reconciliation.csv",
                source_book_availability_csv=root / "availability.csv",
                response_root=root / "xolo-source-books",
            )

        by_period = {row["period"]: row for row in rows}
        self.assertEqual(len(rows), 2)
        self.assertIn("ingresos_book:2023", by_period["2023-Q2"]["missing_deliverables"])
        self.assertIn("gastos_book:2023", by_period["2023-Q2"]["missing_deliverables"])
        self.assertIn("provisiones_suplidos_book:2023", by_period["2023-Q2"]["missing_deliverables"])
        self.assertIn(
            "bienes_inversion_book:2023",
            by_period["2023-Q2"]["matched_deliverables"],
        )
        self.assertEqual(by_period["2023-Q2"]["source_book_reconciliation_status"], "no_imported_source_book_rows")
        self.assertEqual(by_period["2023-Q2"]["package_status"], "ready_to_send_package")
        self.assertIn("gastos_book:2023", by_period["2023-Q3"]["missing_deliverables"])
        self.assertNotIn("modelo130_source_book_tieout", by_period["2023-Q3"]["missing_deliverables"])
        self.assertIn("bienes_inversion_book:2023", by_period["2023-Q3"]["matched_deliverables"])

    def test_cli_writes_waitlist_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_inputs(root)
            out_csv = root / "waitlist.csv"
            out_md = root / "waitlist.md"

            exit_code = main(
                [
                    "audit-source-book-waitlist",
                    "--quarter-acceptance",
                    str(root / "acceptance.csv"),
                    "--source-book-response-check",
                    str(root / "response.csv"),
                    "--source-book-reconciliation",
                    str(root / "reconciliation.csv"),
                    "--source-book-availability",
                    str(root / "availability.csv"),
                    "--response-root",
                    str(root / "xolo-source-books"),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            rows = _read_dicts(out_csv)
            markdown = out_md.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(rows), 2)
        self.assertIn("Do not close an open quarter by guessing Apple amortization", markdown)
        self.assertIn("2023-Q2", markdown)


def _write_inputs(root: Path) -> None:
    _write_dicts(
        root / "acceptance.csv",
        [
            "period",
            "review_order",
            "priority",
            "acceptance_status",
            "target_casilla_02_delta",
            "required_evidence",
            "next_action",
        ],
        [
            {
                "period": "2023-Q2",
                "review_order": "001",
                "priority": "P0",
                "acceptance_status": "not_closed_material_gap_requires_source_books",
                "target_casilla_02_delta": "115.54",
                "required_evidence": "source-book export; asset amortization schedule",
                "next_action": "Resolve material-gap questions.",
            },
            {
                "period": "2023-Q3",
                "review_order": "002",
                "priority": "P1",
                "acceptance_status": "not_closed_material_status_near_fit_requires_source_books",
                "target_casilla_02_delta": "262.01",
                "required_evidence": "source-book export; asset amortization schedule",
                "next_action": "Confirm near-fit treatment.",
            },
        ],
    )
    _write_dicts(
        root / "response.csv",
        ["check", "scope", "status", "matched_count", "matched_files", "required_for", "next_action"],
        [
            {
                "check": "ingresos_book",
                "scope": "2023",
                "status": "missing",
                "matched_count": "0",
                "matched_files": "",
                "required_for": "2023-Q2, 2023-Q3",
                "next_action": "Ask Xolo for 2023 income book.",
            },
            {
                "check": "gastos_book",
                "scope": "2023",
                "status": "missing",
                "matched_count": "0",
                "matched_files": "",
                "required_for": "2023-Q2, 2023-Q3",
                "next_action": "Ask Xolo for 2023 expense book.",
            },
            {
                "check": "bienes_inversion_book",
                "scope": "2023",
                "status": "found",
                "matched_count": "1",
                "matched_files": "assets_2023.xlsx",
                "required_for": "2023-Q2, 2023-Q3",
                "next_action": "Import asset schedule.",
            },
            {
                "check": "provisiones_suplidos_book",
                "scope": "2023",
                "status": "missing",
                "matched_count": "0",
                "matched_files": "",
                "required_for": "2023-Q2, 2023-Q3",
                "next_action": "Ask Xolo for 2023 provisions/suplidos book.",
            },
            {
                "check": "package_verdict",
                "scope": "2023-Q2..2023-Q3",
                "status": "missing_required_deliverables",
                "matched_count": "2",
                "matched_files": "",
                "required_for": "all quarters in quarter acceptance matrix",
                "next_action": "Request missing deliverables.",
            },
        ],
    )
    _write_dicts(
        root / "reconciliation.csv",
        ["period", "status"],
        [
            {"period": "2023-Q2", "status": "no_imported_source_book_rows"},
            {"period": "2023-Q3", "status": "no_imported_source_book_rows"},
        ],
    )
    _write_dicts(
        root / "availability.csv",
        ["source", "status", "evidence_count", "evidence", "conclusion", "next_action"],
        [
            {
                "source": "xolo_source_book_request_package",
                "status": "ready_to_send_package",
                "evidence_count": "7",
                "evidence": "MANIFEST.csv",
                "conclusion": "Package is ready.",
                "next_action": "Send message_to_xolo.md.",
            }
        ],
    )


def _write_dicts(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_dicts(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))
