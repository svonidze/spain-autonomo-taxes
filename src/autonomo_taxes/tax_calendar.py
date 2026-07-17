from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .tax_rules import ALL_FORM_CODES


_PERIOD_PATTERN = re.compile(r"^(?P<year>20\d{2})(?:-Q[1-4])?$")
_DEADLINE_STATUSES = {"confirmed", "provisional"}
_AEAT_SOURCE_PREFIX = "https://sede.agenciatributaria.gob.es/"


@dataclass(frozen=True)
class TaxCalendarEntry:
    calendar_year: int
    period_key: str
    form_code: str
    filing_opens_on: date
    internal_due_on: date
    statutory_due_on: date
    direct_debit_cutoff_on: date | None
    deadline_status: str
    source_url: str
    source_checked_on: date
    notes: str
    source_hash: str


@dataclass(frozen=True)
class TaxCalendarLoadResult:
    calendar_year: int
    source_file_hash: str
    entries: tuple[TaxCalendarEntry, ...]


def load_tax_calendar(path: Path) -> TaxCalendarLoadResult:
    raw_bytes = path.read_bytes()
    payload = json.loads(raw_bytes.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Tax calendar input must be a JSON object")
    calendar_year = _integer(payload.get("calendar_year"), "calendar_year")
    default_source_url = _text(payload.get("source_url"))
    default_source_checked_on = _date(payload.get("source_checked_on"), "source_checked_on")
    groups = payload.get("entries")
    if not isinstance(groups, list) or not groups:
        raise ValueError("Tax calendar input must contain a non-empty entries list")

    expanded: list[TaxCalendarEntry] = []
    seen: set[tuple[str, str]] = set()
    for index, raw_group in enumerate(groups, start=1):
        if not isinstance(raw_group, dict):
            raise ValueError(f"Tax calendar entry {index} must be an object")
        forms = raw_group.get("forms")
        if not isinstance(forms, list) or not forms:
            raise ValueError(f"Tax calendar entry {index} must contain a non-empty forms list")
        base = _validated_group(
            raw_group,
            calendar_year=calendar_year,
            default_source_url=default_source_url,
            default_source_checked_on=default_source_checked_on,
            index=index,
        )
        for raw_form in forms:
            form_code = str(raw_form).strip()
            if form_code not in ALL_FORM_CODES:
                raise ValueError(f"Tax calendar entry {index} has unsupported form {form_code!r}")
            identity = (base["period_key"], form_code)
            if identity in seen:
                raise ValueError(
                    f"Duplicate tax calendar entry for period {identity[0]} and form {identity[1]}"
                )
            seen.add(identity)
            source_payload = {**base, "calendar_year": calendar_year, "form_code": form_code}
            source_hash = hashlib.sha256(
                json.dumps(source_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            expanded.append(
                TaxCalendarEntry(
                    calendar_year=calendar_year,
                    form_code=form_code,
                    source_hash=source_hash,
                    **base,
                )
            )

    expanded.sort(key=lambda row: (row.statutory_due_on, row.period_key, row.form_code))
    return TaxCalendarLoadResult(
        calendar_year=calendar_year,
        source_file_hash=hashlib.sha256(raw_bytes).hexdigest(),
        entries=tuple(expanded),
    )


def entry_as_record(entry: TaxCalendarEntry) -> dict[str, Any]:
    record = asdict(entry)
    for key, value in tuple(record.items()):
        if isinstance(value, date):
            record[key] = value.isoformat()
    return record


def build_period_ics(
    *,
    period_key: str,
    period_ends_on: date,
    obligations: list[dict[str, Any]],
) -> str:
    actionable = [
        row
        for row in obligations
        if row.get("determination") in {"due", "unknown"}
        and row.get("filing_status") not in {"filed", "waived"}
    ]
    forms = sorted({str(row["obligation_code"]) for row in actionable})
    if not forms:
        return _serialize_ics([])

    filing_dates = [
        date.fromisoformat(str(row["filing_opens_on"]))
        for row in actionable
        if row.get("filing_opens_on")
    ]
    internal_dates = [
        date.fromisoformat(str(row["internal_due_on"]))
        for row in actionable
        if row.get("internal_due_on")
    ]
    debit_dates = [
        date.fromisoformat(str(row["direct_debit_cutoff_on"]))
        for row in actionable
        if row.get("direct_debit_cutoff_on")
    ]
    statutory_dates = [
        date.fromisoformat(str(row.get("due_on") or row.get("calendar_statutory_due_on")))
        for row in actionable
        if row.get("due_on") or row.get("calendar_statutory_due_on")
    ]
    if not filing_dates or not internal_dates or not statutory_dates:
        raise ValueError(f"{period_key} has actionable obligations without confirmed calendar dates")

    filing_opens = min(filing_dates)
    internal_due = min(internal_dates)
    statutory_due = min(statutory_dates)
    form_text = ", ".join(f"Modelo {form}" for form in forms)
    events = [
        ("intake-close", period_ends_on, "Close invoice and expense intake", form_text),
        ("draft", min(filing_opens + timedelta(days=9), internal_due), "Prepare draft tax returns", form_text),
        ("blockers", internal_due - timedelta(days=2), "Resolve tax preparation blockers", form_text),
        ("internal", internal_due, "Complete internal tax review", form_text),
        ("cash", statutory_due - timedelta(days=1), "Confirm tax payment cash", form_text),
        ("statutory", statutory_due, "Submit and pay tax returns", form_text),
        ("evidence", statutory_due + timedelta(days=2), "Archive AEAT filing evidence", form_text),
    ]
    if debit_dates:
        events.append(
            ("direct-debit", min(debit_dates), "Direct debit filing cutoff", form_text)
        )
    return _serialize_ics(events, period_key=period_key)


def _serialize_ics(
    events: list[tuple[str, date, str, str]],
    *,
    period_key: str = "empty",
) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//spain-autonomo-taxes//Tax Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    year = period_key[:4] if period_key[:4].isdigit() else "2000"
    for kind, event_date, summary, description in sorted(events, key=lambda row: (row[1], row[0])):
        uid = f"{period_key}-{kind}@spain-autonomo-taxes"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{year}0101T000000Z",
                f"DTSTART;VALUE=DATE:{event_date:%Y%m%d}",
                f"DTEND;VALUE=DATE:{event_date + timedelta(days=1):%Y%m%d}",
                f"SUMMARY:{_ics_escape(summary)}",
                f"DESCRIPTION:{_ics_escape(description)}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _ics_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _validated_group(
    raw: dict[str, Any],
    *,
    calendar_year: int,
    default_source_url: str,
    default_source_checked_on: date,
    index: int,
) -> dict[str, Any]:
    period_key = _text(raw.get("period_key"))
    if _PERIOD_PATTERN.fullmatch(period_key) is None:
        raise ValueError(f"Tax calendar entry {index} has invalid period_key {period_key!r}")
    filing_opens_on = _date(raw.get("filing_opens_on"), f"entries[{index}].filing_opens_on")
    internal_due_on = _date(raw.get("internal_due_on"), f"entries[{index}].internal_due_on")
    statutory_due_on = _date(raw.get("statutory_due_on"), f"entries[{index}].statutory_due_on")
    direct_debit_cutoff_on = _optional_date(
        raw.get("direct_debit_cutoff_on"),
        f"entries[{index}].direct_debit_cutoff_on",
    )
    deadline_status = _text(raw.get("deadline_status") or "confirmed")
    if deadline_status not in _DEADLINE_STATUSES:
        raise ValueError(f"Tax calendar entry {index} has invalid deadline_status {deadline_status!r}")
    source_url = _text(raw.get("source_url")) or default_source_url
    source_checked_on = (
        _date(raw.get("source_checked_on"), f"entries[{index}].source_checked_on")
        if raw.get("source_checked_on")
        else default_source_checked_on
    )
    notes = _text(raw.get("notes"))

    if statutory_due_on.year != calendar_year:
        raise ValueError(
            f"Tax calendar entry {index} statutory deadline is outside calendar_year {calendar_year}"
        )
    if not filing_opens_on <= internal_due_on <= statutory_due_on:
        raise ValueError(
            f"Tax calendar entry {index} must satisfy filing_opens_on <= internal_due_on <= statutory_due_on"
        )
    if direct_debit_cutoff_on is not None and not (
        filing_opens_on <= direct_debit_cutoff_on <= statutory_due_on
    ):
        raise ValueError(
            f"Tax calendar entry {index} direct debit cutoff must fall inside the filing window"
        )
    if not source_url:
        raise ValueError(f"Tax calendar entry {index} must contain a source URL")
    if deadline_status == "confirmed" and not source_url.startswith(_AEAT_SOURCE_PREFIX):
        raise ValueError(
            f"Confirmed tax calendar entry {index} must cite an official AEAT source URL"
        )
    if deadline_status == "provisional" and "replace" not in notes.casefold():
        raise ValueError(
            f"Provisional tax calendar entry {index} notes must say that the date must be replaced"
        )
    return {
        "period_key": period_key,
        "filing_opens_on": filing_opens_on,
        "internal_due_on": internal_due_on,
        "statutory_due_on": statutory_due_on,
        "direct_debit_cutoff_on": direct_debit_cutoff_on,
        "deadline_status": deadline_status,
        "source_url": source_url,
        "source_checked_on": source_checked_on,
        "notes": notes,
    }


def _date(value: object, field: str) -> date:
    text = _text(value)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date") from exc


def _optional_date(value: object, field: str) -> date | None:
    return None if value is None or value == "" else _date(value, field)


def _integer(value: object, field: str) -> int:
    try:
        result = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if result < 2000 or result > 2100:
        raise ValueError(f"{field} is outside the supported range")
    return result


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()
