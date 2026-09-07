from __future__ import annotations

import csv
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autonomo_taxes.cli import main
from autonomo_taxes.reports import write_markdown_report
from autonomo_taxes.modelo130 import calculate_modelo130
from autonomo_taxes.xolo_ledger import write_xolo_expense_ledger_csv


class XoloLedgerCliTests(unittest.TestCase):
    def test_modelo130_xolo_ledger_replaces_local_and_manual_expenses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._xolo_root(Path(tmp) / "xolo")
            out_dir = Path(tmp) / "run"
            manual = Path(tmp) / "manual.csv"
            xolo_ledger = Path(tmp) / "xolo_expenses.csv"
            self._write_manual_ledger(manual)
            self._write_xolo_ledger(xolo_ledger, "EXP1", "1000.00")

            exit_code = main(
                [
                    "modelo130",
                    "--xolo-root",
                    str(root),
                    "--year",
                    "2026",
                    "--quarter",
                    "2",
                    "--manual-ledger",
                    str(manual),
                    "--xolo-expense-ledger",
                    str(xolo_ledger),
                    "--out-dir",
                    str(out_dir),
                ]
            )

            self.assertEqual(exit_code, 0)
            with (out_dir / "ledger.csv").open(newline="", encoding="utf-8") as handle:
                ledger = list(csv.DictReader(handle))
            by_kind = {row["kind"]: row for row in ledger}
            self.assertEqual(set(by_kind), {"income", "expense"})
            self.assertEqual(by_kind["income"]["amount_eur"], "10000.00")
            self.assertEqual(by_kind["expense"]["deductible_eur"], "1000.00")
            compare = json.loads((out_dir / "xolo_compare.json").read_text(encoding="utf-8"))
            self.assertEqual(compare["casillas"]["01"]["calculated"], "10000.00")
            self.assertEqual(compare["casillas"]["02"]["calculated"], "1450.00")
            self.assertEqual(compare["casillas"]["19"]["calculated"], "1710.00")
            report = (out_dir / "modelo130_report.md").read_text(encoding="utf-8")
            self.assertIn("Ignoring reviewed expense rows from --manual-ledger", report)

    def test_xolo_reconcile_cli_passes_asset_threshold_to_expense_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._xolo_root(Path(tmp) / "xolo")
            xolo_ledger = Path(tmp) / "xolo_expenses.csv"
            self._write_xolo_ledger(xolo_ledger, "EXP1", "1000.00")
            out = Path(tmp) / "reconcile.csv"

            with patch("autonomo_taxes.cli.scan_expense_dir", return_value=([], [])) as scan_expense_dir:
                exit_code = main(
                    [
                        "xolo-ledger",
                        "reconcile",
                        "--xolo-root",
                        str(root),
                        "--year",
                        "2026",
                        "--quarter",
                        "2",
                        "--xolo-expense-ledger",
                        str(xolo_ledger),
                        "--asset-review-threshold-eur",
                        "123.45",
                        "--out",
                        str(out),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(scan_expense_dir.call_args.args[1], Decimal("123.45"))

    def test_xolo_reconcile_cli_scans_controlled_evidence_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._xolo_root(Path(tmp) / "xolo")
            evidence = Path(tmp) / "evidence" / "2026-Q2"
            evidence.mkdir(parents=True)
            xolo_ledger = Path(tmp) / "xolo_expenses.csv"
            self._write_xolo_ledger(xolo_ledger, "EXP1", "1000.00")
            out = Path(tmp) / "reconcile.csv"

            with patch("autonomo_taxes.cli.scan_expense_dir", return_value=([], [])) as scan_expense_dir:
                exit_code = main(
                    [
                        "xolo-ledger",
                        "reconcile",
                        "--xolo-root",
                        str(root),
                        "--year",
                        "2026",
                        "--quarter",
                        "2",
                        "--xolo-expense-ledger",
                        str(xolo_ledger),
                        "--additional-expense-root",
                        str(evidence),
                        "--out",
                        str(out),
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                [call.args[0] for call in scan_expense_dir.call_args_list],
                [root / "EXPENSE", evidence],
            )

    def test_xolo_import_api_json_cli_writes_raw_csv_and_combined_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.json"
            out_csv = Path(tmp) / "raw.csv"
            out_json = Path(tmp) / "combined.json"
            page.write_text(
                json.dumps(
                    {
                        "draw": 8,
                        "recordsTotal": 1,
                        "recordsFiltered": 1,
                        "data": [
                            {
                                "id": 3141405,
                                "party": '<a href="/selfservice/expense/invoice/3141405/details?from=expense">Synthetic Party 011</a>',
                                "categoryText": "Social security & prof. fees",
                                "number": "052107461197310749202306020062",
                                "dateString": "2026-06-30",
                                "amount": "€370,59",
                                "status": "PAID",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "xolo-ledger",
                    "import-api-json",
                    "--input",
                    str(page),
                    "--out-csv",
                    str(out_csv),
                    "--out-json",
                    str(out_json),
                ]
            )

            self.assertEqual(exit_code, 0)
            with out_csv.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["xolo_id"], "3141405")
            self.assertEqual(rows[0]["currency"], "EUR")
            combined = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(combined["pages"][0]["draw"], 8)

    def test_report_status_is_not_filing_ready_when_match_uses_residual(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.md"
            result = calculate_modelo130(Decimal("10000.00"), Decimal("1000.00"))

            write_markdown_report(
                path,
                2026,
                2,
                result,
                {"19": result.casilla_19},
                [],
                [],
                [],
                [],
                xolo_reconciliation=[
                    {
                        "status": "residual_pending_confirmation",
                        "irpf_deductible_eur": "807,09",
                    }
                ],
            )

            report = path.read_text(encoding="utf-8")
            self.assertIn("matched target via unresolved expense rows; not filing-ready", report)

    def _xolo_root(self, path: Path) -> Path:
        for child in ("INVOICE", "EXPENSE", "TAX_REPORT"):
            (path / child).mkdir(parents=True, exist_ok=True)
        return path

    def _write_manual_ledger(self, path: Path) -> None:
        fieldnames = [
            "kind",
            "date",
            "document",
            "counterparty",
            "description",
            "amount_original",
            "currency",
            "amount_eur",
            "deductible_eur",
            "category",
            "confidence",
            "review_required",
            "notes",
        ]
        rows = [
            {
                "kind": "income",
                "date": "2026-04-01",
                "document": "manual-income",
                "counterparty": "Client",
                "description": "Manual income",
                "amount_original": "10000.00",
                "currency": "EUR",
                "amount_eur": "10000.00",
                "deductible_eur": "",
                "category": "service_income",
                "confidence": "manual",
                "review_required": "no",
                "notes": "",
            },
            {
                "kind": "expense",
                "date": "2026-04-02",
                "document": "manual-expense",
                "counterparty": "Should be ignored",
                "description": "Manual expense",
                "amount_original": "9999.00",
                "currency": "EUR",
                "amount_eur": "9999.00",
                "deductible_eur": "9999.00",
                "category": "manual",
                "confidence": "manual",
                "review_required": "no",
                "notes": "",
            },
        ]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _write_xolo_ledger(self, path: Path, number: str, amount: str) -> None:
        write_xolo_expense_ledger_csv(
            path,
            [
                {
                    "xolo_url": "",
                    "xolo_id": "1",
                    "recipient": "Supplier",
                    "type": "Professional expenses",
                    "number": number,
                    "date": "2026-04-02",
                    "amount_original": amount,
                    "currency": "EUR",
                    "gross_eur": amount,
                    "vat_base_eur": "",
                    "irpf_deductible_eur": amount,
                    "inclusion_quarter": "2026-Q2",
                    "include_in_modelo130": "yes",
                    "source_document_path": "",
                    "confidence": "xolo_ui",
                    "notes": "",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
