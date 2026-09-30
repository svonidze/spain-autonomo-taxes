from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path, PureWindowsPath
from typing import Mapping
from uuid import UUID

from .money import parse_amount


EXPENSE_INTAKE = "expense_intake"
INCOME_INTAKE = "income_intake"
INTAKE_TABS = (EXPENSE_INTAKE, INCOME_INTAKE)

EXPENSE_INTAKE_FIELDS = (
    "system_id",
    "file_name",
    "supplier",
    "invoice_number",
    "invoice_date",
    "gross_amount",
    "currency",
    "business_purpose",
    "business_use_percent",
    "expected_use_over_one_year",
    "notes",
)
INCOME_INTAKE_FIELDS = (
    "system_id",
    "file_name",
    "customer",
    "invoice_number",
    "issue_date",
    "service_period_from",
    "service_period_to",
    "service_description",
    "gross_amount",
    "currency",
    "payment_due_date",
    "correction_of",
    "notes",
)
INTAKE_FIELDS = {
    EXPENSE_INTAKE: EXPENSE_INTAKE_FIELDS,
    INCOME_INTAKE: INCOME_INTAKE_FIELDS,
}

_REQUIRED_FIELDS = {
    EXPENSE_INTAKE: {
        "file_name",
        "supplier",
        "invoice_number",
        "invoice_date",
        "gross_amount",
        "currency",
        "business_purpose",
    },
    INCOME_INTAKE: {
        "file_name",
        "customer",
        "invoice_number",
        "issue_date",
        "service_period_from",
        "service_period_to",
        "service_description",
        "gross_amount",
        "currency",
    },
}
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_USE_OVER_ONE_YEAR = {
    "": "unknown",
    "yes": "yes",
    "no": "no",
    "unknown": "unknown",
    "да": "yes",
    "нет": "no",
    "неизвестно": "unknown",
}


class IntakeSheetError(ValueError):
    """Raised when an intake CSV or row cannot be accepted safely."""


@dataclass(frozen=True)
class IntakeSheetRow:
    tab: str
    row_number: int
    system_id: str | None
    values: dict[str, str]
    tax_date: date
    gross_amount: Decimal
    currency: str

    @property
    def period_key(self) -> str:
        return quarter_key(self.tax_date)

    @property
    def document_kind(self) -> str:
        return "expense_invoice" if self.tab == EXPENSE_INTAKE else "income_invoice"

    @property
    def counterparty_name(self) -> str:
        field = "supplier" if self.tab == EXPENSE_INTAKE else "customer"
        return self.values[field]

    @property
    def document_number(self) -> str:
        return self.values["invoice_number"]

    @property
    def row_fingerprint(self) -> str:
        payload = {"tab": self.tab, **self.values}
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class IntakeRowError:
    row_number: int
    message: str


@dataclass(frozen=True)
class IntakeCsv:
    tab: str
    headers: tuple[str, ...]
    raw_rows: tuple[dict[str, str], ...]
    rows: tuple[IntakeSheetRow, ...]
    errors: tuple[IntakeRowError, ...]


def load_intake_csv(path: Path, tab: str) -> IntakeCsv:
    fields = _fields_for_tab(tab)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = tuple(reader.fieldnames or ())
        if headers != fields:
            raise IntakeSheetError(
                f"{tab} headers must be exactly: {', '.join(fields)}; "
                f"received: {', '.join(headers) or '<none>'}"
            )
        raw_rows = tuple(
            {field: str(raw.get(field) or "") for field in fields}
            for raw in reader
        )

    rows: list[IntakeSheetRow] = []
    errors: list[IntakeRowError] = []
    for offset, raw in enumerate(raw_rows, start=2):
        if not any(value.strip() for value in raw.values()):
            continue
        try:
            rows.append(parse_intake_row(tab, offset, raw))
        except (IntakeSheetError, ValueError) as exc:
            errors.append(IntakeRowError(row_number=offset, message=str(exc)))
    return IntakeCsv(
        tab=tab,
        headers=headers,
        raw_rows=raw_rows,
        rows=tuple(rows),
        errors=tuple(errors),
    )


