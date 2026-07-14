from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .money import cents


ALLOWED_PRODUCTION_SOURCES = {"ecb", "banco_de_espana", "actual_settlement", "xolo_recorded"}


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
    return cents(amount * rate.eur_per_unit)
