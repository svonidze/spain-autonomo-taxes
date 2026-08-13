from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.asset_schedule_intake import (
    build_asset_schedule_intake,
    write_asset_schedule_intake_csv,
    write_asset_schedule_intake_markdown,
)


class AssetScheduleIntakeTests(unittest.TestCase):
    def test_builds_asset_quarter_intake_without_confirming_xolo_amounts(self):
        assets = (
            "date,purchase_period,recipient,type,number,currency,amount_original,subtotal_amount,"
            "gross_basis_eur,base_basis_eur,annual_rate,basis_source\n"
            "2023-06-30,2023-Q2,GitHub Inc,Computer hardware & software,GH,USD,100.00,100.00,"
            "90.00,90.00,0.26,test\n"
            "2024-01-01,2024-Q1,MediaMarkt,Computer hardware & software,MM,EUR,365.00,365.00,"
            "365.00,365.00,0.26,test\n"
        )
        quarters = (
            "year,quarter,period,target_casilla_02_delta,raw_non_asset_gross_delta,"
            "candidate_amortization_delta,candidate_model_delta,candidate_model_minus_target_delta\n"
            "2023,2,2023-Q2,0.00,0.00,0.00,0.00,0.00\n"
            "2024,1,2024-Q1,0.00,0.00,22.75,22.75,22.75\n"
            "2024,2,2024-Q2,0.00,0.00,22.75,22.75,22.75\n"
        )
        annual = (
            "year,quarter,period,annual_constraint_source,annual_constrained_amortization_delta,"
            "annual_constrained_model_minus_target_delta\n"
            "2024,1,2024-Q1,m100_0208_filed_text_extract,22.75,1.00\n"
            "2024,2,2024-Q2,m100_0208_filed_text_extract,22.75,2.00\n"
        )
        ui = (
            "period,date,xolo_id,recipient,type,number,ui_depreciable_asset,status\n"
            "2023-Q2,2023-06-30,1,GitHub Inc,Computer hardware & software,GH,yes,ui_confirms_depreciable_asset\n"
            "2024-Q1,2024-01-01,2,MediaMarkt,Computer hardware & software,MM,no,local_asset_candidate_without_ui_banner\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            assets_path = tmp_path / "assets.csv"
            quarters_path = tmp_path / "quarters.csv"
            annual_path = tmp_path / "annual.csv"
            ui_path = tmp_path / "ui.csv"
            out_csv = tmp_path / "intake.csv"
            out_md = tmp_path / "intake.md"
            assets_path.write_text(assets, encoding="utf-8")
            quarters_path.write_text(quarters, encoding="utf-8")
            annual_path.write_text(annual, encoding="utf-8")
            ui_path.write_text(ui, encoding="utf-8")

            rows = build_asset_schedule_intake(assets_path, quarters_path, annual_path, ui_path)
            write_asset_schedule_intake_csv(out_csv, rows)
            write_asset_schedule_intake_markdown(out_md, rows)
            csv_exists = out_csv.exists()
            markdown = out_md.read_text(encoding="utf-8")

        github = next(row for row in rows if row["asset_id"].startswith("2023-06-30-GH"))
        mediamarkt_q1 = next(row for row in rows if row["asset_id"].startswith("2024-01-01-MM") and row["period"] == "2024-Q1")
        mediamarkt_q2 = next(row for row in rows if row["asset_id"].startswith("2024-01-01-MM") and row["period"] == "2024-Q2")

        self.assertEqual(github["candidate_included"], "no")
        self.assertEqual(github["xolo_ui_depreciable_asset"], "yes")
        self.assertEqual(github["xolo_ui_asset_status"], "ui_confirms_depreciable_asset")
        self.assertIn("Xolo UI marks", github["question"])
        self.assertIn("local candidate excludes it", github["question"])
        self.assertEqual(github["candidate_amortization_delta_eur"], "0.00")
        self.assertEqual(mediamarkt_q1["candidate_included"], "yes")
        self.assertEqual(mediamarkt_q1["xolo_ui_asset_status"], "local_asset_candidate_without_ui_banner")
        self.assertIn("did not show a depreciable-asset banner", mediamarkt_q1["question"])
        self.assertEqual(mediamarkt_q1["candidate_amortization_delta_eur"], "22.75")
        self.assertEqual(mediamarkt_q2["candidate_amortization_delta_eur"], "22.75")
        self.assertEqual(mediamarkt_q2["candidate_amortization_ytd_eur"], "45.50")
        self.assertEqual(mediamarkt_q2["annual_constraint_source"], "m100_0208_filed_text_extract")
        self.assertEqual(mediamarkt_q2["xolo_confirmed_delta_eur"], "")
        self.assertTrue(csv_exists)
        self.assertIn("Asset Schedule Intake", markdown)


if __name__ == "__main__":
    unittest.main()
