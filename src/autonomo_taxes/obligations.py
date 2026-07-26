from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from .tax_rules import (
    ALL_FORM_CODES,
    FORM_RULES,
    filed_form_codes_from_values,
)


ObligationStatus = Literal["due", "not_due", "unknown"]

MODEL347_THRESHOLD_EUR = Decimal("3005.06")
FOREIGN_REPORTING_THRESHOLD_EUR = Decimal("50000.00")
FOREIGN_REPEAT_REPORTING_DELTA_EUR = Decimal("20000.00")
WEALTH_TAX_ALWAYS_FILE_THRESHOLD_EUR = Decimal("2000000.00")


@dataclass(frozen=True)
class CounterpartyFact:
    name: str
    annual_total_eur: Decimal | None = None
    is_intracommunity: bool | None = None
    rent_subject_to_withholding: bool | None = None
    payment_subject_to_withholding: bool | None = None
    employee_compensation: bool | None = None
    counts_for_modelo347: bool | None = None
    nonresident_income_reportable: bool | None = None
    country_code: str | None = None
    has_reviewed_payment_in_period: bool | None = None
    is_nonresident_individual_professional: bool | None = None
    treaty_relief_applies: bool | None = None
    treaty_residence_evidence_confirmed: bool | None = None
    expense_deductible: bool | None = None


@dataclass(frozen=True)
class ActivityFact:
    tax_year: int
    is_spanish_tax_resident: bool | None = None
    has_self_employment_activity: bool | None = None
    income_tax_method_direct_estimation: bool | None = None
    only_professional_income: bool | None = None
    professional_income_withholding_ratio: Decimal | None = None
    vat_taxable_activity: bool | None = None
    only_vat_exempt_activity: bool | None = None
    has_intracommunity_transactions: bool | None = None
    counterparties_complete: bool | None = None
    has_employees: bool | None = None
    pays_professionals_subject_to_withholding: bool | None = None
    rents_urban_property_subject_to_withholding: bool | None = None
    pays_nonresident_income_reportable: bool | None = None
    uses_sii_for_vat: bool | None = None
    annual_vat_summary_exempt: bool | None = None
    required_to_file_income_tax_return: bool | None = None
    required_to_file_wealth_tax: bool | None = None
    net_assets_eur: Decimal | None = None
    foreign_accounts_value_eur: Decimal | None = None
    foreign_securities_value_eur: Decimal | None = None
    foreign_real_estate_value_eur: Decimal | None = None
    previously_reported_foreign_assets: bool | None = None
    foreign_assets_increase_since_last_report_eur: Decimal | None = None
    foreign_crypto_value_eur: Decimal | None = None
    previously_reported_foreign_crypto: bool | None = None
    foreign_crypto_increase_since_last_report_eur: Decimal | None = None
    nonresident_professional_payments_reviewed: bool | None = None


@dataclass(frozen=True)
class FormObligation:
    form_code: str
    status: ObligationStatus
    explanation: str
    source_citation: str
    blocking: bool
    filed: bool | None


