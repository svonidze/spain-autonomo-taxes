from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.xolo_calculation_compare import build_xolo_calculation_compare


class XoloCalculationCompareTests(unittest.TestCase):
    def test_compares_submitted_xolo_calculation_to_history_and_marks_forecast(self):
        history = (
            "year,quarter,report,target_casilla_01,target_casilla_02,target_casilla_19\n"
            "2026,2,MOD 130 2T 2026 exampleei example.pdf,36770.89,10280.23,2639.12\n"
        )
        xolo = (
            "year,quarter,period,report_id,file_id,filename,submitted_date,xolo_status,amount_due,"
            "total_compounded_sales_ytd,total_compounded_deductible_expenses_ytd,net_results_ytd,"
            "net_results_20_percent,previous_quarters_compensation,withholding_taxes,"
            "article_110_3_reduction,payable_irpf_for_quarter\n"
            "2026,2,2026-Q2,18877,18135895,MOD 130 2T 2026 exampleei example.pdf,08 Jul 2026,"
            "submitted,2639.12,36770.89,10280.23,26490.66,5298.13,2659.01,0.00,,2639.12\n"
            "2026,3,2026-Q3,18878,,,,forecast_or_unsubmitted,3736.17,42565.32,10589.42,"
            "31975.90,6395.18,2659.01,0.00,,3736.17\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_path = tmp_path / "history.csv"
            xolo_path = tmp_path / "xolo.csv"
            history_path.write_text(history, encoding="utf-8")
            xolo_path.write_text(xolo, encoding="utf-8")

            rows = build_xolo_calculation_compare(history_path, xolo_path)

        self.assertEqual(rows[0]["comparison_status"], "matched")
        self.assertEqual(rows[0]["expenses_ytd_diff"], "0.00")
        self.assertEqual(rows[1]["comparison_status"], "no_local_history_row")
        self.assertEqual(rows[1]["xolo_status"], "forecast_or_unsubmitted")


if __name__ == "__main__":
    unittest.main()
