from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import re

from .money import maybe_decimal


DETAIL_FACT_FIELDS = [
    "xolo_id",
    "xolo_url",
    "date",
    "recipient",
    "number",
    "currency",
    "amount_original",
    "detail_currency",
    "exchange_rate",
    "gross_eur",
    "vat_base_eur",
    "is_depreciable_asset",
    "confidence",
]


@dataclass(frozen=True)
class XoloExpenseDetailFacts:
    xolo_id: str
    xolo_url: str
    date: str
    recipient: str
    number: str
    currency: str
    amount_original: Decimal | None
    detail_currency: str
    exchange_rate: Decimal | None
    gross_eur: Decimal | None
    vat_base_eur: Decimal | None
    is_depreciable_asset: bool
    confidence: str

    def to_row(self) -> dict[str, str]:
        return {
            "xolo_id": self.xolo_id,
            "xolo_url": self.xolo_url,
            "date": self.date,
            "recipient": self.recipient,
            "number": self.number,
            "currency": self.currency,
            "amount_original": _money_or_blank(self.amount_original),
            "detail_currency": self.detail_currency,
            "exchange_rate": _rate_or_blank(self.exchange_rate),
            "gross_eur": _money_or_blank(self.gross_eur),
            "vat_base_eur": _money_or_blank(self.vat_base_eur),
            "is_depreciable_asset": "yes" if self.is_depreciable_asset else "no",
            "confidence": self.confidence,
        }


def parse_detail_facts(raw_row: dict[str, str], detail_text: str) -> XoloExpenseDetailFacts:
    currency = (raw_row.get("currency") or "").upper()
    amount = maybe_decimal(raw_row.get("amount_original"))
    subtotal = maybe_decimal(raw_row.get("subtotal_amount"))
    detail_currency, exchange_rate = _parse_currency_and_rate(detail_text)
    if not detail_currency:
        detail_currency = currency
    currencies_match = not currency or not detail_currency or currency == detail_currency
    effective_currency = detail_currency or currency
    gross_eur = _to_eur(amount, effective_currency, exchange_rate) if currencies_match else None
    vat_base_eur = _to_eur(subtotal, effective_currency, exchange_rate) if currencies_match else None
    confidence = _confidence(currency, detail_currency, exchange_rate, gross_eur)
    return XoloExpenseDetailFacts(
        xolo_id=raw_row.get("xolo_id", ""),
        xolo_url=raw_row.get("xolo_url", ""),
        date=(raw_row.get("date") or "")[:10],
        recipient=raw_row.get("recipient", ""),
        number=raw_row.get("number", ""),
        currency=currency,
        amount_original=amount,
        detail_currency=detail_currency,
        exchange_rate=exchange_rate,
        gross_eur=gross_eur,
        vat_base_eur=vat_base_eur,
        is_depreciable_asset="depreciable asset" in detail_text.lower(),
        confidence=confidence,
    )


def enrich_raw_expense_rows(raw_rows: list[dict[str, str]], facts: list[dict[str, str]]) -> list[dict[str, str]]:
    facts_by_id = {row.get("xolo_id", ""): row for row in facts if row.get("xolo_id")}
    output: list[dict[str, str]] = []
    for raw in raw_rows:
        row = dict(raw)
        fact = facts_by_id.get(row.get("xolo_id", ""))
        if fact:
            row["xolo_exchange_rate"] = fact.get("exchange_rate", "")
            row["gross_eur"] = fact.get("gross_eur", "")
            row["vat_base_eur"] = fact.get("vat_base_eur", "")
            row["detail_currency"] = fact.get("detail_currency", "")
            row["is_depreciable_asset"] = fact.get("is_depreciable_asset", "")
            row["detail_confidence"] = fact.get("confidence", "")
        else:
            row.setdefault("xolo_exchange_rate", "")
            row.setdefault("gross_eur", "")
            row.setdefault("vat_base_eur", "")
            row.setdefault("detail_currency", "")
            row.setdefault("is_depreciable_asset", "")
            row.setdefault("detail_confidence", "missing_detail")
        output.append(row)
    return output


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_detail_facts_csv(path: Path, facts: list[XoloExpenseDetailFacts]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DETAIL_FACT_FIELDS)
        writer.writeheader()
        for fact in facts:
            writer.writerow(fact.to_row())


def write_enriched_expenses_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_detail_facts_markdown(path: Path, fact_rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    usd_rows = [row for row in fact_rows if row.get("currency") == "USD"]
    missing = [row for row in fact_rows if row.get("currency") != "EUR" and not row.get("gross_eur")]
    asset_rows = [row for row in fact_rows if row.get("is_depreciable_asset") == "yes"]
    lines = [
        "# Xolo Expense Detail Facts",
        "",
        "Fetched from authenticated Xolo expense detail pages. This captures detail-page exchange rates and asset warnings; it still does not prove submitted Modelo 130 row inclusion.",
        "",
        "## Summary",
        "",
        f"- Detail rows: `{len(fact_rows)}`.",
        f"- USD rows with detail exchange rates: `{sum(1 for row in usd_rows if row.get('exchange_rate'))}` / `{len(usd_rows)}`.",
        f"- Non-EUR rows missing gross EUR: `{len(missing)}`.",
        f"- Rows marked as depreciable assets in Xolo detail pages: `{len(asset_rows)}`.",
        "",
        "## USD Rows",
        "",
        "| Date | Xolo ID | Recipient | Number | USD | Rate | Gross EUR | Asset? |",
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for row in usd_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row.get("date", "")),
                    _cell(row.get("xolo_id", "")),
                    _cell(row.get("recipient", "")),
                    _cell(row.get("number", "")),
                    _cell(row.get("amount_original", "")),
                    _cell(row.get("exchange_rate", "")),
                    _cell(row.get("gross_eur", "")),
                    _cell(row.get("is_depreciable_asset", "")),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Depreciable Asset Rows", "", "| Date | Xolo ID | Recipient | Number | Amount | Currency | Gross EUR |", "|---|---|---|---|---:|---|---:|"])
    for row in asset_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row.get("date", "")),
                    _cell(row.get("xolo_id", "")),
                    _cell(row.get("recipient", "")),
                    _cell(row.get("number", "")),
                    _cell(row.get("amount_original", "")),
                    _cell(row.get("currency", "")),
                    _cell(row.get("gross_eur", "")),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _parse_currency_and_rate(text: str) -> tuple[str, Decimal | None]:
    match = re.search(r"Currency\s+([A-Z]{3})\s+Exchange rate\s+([0-9]+(?:[.,][0-9]+)?|-)", text)
    if not match:
        return "", None
    rate_text = match.group(2)
    rate = None if rate_text == "-" else Decimal(rate_text.replace(",", "."))
    return match.group(1), rate


def _to_eur(amount: Decimal | None, currency: str, exchange_rate: Decimal | None) -> Decimal | None:
    if amount is None:
        return None
    if currency == "EUR":
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if exchange_rate is None or exchange_rate == 0:
        return None
    return (amount / exchange_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _confidence(
    currency: str,
    detail_currency: str,
    exchange_rate: Decimal | None,
    gross_eur: Decimal | None,
) -> str:
    if currency and detail_currency and currency != detail_currency:
        return "currency_mismatch_review"
    if currency == "EUR" and gross_eur is not None:
        return "detail_page_eur"
    if currency != "EUR" and exchange_rate is not None and gross_eur is not None:
        return "detail_page_exchange_rate"
    return "missing_exchange_rate"


def _money_or_blank(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.2f}"


def _rate_or_blank(value: Decimal | None) -> str:
    return "" if value is None else str(value)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