def detect_obligations(
    activity: ActivityFact,
    counterparties: list[CounterpartyFact] | tuple[CounterpartyFact, ...] = (),
    filed_forms: list[str] | tuple[str, ...] | set[str] = (),
) -> dict[str, FormObligation]:
    filed_codes = filed_form_codes_from_values(set(str(value) for value in filed_forms))

    base_status: dict[str, tuple[ObligationStatus, str]] = {}
    base_status["130"] = _modelo130(activity)
    base_status["303"] = _modelo303(activity)
    base_status["349"] = _modelo349(activity, counterparties)
    base_status["390"] = _modelo390(activity, base_status["303"][0])
    base_status["347"] = _modelo347(activity, counterparties)
    base_status["111"] = _modelo111(activity, counterparties)
    base_status["190"] = _annual_from_base("190", base_status["111"][0], "Modelo 190 follows withholding payments reported via Modelo 111.")
    base_status["115"] = _modelo115(activity, counterparties)
    base_status["180"] = _annual_from_base("180", base_status["115"][0], "Modelo 180 follows rent withholding reported via Modelo 115.")
    base_status["216"] = _modelo216(activity, counterparties)
    base_status["296"] = _modelo296(activity, base_status["216"][0])
    base_status["100"] = _modelo100(activity)
    base_status["714"] = _modelo714(activity)
    base_status["720"] = _modelo720(activity)
    base_status["721"] = _modelo721(activity)

    results: dict[str, FormObligation] = {}
    for code in ALL_FORM_CODES:
        status, explanation = base_status[code]
        filed = code in filed_codes if status == "due" else (code in filed_codes if code in filed_codes else None)
        blocking = status == "unknown" or (status == "due" and code not in filed_codes)
        if status == "due":
            if code in filed_codes:
                explanation = f"{explanation} Filing evidence was provided for Modelo {code}."
            else:
                explanation = f"{explanation} No filing evidence was provided for Modelo {code}."
        results[code] = FormObligation(
            form_code=code,
            status=status,
            explanation=explanation,
            source_citation=FORM_RULES[code].source_citation,
            blocking=blocking,
            filed=filed,
        )
    return results


def _modelo130(activity: ActivityFact) -> tuple[ObligationStatus, str]:
    if activity.is_spanish_tax_resident is False:
        return "not_due", "Non-resident facts do not support an IRPF Modelo 130 filing duty."
    if activity.has_self_employment_activity is False:
        return "not_due", "No self-employment activity was provided."
    if activity.income_tax_method_direct_estimation is False:
        return "not_due", "Modelo 130 applies to direct-estimation activity; the supplied facts say the activity is not in direct estimation."
    if activity.has_self_employment_activity is None or activity.income_tax_method_direct_estimation is None:
        return "unknown", "Modelo 130 depends on self-employment and direct-estimation facts that were not provided."
    if activity.only_professional_income is None:
        return "unknown", "Modelo 130 may depend on whether the activity is professional-only and on the 70% withholding exception."
    if activity.only_professional_income:
        if activity.professional_income_withholding_ratio is None:
            return "unknown", "Professional-only activity may be exempt from Modelo 130 when at least 70% of income bears withholding, but the ratio is unknown."
        if activity.professional_income_withholding_ratio >= Decimal("0.70"):
            return "not_due", "Professional-only direct-estimation activity meets or exceeds the 70% withholding exception."
    return "due", "Direct-estimation self-employment facts indicate a Modelo 130 payment obligation."


def _modelo303(activity: ActivityFact) -> tuple[ObligationStatus, str]:
    if activity.has_self_employment_activity is False:
        return "not_due", "No self-employment activity was provided."
    if activity.vat_taxable_activity is False:
        return "not_due", "The supplied facts do not show VAT-taxable activity."
    if activity.vat_taxable_activity is None:
        return "unknown", "Modelo 303 depends on whether the activity is subject to Spanish VAT."
    if activity.only_vat_exempt_activity is True:
        return "not_due", "The supplied facts show only VAT-exempt activity."
    if activity.only_vat_exempt_activity is None:
        return "unknown", "VAT activity exists, but it is unclear whether the activity is fully exempt from Modelo 303 filing."
    return "due", "VAT-taxable non-exempt activity indicates a Modelo 303 obligation."


def _modelo349(
    activity: ActivityFact,
    counterparties: list[CounterpartyFact] | tuple[CounterpartyFact, ...],
) -> tuple[ObligationStatus, str]:
    if activity.has_intracommunity_transactions is True:
        return "due", "Intracommunity transaction facts indicate a Modelo 349 obligation."
    if activity.has_intracommunity_transactions is False:
        return "not_due", "The supplied facts say there were no intracommunity transactions."
    if any(counterparty.is_intracommunity is True for counterparty in counterparties):
        return "due", "Counterparty facts include intracommunity transactions."
    if activity.counterparties_complete is True:
        if not counterparties:
            return "not_due", "Complete counterparty facts were provided and no intracommunity transactions were listed."
        if all(counterparty.is_intracommunity is False for counterparty in counterparties):
            return "not_due", "Complete counterparty facts show no intracommunity transactions."
    return "unknown", "Modelo 349 depends on intracommunity transaction facts that were not fully provided."


