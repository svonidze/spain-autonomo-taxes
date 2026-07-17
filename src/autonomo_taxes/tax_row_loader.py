from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Collection

from .ledger_db import LedgerDB
from .tax_engine import CalculationBlocked, TaxRow, WITHHOLDING_TYPE_BY_TAX_CODE


def load_tax_rows(
    database: LedgerDB,
    year: int,
    *,
    mode: str = "production",
    allow_authoritative_history: bool = False,
    include_approved_periods: Collection[str] = (),
) -> list[TaxRow]:
    approved_periods = set(include_approved_periods)
    authoritative_ids = (
        {
            row["transaction_id"]
            for row in database.list_authoritative_history_transactions(year=year)
        }
        if mode == "production" and allow_authoritative_history
        else set()
    )
    grouped: dict[str, dict[str, Any]] = {}
    for raw in database.list_tax_rows(year=year):
        if raw["entry_type"] == "verify_history_adjustment" and mode != "verify_history":
            continue
        if mode == "production" and raw["lifecycle_status"] not in {
            "posted",
            "included_in_snapshot",
        }:
            approved = raw["lifecycle_status"] == "approved"
            if not (
                approved
                and (
                    raw["transaction_id"] in authoritative_ids
                    or raw["period_key"] in approved_periods
                )
            ):
                continue
        if int(raw.get("asset_count") or 0) > 1:
            raise CalculationBlocked(
                f"Transaction {raw['transaction_id']} is linked to multiple assets and must be split before tax calculation"
            )
        bucket = grouped.get(raw["transaction_id"])
        if bucket is None:
            grouped[raw["transaction_id"]] = dict(raw)
            continue
        raw_asset_id = raw.get("asset_id") or ""
        current_asset_id = bucket.get("asset_id") or ""
        if raw_asset_id and current_asset_id and raw_asset_id != current_asset_id:
            raise CalculationBlocked(
                f"Transaction {raw['transaction_id']} has conflicting asset links"
            )
        if raw_asset_id:
            bucket["asset_id"] = raw_asset_id
        bucket["asset_count"] = max(
            int(bucket.get("asset_count") or 0),
            int(raw.get("asset_count") or 0),
        )
        if raw.get("tax_code") and raw["tax_code"] != "unknown":
            current = bucket.get("tax_code")
            if current not in {None, "", "unknown", raw["tax_code"]}:
                raise CalculationBlocked(f"Conflicting tax codes for {raw['transaction_id']}")
            bucket["tax_code"] = raw["tax_code"]
        for key in (
            "taxable_base_minor",
            "vat_minor",
            "deductible_irpf_minor",
            "deductible_vat_minor",
            "withholding_minor",
        ):
            value = raw.get(key)
            if value is None:
                continue
            current = bucket.get(key)
            if current is not None and int(current) != int(value):
                raise CalculationBlocked(
                    f"Conflicting {key} values for transaction {raw['transaction_id']}"
                )
            bucket[key] = int(value)
        for key in ("include_modelo130", "include_modelo303", "include_modelo347"):
            bucket[key] = int(bool(bucket.get(key)) or bool(raw.get(key)))

    return [_to_tax_row(row) for row in grouped.values()]


def _to_tax_row(row: dict[str, Any]) -> TaxRow:
    entry_type = str(row["entry_type"])
    kind = (
        "income"
        if entry_type.startswith("income")
        else "expense"
        if entry_type.startswith("expense")
        else "adjustment"
    )
    original_currency = str(row.get("original_currency") or row["currency"]).upper()
    amount_minor = row.get("amount_eur_minor")
    if amount_minor is None:
        if original_currency != "EUR":
            raise CalculationBlocked(
                f"Transaction {row['transaction_id']} is missing an explicit EUR amount for {original_currency}"
            )
        amount_minor = row["amount_minor"]
    return TaxRow(
        transaction_id=row["transaction_id"],
        tax_date=date.fromisoformat(row["transaction_date"]),
        kind=kind,
        amount_eur=Decimal(amount_minor) / 100,
        taxable_base_eur=Decimal(row.get("taxable_base_minor") or 0) / 100,
        vat_eur=Decimal(row.get("vat_minor") or 0) / 100,
        deductible_irpf_eur=Decimal(row.get("deductible_irpf_minor") or 0) / 100,
        deductible_vat_eur=Decimal(row.get("deductible_vat_minor") or 0) / 100,
        withholding_eur=Decimal(row.get("withholding_minor") or 0) / 100,
        tax_code=row.get("tax_code") or "unknown",
        counterparty_id=row.get("counterparty_id") or "",
        counterparty_name=row.get("counterparty_name") or "",
        country_code=row.get("country_code") or "",
        vat_id=row.get("vat_id") or "",
        include_modelo130=bool(row.get("include_modelo130")),
        include_modelo303=bool(row.get("include_modelo303")),
        include_modelo347=bool(row.get("include_modelo347")),
        withholding_type=WITHHOLDING_TYPE_BY_TAX_CODE.get(
            row.get("tax_code") or "",
            "professional" if row.get("withholding_minor") else "",
        ),
        asset_id=row.get("asset_id") or "",
    )
