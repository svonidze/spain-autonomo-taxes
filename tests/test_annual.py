from pathlib import Path
from decimal import Decimal
import tempfile
import unittest

from autonomo_taxes.annual import compare_annual_to_quarterly, load_modelo100_summary


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "modelo100_annual_summary.synthetic.csv"


class AnnualAuditTests(unittest.TestCase):
    def test_load_modelo100_summary_fixture(self):
        rows = load_modelo100_summary(FIXTURE)
        by_year = {row.year: row for row in rows}

        self.assertEqual(by_year[2031].amortization_0208, Decimal("123.45"))
        self.assertEqual(by_year[2032].deductible_expenses_0218, Decimal("4321.09"))

    def test_annual_comparison_locks_q4_relation(self):
        history = (
            "year,quarter,target_casilla_02,derived_income_usd_fx\n"
            "2032,4,4321.09,0.80\n"
        )
        raw = (
            "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,gross_amount,"
            "match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,irpf_percentage,status\n"
            ',SYN-RAW-1,Synthetic Asset Supplier,Computer hardware & software,SYN-ASSET-001,2032-06-01,,"€246,80",246.80,EUR,246.80,246.80,200.00,46.80,23.4,0,0,UNPAID\n'
            ',SYN-RAW-2,Synthetic Service Supplier,Professional expenses,SYN-SERVICE-001,2032-12-31,,"€135,79",135.79,EUR,135.79,135.79,135.79,0,,0,0,UNPAID\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            raw_path = tmp_path / "raw.csv"
            history_path.write_text(history, encoding="utf-8")
            raw_path.write_text(raw, encoding="utf-8")

            rows = compare_annual_to_quarterly(FIXTURE, history_path, raw_path)

        row_2032 = next(row for row in rows if row["year"] == "2032")
        self.assertEqual(row_2032["m100_deductible_0218"], "4321.09")
        self.assertEqual(row_2032["m130_q4_casilla02"], "4321.09")
        self.assertEqual(row_2032["annual_minus_m130_q4"], "0.00")
        self.assertEqual(row_2032["raw_non_asset_gross_ytd"], "135.79")
        self.assertEqual(row_2032["raw_asset_gross_ytd"], "246.80")
        self.assertIn("amortization_minus_raw_residual", row_2032)

    def test_annual_comparison_uses_fx_rate_as_decimal_not_money(self):
        history = (
            "year,quarter,target_casilla_02,derived_income_usd_fx\n"
            "2032,4,4321.09,0.80\n"
        )
        raw = (
            "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,gross_amount,"
            "match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,irpf_percentage,status\n"
            ",SYN-RAW-3,Synthetic Currency Supplier,Professional expenses,SYN-FX-001,2032-12-01,,$20,20,USD,20,20,20,0,,0,0,UNPAID\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            raw_path = tmp_path / "raw.csv"
            history_path.write_text(history, encoding="utf-8")
            raw_path.write_text(raw, encoding="utf-8")

            rows = compare_annual_to_quarterly(FIXTURE, history_path, raw_path)

        row_2032 = next(row for row in rows if row["year"] == "2032")
        self.assertEqual(row_2032["raw_non_asset_gross_ytd"], "16.00")


if __name__ == "__main__":
    unittest.main()
