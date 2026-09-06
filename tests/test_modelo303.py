from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.history import RawXoloExpense
from autonomo_taxes.modelo303 import (
    _raw_vat_bearing_totals_from_csv,
    _raw_vat_bearing_totals_by_period,
    _settlement_casilla_evidence_from_fragments,
    _settlement_casillas_from_fragments,
    _structural_casilla_evidence_from_pages,
    values_from_monetary_sequence,
)


class Modelo303Tests(unittest.TestCase):
    def test_structural_layout_extracts_reverse_charge_and_blank_asset_boxes(self):
        layout = """
        Otras operaciones con inversion del sujeto pasivo 12  701,80 13 147,38
        Una fila no relacionada termina con 120 17.460,21
        Total cuota devengada ................................ 27 147,38
        Operaciones interiores corrientes .................... 28 2.254,8329 473,52
        Operaciones interiores con bienes de inversion ....... 30 31
        Total a deducir ...................................... 45473,52
        Resultado regimen general ............................ 46 -326,14
        """
        positioned = [[
            ("12", Decimal("296.1"), Decimal("316.6"), Decimal("1.0")),
            ("701,80", Decimal("364.2"), Decimal("314.5"), Decimal("9.0")),
            ("13", Decimal("0.0"), Decimal("0.0"), Decimal("1.0")),
            ("27", Decimal("460.3"), Decimal("217.4"), Decimal("1.0")),
            ("147,38", Decimal("528.8"), Decimal("216.2"), Decimal("9.0")),
            ("28", Decimal("360.7"), Decimal("182.6"), Decimal("1.0")),
            ("2.254,83", Decimal("420.7"), Decimal("181.6"), Decimal("9.0")),
            ("29", Decimal("460.7"), Decimal("182.6"), Decimal("1.0")),
            ("30", Decimal("360.7"), Decimal("170.6"), Decimal("1.0")),
            ("31", Decimal("460.7"), Decimal("170.6"), Decimal("1.0")),
            ("45", Decimal("460.4"), Decimal("56.5"), Decimal("1.0")),
            ("473,52", Decimal("528.3"), Decimal("55.5"), Decimal("9.0")),
            ("46", Decimal("460.4"), Decimal("30.1"), Decimal("1.0")),
            ("-326,14", Decimal("525.6"), Decimal("28.4"), Decimal("9.0")),
        ]]

        evidence = _structural_casilla_evidence_from_pages([layout], positioned)

        self.assertEqual(evidence.status, "casillas_extracted")
        self.assertEqual(
            dict(evidence.casillas),
            {
                "12": Decimal("701.80"),
                "13": Decimal("147.38"),
                "27": Decimal("147.38"),
                "28": Decimal("2254.83"),
                "29": Decimal("473.52"),
                "30": Decimal("0.00"),
                "31": Decimal("0.00"),
                "45": Decimal("473.52"),
                "46": Decimal("-326.14"),
            },
        )
        self.assertEqual(evidence.blank_casillas, ("30", "31"))
        self.assertEqual(dict(evidence.value_sources)["13"], "layout_line")
        self.assertEqual(dict(evidence.value_sources)["45"], "layout_line+positioned_row")

    def test_structural_layout_distinguishes_printed_zero_from_blank_box(self):
        evidence = _structural_casilla_evidence_from_pages(
            ["Operaciones interiores con bienes de inversion 30 0,00 31 0,00"],
            [[]],
        )

        self.assertEqual(evidence.status, "incomplete_structural_layout")
        self.assertEqual(
            dict(evidence.casillas),
            {"30": Decimal("0.00"), "31": Decimal("0.00")},
        )
        self.assertEqual(evidence.blank_casillas, ())
        self.assertEqual(dict(evidence.value_sources)["30"], "layout_line")

    def test_settlement_layout_extracts_compensation_boxes_and_blank_zeroes(self):
        page = [
            ("64", Decimal("462.8"), Decimal("548.4"), Decimal("1.0")),
            ("-45,36", Decimal("530.7"), Decimal("548.1"), Decimal("9.0")),
            ("110", Decimal("461.1"), Decimal("497.4"), Decimal("1.0")),
            ("876,91", Decimal("528.7"), Decimal("496.4"), Decimal("9.0")),
            ("78", Decimal("462.8"), Decimal("480.4"), Decimal("1.0")),
            ("87", Decimal("462.8"), Decimal("463.4"), Decimal("1.0")),
            ("876,91", Decimal("528.7"), Decimal("462.7"), Decimal("9.0")),
            ("69", Decimal("462.8"), Decimal("436.8"), Decimal("1.0")),
            ("-45,36", Decimal("531.7"), Decimal("435.8"), Decimal("9.0")),
            ("71", Decimal("462.8"), Decimal("388.8"), Decimal("1.0")),
            ("-45,36", Decimal("531.7"), Decimal("386.7"), Decimal("9.0")),
        ]
        final_page = [
            ("72", Decimal("88.2"), Decimal("739.9"), Decimal("1.0")),
            ("45,36", Decimal("161.6"), Decimal("737.2"), Decimal("9.0")),
            ("73", Decimal("161.3"), Decimal("527.1"), Decimal("1.0")),
        ]

        casillas, status = _settlement_casillas_from_fragments([page, final_page])

        self.assertEqual(status, "casillas_extracted")
        self.assertEqual(
            dict(casillas),
            {
                "64": Decimal("-45.36"),
                "110": Decimal("876.91"),
                "78": Decimal("0.00"),
                "87": Decimal("876.91"),
                "69": Decimal("-45.36"),
                "71": Decimal("-45.36"),
                "72": Decimal("45.36"),
                "73": Decimal("0.00"),
            },
        )

        evidence = _settlement_casilla_evidence_from_fragments([page, final_page])
        self.assertIn("78", evidence.blank_casillas)
        self.assertEqual(
            dict(evidence.value_sources)["78"],
            "box_present_no_value_captured",
        )
        self.assertEqual(dict(evidence.value_sources)["110"], "positioned_row")

    def test_settlement_layout_extracts_q4_refund(self):
        final_page = [
            ("72", Decimal("88.2"), Decimal("739.9"), Decimal("1.0")),
            ("73", Decimal("161.3"), Decimal("527.1"), Decimal("1.0")),
            ("170,95", Decimal("189.0"), Decimal("525.6"), Decimal("9.0")),
        ]

        casillas, status = _settlement_casillas_from_fragments([final_page])

        self.assertEqual(status, "incomplete_settlement_layout")
        self.assertEqual(dict(casillas), {"72": Decimal("0.00"), "73": Decimal("170.95")})

    def test_deductible_only_sequence_maps_to_local_input_vat(self):
        values = values_from_monetary_sequence([Decimal("776.66"), Decimal("161.20"), Decimal("-161.20")])

        self.assertEqual(values.extraction_status, "deductible_only")
        self.assertEqual(values.output_vat, Decimal("0.00"))
        self.assertEqual(values.net_local_input_base, Decimal("776.66"))
        self.assertEqual(values.net_local_input_vat, Decimal("161.20"))

    def test_reverse_charge_sequence_nets_output_from_deductible_vat(self):
        values = values_from_monetary_sequence(
            [
                Decimal("701.80"),
                Decimal("147.38"),
                Decimal("2254.83"),
                Decimal("473.52"),
                Decimal("-326.14"),
            ]
        )

        self.assertEqual(values.extraction_status, "output_and_deductible")
        self.assertEqual(values.output_base, Decimal("701.80"))
        self.assertEqual(values.output_vat, Decimal("147.38"))
        self.assertEqual(values.net_local_input_base, Decimal("1553.03"))
        self.assertEqual(values.net_local_input_vat, Decimal("326.14"))

    def test_reverse_charge_only_sequence_sums_deductible_bases(self):
        values = values_from_monetary_sequence(
            [
                Decimal("1000.00"),  # 10: reverse-charge services base
                Decimal("210.00"),  # 27: total output VAT
                Decimal("400.00"),  # 28: domestic deductible base
                Decimal("1000.00"),  # 36: reverse-charge deductible base
                Decimal("294.00"),  # 45: total deductible VAT
                Decimal("-84.00"),  # 46/71: result
            ]
        )

        self.assertEqual(values.extraction_status, "reverse_charge_services")
        self.assertEqual(values.output_base, Decimal("1000.00"))
        self.assertEqual(values.output_vat, Decimal("210.00"))
        self.assertEqual(values.deductible_base, Decimal("1400.00"))
        self.assertEqual(values.deductible_vat, Decimal("294.00"))
        self.assertEqual(values.result, Decimal("-84.00"))
        self.assertEqual(values.net_local_input_base, Decimal("400.00"))
        self.assertEqual(values.net_local_input_vat, Decimal("84.00"))

    def test_reverse_charge_with_other_output_sequence_sums_both_bases(self):
        values = values_from_monetary_sequence(
            [
                Decimal("1000.00"),  # 10: reverse-charge services base
                Decimal("200.00"),  # 12: other reverse-charge base
                Decimal("252.00"),  # 27: total output VAT
                Decimal("600.00"),  # 28: domestic deductible base
                Decimal("1000.00"),  # 36: reverse-charge deductible base
                Decimal("336.00"),  # 45: total deductible VAT
                Decimal("-84.00"),  # 46/71: result
            ]
        )

        self.assertEqual(values.extraction_status, "reverse_charge_with_other_output")
        self.assertEqual(values.output_base, Decimal("1200.00"))
        self.assertEqual(values.output_vat, Decimal("252.00"))
        self.assertEqual(values.deductible_base, Decimal("1600.00"))
        self.assertEqual(values.deductible_vat, Decimal("336.00"))
        self.assertEqual(values.result, Decimal("-84.00"))
        self.assertEqual(values.net_local_input_base, Decimal("400.00"))
        self.assertEqual(values.net_local_input_vat, Decimal("84.00"))

    def test_raw_vat_bearing_totals_ignore_usd_and_zero_vat_rows(self):
        rows = [
            RawXoloExpense(
                date=date(2025, 4, 1),
                recipient="Xolo",
                expense_type="Professional expenses",
                number="INV",
                amount_original=Decimal("121.00"),
                currency="EUR",
                subtotal_amount=Decimal("100.00"),
            ),
            RawXoloExpense(
                date=date(2025, 4, 2),
                recipient="Reverse charge supplier",
                expense_type="Professional expenses",
                number="RC",
                amount_original=Decimal("50.00"),
                currency="EUR",
                subtotal_amount=Decimal("50.00"),
            ),
            RawXoloExpense(
                date=date(2025, 4, 3),
                recipient="USD supplier",
                expense_type="Professional expenses",
                number="USD",
                amount_original=Decimal("20.00"),
                currency="USD",
                subtotal_amount=Decimal("20.00"),
            ),
        ]

        totals = _raw_vat_bearing_totals_by_period(rows)

        self.assertEqual(totals["2025-Q2"]["base"], Decimal("100.00"))
        self.assertEqual(totals["2025-Q2"]["vat"], Decimal("21.00"))

    def test_csv_raw_totals_use_taxable_base_for_exempt_base_rows(self):
        csv_text = (
            "xolo_url,xolo_id,recipient,type,number,date,payment_date,amount_text,amount_original,currency,"
            "gross_amount,match_amount,subtotal_amount,vat_amount,vat_percentages,irpf_amount,irpf_percentage,status\n"
            ',1,Synthetic Party 013,Professional expenses,SYNTH-DOCUMENT-033,2026-03-31,,"€99,08",99.08,EUR,99.08,99.08,82.67,16.41,21,0,0,UNPAID\n'
            ',2,Xolo Business Spain,Professional expenses,INV,2026-03-01,,"€71,39",71.39,EUR,71.39,71.39,59.00,12.39,21,0,0,UNPAID\n'
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.csv"
            path.write_text(csv_text, encoding="utf-8")

            totals = _raw_vat_bearing_totals_from_csv(path)

        self.assertEqual(totals["2026-Q1"]["vat"], Decimal("28.80"))
        self.assertEqual(totals["2026-Q1"]["base"], Decimal("137.14"))


if __name__ == "__main__":
    unittest.main()
