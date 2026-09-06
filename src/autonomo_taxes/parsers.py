from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import re

from .money import RATE_PLACES, cents, parse_amount
from .pdf_text import readable_text

EN_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

ES_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


@dataclass
class LedgerEntry:
    kind: str
    date: date | None
    document: str
    counterparty: str
    description: str
    amount_original: Decimal | None
    currency: str
    amount_eur: Decimal | None
    deductible_eur: Decimal | None
    category: str
    confidence: str
    review_required: bool
    notes: str = ""

    def as_row(self) -> dict[str, str]:
        row = asdict(self)
        row["date"] = self.date.isoformat() if self.date else ""
        for key in ("amount_original", "amount_eur", "deductible_eur"):
            row[key] = "" if row[key] is None else f"{cents(row[key]):.2f}"
        row["review_required"] = "yes" if self.review_required else "no"
        return row


def parse_any_date(text: str) -> date | None:
    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", text)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))

    numeric = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", text)
    if numeric:
        a = int(numeric.group(1))
        b = int(numeric.group(2))
        y = int(numeric.group(3))
        if y < 100:
            y += 2000
        # Spanish documents in this archive use dd/mm; Namecheap receipts use mm/dd.
        if a > 12:
            return date(y, b, a)
        if b > 12:
            return date(y, a, b)
        return date(y, b, a)

    dotted = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b", text)
    if dotted:
        return date(int(dotted.group(3)), int(dotted.group(2)), int(dotted.group(1)))

    month_names = "|".join(EN_MONTHS)
    english = re.search(rf"\b({month_names})\s+(\d{{1,2}}),\s*(20\d{{2}})\b", text, re.I)
    if english:
        return date(int(english.group(3)), EN_MONTHS[english.group(1).lower()], int(english.group(2)))

    es_month_names = "|".join(ES_MONTHS)
    spanish = re.search(rf"\b(\d{{1,2}})\s+({es_month_names})\s+(20\d{{2}})\b", text, re.I)
    if spanish:
        return date(int(spanish.group(3)), ES_MONTHS[spanish.group(2).lower()], int(spanish.group(1)))
    return None


def parse_us_numeric_date(text: str) -> date | None:
    match = re.search(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text)
    if not match:
        return None
    return date(int(match.group(3)), int(match.group(1)), int(match.group(2)))


def parse_date_from_filename(name: str) -> date | None:
    compact = re.search(r"\b(20\d{2})(\d{2})(\d{2})\b", name)
    if compact:
        try:
            return date(int(compact.group(1)), int(compact.group(2)), int(compact.group(3)))
        except ValueError:
            pass
    dashed = re.search(r"\b(20\d{2})[-_](\d{2})[-_](\d{2})\b", name)
    if dashed:
        try:
            return date(int(dashed.group(1)), int(dashed.group(2)), int(dashed.group(3)))
        except ValueError:
            return None
    return None


def quarter_end(year: int, quarter: int) -> date:
    return date(year, quarter * 3, 31 if quarter in {1, 4} else 30)


def in_ytd(value: date | None, year: int, quarter: int) -> bool:
    return value is not None and date(year, 1, 1) <= value <= quarter_end(year, quarter)


def parse_income_invoice(path: Path, text: str) -> LedgerEntry | None:
    invoice_no = _search(r"Invoice No:\s*(FACT-\d{4}-\d{5})", text)
    issued = _search(r"Date:\s*(20\d{2}-\d{2}-\d{2})", text)
    total = re.search(r"Invoice total:\s*([\d\s\u00a0\u202f.,]+)\s*(EUR|USD)", text, re.I)
    if not invoice_no or not issued or not total:
        return None

    client = "Unknown"
    if "Example Customer One" in text:
        client = "Example Customer One"
    elif "Example Customer Two" in text:
        client = "Example Customer Two"

    original = parse_amount(total.group(1))
    currency = total.group(2).upper()
    amount_eur = original if currency == "EUR" else None
    return LedgerEntry(
        kind="income",
        date=datetime.strptime(issued, "%Y-%m-%d").date(),
        document=str(path),
        counterparty=client,
        description=invoice_no,
        amount_original=original,
        currency=currency,
        amount_eur=amount_eur,
        deductible_eur=None,
        category="service_income",
        confidence="high",
        review_required=False,
        notes="",
    )


