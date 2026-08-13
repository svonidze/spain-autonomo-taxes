from datetime import date
from decimal import Decimal

import pytest

from autonomo_taxes.fx_policy import FXRate, convert_to_eur


def test_official_rate_converts_and_rounds_to_cents() -> None:
    rate = FXRate("USD", date(2026, 7, 14), Decimal("0.85680746"), "ecb", "fx-2026-v1")
    assert convert_to_eur(Decimal("100"), rate) == Decimal("85.68")


def test_target_derived_rate_is_forbidden_in_production() -> None:
    rate = FXRate("USD", date(2026, 6, 30), Decimal("0.85680746"), "target_derived", "legacy-q2")
    with pytest.raises(ValueError, match="restricted to verify_history"):
        convert_to_eur(Decimal("100"), rate)
    assert convert_to_eur(Decimal("100"), rate, mode="verify_history") == Decimal("85.68")


def test_unknown_source_is_rejected_in_production() -> None:
    rate = FXRate("USD", date(2026, 7, 14), Decimal("0.85"), "spreadsheet_guess", "guess-v1")
    with pytest.raises(ValueError, match="not allowed in production"):
        convert_to_eur(Decimal("10"), rate)


def test_xolo_recorded_rate_is_historical_only_after_q2_cutover() -> None:
    historical = FXRate(
        "USD", date(2026, 6, 30), Decimal("0.85"), "xolo_recorded", "xolo-q2"
    )
    post_cutover = FXRate(
        "USD", date(2026, 7, 1), Decimal("0.85"), "xolo_recorded", "xolo-q3"
    )

    assert convert_to_eur(Decimal("10"), historical) == Decimal("8.50")
    with pytest.raises(ValueError, match="not allowed after 2026-06-30"):
        convert_to_eur(Decimal("10"), post_cutover)
    assert convert_to_eur(Decimal("10"), post_cutover, mode="verify_history") == Decimal("8.50")
