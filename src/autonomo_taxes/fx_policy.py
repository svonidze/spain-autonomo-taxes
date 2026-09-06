from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .fx_reference import ECBRateResult, FXReferenceError
from .money import cents


ALLOWED_PRODUCTION_SOURCES = {"ecb", "banco_de_espana", "actual_settlement", "xolo_recorded"}
# Rates labeled with these sources claim to be the official fixing, so they can
# be checked against the ECB observation; settlement and recorded rates
# legitimately differ from it.
OFFICIAL_REFERENCE_SOURCES = frozenset({"ecb", "banco_de_espana"})
# A pasted units-per-EUR quote reproduces the published figure almost exactly,
# while an official rate for the right day never drifts 5% from the ECB fixing.
INVERTED_QUOTE_TOLERANCE = Decimal("0.005")
REFERENCE_DEVIATION_TOLERANCE = Decimal("0.05")
XOLO_RECORDED_PRODUCTION_THROUGH = date(2026, 6, 30)

ReferenceLookup = Callable[[str, date], ECBRateResult]


class FXRateDirectionError(ValueError):
    """Raised when a manual rate is the units-per-EUR quote instead of EUR per unit."""


class FXRateDeviationError(ValueError):
    """Raised when a manual official rate is too far from the ECB reference."""


@dataclass(frozen=True)
class ManualRateCheck:
    """Outcome of comparing a manual rate with the ECB reference for its date."""

    status: str  # "exempt" | "verified" | "unverified" | "unavailable"
    detail: str = ""


@dataclass(frozen=True)
class FXRate:
    currency: str
    rate_date: date
    eur_per_unit: Decimal
    source: str
    rule_version: str

    def __post_init__(self) -> None:
        if self.currency.upper() == "EUR":
            raise ValueError("EUR does not need an FX rate")
        if self.eur_per_unit <= 0:
            raise ValueError("FX rate must be positive")
        if not self.rule_version.strip():
            raise ValueError("FX rate requires a rule version")


def convert_to_eur(
    amount: Decimal,
    rate: FXRate,
    *,
    mode: str = "production",
) -> Decimal:
    if mode not in {"production", "verify_history"}:
        raise ValueError(f"Unsupported FX mode: {mode}")
    if rate.source == "target_derived" and mode != "verify_history":
        raise ValueError("Target-derived FX is restricted to verify_history")
    if mode == "production" and rate.source not in ALLOWED_PRODUCTION_SOURCES:
        raise ValueError(f"FX source {rate.source!r} is not allowed in production")
    if (
        mode == "production"
        and rate.source == "xolo_recorded"
        and rate.rate_date > XOLO_RECORDED_PRODUCTION_THROUGH
    ):
        raise ValueError(
            "FX source 'xolo_recorded' is not allowed after "
            f"{XOLO_RECORDED_PRODUCTION_THROUGH.isoformat()}"
        )
    return cents(amount * rate.eur_per_unit)


def check_manual_rate(
    rate: Decimal,
    *,
    currency: str,
    rate_date: date,
    rate_source: str,
    reference_lookup: ReferenceLookup,
    allow_unverified: bool = False,
) -> ManualRateCheck:
    """Compare a manual EUR-per-unit rate with the ECB reference for its date.

    An inverted quote is always refused. Any other rate outside the deviation
    tolerance is refused unless ``allow_unverified`` is set, in which case the
    returned detail is the note to record next to the rate. A failed lookup
    never blocks: offline use only loses the check.
    """

    if rate <= 0:
        raise ValueError("FX rate must be positive")
    if rate_source not in OFFICIAL_REFERENCE_SOURCES:
        return ManualRateCheck("exempt")
    try:
        observation = reference_lookup(currency, rate_date).observation
    except FXReferenceError as exc:
        return ManualRateCheck(
            "unavailable",
            f"FX rate {rate} for {currency} on {rate_date.isoformat()} could not be "
            f"verified against the ECB reference: {exc}",
        )
    expected = observation.eur_per_unit
    reference = (
        f"ECB {observation.rate_date.isoformat()}: {observation.units_per_eur} "
        f"{currency} per EUR = {expected:.8f} EUR per {currency}"
    )
    deviation = abs(rate / expected - 1)
    if deviation <= REFERENCE_DEVIATION_TOLERANCE:
        return ManualRateCheck("verified", reference)
    if abs(rate / observation.units_per_eur - 1) < INVERTED_QUOTE_TOLERANCE:
        raise FXRateDirectionError(
            f"FX rate {rate} for {currency} looks like a units-per-EUR quote; the ledger "
            f"stores EUR per unit ({reference}); expected EUR-per-unit close to "
            f"{expected:.8f}"
        )
    percent = (deviation * 100).quantize(Decimal("0.01"))
    if not allow_unverified:
        raise FXRateDeviationError(
            f"FX rate {rate} for {currency} deviates {percent}% from the ECB reference "
            f"({reference})"
        )
    return ManualRateCheck(
        "unverified",
        f"rate not verified against the ECB reference: deviates {percent}% from "
        f"{expected:.8f} EUR per {currency} on {observation.rate_date.isoformat()}",
    )