def _modelo390(activity: ActivityFact, modelo303_status: ObligationStatus) -> tuple[ObligationStatus, str]:
    if activity.annual_vat_summary_exempt is True:
        return "not_due", "The supplied facts explicitly mark the taxpayer as exempt from Modelo 390."
    if activity.annual_vat_summary_exempt is False:
        return "due", "The supplied facts explicitly require the annual VAT summary."
    if modelo303_status == "not_due":
        return "not_due", "Without a Modelo 303 VAT filing obligation, Modelo 390 is also not due."
    if modelo303_status == "unknown":
        return "unknown", "Modelo 390 depends on the unresolved VAT filing status."
    if activity.uses_sii_for_vat is True:
        return "not_due", "SII facts indicate the annual VAT summary is not due."
    if activity.uses_sii_for_vat is None:
        return "unknown", "Modelo 390 depends on whether the taxpayer is exempt, including SII status, and that fact was not provided."
    return "due", "Quarterly VAT filing facts indicate an annual Modelo 390 summary obligation."


def _modelo347(
    activity: ActivityFact,
    counterparties: list[CounterpartyFact] | tuple[CounterpartyFact, ...],
) -> tuple[ObligationStatus, str]:
    if activity.has_self_employment_activity is False:
        return "not_due", "No business or professional activity was provided."
    if activity.counterparties_complete is not True:
        return "unknown", "Modelo 347 needs a complete counterparty view to confirm whether any annual relationship exceeded 3,005.06 EUR."
    if not counterparties:
        return "not_due", "Complete counterparty facts were provided and no reportable counterparties were listed."

    qualifying_totals: list[Decimal] = []
    for counterparty in counterparties:
        if _excluded_from_modelo347(counterparty):
            continue
        if counterparty.annual_total_eur is None:
            return "unknown", f"Counterparty {counterparty.name} is missing an annual total needed for Modelo 347."
        qualifying_totals.append(counterparty.annual_total_eur)
    if any(total > MODEL347_THRESHOLD_EUR for total in qualifying_totals):
        return "due", "At least one reportable counterparty exceeds the 3,005.06 EUR Modelo 347 threshold."
    return "not_due", "Complete counterparty facts show no reportable third party above 3,005.06 EUR."


def _modelo111(
    activity: ActivityFact,
    counterparties: list[CounterpartyFact] | tuple[CounterpartyFact, ...],
) -> tuple[ObligationStatus, str]:
    if activity.has_employees is True or activity.pays_professionals_subject_to_withholding is True:
        return "due", "Withholding facts for workers or professionals indicate a Modelo 111 obligation."
    if any(
        counterparty.payment_subject_to_withholding is True or counterparty.employee_compensation is True
        for counterparty in counterparties
    ):
        return "due", "Counterparty facts include payments subject to withholding."
    if activity.has_employees is False and activity.pays_professionals_subject_to_withholding is False:
        return "not_due", "The supplied facts show no employment or professional payments subject to withholding."
    return "unknown", "Modelo 111 depends on withholding-payment facts that were not fully provided."


def _modelo115(
    activity: ActivityFact,
    counterparties: list[CounterpartyFact] | tuple[CounterpartyFact, ...],
) -> tuple[ObligationStatus, str]:
    if activity.rents_urban_property_subject_to_withholding is True:
        return "due", "Urban-property rent facts subject to withholding indicate a Modelo 115 obligation."
    if any(counterparty.rent_subject_to_withholding is True for counterparty in counterparties):
        return "due", "Counterparty facts include rent subject to withholding."
    if activity.rents_urban_property_subject_to_withholding is False:
        return "not_due", "The supplied facts show no urban-property rent subject to withholding."
    return "unknown", "Modelo 115 depends on rent-withholding facts that were not fully provided."