def parse_expense(
    path: Path,
    text: str,
    extraction_error: str | None = None,
    asset_review_threshold_eur: Decimal = Decimal("600.00"),
) -> LedgerEntry:
    suffix = path.suffix.lower()
    if suffix != ".pdf":
        return _manual(path, "unsupported non-PDF document", extraction_error)
    if extraction_error:
        return _manual(path, "PDF text extraction failed", extraction_error)

    if "Example Accounting Provider" in text and "Base (sin IVA):" in text:
        return _expense_from_pattern(
            path,
            text,
            counterparty="Example Accounting Provider SL",
            category="accounting_service_domestic",
            date_pattern=r"Fecha de emisión:\s*(\d{2}/\d{2}/20\d{2})",
            amount_pattern=r"Base \(sin IVA\):\s*([\d.,]+)\s*EUR",
            currency="EUR",
            confidence="high",
            notes="Uses base without recoverable IVA",
        )

    if "TGSS. COTIZACION" in text:
        issued = parse_any_date(text)
        tgss_text = text[text.index("TGSS. COTIZACION") :]
        labelled = re.search(
            r"(?im)^[^\r\n]{0,160}\b\d{3,}(?:-\d{2,})+\s+([0-9]+(?:\.[0-9]{3})*,[0-9]{2})\s*$",
            tgss_text,
        )
        amount = parse_amount(labelled.group(1)) if labelled else None
        return LedgerEntry(
            kind="expense",
            date=issued,
            document=str(path),
            counterparty="TGSS",
            description="RETA/autonomos contribution",
            amount_original=amount,
            currency="EUR",
            amount_eur=amount,
            deductible_eur=amount,
            category="reta",
            confidence="high" if amount else "low",
            review_required=amount is None,
            notes="Parsed from labelled TGSS bank debit amount" if amount else "Could not find labelled TGSS amount",
        )

    if "Synthetic Party 010" in text:
        return _expense_from_pattern(
            path,
            text,
            counterparty="Synthetic Party 010",
            category="ai_tools_eu_vat",
            date_pattern=r"Date of issue\s+([A-Za-z]+\s+\d{1,2},\s*20\d{2})",
            amount_pattern=r"Total excluding tax\s*€([\d.,]+)",
            currency="EUR",
            confidence="medium",
            notes="Uses total excluding VAT",
        )

    if "Synthetic Party 007" in text:
        return _expense_from_pattern(
            path,
            text,
            counterparty="Anthropic PBC",
            category="ai_tools_foreign",
            date_pattern=r"Date of issue\s+([A-Za-z]+\s+\d{1,2},\s*20\d{2})",
            amount_pattern=r"Total\s*€([\d.,]+)",
            currency="EUR",
            confidence="medium",
            notes="No VAT base found; uses invoice total",
        )

    if "Synthetic Party 014" in text:
        return _expense_from_pattern(
            path,
            text,
            counterparty="Synthetic Party 014",
            category="ai_tools_foreign",
            date_pattern=r"Date paid\s+([A-Za-z]+\s+\d{1,2},\s*20\d{2})",
            amount_pattern=r"Subtotal\s*€([\d.,]+)",
            currency="EUR",
            confidence="medium",
            notes="Reverse-charge receipt; uses subtotal",
        )

    if "Cursor" in text and "Anysphere" in text:
        return _expense_from_pattern(
            path,
            text,
            counterparty="Anysphere Inc / Cursor",
            category="dev_tools_foreign",
            date_pattern=r"Date of issue\s+([A-Za-z]+\s+\d{1,2},\s*20\d{2})",
            amount_pattern=r"Total excluding tax\s*\$([\d.,]+)",
            currency="USD",
            confidence="medium",
            notes="Uses total excluding VAT",
        )

    if "Synthetic Party 002" in text:
        entry = _expense_from_pattern(
            path,
            text,
            counterparty="Namecheap Inc",
            category="domains_foreign",
            date_pattern=r"Order Date\s*:\s*(\d{1,2}/\d{1,2}/20\d{2})",
            amount_pattern=r"(?:Sub Total|TOTAL)\s*\$([\d.,]+)",
            currency="USD",
            confidence="medium",
            notes="Receipt, not factura; manual review required before deducting",
        )
        raw = _search(r"Order Date\s*:\s*(\d{1,2}/\d{1,2}/20\d{2})", text)
        if raw:
            entry.date = parse_us_numeric_date(raw) or entry.date
        entry.review_required = True
        entry.deductible_eur = None
        return entry

    if "Amazon EU" in text and "IVA" in text:
        return _expense_from_pattern(
            path,
            text,
            counterparty="Amazon EU",
            category="office_equipment_domestic",
            date_pattern=r"Fecha del pedido\s+(\d{1,2}\s+\w+\s+20\d{2})",
            amount_pattern=r"21%\s+([\d.,]+)\s*€\s+[\d.,]+\s*€",
            currency="EUR",
            confidence="medium",
            notes="Uses base without recoverable IVA",
        )

    if "Apple Retail Spain" in text or "Apple Distribution International" in text:
        entry = _expense_from_pattern(
            path,
            text,
            counterparty="Apple",
            category="apple_domestic",
            date_pattern=r"Fecha de (?:factura|emisión):\s*(\d{1,2}\.\d{1,2}\.20\d{2})",
            amount_pattern=r"Base imponible\s+IVA\s+Tasa de IVA\s*([\d.,]+)",
            currency="EUR",
            confidence="medium",
            notes="Uses base without recoverable IVA",
        )
        if entry.amount_original and entry.amount_original >= asset_review_threshold_eur:
            entry.category = "asset_review"
            entry.review_required = True
            entry.deductible_eur = None
            entry.notes = "Large Apple equipment purchase; amortization/capitalization review required"
        return entry

    if "ROSSELLI" in text or "MBP 16" in text or "MacBook" in text:
        entry = _expense_from_pattern(
            path,
            text,
            counterparty="Rosselli y Ruiz SL",
            category="asset_review",
            date_pattern=r"Fecha\s*(\d{2}/\d{2}/20\d{2})",
            amount_pattern=r"Base imponible\s+([\d.,]+)\s+21,00",
            currency="EUR",
            confidence="medium",
            notes="Large equipment purchase; amortization/capitalization review required",
        )
        if entry.amount_original is None or entry.amount_original >= asset_review_threshold_eur:
            entry.review_required = True
            entry.deductible_eur = None
        return entry

    if "Eni Plenitude Iberia" in text and "FACTURA DE ELECTRICIDAD" in text:
        entry = _expense_from_pattern(
            path,
            text,
            counterparty="Synthetic Party 005",
            category="home_utility_review",
            date_pattern=r"Fecha de factura\s*:\s*(\d{1,2}/\d{1,2}/20\d{2})",
            amount_pattern=r"TOTAL IMPORTE FACTURA\s*([\d.,]+)\s*€",
            currency="EUR",
            confidence="high",
            notes="Home electricity bill; business-use percentage and IVA treatment require review",
        )
        invoice_number = _search(r"N[º°o]\s*de factura\s*:\s*([A-Z0-9-]+)", text)
        if invoice_number:
            entry.description = invoice_number
        entry.deductible_eur = None
        entry.review_required = True
        return entry

    issued = parse_any_date(text) or parse_date_from_filename(path.name)
    return LedgerEntry(
        kind="expense",
        date=issued,
        document=str(path),
        counterparty="Unknown",
        description=path.name,
        amount_original=None,
        currency="",
        amount_eur=None,
        deductible_eur=None,
        category="unknown",
        confidence="low",
        review_required=True,
        notes="No supported parser matched",
    )


