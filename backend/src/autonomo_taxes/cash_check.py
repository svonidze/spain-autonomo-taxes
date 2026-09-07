from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from .money import cents


ZERO = Decimal("0.00")
PAYABLE_KEYS = {
    "130": "19",
    "303": "71",
    "111": "withholding",
    "115": "withholding",
    "216": "withholding",
}
NO_PAYMENT_FORMS = {"349"}


def build_cash_check(
    calculations: Mapping[str, Mapping[str, Any]],
    *,
    due_forms: set[str],
    available_eur: Decimal | None,
    buffer_eur: Decimal | None = None,
) -> dict[str, Any]:
    if available_eur is not None and available_eur < ZERO:
        raise ValueError("available_eur cannot be negative")
    if buffer_eur is not None and buffer_eur < ZERO:
        raise ValueError("buffer_eur cannot be negative")

    forms: list[dict[str, Any]] = []
    unsupported: list[str] = []
    calculation_blocked: list[str] = []
    required = ZERO
    for form in sorted(due_forms):
        if form in NO_PAYMENT_FORMS:
            forms.append(
                {
                    "form": form,
                    "payable_eur": ZERO,
                    "status": "information_return_no_payment",
                }
            )
            continue
        calculation = calculations.get(form)
        if calculation is None or calculation.get("blocked"):
            calculation_blocked.append(form)
            forms.append(
                {
                    "form": form,
                    "payable_eur": None,
                    "status": "calculation_blocked",
                }
            )
            continue
        key = PAYABLE_KEYS.get(form)
        if key is None:
            unsupported.append(form)
            forms.append(
                {"form": form, "payable_eur": None, "status": "unsupported_payable_mapping"}
            )
            continue
        values = calculation.get("values") or {}
        payable = cents(max(Decimal(str(values.get(key, "0"))), ZERO))
        required = cents(required + payable)
        forms.append(
            {
                "form": form,
                "payable_casilla": key,
                "payable_eur": payable,
                "status": "payment_due" if payable else "no_payment",
            }
        )

    default_buffer = (
        ZERO
        if required == ZERO
        else max(Decimal("100.00"), cents(required * Decimal("0.10")))
    )
    effective_buffer = cents(default_buffer if buffer_eur is None else buffer_eur)
    recommended_reserve = cents(required + effective_buffer)

    if available_eur is None:
        if required == ZERO:
            status = "not_required"
            tax_payment_ready = True
            buffer_ready = True
            tax_shortfall = ZERO
            reserve_shortfall = ZERO
        else:
            status = "not_checked"
            tax_payment_ready = False
            buffer_ready = False
            tax_shortfall = None
            reserve_shortfall = None
    else:
        available_eur = cents(available_eur)
        tax_shortfall = cents(max(required - available_eur, ZERO))
        reserve_shortfall = cents(max(recommended_reserve - available_eur, ZERO))
        tax_payment_ready = tax_shortfall == ZERO
        buffer_ready = reserve_shortfall == ZERO
        if not tax_payment_ready:
            status = "shortfall"
        elif not buffer_ready:
            status = "tax_covered_buffer_low"
        else:
            status = "sufficient"

    if calculation_blocked:
        status = "calculation_blocked"
    elif unsupported:
        status = "unsupported_form"

    return {
        "status": status,
        "tax_payment_ready": (
            tax_payment_ready and not unsupported and not calculation_blocked
        ),
        "recommended_buffer_ready": (
            buffer_ready and not unsupported and not calculation_blocked
        ),
        "required_tax_eur": required,
        "buffer_eur": effective_buffer,
        "recommended_reserve_eur": recommended_reserve,
        "available_eur": available_eur,
        "tax_shortfall_eur": tax_shortfall,
        "reserve_shortfall_eur": reserve_shortfall,
        "forms": forms,
        "unsupported_forms": unsupported,
        "calculation_blocked_forms": calculation_blocked,
    }