def _modelo216(
    activity: ActivityFact,
    counterparties: list[CounterpartyFact] | tuple[CounterpartyFact, ...],
) -> tuple[ObligationStatus, str]:
    if activity.pays_nonresident_income_reportable is True:
        return "due", "Payments to non-residents reportable under IRNR indicate a Modelo 216 obligation."

    reportable = [
        counterparty
        for counterparty in counterparties
        if counterparty.nonresident_income_reportable is True
        and counterparty.has_reviewed_payment_in_period is not False
    ]
    treaty_relief_payments = [
        counterparty
        for counterparty in reportable
        if counterparty.has_reviewed_payment_in_period is True
        and counterparty.is_nonresident_individual_professional is True
        and counterparty.treaty_relief_applies is True
    ]
    if any(
        counterparty.treaty_residence_evidence_confirmed is not True
        for counterparty in treaty_relief_payments
    ):
        return (
            "unknown",
            "The payment is reportable under IRNR and treaty relief was selected, but the residence evidence "
            "supporting that relief has not been confirmed.",
        )
    if reportable:
        if treaty_relief_payments and len(treaty_relief_payments) == len(reportable):
            return (
                "due",
                "Reviewed treaty-exempt payments are reportable under IRNR with zero withholding, requiring "
                "a negative Modelo 216 and inclusion in annual Modelo 296.",
            )
        return "due", "Counterparty facts include non-resident income reportable under IRNR."

    reviewed_nonresident_professional_payments = [
        counterparty
        for counterparty in counterparties
        if counterparty.has_reviewed_payment_in_period is True
        and counterparty.is_nonresident_individual_professional is True
    ]
    if any(
        counterparty.nonresident_income_reportable is None
        for counterparty in reviewed_nonresident_professional_payments
    ):
        return (
            "unknown",
            "A reviewed current-period payment to a non-resident individual professional has unresolved IRNR "
            "reportability. This determination does not affect expense deductibility.",
        )
    if activity.pays_nonresident_income_reportable is False:
        return "not_due", "The supplied facts explicitly show no non-resident income reportable under IRNR."
    if activity.nonresident_professional_payments_reviewed is True:
        return (
            "not_due",
            "The current-period payment review found no non-resident individual professional payment "
            "reportable under IRNR.",
        )
    if activity.counterparties_complete is True and counterparties:
        if all(counterparty.nonresident_income_reportable is False for counterparty in counterparties):
            return "not_due", "Complete counterparty facts show no non-resident income reportable under IRNR."
    return (
        "unknown",
        "Modelo 216 depends on whether any reviewed payment is income reportable under IRNR. "
        "Supplier identity, country, and expense treatment alone do not determine that.",
    )


def _modelo296(
    activity: ActivityFact,
    modelo216_status: ObligationStatus,
) -> tuple[ObligationStatus, str]:
    if (
        modelo216_status == "not_due"
        and activity.nonresident_professional_payments_reviewed is True
        and activity.pays_nonresident_income_reportable is None
    ):
        return (
            "unknown",
            "A completed current-period review can establish that Modelo 216 is not due for that period, "
            "but it does not retroactively determine the annual Modelo 296 obligation.",
        )
    return _annual_from_base(
        "296",
        modelo216_status,
        "Modelo 296 follows non-resident income reported via Modelo 216.",
    )


def _modelo100(activity: ActivityFact) -> tuple[ObligationStatus, str]:
    if activity.required_to_file_income_tax_return is True:
        return "due", "The supplied facts explicitly require a Modelo 100 return."
    if activity.required_to_file_income_tax_return is False:
        return "not_due", "The supplied facts explicitly say no Modelo 100 return is required."
    if activity.is_spanish_tax_resident is False:
        return "not_due", "Non-resident facts do not support a resident IRPF Modelo 100 filing duty."
    if activity.is_spanish_tax_resident is True and activity.has_self_employment_activity is True:
        return "due", "Spanish-resident self-employment facts indicate an annual Modelo 100 filing obligation."
    return "unknown", "Modelo 100 depends on residence and annual-return facts that were not fully provided."