def scan_income_dir(path: Path) -> tuple[list[LedgerEntry], list[LedgerEntry]]:
    parsed: list[LedgerEntry] = []
    manual: list[LedgerEntry] = []
    for doc in sorted(p for p in path.iterdir() if p.is_file()):
        if doc.suffix.lower() != ".pdf":
            manual.append(_manual(doc, "unsupported income invoice", "Income document is not a PDF"))
            continue
        text, error = readable_text(doc)
        if error:
            manual.append(_manual(doc, "income PDF extraction failed", error))
            continue
        entry = parse_income_invoice(doc, text)
        if entry is None:
            manual.append(_manual(doc, "unsupported income invoice", "No parser matched"))
        else:
            parsed.append(entry)
    return parsed, manual


def scan_expense_dir(
    path: Path,
    asset_review_threshold_eur: Decimal = Decimal("600.00"),
) -> tuple[list[LedgerEntry], list[LedgerEntry]]:
    parsed: list[LedgerEntry] = []
    manual: list[LedgerEntry] = []
    for doc in sorted(p for p in path.iterdir() if p.is_file()):
        text = ""
        error = None
        if doc.suffix.lower() == ".pdf":
            text, error = readable_text(doc)
        entry = parse_expense(doc, text, error, asset_review_threshold_eur)
        if entry.review_required:
            manual.append(entry)
        else:
            parsed.append(entry)
    return parsed, manual


