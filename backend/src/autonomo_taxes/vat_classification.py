"""Separate reviewed IVA investment goods from IRPF asset ownership."""

from collections.abc import Iterable

SERVICE_EXPENSE_CODES = frozenset({
    "eu_service_expense", "non_eu_service_expense", "import_service_expense",
    "professional_withholding", "rent_withholding",
})

LEGACY_VAT_CLASSIFICATION_WARNING = (
    "Unreviewed IVA investment classification: legacy asset links were used. "
    "Review vat_investment_good before relying on these categories for filing."
)


def is_vat_investment_good(
    reviewed: bool | int | None, *, legacy_asset: bool, tax_code: str = ""
) -> bool:
    """Honor an explicit false; retain the historical rule only for null."""
    if reviewed is None:
        return legacy_asset
    if type(reviewed) not in (bool, int) or reviewed not in (0, 1):
        raise ValueError("vat_investment_good must be boolean or null")
    if reviewed and tax_code in SERVICE_EXPENSE_CODES:
        raise ValueError("vat_investment_good cannot be true for a service expense")
    return bool(reviewed)


def effective_vat_investment_choice(values: Iterable[bool | int | None]) -> bool | None:
    """Resolve the decision across treatments without masking contradictions."""
    choices = {
        is_vat_investment_good(value, legacy_asset=False)
        for value in values if value is not None
    }
    if len(choices) > 1:
        raise ValueError("Conflicting vat_investment_good values")
    return next(iter(choices), None)
