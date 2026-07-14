from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
import re
from typing import Mapping

from .money import cents, parse_amount
from .parsers import parse_any_date


@dataclass(frozen=True)
class RevolutPayment:
    payment_id: str
    payment_date: date
    reference: str
    counterparty: str
    description: str
    amount_original: Decimal
    currency: str
    amount_eur: Decimal | None
    fee_original: Decimal | None
    fee_eur: Decimal | None
    state: str
    source_row: dict[str, str]


@dataclass(frozen=True)
class RevolutMatchCandidate:
    candidate_id: str
    recognition_date: date
    payment_date: date | None
    reference: str
    amount_original: Decimal | None
    currency: str
    amount_eur: Decimal | None


@dataclass(frozen=True)
class RevolutMatchResult:
    payment_id: str
    outcome: str
    matched_candidate_ids: tuple[str, ...]
    payment_date: date
    recognition_date: date | None
    reference: str


_DATE_KEYS = ("completed date", "date", "started date", "created date")
_REFERENCE_KEYS = ("reference", "payment reference", "bank transfer reference", "transfer reference")
_COUNTERPARTY_KEYS = ("counterparty", "beneficiary", "merchant", "payee")
_DESCRIPTION_KEYS = ("description", "details", "type")
_AMOUNT_KEYS = ("amount", "total amount", "card amount")
_PAID_OUT_KEYS = ("paid out", "amount paid out", "outgoing")
_PAID_IN_KEYS = ("paid in", "amount paid in", "incoming")
_FEE_KEYS = ("fee", "fees", "fee paid", "commission")
_CURRENCY_KEYS = ("currency", "card currency", "payment currency")
_AMOUNT_EUR_KEYS = ("amount in eur", "settled amount", "settled amount eur", "eur amount")
_FEE_EUR_KEYS = ("fee in eur", "settled fee", "settled fee eur", "eur fee")
_STATE_KEYS = ("state", "status")


def load_revolut_payments_csv(path: Path) -> list[RevolutPayment]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return [
            normalize_revolut_payment(row, source_row_number=row_number)
            for row_number, row in enumerate(reader, start=2)
        ]


def normalize_revolut_payment(
    row: Mapping[str, object],
    *,
    source_row_number: int | None = None,
) -> RevolutPayment:
    lowered = {_normalize_header(key): _stringify(value) for key, value in row.items() if key is not None}

    payment_date = _require_date(_pick(lowered, *_DATE_KEYS), row)
    reference = _pick(lowered, *_REFERENCE_KEYS)
    counterparty = _pick(lowered, *_COUNTERPARTY_KEYS)
    description = _pick(lowered, *_DESCRIPTION_KEYS)
    amount_original = _require_amount(lowered)
    currency = (_pick(lowered, *_CURRENCY_KEYS) or _detect_currency(lowered) or "EUR").upper()
    amount_eur = _optional_amount(_pick(lowered, *_AMOUNT_EUR_KEYS))
    if amount_eur is None and currency == "EUR":
        amount_eur = amount_original
    elif amount_eur is not None and amount_original < 0 and amount_eur > 0:
        amount_eur = -amount_eur
    fee_original = _optional_amount(_pick(lowered, *_FEE_KEYS))
    fee_eur = _optional_amount(_pick(lowered, *_FEE_EUR_KEYS))
    if fee_eur is None and fee_original is not None and currency == "EUR":
        fee_eur = fee_original
    state = _pick(lowered, *_STATE_KEYS)
    source_row = {str(key): _stringify(value) for key, value in row.items() if key is not None}

    payment_id = "|".join(
        [
            payment_date.isoformat(),
            currency,
            f"{amount_original:.2f}",
            f"{amount_eur:.2f}" if amount_eur is not None else "",
            _normalize_reference(reference),
            _normalize_reference(counterparty),
            str(source_row_number or ""),
        ]
    )
    return RevolutPayment(
        payment_id=payment_id,
        payment_date=payment_date,
        reference=reference,
        counterparty=counterparty,
        description=description,
        amount_original=amount_original,
        currency=currency,
        amount_eur=amount_eur,
        fee_original=fee_original,
        fee_eur=fee_eur,
        state=state,
        source_row=source_row,
    )