def apply_fx(entries: list[LedgerEntry], fx_rates: dict[str, Decimal]) -> list[str]:
    warnings: list[str] = []
    for entry in entries:
        if entry.currency == "EUR":
            if entry.amount_eur is None and entry.amount_original is not None:
                entry.amount_eur = entry.amount_original
            if entry.kind == "expense" and entry.deductible_eur is None and not entry.review_required:
                entry.deductible_eur = entry.amount_eur
            continue
        if not entry.currency or entry.amount_original is None:
            continue
        rate = fx_rates.get(entry.currency)
        if rate is None and entry.amount_eur is None:
            entry.review_required = True
            entry.notes = (entry.notes + "; " if entry.notes else "") + f"Missing FX rate for {entry.currency}"
            warnings.append(f"Missing FX rate for {entry.currency}: {entry.document}")
            continue
        converted_from_fx = False
        if entry.amount_eur is None:
            assert rate is not None
            entry.amount_eur = cents(entry.amount_original * rate)
            converted_from_fx = True
        if entry.kind == "expense" and entry.deductible_eur is None and not entry.review_required:
            entry.deductible_eur = entry.amount_eur
        if converted_from_fx:
            entry.notes = (entry.notes + "; " if entry.notes else "") + f"FX {entry.currency}->{rate}"
        elif rate is not None:
            entry.notes = (entry.notes + "; " if entry.notes else "") + f"Uses provided EUR amount; configured FX {entry.currency}->{rate} not applied"
        else:
            entry.notes = (entry.notes + "; " if entry.notes else "") + "Uses provided EUR amount"
    return warnings


def derive_single_currency_rate(
    entries: list[LedgerEntry],
    target_total_eur: Decimal,
    currency: str,
    year: int,
    quarter: int,
) -> Decimal | None:
    direct = Decimal("0.00")
    original = Decimal("0.00")
    for entry in entries:
        if entry.kind != "income" or not in_ytd(entry.date, year, quarter):
            continue
        if entry.currency == currency and entry.amount_original is not None:
            original += entry.amount_original
        elif entry.amount_eur is not None:
            direct += entry.amount_eur
    if original == 0:
        return None
    return ((target_total_eur - direct) / original).quantize(RATE_PLACES, rounding=ROUND_HALF_UP)


def _expense_from_pattern(
    path: Path,
    text: str,
    counterparty: str,
    category: str,
    date_pattern: str,
    amount_pattern: str,
    currency: str,
    confidence: str,
    notes: str,
) -> LedgerEntry:
    date_raw = _search(date_pattern, text)
    amount_raw = _search(amount_pattern, text)
    amount = parse_amount(amount_raw) if amount_raw else None
    issued = parse_any_date(date_raw) if date_raw else parse_any_date(text)
    issued = issued or parse_date_from_filename(path.name)
    amount_eur = amount if currency == "EUR" else None
    return LedgerEntry(
        kind="expense",
        date=issued,
        document=str(path),
        counterparty=counterparty,
        description=path.name,
        amount_original=amount,
        currency=currency,
        amount_eur=amount_eur,
        deductible_eur=amount_eur,
        category=category,
        confidence=confidence if amount is not None and issued is not None else "low",
        review_required=amount is None or issued is None,
        notes=notes,
    )


def _search(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, re.I | re.S)
    return match.group(1).strip() if match else None


def _manual(path: Path, category: str, notes: str | None) -> LedgerEntry:
    return LedgerEntry(
        kind="manual_review",
        date=parse_date_from_filename(path.name),
        document=str(path),
        counterparty="Unknown",
        description=path.name,
        amount_original=None,
        currency="",
        amount_eur=None,
        deductible_eur=None,
        category=category,
        confidence="low",
        review_required=True,
        notes=notes or "",
    )
