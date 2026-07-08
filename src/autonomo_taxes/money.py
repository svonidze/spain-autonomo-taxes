from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

CENT = Decimal("0.01")
RATE_PLACES = Decimal("0.00000001")


def cents(value: Decimal | str | int | float) -> Decimal:
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_amount(raw: str) -> Decimal:
    """Parse Spanish or English formatted money into Decimal.

    Supports examples such as `36.770,89`, `6 281,71`, `€85.12`,
    `$1,234.56`, and `1081,82`.
    """
    text = (
        raw.replace("\u00a0", " ")
        .replace("\u202f", " ")
        .replace("EUR", "")
        .replace("USD", "")
        .replace("€", "")
        .replace("$", "")
        .strip()
    )
    text = re.sub(r"[^0-9,.\- ]", "", text).replace(" ", "")
    if not text:
        raise ValueError(f"Cannot parse empty amount from {raw!r}")

    comma = text.rfind(",")
    dot = text.rfind(".")
    if comma >= 0 and dot >= 0:
        decimal_sep = "," if comma > dot else "."
    elif comma >= 0:
        decimal_sep = "," if len(text) - comma - 1 in {1, 2} else "."
    elif dot >= 0:
        decimal_sep = "." if len(text) - dot - 1 in {1, 2} else ","
    else:
        decimal_sep = "."

    if decimal_sep == ",":
        normalized = text.replace(".", "").replace(",", ".")
    else:
        normalized = text.replace(",", "")

    try:
        return cents(Decimal(normalized))
    except InvalidOperation as exc:
        raise ValueError(f"Cannot parse amount from {raw!r}") from exc


def parse_rate(raw: str) -> Decimal:
    text = raw.strip().replace(",", ".")
    try:
        return Decimal(text).quantize(RATE_PLACES, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError(f"Cannot parse FX rate from {raw!r}") from exc


def format_es(value: Decimal | None) -> str:
    if value is None:
        return ""
    value = cents(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    whole, frac = f"{value:.2f}".split(".")
    groups = []
    while whole:
        groups.append(whole[-3:])
        whole = whole[:-3]
    return f"{sign}{'.'.join(reversed(groups))},{frac}"


def maybe_decimal(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    return parse_amount(value)
