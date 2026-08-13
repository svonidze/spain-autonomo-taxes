from decimal import Decimal
import unittest

from autonomo_taxes.obligations import (
    ActivityFact,
    CounterpartyFact,
    detect_obligations,
)


class ObligationDetectionTests(unittest.TestCase):
    def test_detects_due_not_due_unknown_and_blocking(self):
        activity = ActivityFact(
            tax_year=2026,
            is_spanish_tax_resident=True,
            has_self_employment_activity=True,
            income_tax_method_direct_estimation=True,
            only_professional_income=True,
            professional_income_withholding_ratio=Decimal("0.50"),
            vat_taxable_activity=True,
            only_vat_exempt_activity=False,
            has_intracommunity_transactions=True,
            counterparties_complete=True,
            has_employees=False,
            pays_professionals_subject_to_withholding=True,
            rents_urban_property_subject_to_withholding=False,
            pays_nonresident_income_reportable=True,
            uses_sii_for_vat=False,
            net_assets_eur=Decimal("2500000.00"),
            foreign_accounts_value_eur=Decimal("60000.00"),
            foreign_securities_value_eur=Decimal("0.00"),
            foreign_real_estate_value_eur=Decimal("0.00"),
            previously_reported_foreign_assets=False,
            foreign_crypto_value_eur=Decimal("70000.00"),
            previously_reported_foreign_crypto=False,
        )
        counterparties = [
            CounterpartyFact(name="Main client", annual_total_eur=Decimal("4000.00")),
            CounterpartyFact(name="Advisor", annual_total_eur=Decimal("1200.00"), payment_subject_to_withholding=True),
        ]

        obligations = detect_obligations(
            activity,
            counterparties=counterparties,
            filed_forms={"M130 1T 2026.pdf", "303", "modelo111_report"},
        )

        self.assertEqual(obligations["130"].status, "due")
        self.assertFalse(obligations["130"].blocking)
        self.assertEqual(obligations["303"].status, "due")
        self.assertFalse(obligations["303"].blocking)
        self.assertEqual(obligations["349"].status, "due")
        self.assertTrue(obligations["349"].blocking)
        self.assertEqual(obligations["390"].status, "due")
        self.assertTrue(obligations["390"].blocking)
        self.assertEqual(obligations["347"].status, "due")
        self.assertTrue(obligations["347"].blocking)
        self.assertEqual(obligations["111"].status, "due")
        self.assertFalse(obligations["111"].blocking)
        self.assertEqual(obligations["190"].status, "due")
        self.assertTrue(obligations["190"].blocking)
        self.assertEqual(obligations["115"].status, "not_due")
        self.assertFalse(obligations["115"].blocking)
        self.assertEqual(obligations["180"].status, "not_due")
        self.assertFalse(obligations["180"].blocking)
        self.assertEqual(obligations["216"].status, "due")
        self.assertTrue(obligations["216"].blocking)
        self.assertEqual(obligations["296"].status, "due")
        self.assertTrue(obligations["296"].blocking)
        self.assertEqual(obligations["100"].status, "due")
        self.assertTrue(obligations["100"].blocking)
        self.assertEqual(obligations["714"].status, "due")
        self.assertTrue(obligations["714"].blocking)
        self.assertEqual(obligations["720"].status, "due")
        self.assertTrue(obligations["720"].blocking)
        self.assertEqual(obligations["721"].status, "due")
        self.assertTrue(obligations["721"].blocking)
        self.assertIn("AEAT", obligations["721"].source_citation)

    def test_ambiguous_facts_stay_unknown(self):
        activity = ActivityFact(
            tax_year=2026,
            is_spanish_tax_resident=True,
            has_self_employment_activity=True,
            income_tax_method_direct_estimation=True,
            only_professional_income=None,
            vat_taxable_activity=None,
            counterparties_complete=None,
            has_employees=False,
            pays_professionals_subject_to_withholding=None,
            rents_urban_property_subject_to_withholding=None,
        )

        obligations = detect_obligations(activity)

        self.assertEqual(obligations["130"].status, "unknown")
        self.assertTrue(obligations["130"].blocking)
        self.assertEqual(obligations["303"].status, "unknown")
        self.assertTrue(obligations["303"].blocking)
        self.assertEqual(obligations["349"].status, "unknown")
        self.assertTrue(obligations["349"].blocking)
        self.assertEqual(obligations["111"].status, "unknown")
        self.assertTrue(obligations["111"].blocking)
        self.assertEqual(obligations["115"].status, "unknown")
        self.assertTrue(obligations["115"].blocking)
        self.assertEqual(obligations["216"].status, "unknown")
        self.assertTrue(obligations["216"].blocking)
        self.assertIn("reportable under IRNR", obligations["216"].explanation)
        self.assertNotIn("certificate", obligations["216"].explanation.lower())
        self.assertEqual(obligations["714"].status, "unknown")
        self.assertTrue(obligations["714"].blocking)

    def test_modelo721_is_not_due_before_2023(self):
        activity = ActivityFact(
            tax_year=2022,
            is_spanish_tax_resident=True,
            foreign_crypto_value_eur=Decimal("90000.00"),
            previously_reported_foreign_crypto=False,
        )

        obligations = detect_obligations(activity)

        self.assertEqual(obligations["721"].status, "not_due")
        self.assertFalse(obligations["721"].blocking)

    def test_modelo216_can_be_driven_by_reviewed_counterparty_facts(self):
        activity = ActivityFact(tax_year=2026, counterparties_complete=True)
        counterparties = [
            CounterpartyFact(
                name="Foreign contractor",
                nonresident_income_reportable=True,
            )
        ]

        obligations = detect_obligations(activity, counterparties)

        self.assertEqual(obligations["216"].status, "due")
        self.assertEqual(obligations["296"].status, "due")

    def test_legacy_period_without_new_review_facts_is_not_reclassified(self):
        activity = ActivityFact(tax_year=2026, counterparties_complete=True)
        counterparties = [
            CounterpartyFact(
                name="Georgian contractor",
                country_code="GE",
            )
        ]

        obligations = detect_obligations(activity, counterparties)

        self.assertEqual(obligations["216"].status, "unknown")
        self.assertEqual(obligations["296"].status, "unknown")
        self.assertTrue(obligations["216"].blocking)
        self.assertNotIn("certificate", obligations["216"].explanation.lower())

    def test_completed_current_period_review_without_relevant_payment_is_not_due(self):
        activity = ActivityFact(
            tax_year=2026,
            counterparties_complete=True,
            nonresident_professional_payments_reviewed=True,
        )
        counterparties = [
            CounterpartyFact(
                name="Foreign company",
                country_code="US",
                has_reviewed_payment_in_period=True,
                is_nonresident_individual_professional=False,
            )
        ]

        obligations = detect_obligations(activity, counterparties)

        self.assertEqual(obligations["216"].status, "not_due")
        self.assertEqual(obligations["296"].status, "unknown")
        self.assertTrue(obligations["296"].blocking)
        self.assertFalse(obligations["216"].blocking)

    def test_historical_reportable_counterparty_without_current_payment_is_not_currently_due(self):
        activity = ActivityFact(
            tax_year=2026,
            counterparties_complete=True,
            nonresident_professional_payments_reviewed=True,
        )
        counterparties = [
            CounterpartyFact(
                name="Historically reportable professional",
                country_code="GE",
                has_reviewed_payment_in_period=False,
                is_nonresident_individual_professional=True,
                nonresident_income_reportable=True,
            )
        ]

        obligations = detect_obligations(activity, counterparties)

        self.assertEqual(obligations["216"].status, "not_due")
        self.assertFalse(obligations["216"].blocking)
        self.assertEqual(obligations["296"].status, "unknown")
        self.assertTrue(obligations["296"].blocking)

    def test_reviewed_foreign_b2b_service_is_not_due_for_216_or_296(self):
        activity = ActivityFact(
            tax_year=2026,
            counterparties_complete=True,
            nonresident_professional_payments_reviewed=True,
            pays_nonresident_income_reportable=False,
        )
        counterparties = [
            CounterpartyFact(
                name="Reviewed foreign B2B developer",
                country_code="GE",
                has_reviewed_payment_in_period=True,
                is_nonresident_individual_professional=True,
                nonresident_income_reportable=False,
                expense_deductible=True,
            )
        ]

        obligations = detect_obligations(activity, counterparties)

        for code in ("216", "296"):
            self.assertEqual(obligations[code].status, "not_due")
            self.assertFalse(obligations[code].blocking)
            self.assertNotIn("certificate", obligations[code].explanation.lower())
            self.assertNotIn("treaty", obligations[code].explanation.lower())

    def test_new_unresolved_nonresident_professional_payment_blocks_modelo216(self):
        activity = ActivityFact(
            tax_year=2026,
            counterparties_complete=True,
            nonresident_professional_payments_reviewed=True,
        )
        counterparties = [
            CounterpartyFact(
                name="New foreign professional",
                country_code="GE",
                has_reviewed_payment_in_period=True,
                is_nonresident_individual_professional=True,
                nonresident_income_reportable=None,
                expense_deductible=True,
            )
        ]

        obligations = detect_obligations(activity, counterparties)

        self.assertEqual(obligations["216"].status, "unknown")
        self.assertTrue(obligations["216"].blocking)
        self.assertEqual(obligations["296"].status, "unknown")
        self.assertIn("reportability", obligations["216"].explanation)
        self.assertIn("does not affect expense deductibility", obligations["216"].explanation)

    def test_expense_deductibility_does_not_change_modelo216_gate(self):
        activity = ActivityFact(
            tax_year=2026,
            counterparties_complete=True,
            nonresident_professional_payments_reviewed=True,
        )

        statuses = set()
        for expense_deductible in (True, False):
            obligations = detect_obligations(
                activity,
                [
                    CounterpartyFact(
                        name="New foreign professional",
                        has_reviewed_payment_in_period=True,
                        is_nonresident_individual_professional=True,
                        nonresident_income_reportable=None,
                        expense_deductible=expense_deductible,
                    )
                ],
            )
            statuses.add(obligations["216"].status)

        self.assertEqual(statuses, {"unknown"})

    def test_treaty_evidence_is_ignored_until_reportability_and_relief_are_classified(self):
        activity = ActivityFact(
            tax_year=2026,
            counterparties_complete=True,
            nonresident_professional_payments_reviewed=True,
        )
        counterparties = [
            CounterpartyFact(
                name="New foreign professional",
                has_reviewed_payment_in_period=True,
                is_nonresident_individual_professional=True,
                nonresident_income_reportable=None,
                treaty_residence_evidence_confirmed=True,
            )
        ]

        obligations = detect_obligations(activity, counterparties)

        self.assertEqual(obligations["216"].status, "unknown")
        self.assertIn("reportability", obligations["216"].explanation)
        self.assertNotIn("residence evidence", obligations["216"].explanation.lower())

    def test_reportable_treaty_exempt_payment_requires_negative_216_and_annual_296(self):
        activity = ActivityFact(
            tax_year=2026,
            counterparties_complete=True,
            nonresident_professional_payments_reviewed=True,
        )
        counterparties = [
            CounterpartyFact(
                name="Treaty-exempt professional",
                has_reviewed_payment_in_period=True,
                is_nonresident_individual_professional=True,
                nonresident_income_reportable=True,
                treaty_relief_applies=True,
                treaty_residence_evidence_confirmed=True,
            )
        ]

        obligations = detect_obligations(activity, counterparties)

        self.assertEqual(obligations["216"].status, "due")
        self.assertIn("negative Modelo 216", obligations["216"].explanation)
        self.assertEqual(obligations["296"].status, "due")

    def test_treaty_evidence_becomes_relevant_only_when_relief_is_relied_on(self):
        activity = ActivityFact(
            tax_year=2026,
            counterparties_complete=True,
            nonresident_professional_payments_reviewed=True,
        )
        counterparties = [
            CounterpartyFact(
                name="Treaty-relief professional",
                has_reviewed_payment_in_period=True,
                is_nonresident_individual_professional=True,
                nonresident_income_reportable=True,
                treaty_relief_applies=True,
                treaty_residence_evidence_confirmed=None,
            )
        ]

        obligations = detect_obligations(activity, counterparties)

        self.assertEqual(obligations["216"].status, "unknown")
        self.assertTrue(obligations["216"].blocking)
        self.assertIn("residence evidence", obligations["216"].explanation.lower())


if __name__ == "__main__":
    unittest.main()
