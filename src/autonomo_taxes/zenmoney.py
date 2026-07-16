from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import hashlib
import io
from pathlib import Path
import re
from typing import Mapping

from .money import cents, parse_amount
from .parsers import parse_any_date


@dataclass(frozen=True)
class ZenMoneyPayment:
    external_id: str
    payment_date: date
    reference: str
    counterparty: str
    category: str
    comment: str
    account_name: str
    amount_original: Decimal
    currency: str
    amount_eur: Decimal | None
    source_row: dict[str, str]

    @property
    def payment_id(self) -> str:
        return self.external_id


@dataclass(frozen=True)
class ZenMoneySkip:
    source_row_number: int
    reason: str


@dataclass(frozen=True)
class ZenMoneyLoadResult:
    payments: tuple[ZenMoneyPayment, ...]
    skipped: tuple[ZenMoneySkip, ...]


_DATE_KEYS = ("date", "дата")
_CATEGORY_KEYS = ("category", "категория", "tag", "тег")
_PAYEE_KEYS = ("payee", "payer", "merchant", "плательщик", "получатель")
_COMMENT_KEYS = ("comment", "description", "комментарий", "описание")
_OUTCOME_ACCOUNT_KEYS = (
    "account",
    "accountoutcome",
    "outcomeaccount",
    "outcomeaccountname",
    "счет",
    "счетрасхода",
)
_INCOME_ACCOUNT_KEYS = (
    "accountincome",
    "incomeaccount",
    "incomeaccountname",
    "счетполучатель",
    "счетдохода",
)
_OUTCOME_KEYS = ("outcome", "amountoutcome", "outcomeamount", "расход", "суммарасход")
_INCOME_KEYS = ("income", "amountincome", "incomeamount", "доход", "суммадоход")
_SIGNED_AMOUNT_KEYS = ("amount", "signedamount", "сумма", "суммасознаком")
_OUTCOME_CURRENCY_KEYS = (
    "outcomecurrency",
    "currencyoutcome",
    "валютарасход",
    "валютарасхода",
)
_INCOME_CURRENCY_KEYS = (
    "incomecurrency",
    "currencyincome",
    "валютадоход",
    "валютадохода",
)
_CURRENCY_KEYS = ("currency", "валюта")
_EUR_AMOUNT_KEYS = ("amounteur", "amountineur", "суммавeur", "суммаевро")
_ID_KEYS = ("id", "transactionid", "transaction_id", "идентификатор")
_DELETED_KEYS = ("deleted", "isdeleted", "удалена", "удалено")


