from __future__ import annotations

import hashlib
import inspect
import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

import autonomo_taxes.fx_reference as fx_reference
from autonomo_taxes.fx_reference import (
    ECBRateResult,
    FXMalformedResponseError,
    FXRateUnavailableError,
    FXUnsupportedCurrencyError,
    build_rate_url,
    clear_cache,
    fetch_eur_rate,
    invert_to_eur_per_unit,
    parse_ecb_response,
    select_observation,
    validate_currency,
)


@pytest.fixture(autouse=True)
def _isolated_cache() -> None:
    clear_cache()
    yield
    clear_cache()


def _ecb_body(days: dict[str, str], currency: str = "USD") -> bytes:
    observations = {day: [{"value": value}] for day, value in sorted(days.items())}
    payload = {
        "dataSets": {"EXR": {"observations": observations}},
    }
    return json.dumps(payload).encode("utf-8")


def _getter(body: bytes, calls: list[str], status: int = 200):
    def http_get(url: str) -> tuple[int, bytes]:
        calls.append(url)
        return status, body

    return http_get


def test_exact_date_rate_is_inverted_to_eur_per_unit() -> None:
    as_of = date(2026, 7, 1)
    calls: list[str] = []
    result = fetch_eur_rate(
        "USD",
        as_of,
        http_get=_getter(_ecb_body({as_of.isoformat(): "1.0850"}), calls),
    )
    assert result.status == "exact"
    assert result.observation.rate_date == as_of
    assert result.observation.units_per_eur == Decimal("1.0850")
    assert result.observation.eur_per_unit == Decimal(1) / Decimal("1.0850")
    assert result.observation.raw_observation_hash == hashlib.sha256(
        result.observation.raw_observation.encode("utf-8")
    ).hexdigest()
    assert len(calls) == 1
    assert "EXR/D.USD.EUR.SP00.A" in calls[0]


def test_fallback_uses_newest_observation_within_seven_days() -> None:
    as_of = date(2026, 7, 6)
    observed = as_of - timedelta(days=2)
    result = fetch_eur_rate(
        "USD",
        as_of,
        http_get=_getter(_ecb_body({observed.isoformat(): "1.1000"}), []),
    )
    assert result.status == "prior"
    assert result.observation.rate_date == observed
    assert result.observation.eur_per_unit == Decimal(1) / Decimal("1.1000")


def test_seven_day_boundary_is_accepted() -> None:
    as_of = date(2026, 7, 6)
    observed = as_of - timedelta(days=7)
    result = fetch_eur_rate(
        "USD",
        as_of,
        http_get=_getter(_ecb_body({observed.isoformat(): "1.1000"}), []),
    )
    assert result.status == "prior"
    assert result.observation.rate_date == observed


def test_observation_older_than_seven_days_is_unavailable() -> None:
    as_of = date(2026, 7, 6)
    observed = as_of - timedelta(days=8)
    with pytest.raises(FXRateUnavailableError, match="fallback window"):
        fetch_eur_rate(
            "USD",
            as_of,
            http_get=_getter(_ecb_body({observed.isoformat(): "1.1000"}), []),
        )


def test_only_later_observations_are_rejected() -> None:
    as_of = date(2026, 7, 6)
    later = as_of + timedelta(days=1)
    with pytest.raises(FXRateUnavailableError):
        fetch_eur_rate(
            "USD",
            as_of,
            http_get=_getter(_ecb_body({later.isoformat(): "1.1000"}), []),
        )


@pytest.mark.parametrize(
    "currency",
    ["XXX", "US", "USDX", "", None],
)
def test_unsupported_currency_fails_before_any_network_call(currency: object) -> None:
    calls: list[str] = []

    def failing_getter(url: str) -> tuple[int, bytes]:
        calls.append(url)
        return 200, b"{}"

    with pytest.raises(FXUnsupportedCurrencyError):
        fetch_eur_rate(currency, date(2026, 7, 6), http_get=failing_getter)
    assert calls == []


def test_http_error_status_is_unavailable() -> None:
    with pytest.raises(FXRateUnavailableError, match="HTTP 500"):
        fetch_eur_rate("USD", date(2026, 7, 6), http_get=_getter(b"{}", [], status=500))


def test_transport_failure_is_unavailable() -> None:
    def raising_getter(url: str) -> tuple[int, bytes]:
        raise TimeoutError("timed out")

    with pytest.raises(FXRateUnavailableError):
        fetch_eur_rate("USD", date(2026, 7, 6), http_get=raising_getter)


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[1, 2]",
        b"{}",
        b'{"dataSets": {}}',
        b'{"dataSets": {"EXR": {}}}',
        b'{"dataSets": {"EXR": {"observations": {}}}}',
        b'{"dataSets": {"EXR": {"observations": {"2026-07-01": []}}}}',
        b'{"dataSets": {"EXR": {"observations": {"2026-07-01": [{}]}}}}',
        b'{"dataSets": {"EXR": {"observations": {"2026-07-01": [{"value": "NaN"}]}}}}',
        b'{"dataSets": {"EXR": {"observations": {"2026-07-01": [{"value": "-1"}]}}}}',
        b'{"dataSets": {"EXR": {"observations": {"2026/07/01": [{"value": "1"}]}}}}',
    ],
)
def test_malformed_response_is_rejected(body: bytes) -> None:
    with pytest.raises((FXMalformedResponseError, FXRateUnavailableError)):
        fetch_eur_rate("USD", date(2026, 7, 6), http_get=_getter(body, []))


def test_parse_ecb_response_requires_matching_currency() -> None:
    with pytest.raises(FXUnsupportedCurrencyError):
        parse_ecb_response(
            json.dumps({"dataSets": {"EXR": {"observations": {}}}}),
            currency="XXX",
        )


def test_decimal_inversion_is_exact() -> None:
    units = Decimal("1.0850")
    eur_per_unit = invert_to_eur_per_unit(units)
    assert (eur_per_unit * units).quantize(Decimal("0.0000000001")) == Decimal("1.0000000000")
    with pytest.raises(FXMalformedResponseError):
        invert_to_eur_per_unit(Decimal("0"))


def test_select_observation_prefers_exact_date() -> None:
    as_of = date(2026, 7, 6)
    observations = {
        as_of - timedelta(days=1): Decimal("1.0"),
        as_of: Decimal("1.1"),
    }
    status, day, rate = select_observation(observations, as_of)
    assert (status, day, rate) == ("exact", as_of, Decimal("1.1"))


def test_fetch_caches_result_per_currency_and_date() -> None:
    as_of = date(2026, 7, 6)
    calls: list[str] = []
    getter = _getter(_ecb_body({as_of.isoformat(): "1.0500"}), calls)
    first = fetch_eur_rate("USD", as_of, http_get=getter)
    second = fetch_eur_rate("USD", as_of, http_get=getter)
    assert calls == [calls[0]]
    assert first == second


def test_clear_cache_allows_refetch() -> None:
    as_of = date(2026, 7, 6)
    calls: list[str] = []
    getter = _getter(_ecb_body({as_of.isoformat(): "1.0500"}), calls)
    fetch_eur_rate("USD", as_of, http_get=getter)
    clear_cache()
    fetch_eur_rate("USD", as_of, http_get=getter)
    assert len(calls) == 2


def test_module_never_touches_the_ledger_database() -> None:
    source = inspect.getsource(fx_reference)
    assert "sqlite3" not in source
    assert "ledger_db" not in source
    assert "autonomo_taxes.ledger" not in source
    assert "import sqlite3" not in source
