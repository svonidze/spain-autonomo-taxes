from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.annual_categories import (
    build_annual_category_reconciliation,
    write_annual_category_reconciliation_csv,
    write_annual_category_reconciliation_markdown,
)


class AnnualCategoryReconciliationTests(unittest.TestCase):
    def test_reconciles_gross_and_base_categories_with_q4_fx(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path, history_path, raw_path = _write_inputs(
                tmp_path,
                summary_row=(
                    "2025,M100 2025,draft,0,0,100,88,0,20,200,0,200,test\n"
                ),
                raw_rows=(
                    ",1,Synthetic Party 011,Social security & prof. fees,TGSS,"
                    "2025-01-31,,EUR 100,100,EUR,100,100,100,0,,0,0,BOOKED\n"
                    ",2,Synthetic Party 010,Professional expenses,INV1,"
                    "2025-02-01,,EUR 84.70,84.70,EUR,84.70,84.70,70,14.70,21,0,0,BOOKED\n"
                    ",3,Anysphere Inc.,Professional expenses,USD1,"
                    "2025-03-01,,USD 20,20,USD,20,20,20,0,,0,0,BOOKED\n"
                    ",4,Apple Retail Spain SLU,Computer hardware & software,AD1,"
                    "2025-04-01,,EUR 200,200,EUR,200,200,165.29,34.71,21,0,0,BOOKED\n"
                    ",5,Synthetic Party 011,Multiple,MULTI,"
                    "2025-05-01,,EUR 121,121,EUR,121,121,100,21,21,0,0,BOOKED\n"
                ),
            )

            rows = build_annual_category_reconciliation(summary_path, history_path, raw_path)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["q4_derived_income_usd_fx"], "0.90")
        self.assertEqual(row["raw_social_security_gross"], "100.00")
        self.assertEqual(row["raw_social_security_base"], "100.00")
        self.assertEqual(row["social_security_base_diff"], "0.00")
        self.assertEqual(row["raw_professional_expenses_gross"], "102.70")
        self.assertEqual(row["raw_professional_expenses_base"], "88.00")
        self.assertEqual(row["professional_gross_diff"], "-14.70")
        self.assertEqual(row["professional_base_diff"], "0.00")
        self.assertEqual(row["raw_computer_hardware_software_gross"], "200.00")
        self.assertEqual(row["raw_computer_hardware_software_base"], "165.29")
        self.assertEqual(row["raw_asset_like_plus_multiple_gross"], "321.00")
        self.assertEqual(row["raw_asset_like_plus_multiple_base"], "265.29")
        self.assertIn("asset schedule", row["category_signal"])

    def test_flags_direct_expense_or_reclassification_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path, history_path, raw_path = _write_inputs(
                tmp_path,
                summary_row=(
                    "2023,M100 2023,filed,0,0,0,0,195.85,0,195.85,0,195.85,test\n"
                ),
                raw_rows=(
                    ",1,GitHub,Computer hardware & software,GH1,"
                    "2023-06-01,,EUR 197.91,197.91,EUR,197.91,197.91,197.91,0,,0,0,BOOKED\n"
                ),
            )

            rows = build_annual_category_reconciliation(summary_path, history_path, raw_path)

        self.assertIn("direct-expense/reclassify", rows[0]["category_signal"])

    def test_flags_professional_base_difference_signal(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path, history_path, raw_path = _write_inputs(
                tmp_path,
                summary_row=(
                    "2025,M100 2025,draft,0,0,0,150,0,0,150,0,150,test\n"
                ),
                raw_rows=(
                    ",1,Synthetic Party 010,Professional expenses,INV1,"
                    "2025-02-01,,EUR 121,121,EUR,121,121,100,21,21,0,0,BOOKED\n"
                ),
            )

            rows = build_annual_category_reconciliation(summary_path, history_path, raw_path)

        self.assertEqual(rows[0]["professional_base_diff"], "50.00")
        self.assertIn("exceed raw VAT-base", rows[0]["category_signal"])

    def test_flags_missing_fx_and_unknown_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path, history_path, raw_path = _write_inputs(
                tmp_path,
                summary_row=(
                    "2024,M100 2024,filed,0,0,0,0,0,0,0,0,0,test\n"
                ),
                raw_rows=(
                    ",1,Unknown Supplier,New Xolo Category,UNK1,"
                    "2024-02-01,,EUR 10,10,EUR,10,10,10,0,,0,0,BOOKED\n"
                    ",2,Anysphere Inc.,Professional expenses,USD1,"
                    "2024-03-01,,USD 20,20,USD,20,20,20,0,,0,0,BOOKED\n"
                ),
                history_rows="year,quarter,derived_income_usd_fx\n",
            )

            rows = build_annual_category_reconciliation(summary_path, history_path, raw_path)

        self.assertEqual(rows[0]["missing_fx_expense_rows"], "1")
        self.assertEqual(rows[0]["unmapped_expense_types"], "New Xolo Category")

    def test_default_signal_for_near_matching_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path, history_path, raw_path = _write_inputs(
                tmp_path,
                summary_row=(
                    "2025,M100 2025,draft,0,0,100,70,0,0,170,0,170,test\n"
                ),
                raw_rows=(
                    ",1,Synthetic Party 011,Social security & prof. fees,TGSS,"
                    "2025-01-31,,EUR 100,100,EUR,100,100,100,0,,0,0,BOOKED\n"
                    ",2,Synthetic Party 010,Professional expenses,INV1,"
                    "2025-02-01,,EUR 84.70,84.70,EUR,84.70,84.70,70,14.70,21,0,0,BOOKED\n"
                ),
            )

            rows = build_annual_category_reconciliation(summary_path, history_path, raw_path)

        self.assertEqual(rows[0]["category_signal"], "annual categories are near raw category totals")

    def test_writes_csv_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary_path, history_path, raw_path = _write_inputs(
                tmp_path,
                summary_row=(
                    "2025,M100 2025,draft,0,0,100,80,0,20,200,0,200,test\n"
                ),
                raw_rows=(
                    ",1,Synthetic Party 011,Social security & prof. fees,TGSS,"
                    "2025-01-31,,EUR 100,100,EUR,100,100,100,0,,0,0,BOOKED\n"
                ),
            )
            rows = build_annual_category_reconciliation(summary_path, history_path, raw_path)
            out_csv = tmp_path / "annual_categories.csv"
            out_md = tmp_path / "annual_categories.md"

            write_annual_category_reconciliation_csv(out_csv, rows)
            write_annual_category_reconciliation_markdown(out_md, rows)

            self.assertIn("professional_base_diff", out_csv.read_text(encoding="utf-8"))
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Modelo 100 Annual Category Reconciliation", markdown)
            self.assertIn("Professional-service gross gaps collapse", markdown)


def _write_inputs(
    tmp_path: Path,
    summary_row: str,
    raw_rows: str,
    history_rows: str | None = None,
) -> tuple[Path, Path, Path]:
    summary_path = tmp_path / "modelo100_summary.csv"
    history_path = tmp_path / "history.csv"
    raw_path = tmp_path / "raw.csv"
    summary_path.write_text(
        "year,source_report,status,income_0171,total_income_0180,social_security_0186,"
        "professional_services_0199,other_external_services_0202,amortization_0208,"
        "deductible_expenses_0218,difficult_expenses_0222,total_deductible_0223,notes\n"
        + summary_row,
        encoding="utf-8",
    )
    history_path.write_text(
        history_rows
        or (
            "year,quarter,derived_income_usd_fx\n"
            "2023,4,0.90\n"
            "2025,4,0.90\n"
        ),
        encoding="utf-8",
    )
    raw_path.write_text(
        "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,"
        "gross_amount,match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,"
        "irpf_percentage,status\n"
        + raw_rows,
        encoding="utf-8",
    )
    return summary_path, history_path, raw_path


if __name__ == "__main__":
    unittest.main()
