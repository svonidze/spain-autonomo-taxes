"""Counterparty name aliases are search hints, never proof of tax identity."""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Any, Mapping


class CounterpartyMatchError(ValueError):
    """Matching must be resolved explicitly before writing accounting data."""


def normalize_counterparty_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def validate_counterparty_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Counterparty name must be a string")
    if any(unicodedata.category(character) in {"Cc", "Zl", "Zp"} for character in value):
        raise ValueError("Counterparty name must be a single line without control characters")
    result = value.strip()
    if not result:
        raise ValueError("Counterparty name is required")
    return result


def usable_tax_id(value: str) -> bool:
    compact = re.sub(r"[^0-9A-Z]", "", (value or "").upper())
    if not compact or compact in {"NA", "NONE", "UNKNOWN", "NODISPONIBLE", "SINDATOS"}:
        return False
    local = compact[2:] if re.match(r"^[A-Z]{2}", compact) else compact
    return re.fullmatch(r"0{7,9}[A-Z]?|9{7,9}[A-Z]?", local) is None


def is_oss_non_union_identifier(value: str | None) -> bool:
    # Directive 2006/112/EC art. 362 (LIVA arts. 163 octiesdecies ff.) assigns "EU" + 9 digits
    # to suppliers in the One-Stop-Shop non-Union scheme, who are established outside the EU.
    # "EU" is not an ISO country code and the value is not a Member-State VAT number.
    compact = re.sub(r"[^0-9A-Z]", "", (value or "").upper())
    return re.fullmatch(r"EU[0-9]{9}", compact) is not None


def identity_conflicts(
    counterparty: Mapping[str, Any], *, country_code: str | None = None,
    tax_id: str | None = None, vat_id: str | None = None,
    connection: sqlite3.Connection | None = None,
) -> bool:
    current = dict(counterparty)
    country = (country_code or "").strip().upper()
    existing_country = str(current.get("country_code") or "").strip().upper()
    if country not in {"", "ZZ"} and existing_country not in {"", "ZZ", country}:
        return True

    def identifiers(values, country):
        result = set()
        for value in values:
            if not usable_tax_id(str(value or "")):
                continue
            compact = re.sub(r"[^0-9A-Z]", "", str(value).upper())
            if country not in {"", "ZZ"} and compact.startswith(country):
                compact = compact[len(country):]
            result.add(compact)
        return result

    known = identifiers((current.get("tax_id"), current.get("vat_id")), existing_country)
    supplied = identifiers((tax_id, vat_id), country or existing_country)
    for field, value in (("tax_id", tax_id), ("vat_id", vat_id)):
        existing_values = identifiers((current.get(field),), existing_country)
        source_values = identifiers((value,), country or existing_country)
        if existing_values and source_values and existing_values.isdisjoint(source_values):
            return True
    if connection is not None:
        primary = connection.execute(
            """SELECT country_code, identifier FROM counterparty_identities
               WHERE counterparty_id = ? AND is_primary = 1 AND identity_kind = 'vat_id'""",
            (current["counterparty_id"],),
        ).fetchone()
        if primary is not None:
            primary_country = primary["country_code"]
            if country not in {"", "ZZ", primary_country} and primary_country not in {"", "ZZ"}:
                return True
            primary_ids = identifiers((primary["identifier"],), primary_country)
            comparable = identifiers((vat_id or tax_id,), country or primary_country)
            if primary_ids and comparable and primary_ids.isdisjoint(comparable):
                return True
    return bool(known and supplied and known.isdisjoint(supplied))


def find_name_candidates(connection: sqlite3.Connection, name: str) -> list[dict[str, Any]]:
    normalized = normalize_counterparty_name(name)
    aliases = {
        row[0] for row in connection.execute(
            """SELECT counterparty_id FROM counterparty_name_changes
               WHERE old_name_normalized = ? OR new_name_normalized = ?""",
            (normalized, normalized),
        )
    } if normalized else set()
    return [
        dict(row) for row in connection.execute(
            "SELECT * FROM counterparties ORDER BY created_at, counterparty_id"
        )
        if row["counterparty_id"] in aliases
        or (normalized and normalize_counterparty_name(row["display_name"]) == normalized)
        or row["display_name"].casefold() == name.casefold()
    ]


def accepts_counterparty_name(
    connection: sqlite3.Connection, counterparty: Mapping[str, Any], name: str,
) -> bool:
    return any(
        row["counterparty_id"] == counterparty["counterparty_id"]
        for row in find_name_candidates(connection, name)
    )
