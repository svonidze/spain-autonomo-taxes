import unittest

from autonomo_taxes.xolo_tax_calculations import build_calculation_rows, parse_calculation_html, parse_tax_report_links


class XoloTaxCalculationsTests(unittest.TestCase):
    def test_parses_tax_report_links_and_calculation_html(self):
        tax_report_html = """
        <a id="row-dots-2023-2-view" data-report-filename="M130 2T 2023 exampleei example.pdf"
           data-report-submitted-date="13 Jul 2023" data-report-period="2023-04-01 - 2023-06-30"
           data-report-file-id="6919692"></a>
        <a id="row-dots-2023-2-calculation" data-report-id="8246"></a>
        """
        calculation_html = """
        <div class="modal">
          <h1>Model 130</h1>
          <p>2023-04-01 - 2023-06-30</p>
          <table>
            <tr><td>Total compounded sales YTD</td><td>€5&nbsp;757,20</td></tr>
            <tr><td>Total compounded deductible expenses YTD</td><td>€115,54</td></tr>
            <tr><td>Net results YTD</td><td>€5&nbsp;641,66</td></tr>
            <tr><td>20% of net results</td><td>€1&nbsp;128,33</td></tr>
            <tr><td>Subtract the amount to be compensated from previous quarters</td><td>€0,00</td></tr>
            <tr><td>Substract withholding taxes applied in sales invoices</td><td>€0,00</td></tr>
            <tr><td>Reduction by application of the deduction of article 110.3</td><td>€100,00</td></tr>
            <tr><td>Payable IRPF for the quarter</td><td>€1&nbsp;028,33</td></tr>
          </table>
        </div>
        """

        reports = parse_tax_report_links(tax_report_html)
        calculation = parse_calculation_html(calculation_html)
        rows = build_calculation_rows(tax_report_html, {"8246": calculation_html})

        self.assertEqual(reports[0]["report_id"], "8246")
        self.assertEqual(reports[0]["file_id"], "6919692")
        self.assertEqual(calculation["total_compounded_sales_ytd"], "5757.20")
        self.assertEqual(calculation["total_compounded_deductible_expenses_ytd"], "115.54")
        self.assertEqual(calculation["net_results_ytd"], "5641.66")
        self.assertEqual(calculation["payable_irpf_for_quarter"], "1028.33")
        self.assertEqual(rows[0]["period"], "2023-Q2")
        self.assertEqual(rows[0]["filename"], "M130 2T 2023 exampleei example.pdf")
        self.assertEqual(rows[0]["xolo_status"], "submitted")
        self.assertEqual(rows[0]["amount_due"], "1028.33")


if __name__ == "__main__":
    unittest.main()