def load_zenmoney_payments_csv(
    path: Path,
    *,
    business_accounts: set[str],
    starts_on: date,
    ends_on: date,
    default_currency: str | None = None,
) -> ZenMoneyLoadResult:
    if not business_accounts:
        raise ValueError("At least one ZenMoney business account is required")
    rows = _read_rows(path)
    normalized_accounts = {_normalize_value(value) for value in business_accounts}
    payments: list[ZenMoneyPayment] = []
    skipped: list[ZenMoneySkip] = []
    duplicate_counts: dict[str, int] = {}

    for row_number, row in enumerate(rows, start=2):
        lowered = {_normalize_header(key): _text(value) for key, value in row.items() if key}
        if _is_true(_pick(lowered, *_DELETED_KEYS)):
            skipped.append(ZenMoneySkip(row_number, "deleted"))
            continue
        payment_date = parse_any_date(_pick(lowered, *_DATE_KEYS))
        if payment_date is None:
            raise ValueError(f"ZenMoney row {row_number} has no parseable date")
        if not starts_on <= payment_date <= ends_on:
            skipped.append(ZenMoneySkip(row_number, "outside_period"))
            continue

        outcome_account = _pick(lowered, *_OUTCOME_ACCOUNT_KEYS)
        income_account = _pick(lowered, *_INCOME_ACCOUNT_KEYS)
        outcome_selected = _normalize_value(outcome_account) in normalized_accounts
        income_selected = _normalize_value(income_account) in normalized_accounts
        if outcome_selected and income_selected:
            skipped.append(ZenMoneySkip(row_number, "internal_business_transfer"))
            continue

        signed_amount = _optional_amount(_pick(lowered, *_SIGNED_AMOUNT_KEYS))
        if outcome_selected:
            directional_amount = _optional_amount(_pick(lowered, *_OUTCOME_KEYS))
            amount = directional_amount if directional_amount is not None else signed_amount
            account_name = outcome_account
            currency = _currency(lowered, _OUTCOME_CURRENCY_KEYS, default_currency)
            if directional_amount is not None:
                amount = -abs(amount)
        elif income_selected:
            directional_amount = _optional_amount(_pick(lowered, *_INCOME_KEYS))
            amount = directional_amount if directional_amount is not None else signed_amount
            account_name = income_account
            currency = _currency(lowered, _INCOME_CURRENCY_KEYS, default_currency)
            if directional_amount is not None:
                amount = abs(amount)
        else:
            account_name = outcome_account or income_account
            if _normalize_value(account_name) not in normalized_accounts:
                skipped.append(ZenMoneySkip(row_number, "non_business_account"))
                continue
            amount = signed_amount
            currency = _currency(lowered, (), default_currency)

        if amount is None:
            raise ValueError(f"ZenMoney row {row_number} for {account_name!r} has no amount")
        amount = cents(amount)
        amount_eur = _optional_amount(_pick(lowered, *_EUR_AMOUNT_KEYS))
        if amount_eur is None and currency == "EUR":
            amount_eur = amount
        elif amount_eur is not None:
            amount_eur = -abs(amount_eur) if amount < 0 else abs(amount_eur)

        counterparty = _pick(lowered, *_PAYEE_KEYS)
        category = _pick(lowered, *_CATEGORY_KEYS)
        comment = _pick(lowered, *_COMMENT_KEYS)
        reference = comment or counterparty
        semantic_key = "|".join(
            (
                payment_date.isoformat(),
                _normalize_value(account_name),
                currency,
                f"{amount:.2f}",
                _normalize_value(counterparty),
            )
        )
        explicit_id = _pick(lowered, *_ID_KEYS)
        if explicit_id:
            external_id = explicit_id
        else:
            occurrence = duplicate_counts.get(semantic_key, 0) + 1
            duplicate_counts[semantic_key] = occurrence
            digest = hashlib.sha256(semantic_key.encode("utf-8")).hexdigest()
            external_id = f"semantic:{digest}:{occurrence}"

        payments.append(
            ZenMoneyPayment(
                external_id=external_id,
                payment_date=payment_date,
                reference=reference,
                counterparty=counterparty,
                category=category,
                comment=comment,
                account_name=account_name,
                amount_original=amount,
                currency=currency,
                amount_eur=cents(amount_eur) if amount_eur is not None else None,
                source_row={str(key): _text(value) for key, value in row.items() if key},
            )
        )
    return ZenMoneyLoadResult(tuple(payments), tuple(skipped))


def _read_rows(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    text = _decode(raw)
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(text, newline=""), dialect=dialect))


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _normalize_header(value: str) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", value.casefold().replace("ё", "е"))


def _normalize_value(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().strip().replace("ё", "е"))


def _pick(row: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(_normalize_header(key), "")
        if value:
            return value.strip()
    return ""


def _currency(
    row: Mapping[str, str],
    directional_keys: tuple[str, ...],
    default_currency: str | None,
) -> str:
    currency = _pick(row, *directional_keys, *_CURRENCY_KEYS) or default_currency
    if not currency:
        raise ValueError("ZenMoney business row has no currency; pass --default-currency explicitly")
    return currency.upper()


def _optional_amount(value: str) -> Decimal | None:
    return cents(parse_amount(value)) if value else None


def _is_true(value: str) -> bool:
    return value.casefold() in {"1", "true", "yes", "y", "да"}


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
