"""On-demand EU VAT number check against the European Commission VIES service.

Contract: https://ec.europa.eu/assets/taxud/vow-information/swagger_publicVAT.yaml.
Only ``valid`` and ``invalid`` are answers about the number. Every VIES error,
transport failure or unexpected response is ``unavailable`` or ``invalid_input``
and never means that the customer is not registered.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
from http.client import HTTPException
import json
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
import urllib.request

from .history_migration import EU_COUNTRY_CODES


VIES_CHECK_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/check-vat-number"
DEFAULT_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 256 * 1024
# VIES uses EL, not the ISO code GR, for Greece.
VIES_COUNTRY_CODES = frozenset(EU_COUNTRY_CODES - {"GR"} | {"EL"})
INVALID_INPUT_ERRORS = frozenset({"INVALID_INPUT", "INVALID_REQUESTER_INFO"})

HttpPost = Callable[[str, bytes, float], tuple[int, bytes]]


class ViesConsentError(ValueError):
    """Raised when a VIES request was not explicitly confirmed."""


def vies_country_code(country_code: str) -> str:
    country = (country_code or "").strip().upper()
    country = "EL" if country == "GR" else country
    if country == "XI":
        raise ValueError(
            "XI numbers cover goods under the Windsor Framework; services to Northern "
            "Ireland are UK supplies, so record the customer's GB VAT number instead"
        )
    if country not in VIES_COUNTRY_CODES:
        raise ValueError(f"VIES checks only EU member-state VAT numbers, not {country or 'blank'}")
    return country


def normalize_vat_number(country_code: str, vat_number: str) -> tuple[str, str]:
    """Return the VIES country code and the number without prefix or punctuation."""

    country = vies_country_code(country_code)
    number = re.sub(r"[^0-9A-Z]", "", (vat_number or "").upper())
    prefixes = ("EL", "GR") if country == "EL" else (country,)
    for prefix in prefixes:
        if number.startswith(prefix) and len(number) > len(prefix):
            number = number[len(prefix):]
            break
    else:
        if len(number) > 2 and number[:2].isalpha() and number[:2] in VIES_COUNTRY_CODES | {"GR"}:
            raise ValueError(
                f"VAT number prefix {number[:2]} does not match country {country}; "
                f"record the number with its {country} prefix"
            )
    if not 2 <= len(number) <= 14:
        raise ValueError("VAT number must contain 2 to 14 letters or digits after the country code")
    return country, number


def split_requester_vat(value: str) -> tuple[str, str]:
    """Split the operator's own prefixed VAT number, for example ``ES...``."""

    text = re.sub(r"[^0-9A-Z]", "", (value or "").upper())
    if len(text) < 3 or not text[:2].isalpha():
        raise ValueError("Requester VAT must start with its two-letter member-state code")
    return normalize_vat_number(text[:2], text)


def check_vat(
    country_code: str,
    vat_number: str,
    *,
    confirm_network: bool,
    requester: str | None = None,
    http_post: HttpPost | None = None,
    url: str = VIES_CHECK_URL,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if not confirm_network:
        raise ViesConsentError(
            "A VIES check sends the VAT number (and the requester VAT, if given) to the "
            "European Commission; pass explicit confirmation"
        )
    country, number = normalize_vat_number(country_code, vat_number)
    request_body: dict[str, str] = {"countryCode": country, "vatNumber": number}
    if requester:
        request_body["requesterMemberStateCode"], request_body["requesterNumber"] = (
            split_requester_vat(requester)
        )
    result: dict[str, Any] = {
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "country_code": country,
        "vat_number": number,
        "outcome": "unavailable",
        "error_code": None,
        "request_date": None,
        "request_identifier": None,
        "trader_name": None,
        "trader_address": None,
        "requester_used": bool(requester),
        "http_status": None,
        "response_body": None,
        "response_encoding": None,
        "response_sha256": None,
        "request_sent": True,
    }
    try:
        status, raw = (http_post or _default_http_post)(
            url, json.dumps(request_body).encode("utf-8"), timeout_seconds
        )
    except URLError as exc:
        # urllib raises URLError only while connecting or sending (DNS, TLS, refused).
        cause = exc.reason if isinstance(exc.reason, BaseException) else exc
        result["error_code"] = "CONNECTION_FAILED:" + type(cause).__name__
        result["request_sent"] = False
        return result
    except (OSError, HTTPException, ValueError) as exc:  # timeouts, broken or oversized replies
        result["error_code"] = "TRANSPORT_ERROR:" + type(exc).__name__
        return result
    result["http_status"] = status
    try:
        result["response_body"], result["response_encoding"] = raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        result["response_body"] = base64.b64encode(raw).decode("ascii")
        result["response_encoding"] = "base64"
    result["response_sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        result["error_code"] = "MALFORMED_RESPONSE"
        return result
    errors = payload.get("errorWrappers")
    if isinstance(errors, list) and errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        code = str(first.get("error") or "UNKNOWN_ERROR")
        result["error_code"] = code
        if code in INVALID_INPUT_ERRORS:
            result["outcome"] = "invalid_input"
        return result
    valid = payload.get("valid")
    if status != 200 or not isinstance(valid, bool):
        result["error_code"] = "MALFORMED_RESPONSE"
        return result
    if (payload.get("countryCode"), payload.get("vatNumber")) != (country, number):
        result["error_code"] = "RESPONSE_MISMATCH"
        return result
    text = {key: None if payload.get(key) is None else str(payload[key]) for key in (
        "requestDate", "requestIdentifier", "name", "address",
    )}
    result.update(
        outcome="valid" if valid else "invalid",
        request_date=text["requestDate"],
        request_identifier=text["requestIdentifier"] or None,
        trader_name=text["name"],
        trader_address=text["address"],
    )
    return result


def _default_http_post(url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "autonomo-tax/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), _read_limited(response)
    except HTTPError as exc:
        return int(exc.code), _read_limited(exc)


def _read_limited(stream: Any) -> bytes:
    raw = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("VIES response exceeds the size limit")
    return raw