def _modelo714(activity: ActivityFact) -> tuple[ObligationStatus, str]:
    if activity.required_to_file_wealth_tax is True:
        return "due", "The supplied facts explicitly require Modelo 714."
    if activity.required_to_file_wealth_tax is False:
        return "not_due", "The supplied facts explicitly say Modelo 714 is not required."
    if activity.net_assets_eur is not None and activity.net_assets_eur > WEALTH_TAX_ALWAYS_FILE_THRESHOLD_EUR:
        return "due", "Net assets exceed 2,000,000 EUR, which is sufficient to require checking Modelo 714 filing."
    return "unknown", "Modelo 714 depends on wealth-tax facts and regional thresholds that were not fully provided."


def _modelo720(activity: ActivityFact) -> tuple[ObligationStatus, str]:
    if activity.is_spanish_tax_resident is False:
        return "not_due", "Non-resident facts do not support a Modelo 720 reporting duty."
    values = (
        activity.foreign_accounts_value_eur,
        activity.foreign_securities_value_eur,
        activity.foreign_real_estate_value_eur,
    )
    return _foreign_information_obligation(
        values=values,
        previously_reported=activity.previously_reported_foreign_assets,
        increase_since_last_report=activity.foreign_assets_increase_since_last_report_eur,
        description="foreign non-crypto assets",
    )


def _modelo721(activity: ActivityFact) -> tuple[ObligationStatus, str]:
    if activity.tax_year < 2023:
        return "not_due", "Modelo 721 does not apply before tax year 2023."
    if activity.is_spanish_tax_resident is False:
        return "not_due", "Non-resident facts do not support a Modelo 721 reporting duty."
    if activity.foreign_crypto_value_eur is None:
        return "unknown", "Modelo 721 depends on foreign-crypto custody facts that were not fully provided."
    return _foreign_information_obligation(
        values=(activity.foreign_crypto_value_eur,),
        previously_reported=activity.previously_reported_foreign_crypto,
        increase_since_last_report=activity.foreign_crypto_increase_since_last_report_eur,
        description="foreign virtual currency",
    )


def _annual_from_base(form_code: str, base_status: ObligationStatus, explanation: str) -> tuple[ObligationStatus, str]:
    if base_status == "due":
        return "due", explanation
    if base_status == "not_due":
        return "not_due", explanation
    return "unknown", explanation


def _excluded_from_modelo347(counterparty: CounterpartyFact) -> bool:
    if counterparty.counts_for_modelo347 is False:
        return True
    if counterparty.is_intracommunity is True:
        return True
    if counterparty.rent_subject_to_withholding is True:
        return True
    if counterparty.payment_subject_to_withholding is True:
        return True
    if counterparty.employee_compensation is True:
        return True
    return False


def _foreign_information_obligation(
    values: tuple[Decimal | None, ...],
    previously_reported: bool | None,
    increase_since_last_report: Decimal | None,
    description: str,
) -> tuple[ObligationStatus, str]:
    known_values = [value for value in values if value is not None]
    if not known_values:
        return "unknown", f"The filing decision depends on {description} values that were not provided."
    if any(value > FOREIGN_REPORTING_THRESHOLD_EUR for value in known_values):
        if previously_reported is False:
            return "due", f"{description.capitalize()} exceed the 50,000 EUR first-reporting threshold."
        if previously_reported is True:
            if increase_since_last_report is None:
                return "unknown", f"The repeat-reporting decision for {description} needs the increase since the last filing."
            if increase_since_last_report > FOREIGN_REPEAT_REPORTING_DELTA_EUR:
                return "due", f"{description.capitalize()} increased by more than 20,000 EUR since the last filing."
            return "not_due", f"{description.capitalize()} stay above 50,000 EUR but the reported increase does not exceed 20,000 EUR."
        return "unknown", f"The first-versus-repeat filing status for {description} was not provided."
    if len(known_values) == len(values):
        return "not_due", f"Known {description} values do not exceed the 50,000 EUR reporting threshold."
    return "unknown", f"Some {description} values are missing, so a threshold determination cannot be made safely."
