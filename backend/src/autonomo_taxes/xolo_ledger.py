from __future__ import annotations

from dataclasses import dataclass
import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
import re

from .money import cents, format_es, maybe_decimal, parse_amount
from .parsers import LedgerEntry, in_ytd, quarter_end


XOLO_LEDGER_FIELDS = [
    "xolo_url",
    "xolo_id",
    "recipient",
    "type",
    "number",
    "date",
    "amount_original",
    "currency",
    "gross_eur",
    "vat_base_eur",
    "irpf_deductible_eur",
    "inclusion_quarter",
    "include_in_modelo130",
    "source_document_path",
    "confidence",
    "notes",
]


@dataclass(frozen=True)
class XoloExpenseRow:
    xolo_url: str
    xolo_id: str
    recipient: str
    expense_type: str
    number: str
    date: date
    amount_original: Decimal
    currency: str
    gross_eur: Decimal | None
    vat_base_eur: Decimal | None
    irpf_deductible_eur: Decimal | None
    inclusion_quarter: str
    include_in_modelo130: bool
    source_document_path: str
    confidence: str
    notes: str

    def to_ledger_entry(self) -> LedgerEntry:
        if self.irpf_deductible_eur is None:
            raise ValueError(f"Missing IRPF deductible amount for Xolo row {self.number or self.xolo_id}")
        if self.gross_eur is None:
            raise ValueError(f"Missing gross EUR amount for Xolo row {self.number or self.xolo_id}")
        document = self.source_document_path or self.xolo_url or f"xolo:{self.xolo_id or self.number}"
        return LedgerEntry(
            kind="expense",
            date=self.date,
            document=document,
            counterparty=self.recipient,
            description=self.number,
            amount_original=self.amount_original,
            currency=self.currency,
            amount_eur=self.gross_eur,
            deductible_eur=self.irpf_deductible_eur,
            category=self.expense_type,
            confidence=self.confidence,
            review_required=False,
            notes=self.notes,
        )


def load_xolo_expense_ledger(path: Path) -> list[XoloExpenseRow]:
    if not path.exists():
        raise FileNotFoundError(f"Xolo expense ledger not found: {path}")
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [field for field in XOLO_LEDGER_FIELDS if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Xolo expense ledger missing required columns: {', '.join(missing)}")
        return [_row_from_csv(row) for row in reader]


def import_xolo_expense_csv(path: Path, fx_rates: dict[str, Decimal] | None = None) -> list[dict[str, str]]:
    fx_rates = fx_rates or {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    if all(field in fieldnames for field in XOLO_LEDGER_FIELDS):
        return [{field: row.get(field, "") for field in XOLO_LEDGER_FIELDS} for row in rows]
    required = {"xolo_url", "xolo_id", "recipient", "type", "number", "date", "amount_original", "currency"}
    missing = sorted(required - set(fieldnames))
    if missing:
        raise ValueError(f"Cannot import Xolo CSV; missing columns: {', '.join(missing)}")
    output: list[dict[str, str]] = []
    for row in rows:
        currency = (row.get("currency") or "").upper()
        amount = maybe_decimal(row.get("amount_original"))
        gross_eur = ""
        if amount is not None and currency == "EUR":
            gross_eur = f"{amount:.2f}"
        elif amount is not None and currency in fx_rates:
            gross_eur = f"{cents(amount * fx_rates[currency]):.2f}"
        vat_base = ""
        subtotal = maybe_decimal(row.get("subtotal_amount"))
        if subtotal is not None and currency == "EUR":
            vat_base = f"{subtotal:.2f}"
        output.append(
            {
                "xolo_url": row.get("xolo_url", ""),
                "xolo_id": row.get("xolo_id", ""),
                "recipient": row.get("recipient", ""),
                "type": row.get("type", ""),
                "number": row.get("number", ""),
                "date": _date_only(row.get("date", "")),
                "amount_original": "" if amount is None else f"{amount:.2f}",
                "currency": currency,
                "gross_eur": gross_eur,
                "vat_base_eur": vat_base,
                "irpf_deductible_eur": "",
                "inclusion_quarter": "",
                "include_in_modelo130": "no",
                "source_document_path": "",
                "confidence": "raw_xolo_import",
                "notes": "Review deductible amount and inclusion quarter before Modelo 130 use",
            }
        )
    return output


def write_xolo_expense_ledger_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=XOLO_LEDGER_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in XOLO_LEDGER_FIELDS})