def match_revolut_payments(
    payments: list[RevolutPayment],
    candidates: list[RevolutMatchCandidate],
) -> list[RevolutMatchResult]:
    results: list[RevolutMatchResult] = []
    consumed_candidate_ids: set[str] = set()
    for payment in payments:
        exact_candidates = [
            candidate
            for candidate in candidates
            if candidate.candidate_id not in consumed_candidate_ids
            and _is_candidate_match(payment, candidate)
        ]
        exact_candidates.sort(key=lambda candidate: candidate.candidate_id)
        if len(exact_candidates) == 1:
            candidate = exact_candidates[0]
            consumed_candidate_ids.add(candidate.candidate_id)
            results.append(
                RevolutMatchResult(
                    payment_id=payment.payment_id,
                    outcome="exact",
                    matched_candidate_ids=(candidate.candidate_id,),
                    payment_date=payment.payment_date,
                    recognition_date=candidate.recognition_date,
                    reference=payment.reference,
                )
            )
            continue
        if exact_candidates:
            results.append(
                RevolutMatchResult(
                    payment_id=payment.payment_id,
                    outcome="ambiguous",
                    matched_candidate_ids=tuple(candidate.candidate_id for candidate in exact_candidates),
                    payment_date=payment.payment_date,
                    recognition_date=None,
                    reference=payment.reference,
                )
            )
            continue
        results.append(
            RevolutMatchResult(
                payment_id=payment.payment_id,
                outcome="unmatched",
                matched_candidate_ids=(),
                payment_date=payment.payment_date,
                recognition_date=None,
                reference=payment.reference,
            )
        )
    return results


def _is_candidate_match(payment: RevolutPayment, candidate: RevolutMatchCandidate) -> bool:
    if not _amounts_match(payment, candidate):
        return False
    candidate_date = candidate.payment_date or candidate.recognition_date
    if abs((payment.payment_date - candidate_date).days) > 90:
        return False
    payment_reference = _normalize_reference(payment.reference)
    candidate_reference = _normalize_reference(candidate.reference)
    if payment_reference and candidate_reference:
        return payment_reference == candidate_reference
    if candidate.payment_date is not None:
        return candidate.payment_date == payment.payment_date
    return True


def _amounts_match(payment: RevolutPayment, candidate: RevolutMatchCandidate) -> bool:
    if (
        candidate.amount_original is not None
        and candidate.currency.upper() == payment.currency
        and cents(candidate.amount_original) == cents(payment.amount_original)
    ):
        return True
    if candidate.amount_eur is not None and payment.amount_eur is not None:
        return cents(candidate.amount_eur) == cents(payment.amount_eur)
    return False


def _normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _normalize_reference(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _pick(row: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value:
            return value.strip()
    return ""


def _stringify(value: object) -> str:
    return "" if value is None else str(value).strip()


def _require_date(raw: str, row: Mapping[str, object]) -> date:
    parsed = parse_any_date(raw)
    if parsed is None:
        raise ValueError(f"Revolut row is missing a parseable payment date: {row!r}")
    return parsed


def _require_amount(row: Mapping[str, str]) -> Decimal:
    amount = _optional_amount(_pick(row, *_AMOUNT_KEYS))
    if amount is not None:
        return amount
    paid_out = _optional_amount(_pick(row, *_PAID_OUT_KEYS))
    if paid_out is not None:
        return -abs(paid_out)
    paid_in = _optional_amount(_pick(row, *_PAID_IN_KEYS))
    if paid_in is not None:
        return abs(paid_in)
    raise ValueError(f"Revolut row is missing an amount: {row!r}")


def _optional_amount(raw: str) -> Decimal | None:
    if not raw:
        return None
    return cents(parse_amount(raw))


def _detect_currency(row: Mapping[str, str]) -> str:
    for key in (*_AMOUNT_KEYS, *_PAID_OUT_KEYS, *_PAID_IN_KEYS, *_AMOUNT_EUR_KEYS):
        value = row.get(key, "")
        if "€" in value:
            return "EUR"
        if "$" in value:
            return "USD"
        match = re.search(r"\b([A-Z]{3})\b", value)
        if match:
            return match.group(1)
    return ""
