import hashlib
from datetime import date
from decimal import Decimal

import pytest

from autonomo_taxes.fx_policy import (
    FXRate,
    FXRateDeviationError,
    FXRateDirectionError,
    check_manual_rate,
    convert_to_eur,
)
from autonomo_taxes.fx_reference import (
    ECBRateObservation,
    ECBRateResult,
    FXRateUnavailableError,
)


def _lookup(units: str, calls: list[tuple[str, date]] | None = None):
    def reference_lookup(currency: str, as_of: date) -> ECBRateResult:
        if calls is not None:
            calls.append((currency, as_of))
        units_per_eur = Decimal(units)
        raw = f'{{"currency":"{currency}","date":"{as_of.isoformat()}","value":"{units}"}}'
        observation = ECBRateObservation(
            currency=currency,
            rate_date=as_of,
            units_per_eur=units_per_eur,
            eur_per_unit=Decimal(1) / units_per_eur,
            source_url="https://example.invalid/ecb",
            raw_observation=raw,
            raw_observation_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        )
        return ECBRateResult(status="exact", observation=observation)

    return reference_lookup


def _check(
    rate: str,
    *,
    source: str = "ecb",
    allow_unverified: bool = False,
    reference_lookup=None,
):
    return check_manual_rate(
        Decimal(rate),
        currency="USD",
        rate_date=date(2026, 7, 1),
        rate_source=source,
        reference_lookup=reference_lookup or _lookup("1.2500"),
        allow_unverified=allow_unverified,
    )


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


def test_manual_rate_within_tolerance_is_verified() -> None:
    check = _check("0.8020")
    assert check.status == "verified"
    assert "1.2500 USD per EUR = 0.80000000 EUR per USD" in check.detail


def test_manual_units_per_eur_quote_is_refused_as_inverted() -> None:
    with pytest.raises(FXRateDirectionError) as raised:
        _check("1.2550", allow_unverified=True)
    message = str(raised.value)
    assert "looks like a units-per-EUR quote" in message
    assert "1.2500 USD per EUR = 0.80000000 EUR per USD" in message
    assert "expected EUR-per-unit close to 0.80000000" in message


def test_manual_rate_far_from_reference_needs_explicit_override() -> None:
    with pytest.raises(FXRateDeviationError, match="deviates 12.50% from the ECB reference"):
        _check("0.7000")
    check = _check("0.7000", allow_unverified=True)
    assert check.status == "unverified"
    assert check.detail == (
        "rate not verified against the ECB reference: deviates 12.50% from "
        "0.80000000 EUR per USD on 2026-07-01"
    )


def test_inverted_quote_near_parity_is_only_a_small_deviation() -> None:
    # 1.0200 units per EUR inverts to 0.98039; entering 1.0200 is a 4% error,
    # inside the tolerance, so nothing is refused.
    assert _check("1.0200", reference_lookup=_lookup("1.0200")).status == "verified"


def test_unavailable_reference_does_not_block() -> None:
    def offline(currency: str, as_of: date) -> ECBRateResult:
        raise FXRateUnavailableError("ECB reference service is unreachable")

    check = _check("1.2500", reference_lookup=offline)
    assert check.status == "unavailable"
    assert "could not be verified against the ECB reference" in check.detail
    assert "ECB reference service is unreachable" in check.detail


@pytest.mark.parametrize("source", ["actual_settlement", "xolo_recorded"])
def test_non_official_sources_skip_the_reference_lookup(source: str) -> None:
    calls: list[tuple[str, date]] = []
    check = _check("1.2500", source=source, reference_lookup=_lookup("1.2500", calls))
    assert check.status == "exempt"
    assert calls == []


def test_manual_rate_must_be_positive() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        _check("0")
