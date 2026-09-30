from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.asset_audit import (
    build_asset_audit,
    build_asset_scenarios,
    build_candidate_quarter_reconciliation,
)


class AssetAuditTests(unittest.TestCase):
    def test_usd_asset_uses_purchase_quarter_fx_for_fixed_basis(self):
        history = (
            "year,quarter,report,no_provision_gross_residual,estimated_asset_amortization_gross_ytd,"
            "estimated_asset_amortization_base_ytd,derived_income_usd_fx\n"
            "2023,2,Q2.pdf,10.00,1.03,1.03,0.90\n"
            "2023,3,Q3.pdf,20.00,6.16,6.16,0.80\n"
        )
        raw = (
            "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,gross_amount,"
            "match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,irpf_percentage,status\n"
            ",1,GitHub Inc,Computer hardware & software,SYNTH-ASSET-001,2023-06-15,,"
            "$100.00,100.00,USD,100.00,100.00,100.00,0,,0,0,UNPAID\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            raw_path = tmp_path / "raw.csv"
            history_path.write_text(history, encoding="utf-8")
            raw_path.write_text(raw, encoding="utf-8")

            assets, quarters = build_asset_audit(history_path, raw_path)

        self.assertEqual(assets[0]["gross_basis_eur"], "90.00")
        self.assertIn("purchase-quarter derived_income_usd_fx=0.90", assets[0]["basis_source"])
        self.assertEqual(quarters[0]["asset_amortization_gross_ytd_fixed_purchase_fx"], "1.03")
        self.assertEqual(quarters[1]["asset_amortization_gross_ytd_fixed_purchase_fx"], "6.92")
        self.assertEqual(quarters[1]["asset_amortization_gross_delta_fixed_purchase_fx"], "5.89")

    def test_usd_asset_prefers_trusted_detail_page_eur_basis(self):
        history = (
            "year,quarter,report,no_provision_gross_residual,estimated_asset_amortization_gross_ytd,"
            "estimated_asset_amortization_base_ytd,derived_income_usd_fx\n"
            "2023,2,Q2.pdf,10.00,1.03,1.03,0.90\n"
        )
        raw = (
            "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,gross_amount,"
            "match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,irpf_percentage,status,"
            "gross_eur,vat_base_eur,detail_confidence,is_depreciable_asset\n"
            ",1,GitHub Inc,Computer hardware & software,SYNTH-ASSET-001,2023-06-15,,"
            "$125.00,125.00,USD,125.00,125.00,125.00,0,,0,0,UNPAID,"
            "115.54,115.54,detail_page_exchange_rate,yes\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            raw_path = tmp_path / "raw.csv"
            history_path.write_text(history, encoding="utf-8")
            raw_path.write_text(raw, encoding="utf-8")

            assets, _ = build_asset_audit(history_path, raw_path)

        self.assertEqual(assets[0]["gross_basis_eur"], "115.54")
        self.assertEqual(assets[0]["base_basis_eur"], "115.54")
        self.assertIn("Xolo detail-page gross EUR", assets[0]["basis_source"])

    def test_eur_asset_prefers_trusted_detail_page_eur_basis(self):
        history = (
            "year,quarter,report,no_provision_gross_residual,estimated_asset_amortization_gross_ytd,"
            "estimated_asset_amortization_base_ytd,derived_income_usd_fx\n"
            "2024,1,Q1.pdf,10.00,1.03,1.03,0.90\n"
        )
        raw = (
            "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,gross_amount,"
            "match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,irpf_percentage,status,"
            "gross_eur,vat_base_eur,detail_confidence,is_depreciable_asset\n"
            ",1,MediaMarkt,Computer hardware & software,MM,2024-01-04,,"
            "EUR 419.30,419.30,EUR,419.30,419.30,346.53,72.77,21,0,0,UNPAID,"
            "419.30,346.53,detail_page_eur,yes\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            raw_path = tmp_path / "raw.csv"
            history_path.write_text(history, encoding="utf-8")
            raw_path.write_text(raw, encoding="utf-8")

            assets, _ = build_asset_audit(history_path, raw_path)

        self.assertEqual(assets[0]["gross_basis_eur"], "419.30")
        self.assertEqual(assets[0]["base_basis_eur"], "346.53")
        self.assertIn("detail_page_eur", assets[0]["basis_source"])

    def test_empty_detail_confidence_keeps_legacy_manual_reviewed_basis(self):
        history = (
            "year,quarter,report,no_provision_gross_residual,estimated_asset_amortization_gross_ytd,"
            "estimated_asset_amortization_base_ytd,derived_income_usd_fx\n"
            "2023,2,Q2.pdf,10.00,1.03,1.03,0.90\n"
        )
        raw = (
            "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,gross_amount,"
            "match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,irpf_percentage,status,"
            "gross_eur,vat_base_eur,detail_confidence,is_depreciable_asset\n"
            ",1,GitHub Inc,Computer hardware & software,SYNTH-ASSET-001,2023-06-15,,"
            "$125.00,125.00,USD,125.00,125.00,125.00,0,,0,0,UNPAID,"
            "115.54,115.54,,yes\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            raw_path = tmp_path / "raw.csv"
            history_path.write_text(history, encoding="utf-8")
            raw_path.write_text(raw, encoding="utf-8")

            assets, _ = build_asset_audit(history_path, raw_path)

        self.assertEqual(assets[0]["gross_basis_eur"], "115.54")
        self.assertIn("legacy/manual reviewed gross EUR", assets[0]["basis_source"])

    def test_usd_asset_ignores_untrusted_detail_page_eur_basis(self):
        history = (
            "year,quarter,report,no_provision_gross_residual,estimated_asset_amortization_gross_ytd,"
            "estimated_asset_amortization_base_ytd,derived_income_usd_fx\n"
            "2023,2,Q2.pdf,10.00,1.03,1.03,0.90\n"
        )
        raw = (
            "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,gross_amount,"
            "match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,irpf_percentage,status,"
            "gross_eur,vat_base_eur,detail_confidence,is_depreciable_asset\n"
            ",1,GitHub Inc,Computer hardware & software,SYNTH-ASSET-001,2023-06-15,,"
            "$125.00,125.00,USD,125.00,125.00,125.00,0,,0,0,UNPAID,"
            "115.54,115.54,currency_mismatch_review,yes\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            raw_path = tmp_path / "raw.csv"
            history_path.write_text(history, encoding="utf-8")
            raw_path.write_text(raw, encoding="utf-8")

            assets, _ = build_asset_audit(history_path, raw_path)

        self.assertEqual(assets[0]["gross_basis_eur"], "112.50")
        self.assertIn("purchase-quarter derived_income_usd_fx=0.90", assets[0]["basis_source"])

    def test_annual_scenario_can_exclude_2023_usd_low_value_assets(self):
        history = (
            "year,quarter,report,no_provision_gross_residual,estimated_asset_amortization_gross_ytd,"
            "estimated_asset_amortization_base_ytd,derived_income_usd_fx\n"
            "2023,2,Q2.pdf,0.00,0.00,0.00,0.90\n"
            "2024,4,Q4.pdf,0.00,0.00,0.00,0.90\n"
        )
        raw = (
            "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,gross_amount,"
            "match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,irpf_percentage,status\n"
            ",1,GitHub Inc,Computer hardware & software,GH,2023-06-15,,$100.00,100.00,USD,100.00,100.00,100.00,0,,0,0,UNPAID\n"
            ',2,MediaMarkt,Computer hardware & software,MM,2024-01-04,,"EUR 419.30",419.30,EUR,419.30,419.30,346.53,72.77,21,0,0,UNPAID\n'
        )
        summary = (
            "year,source_report,status,income_0171,total_income_0180,social_security_0186,professional_services_0199,"
            "other_external_services_0202,amortization_0208,deductible_expenses_0218,difficult_expenses_0222,"
            "total_deductible_0223,notes\n"
            "2024,M100.pdf,filed,0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00,0.00,\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            raw_path = tmp_path / "raw.csv"
            summary_path = tmp_path / "summary.csv"
            history_path.write_text(history, encoding="utf-8")
            raw_path.write_text(raw, encoding="utf-8")
            summary_path.write_text(summary, encoding="utf-8")

            assets, _ = build_asset_audit(history_path, raw_path)
            scenarios = build_asset_scenarios(assets, summary_path)

        all_base = next(
            row
            for row in scenarios
            if row["scenario"] == "all_candidates" and row["basis"] == "vat_base" and row["rate"] == "0.26"
        )
        excluded_base = next(
            row
            for row in scenarios
            if row["scenario"] == "exclude_2023_usd_low_value_or_subscription"
            and row["basis"] == "vat_base"
            and row["rate"] == "0.26"
        )

        self.assertGreater(float(all_base["estimate_ytd"]), float(excluded_base["estimate_ytd"]))
        self.assertIn("GitHub", excluded_base["excluded_assets"])
        self.assertNotIn("GitHub", excluded_base["included_assets"])

    def test_candidate_quarter_reconciliation_searches_subset_after_amortization(self):
        history = (
            "year,quarter,report,target_casilla_02_delta,raw_quarter_non_asset_gross_eur,"
            "no_provision_gross_residual,estimated_asset_amortization_gross_ytd,"
            "estimated_asset_amortization_base_ytd,derived_income_usd_fx\n"
            "2024,1,Q1.pdf,100.00,200.00,100.00,0.00,0.00,0.90\n"
        )
        raw = (
            "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,gross_amount,"
            "match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,irpf_percentage,status\n"
            ',1,MediaMarkt,Computer hardware & software,MM,2024-01-01,,"EUR 365.00",365.00,EUR,365.00,365.00,365.00,0,,0,0,UNPAID\n'
            ',2,Xolo,Professional expenses,INV1,2024-01-02,,"EUR 122.50",122.50,EUR,122.50,122.50,122.50,0,,0,0,UNPAID\n'
            ',3,Supplier,Professional expenses,INV2,2024-01-03,,"EUR 77.50",77.50,EUR,77.50,77.50,77.50,0,,0,0,UNPAID\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            raw_path = tmp_path / "raw.csv"
            history_path.write_text(history, encoding="utf-8")
            raw_path.write_text(raw, encoding="utf-8")

            rows = build_candidate_quarter_reconciliation(history_path, raw_path)

        self.assertEqual(rows[0]["candidate_amortization_delta"], "22.75")
        self.assertEqual(rows[0]["candidate_model_minus_target_delta"], "122.75")
        self.assertEqual(rows[0]["nearest_excluded_subset_eur"], "122.50")
        self.assertEqual(rows[0]["nearest_excluded_subset_error_eur"], "0.25")
        self.assertIn("INV1", rows[0]["nearest_excluded_subset_rows"])


if __name__ == "__main__":
    unittest.main()
