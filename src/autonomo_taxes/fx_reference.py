"""ECB reference FX service built on the Python standard library.

The service fetches official ECB daily reference rates (``EXR/D``) and
converts them from the ECB convention — units of the currency per 1 EUR —
into the ledger convention — EUR per 1 unit of the currency — using exact
``Decimal`` arithmetic.

This module is deliberately side-effect free: it never touches the ledger
database and never applies rates. It only returns an observation with its
provenance (source URL, raw observation text and its SHA-256) so the
caller can decide what to do with it. Rates are historical once
published, so a small process-lifetime cache is safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import threading
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
import urllib.parse
import urllib.request

ECB_DATA_API_HOST = "data-api.ecb.europa.eu"
ECB_RATE_SERVICE_URL = "https://data-api.ecb.europa.eu/service/data"
# The ECB publishes reference rates for the currencies below (EXR/D).
SUPPORTED_CURRENCIES = frozenset(
    {
        "AUD",
        "BGN",
        "BRL",
        "CAD",
        "CHF",
        "CNY",
        "CZK",
        "DKK",
        "GBP",
        "HKD",
        "HUF",
        "IDR",
        "ILS",
        "INR",
        "ISK",
        "JPY",
        "KRW",
        "MXN",
        "MYR",
        "NOK",
        "NZD",
        "PHP",
        "PLN",
        "RON",
        "SEK",
        "SGD",
        "THB",
        "TRY",
        "USD",
        "ZAR",
    }
)
# A reference rate from a later business day is not a settlement rate;
# only observations this close to the operation date may be suggested.
FALLBACK_WINDOW_DAYS = 7
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_OBSERVATION_COUNT = 1024
_MAX_CACHE_ENTRIES = 512

HttpGetter = Callable[[str], "tuple[int, bytes]"]


class FXReferenceError(ValueError):
    """Base error for FX reference lookups."""


class FXUnsupportedCurrencyError(FXReferenceError):
    """Raised when the currency has no ECB daily reference rate."""


class FXRateUnavailableError(FXReferenceError):
    """Raised when no usable observation exists within the fallback window."""


class FXMalformedResponseError(FXReferenceError):
    """Raised when the response cannot be validated against the ECB contract."""


@dataclass(frozen=True)
class ECBRateObservation:
    """One validated ECB observation converted into ledger convention."""

    currency: str
    rate_date: date
    units_per_eur: Decimal
    eur_per_unit: Decimal
    source_url: str
    raw_observation: str
    raw_observation_hash: str


@dataclass(frozen=True)
class ECBRateResult:
    """A lookup result: either the exact-date rate or the latest prior one."""

    status: str  # "exact" | "prior"
    observation: ECBRateObservation


class _Cache:
    def __init__(self, max_entries: int = _MAX_CACHE_ENTRIES) -> None:
        self._entries: dict[tuple[str, str], ECBRateResult] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple[str, str]) -> ECBRateResult | None:
        with self._lock:
            return self._entries.get(key)

    def put(self, key: tuple[str, str], value: ECBRateResult) -> None:
        with self._lock:
            if len(self._entries) >= _MAX_CACHE_ENTRIES:
                self._entries.clear()
            self._entries[key] = value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_CACHE = _Cache()


def clear_cache() -> None:
    """Drop cached observations (used by tests and deployments)."""

    _CACHE.clear()


def validate_currency(currency: object) -> str:
    normalized = str(currency or "").strip().upper()
    if len(normalized) != 3 or not normalized.isalpha():
        raise FXUnsupportedCurrencyError(
            f"Currency {currency!r} must be an ISO alpha-3 code"
        )
    if normalized not in SUPPORTED_CURRENCIES:
        raise FXUnsupportedCurrencyError(
            f"Currency {normalized} has no ECB daily reference rate"
        )
    return normalized


def build_rate_url(currency: str, start_period: date, end_period: date) -> str:
    normalized = validate_currency(currency)
    query = urllib.parse.urlencode(
        {"startPeriod": start_period.isoformat(), "endPeriod": end_period.isoformat()}
    )
    return f"{ECB_RATE_SERVICE_URL}/EXR/D.{normalized}.EUR.SP00.A?{query}"


def invert_to_eur_per_unit(units_per_eur: Decimal) -> Decimal:
    """Convert ECB 'units per EUR' into the ledger's 'EUR per unit'.

    The inversion is exact Decimal division; both sides of a confirmation
    compute it identically, so equality checks stay deterministic.
    """

    if units_per_eur <= 0:
        raise FXMalformedResponseError("ECB observation must be positive")
    return Decimal(1) / units_per_eur


def parse_ecb_response(body: str, *, currency: str) -> dict[date, Decimal]:
    """Validate an ECB data-API response and return ``{date: units_per_eur}``.

    Every structural deviation raises an ``FXReferenceError`` so a tampered
    or unrelated response can never be treated as an official rate.
    """

    normalized = validate_currency(currency)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise FXMalformedResponseError("ECB response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise FXMalformedResponseError("ECB response must be a JSON object")
    data_sets = payload.get("dataSets")
    if not isinstance(data_sets, dict) or "EXR" not in data_sets:
        raise FXMalformedResponseError("ECB response is missing the EXR data set")
    data_set = data_sets["EXR"]
    if not isinstance(data_set, dict):
        raise FXMalformedResponseError("ECB data set must be an object")
    observations_raw = data_set.get("observations")
    if not isinstance(observations_raw, dict):
        raise FXMalformedResponseError("ECB observations are missing")
    if not observations_raw:
        raise FXRateUnavailableError("ECB returned no observations for the window")
    if len(observations_raw) > MAX_OBSERVATION_COUNT:
        raise FXMalformedResponseError("ECB observation count exceeds the limit")
    observations: dict[date, Decimal] = {}
    for key, values in observations_raw.items():
        try:
            observation_date = _parse_observation_date(str(key))
        except (TypeError, ValueError) as exc:
            raise FXMalformedResponseError(
                f"ECB observation date is invalid: {key!r}"
            ) from exc
        if not isinstance(values, list) or not values:
            raise FXMalformedResponseError(
                f"ECB observation {key!r} must be a non-empty list"
            )
        first = values[0]
        if not isinstance(first, dict) or "value" not in first:
            raise FXMalformedResponseError(
                f"ECB observation {key!r} must contain a value"
            )
        value = first["value"]
        if not isinstance(value, str):
            raise FXMalformedResponseError(
                f"ECB observation {key!r} value must be a decimal string"
            )
        try:
            rate = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise FXMalformedResponseError(
                f"ECB observation {key!r} value is not a decimal"
            ) from exc
        if not rate.is_finite() or rate <= 0:
            raise FXMalformedResponseError(
                f"ECB observation {key!r} must be a positive finite rate"
            )
        observations[observation_date] = rate
    return observations


def _parse_observation_date(value: str) -> date:
    # Daily (SP00.A) observations use bare ISO dates.
    text = value.strip()
    return date.fromisoformat(text)


def _default_http_get(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "autonomo-tax/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            return int(response.status), response.read()
    except HTTPError as exc:
        return int(exc.code), exc.read()
    except (URLError, TimeoutError, OSError) as exc:
        raise FXRateUnavailableError("ECB reference service is unreachable") from exc


def select_observation(
    observations: Mapping[date, Decimal],
    as_of: date,
    *,
    fallback_window_days: int = FALLBACK_WINDOW_DAYS,
) -> tuple[str, date, Decimal]:
    """Pick the exact-date observation or the newest one within the window."""

    if as_of in observations:
        return "exact", as_of, observations[as_of]
    candidates = [day for day in observations if day <= as_of]
    if not candidates:
        raise FXRateUnavailableError(
            "No ECB observation on or before the requested date"
        )
    latest = max(candidates)
    if (as_of - latest).days > fallback_window_days:
        raise FXRateUnavailableError(
            f"Newest ECB observation is {(as_of - latest).days} days old; "
            f"the fallback window is {fallback_window_days} days"
        )
    return "prior", latest, observations[latest]


def fetch_eur_rate(
    currency: str,
    as_of: date,
    *,
    http_get: HttpGetter | None = None,
) -> ECBRateResult:
    """Fetch the EUR reference rate for ``currency`` as of ``as_of``.

    A single request covers the fallback window. The returned observation
    always carries its real observation date, so a ``prior`` rate is never
    mislabeled as the operation date.
    """

    normalized = validate_currency(currency)
    key = (normalized, as_of.isoformat())
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    start = as_of - timedelta(days=FALLBACK_WINDOW_DAYS)
    source_url = build_rate_url(normalized, start, as_of)
    getter = http_get or _default_http_get
    try:
        status, body = getter(source_url)
    except FXReferenceError:
        raise
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise FXRateUnavailableError("ECB reference lookup failed") from exc
    if status != 200:
        raise FXRateUnavailableError(f"ECB reference service returned HTTP {status}")
    try:
        text = body.decode("utf-8") if isinstance(body, bytes) else str(body)
    except UnicodeDecodeError as exc:
        raise FXMalformedResponseError("ECB response is not valid UTF-8") from exc
    observations = parse_ecb_response(text, currency=normalized)
    status_name, rate_date, units_per_eur = select_observation(observations, as_of)
    raw_observation = json.dumps(
        {
            "currency": normalized,
            "date": rate_date.isoformat(),
            "value": format(units_per_eur, "f"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    observation = ECBRateObservation(
        currency=normalized,
        rate_date=rate_date,
        units_per_eur=units_per_eur,
        eur_per_unit=invert_to_eur_per_unit(units_per_eur),
        source_url=source_url,
        raw_observation=raw_observation,
        raw_observation_hash=hashlib.sha256(raw_observation.encode("utf-8")).hexdigest(),
    )
    result = ECBRateResult(status=status_name, observation=observation)
    _CACHE.put(key, result)
    return result
