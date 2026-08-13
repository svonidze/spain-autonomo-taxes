from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.asset_ui_evidence import build_asset_ui_evidence
from autonomo_taxes.cli import main


class AssetUiEvidenceTests(unittest.TestCase):
    def test_classifies_ui_confirmed_and_heuristic_only_asset_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "expenses.csv"
            _write_expenses(source)

            rows = build_asset_ui_evidence(source)

        by_number = {row["number"]: row for row in rows}
        self.assertEqual(by_number["SYNTH-DOCUMENT-031"]["status"], "ui_confirms_depreciable_asset")
        self.assertEqual(by_number["SYNTH-DOCUMENT-031"]["ui_depreciable_asset"], "yes")
        self.assertIn("asset schedule", by_number["SYNTH-DOCUMENT-031"]["next_action"])
        self.assertEqual(by_number["SYNTH-DOCUMENT-026"]["status"], "local_asset_candidate_without_ui_banner")
        self.assertIn("apple retal", by_number["SYNTH-DOCUMENT-026"]["asset_like_reason"])
        self.assertEqual(
            by_number["NO-DETAIL"]["asset_like_reason"],
            "xolo_depreciable_asset_flag_without_detail_page",
        )
        self.assertNotIn("Authenticated Xolo expense detail page", by_number["NO-DETAIL"]["evidence"])
        self.assertNotIn("Xolo fee", by_number)

    def test_cli_writes_asset_ui_evidence_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "expenses.csv"
            out_csv = tmp_path / "asset_ui.csv"
            out_md = tmp_path / "asset_ui.md"
            _write_expenses(source)

            exit_code = main(
                [
                    "audit-asset-ui-evidence",
                    "--xolo-raw-expenses",
                    str(source),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Xolo Asset UI Evidence", markdown)
            self.assertIn("ui_confirms_depreciable_asset", markdown)


def _write_expenses(path: Path) -> None:
    fields = [
        "date",
        "recipient",
        "type",
        "number",
        "amount_original",
        "currency",
        "subtotal_amount",
        "gross_eur",
        "vat_base_eur",
        "detail_confidence",
        "is_depreciable_asset",
        "xolo_id",
        "xolo_url",
    ]
    rows = [
        {
            "date": "2026-04-08",
            "recipient": "Synthetic Party 016",
            "type": "Computer hardware & software",
            "number": "SYNTH-DOCUMENT-031",
            "amount_original": "1994.00",
            "currency": "EUR",
            "subtotal_amount": "1647.93",
            "gross_eur": "1994.00",
            "vat_base_eur": "1647.93",
            "detail_confidence": "detail_page_eur",
            "is_depreciable_asset": "yes",
            "xolo_id": "3019975",
            "xolo_url": "https://xolo.test/3019975",
        },
        {
            "date": "2024-05-07",
            "recipient": "Apple Retal Spain, s.L.U.",
            "type": "Multiple",
            "number": "SYNTH-DOCUMENT-026",
            "amount_original": "642.95",
            "currency": "EUR",
            "subtotal_amount": "531.36",
            "gross_eur": "642.95",
            "vat_base_eur": "531.36",
            "detail_confidence": "detail_page_eur",
            "is_depreciable_asset": "no",
            "xolo_id": "2117398",
            "xolo_url": "https://xolo.test/2117398",
        },
        {
            "date": "2025-01-10",
            "recipient": "Example Hardware",
            "type": "Computer hardware & software",
            "number": "NO-DETAIL",
            "amount_original": "300.00",
            "currency": "EUR",
            "subtotal_amount": "247.93",
            "gross_eur": "300.00",
            "vat_base_eur": "247.93",
            "detail_confidence": "raw_api",
            "is_depreciable_asset": "yes",
            "xolo_id": "2500000",
            "xolo_url": "https://xolo.test/2500000",
        },
        {
            "date": "2026-07-01",
            "recipient": "Xolo fee",
            "type": "Professional expenses",
            "number": "INV",
            "amount_original": "71.39",
            "currency": "EUR",
            "subtotal_amount": "59.00",
            "gross_eur": "71.39",
            "vat_base_eur": "59.00",
            "detail_confidence": "detail_page_eur",
            "is_depreciable_asset": "no",
            "xolo_id": "3137426",
            "xolo_url": "https://xolo.test/3137426",
        },
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


if __name__ == "__main__":
    unittest.main()