def reviewed_expense_entries(
    rows: list[XoloExpenseRow],
    year: int,
    quarter: int,
) -> tuple[list[LedgerEntry], list[str]]:
    entries: list[LedgerEntry] = []
    warnings: list[str] = []
    for row in rows_for_modelo130(rows, year, quarter):
        try:
            entries.append(row.to_ledger_entry())
        except ValueError as exc:
            warnings.append(str(exc))
    return entries, warnings


def rows_for_modelo130(rows: list[XoloExpenseRow], year: int, quarter: int) -> list[XoloExpenseRow]:
    selected: list[XoloExpenseRow] = []
    for row in rows:
        if not row.include_in_modelo130:
            continue
        period = _parse_period(row.inclusion_quarter)
        if period is not None:
            period_year, period_quarter = period
            if period_year == year and period_quarter <= quarter:
                selected.append(row)
            continue
        if in_ytd(row.date, year, quarter):
            selected.append(row)
    return selected


def xolo_ledger_total(rows: list[XoloExpenseRow], year: int, quarter: int) -> Decimal:
    return cents(sum((row.irpf_deductible_eur or Decimal("0.00")) for row in rows_for_modelo130(rows, year, quarter)))


def reconcile_xolo_expenses(
    rows: list[XoloExpenseRow],
    local_entries: list[LedgerEntry],
    year: int,
    quarter: int,
) -> list[dict[str, str]]:
    used_local: set[int] = set()
    output: list[dict[str, str]] = []
    for row in sorted(rows, key=lambda item: (item.date, item.recipient, item.number)):
        match_index = _match_local(row, local_entries, used_local)
        matched = local_entries[match_index] if match_index is not None else None
        if match_index is not None:
            used_local.add(match_index)
        status = _reconciliation_status(row, matched, year, quarter)
        local_deductible = matched.deductible_eur if matched else None
        if local_deductible is None and matched and matched.amount_eur is not None and matched.kind == "expense":
            local_deductible = matched.amount_eur
        diff = None
        if row.irpf_deductible_eur is not None and local_deductible is not None:
            diff = cents(row.irpf_deductible_eur - local_deductible)
        output.append(
            {
                "status": status,
                "date": row.date.isoformat(),
                "recipient": row.recipient,
                "number": row.number,
                "currency": row.currency,
                "amount_original": format_es(row.amount_original),
                "gross_eur": format_es(row.gross_eur),
                "irpf_deductible_eur": format_es(row.irpf_deductible_eur),
                "local_deductible_eur": format_es(local_deductible),
                "deductible_diff_eur": format_es(diff),
                "local_document": Path(matched.document).name if matched else "",
                "xolo_id": row.xolo_id,
                "notes": row.notes,
            }
        )
    for index, entry in enumerate(local_entries):
        if index in used_local or not in_ytd(entry.date, year, quarter):
            continue
        if entry.kind not in {"expense", "manual_review"}:
            continue
        output.append(
            {
                "status": "parsed_without_xolo_row",
                "date": entry.date.isoformat() if entry.date else "",
                "recipient": entry.counterparty,
                "number": entry.description,
                "currency": entry.currency,
                "amount_original": format_es(entry.amount_original),
                "gross_eur": format_es(entry.amount_eur),
                "irpf_deductible_eur": "",
                "local_deductible_eur": format_es(entry.deductible_eur or entry.amount_eur),
                "deductible_diff_eur": "",
                "local_document": Path(entry.document).name,
                "xolo_id": "",
                "notes": entry.notes,
            }
        )
    return output


def write_xolo_reconciliation(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "status",
        "date",
        "recipient",
        "number",
        "currency",
        "amount_original",
        "gross_eur",
        "irpf_deductible_eur",
        "local_deductible_eur",
        "deductible_diff_eur",
        "local_document",
        "xolo_id",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _row_from_csv(row: dict[str, str]) -> XoloExpenseRow:
    amount_original = _required_amount(row, "amount_original")
    xolo_url = (row.get("xolo_url") or "").strip()
    xolo_id = (row.get("xolo_id") or "").strip() or _xolo_id_from_url(xolo_url)
    return XoloExpenseRow(
        xolo_url=xolo_url,
        xolo_id=xolo_id,
        recipient=(row.get("recipient") or "").strip(),
        expense_type=(row.get("type") or "").strip(),
        number=(row.get("number") or "").strip(),
        date=date.fromisoformat((row.get("date") or "").strip()),
        amount_original=amount_original,
        currency=(row.get("currency") or "EUR").strip().upper(),
        gross_eur=maybe_decimal(row.get("gross_eur")),
        vat_base_eur=maybe_decimal(row.get("vat_base_eur")),
        irpf_deductible_eur=maybe_decimal(row.get("irpf_deductible_eur")),
        inclusion_quarter=(row.get("inclusion_quarter") or "").strip(),
        include_in_modelo130=_truthy(row.get("include_in_modelo130")),
        source_document_path=(row.get("source_document_path") or "").strip(),
        confidence=(row.get("confidence") or "manual").strip(),
        notes=(row.get("notes") or "").strip(),
    )


def _required_amount(row: dict[str, str], key: str) -> Decimal:
    value = row.get(key)
    if value is None or value == "":
        raise ValueError(f"Missing required amount {key}: {row}")
    return parse_amount(value)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "si", "sí"}