def parse_intake_row(
    tab: str,
    row_number: int,
    raw: Mapping[str, object],
) -> IntakeSheetRow:
    fields = _fields_for_tab(tab)
    values = {field: str(raw.get(field) or "").strip() for field in fields}
    missing = sorted(field for field in _REQUIRED_FIELDS[tab] if not values[field])
    if missing:
        raise IntakeSheetError(f"row {row_number}: missing {', '.join(missing)}")

    for field, value in values.items():
        if field == "system_id" or not value:
            continue
        if value.startswith(_FORMULA_PREFIXES):
            raise IntakeSheetError(
                f"row {row_number}: {field} must not start with a spreadsheet formula prefix"
            )

    system_id = values.pop("system_id") or None
    if system_id is not None:
        try:
            UUID(system_id)
        except ValueError as exc:
            raise IntakeSheetError(f"row {row_number}: system_id is not a UUID") from exc

    date_field = "invoice_date" if tab == EXPENSE_INTAKE else "issue_date"
    tax_date = _parse_date(values[date_field], row_number=row_number, field=date_field)
    gross_amount = parse_amount(values["gross_amount"])
    if gross_amount <= 0:
        raise IntakeSheetError(f"row {row_number}: gross_amount must be greater than zero")
    values["gross_amount"] = f"{gross_amount:.2f}"

    currency = values["currency"].upper()
    if len(currency) != 3 or not currency.isalpha():
        raise IntakeSheetError(f"row {row_number}: currency must be a three-letter ISO code")
    values["currency"] = currency

    if tab == EXPENSE_INTAKE:
        business_use = values["business_use_percent"]
        if business_use:
            ratio = parse_amount(business_use)
            if ratio < 0 or ratio > 100:
                raise IntakeSheetError(
                    f"row {row_number}: business_use_percent must be between 0 and 100"
                )
            values["business_use_percent"] = _canonical_decimal(ratio)
        else:
            values["business_use_percent"] = "100"
        use_key = values["expected_use_over_one_year"].casefold()
        if use_key not in _USE_OVER_ONE_YEAR:
            raise IntakeSheetError(
                f"row {row_number}: expected_use_over_one_year must be yes, no, or unknown"
            )
        values["expected_use_over_one_year"] = _USE_OVER_ONE_YEAR[use_key]
    else:
        service_from = _parse_date(
            values["service_period_from"],
            row_number=row_number,
            field="service_period_from",
        )
        service_to = _parse_date(
            values["service_period_to"],
            row_number=row_number,
            field="service_period_to",
        )
        if service_to < service_from:
            raise IntakeSheetError(
                f"row {row_number}: service_period_to precedes service_period_from"
            )
        if values["payment_due_date"]:
            due_on = _parse_date(
                values["payment_due_date"],
                row_number=row_number,
                field="payment_due_date",
            )
            if due_on < tax_date:
                raise IntakeSheetError(
                    f"row {row_number}: payment_due_date precedes issue_date"
                )

    return IntakeSheetRow(
        tab=tab,
        row_number=row_number,
        system_id=system_id,
        values=values,
        tax_date=tax_date,
        gross_amount=gross_amount,
        currency=currency,
    )


def resolve_evidence_path(row: IntakeSheetRow, inbox_root: Path) -> Path:
    folder = "expense_invoice" if row.tab == EXPENSE_INTAKE else "income_invoice"
    base = (inbox_root / row.period_key / folder).resolve()
    raw_value = row.values["file_name"]
    if "\\" in raw_value:
        windows_path = PureWindowsPath(raw_value)
        if windows_path.is_absolute() or windows_path.drive:
            raise IntakeSheetError(
                f"row {row.row_number}: file_name must resolve inside "
                f"{row.period_key}/{folder}"
            )
        raw_path = Path(*windows_path.parts)
    else:
        raw_path = Path(raw_value)
    candidate = raw_path.resolve() if raw_path.is_absolute() else (base / raw_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise IntakeSheetError(
            f"row {row.row_number}: file_name must resolve inside {row.period_key}/{folder}"
        ) from exc
    if not candidate.is_file():
        raise IntakeSheetError(
            f"row {row.row_number}: evidence file not found in {row.period_key}/{folder}: "
            f"{row.values['file_name']}"
        )
    return candidate


def write_intake_writeback_csv(
    path: Path,
    intake: IntakeCsv,
    system_ids_by_row: Mapping[int, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(intake.headers))
        writer.writeheader()
        for row_number, raw in enumerate(intake.raw_rows, start=2):
            output = dict(raw)
            if row_number in system_ids_by_row:
                output["system_id"] = system_ids_by_row[row_number]
            writer.writerow(output)


def quarter_key(value: date) -> str:
    return f"{value.year}-Q{((value.month - 1) // 3) + 1}"


def _fields_for_tab(tab: str) -> tuple[str, ...]:
    try:
        return INTAKE_FIELDS[tab]
    except KeyError as exc:
        raise IntakeSheetError(
            f"Unsupported intake tab {tab!r}; expected one of {', '.join(INTAKE_TABS)}"
        ) from exc


def _parse_date(raw: str, *, row_number: int, field: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise IntakeSheetError(
            f"row {row_number}: {field} must use YYYY-MM-DD"
        ) from exc


def _canonical_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")