def _parse_period(value: str) -> tuple[int, int] | None:
    if not value:
        return None
    match = re.fullmatch(r"(20\d{2})-?Q([1-4])", value.strip(), flags=re.I)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _xolo_id_from_url(url: str) -> str:
    match = re.search(r"/invoice/(\d+)/", url)
    return match.group(1) if match else ""


def _date_only(value: str) -> str:
    return value.strip()[:10]


def _match_local(row: XoloExpenseRow, local_entries: list[LedgerEntry], used_local: set[int]) -> int | None:
    source_name = _normalize(Path(row.source_document_path).name)
    if source_name:
        for index, entry in enumerate(local_entries):
            if index in used_local:
                continue
            if source_name and source_name in _normalize(Path(entry.document).name):
                return index
    needle = _normalize(row.number)
    if needle:
        for index, entry in enumerate(local_entries):
            if index in used_local:
                continue
            haystack = _normalize(f"{entry.document} {entry.description}")
            if needle in haystack:
                return index
    counterparty_matches = [
        index
        for index, entry in enumerate(local_entries)
        if index not in used_local
        and entry.date == row.date
        and _same_amount(row, entry)
        and _counterparty_overlap(row.recipient, entry.counterparty)
    ]
    if len(counterparty_matches) == 1:
        return counterparty_matches[0]
    amount_matches = [
        index
        for index, entry in enumerate(local_entries)
        if index not in used_local and entry.date == row.date and _same_amount(row, entry)
    ]
    if len(amount_matches) == 1:
        return amount_matches[0]
    return None


def _same_amount(row: XoloExpenseRow, entry: LedgerEntry) -> bool:
    candidates = [entry.amount_original, entry.amount_eur, entry.deductible_eur]
    expected = [row.amount_original, row.gross_eur, row.irpf_deductible_eur, row.vat_base_eur]
    for actual in candidates:
        if actual is None:
            continue
        for target in expected:
            if target is not None and abs(cents(actual) - cents(target)) <= Decimal("0.01"):
                return True
    return False


def _counterparty_overlap(left: str, right: str) -> bool:
    left_tokens = {token for token in re.split(r"\W+", left.lower()) if len(token) >= 4}
    right_tokens = {token for token in re.split(r"\W+", right.lower()) if len(token) >= 4}
    return bool(left_tokens & right_tokens)


def _reconciliation_status(
    row: XoloExpenseRow,
    matched: LedgerEntry | None,
    year: int,
    quarter: int,
) -> str:
    period = _parse_period(row.inclusion_quarter)
    if period is not None and (period[0] > year or (period[0] == year and period[1] > quarter)):
        return "post_filing_row"
    if row.date > quarter_end(year, quarter):
        return "post_filing_row"
    if not row.include_in_modelo130:
        return "parsed_but_excluded" if matched else "excluded_from_submitted_quarter"
    if row.expense_type.lower() == "reconciliation residual":
        return "residual_pending_confirmation"
    if _needs_tax_confirmation(row):
        return "deductibility_pending_confirmation"
    if "asset amortization" in row.recipient.lower() or "amortization" in row.notes.lower():
        return "asset_amortization_decision"
    if matched is None:
        return "missing_locally"
    local_deductible = matched.deductible_eur or matched.amount_eur
    if local_deductible is None:
        return "matched_local_manual_review"
    if row.irpf_deductible_eur is not None and abs(cents(row.irpf_deductible_eur) - cents(local_deductible)) > Decimal("0.01"):
        if "apple" in row.recipient.lower() or "amort" in row.notes.lower() or "asset" in row.notes.lower():
            return "asset_amortization_decision"
        return "amount_mismatch"
    return "matched_local"


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", value.lower())


def _needs_tax_confirmation(row: XoloExpenseRow) -> bool:
    text = f"{row.confidence} {row.notes}".lower()
    return "verify deductibility" in text or "pending confirmation" in text
