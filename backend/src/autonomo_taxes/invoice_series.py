from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import sqlite3
from typing import Any, Iterable


_FULL_NUMBER_RE = re.compile(r"^(?P<series>.+)-(?P<suffix>\d+)$")
_BARE_NUMBER_RE = re.compile(r"^\d+$")


@dataclass(frozen=True)
class InvoiceNumberObservation:
    source: str
    record_id: str
    number: str
    lifecycle_status: str
    issued_on: str | None
    counterparty_id: str | None
    amount_eur_minor: int | None
    declared_series: str | None = None
    linked_document_id: str | None = None


@dataclass(frozen=True)
class ParsedInvoiceNumber:
    observation: InvoiceNumberObservation
    series: str | None
    suffix: int
    width: int
    is_bare: bool


def collect_invoice_number_observations(
    connection: sqlite3.Connection,
    *,
    year: int,
) -> list[InvoiceNumberObservation]:
    year_prefix = f"{year:04d}-%"
    document_rows = connection.execute(
        """
        SELECT d.document_id AS record_id,
               d.document_type AS source,
               d.document_number AS number,
               d.lifecycle_status,
               d.issued_on,
               d.counterparty_id,
               COALESCE(
                   (
                       SELECT t.amount_eur_minor
                       FROM transactions t
                       WHERE t.document_id = d.document_id
                         AND t.direction = 'credit'
                         AND t.lifecycle_status NOT IN ('void', 'rejected', 'duplicate')
                       ORDER BY t.created_at, t.transaction_id
                       LIMIT 1
                   ),
                   CASE WHEN d.currency = 'EUR' THEN d.total_minor END
               ) AS amount_eur_minor
        FROM documents d
        WHERE d.document_type IN ('income_invoice', 'ingresos_book')
          AND d.issued_on LIKE ?
        ORDER BY d.issued_on, d.document_number, d.document_id
        """,
        (year_prefix,),
    ).fetchall()
    draft_rows = connection.execute(
        """
        SELECT oi.outgoing_invoice_draft_id AS record_id,
               oi.external_number AS number,
               oi.lifecycle_status,
               COALESCE(d.issued_on, oi.planned_issue_on) AS issued_on,
               oi.counterparty_id,
               COALESCE(
                   t.amount_eur_minor,
                   CASE WHEN oi.currency = 'EUR' THEN oi.total_minor END
               ) AS amount_eur_minor,
               oi.external_series AS declared_series,
               oi.document_id AS linked_document_id
        FROM outgoing_invoice_drafts oi
        LEFT JOIN documents d ON d.document_id = oi.document_id
        LEFT JOIN transactions t ON t.transaction_id = oi.transaction_id
        WHERE oi.lifecycle_status = 'issued'
          AND COALESCE(d.issued_on, oi.planned_issue_on) LIKE ?
          AND oi.external_number IS NOT NULL
        ORDER BY COALESCE(d.issued_on, oi.planned_issue_on), oi.external_number,
                 oi.outgoing_invoice_draft_id
        """,
        (year_prefix,),
    ).fetchall()

    observations = [
        InvoiceNumberObservation(
            source=str(row["source"]),
            record_id=str(row["record_id"]),
            number=str(row["number"] or "").strip(),
            lifecycle_status=str(row["lifecycle_status"]),
            issued_on=row["issued_on"],
            counterparty_id=row["counterparty_id"],
            amount_eur_minor=row["amount_eur_minor"],
        )
        for row in document_rows
    ]
    observations.extend(
        InvoiceNumberObservation(
            source="issued_draft",
            record_id=str(row["record_id"]),
            number=str(row["number"] or "").strip(),
            lifecycle_status=str(row["lifecycle_status"]),
            issued_on=row["issued_on"],
            counterparty_id=row["counterparty_id"],
            amount_eur_minor=row["amount_eur_minor"],
            declared_series=(
                str(row["declared_series"]).strip()
                if row["declared_series"]
                else None
            ),
            linked_document_id=row["linked_document_id"],
        )
        for row in draft_rows
    )
    return observations


def invoice_series_status(
    observations: Iterable[InvoiceNumberObservation],
    *,
    year: int,
    requested_series: str | None,
    bare_belongs_to_series: bool,
) -> dict[str, Any]:
    if year < 1 or year > 9999:
        raise ValueError("year must be between 1 and 9999")
    requested = (requested_series or "").strip() or None
    parsed: list[ParsedInvoiceNumber] = []
    unrecognized: list[dict[str, Any]] = []
    for observation in observations:
        result, reason = _parse_observation(observation)
        if result is None:
            item = _observation_record(observation)
            item["reason"] = reason
            unrecognized.append(item)
        else:
            parsed.append(result)

    observed_series = sorted({item.series for item in parsed if item.series is not None})
    blockers: list[str] = []
    target_series = requested
    if target_series is None:
        if len(observed_series) == 1:
            target_series = observed_series[0]
        elif not observed_series:
            blockers.append("series_missing")
        else:
            blockers.append("ambiguous_series")

    bare = [item for item in parsed if item.is_bare]
    full_in_series = [
        item for item in parsed if not item.is_bare and item.series == target_series
    ]
    other_series = [
        _parsed_record(item)
        for item in parsed
        if not item.is_bare and item.series != target_series
    ]
    unresolved_bare = [] if bare_belongs_to_series else [_parsed_record(item) for item in bare]
    if bare and not bare_belongs_to_series:
        blockers.append("unresolved_bare")
    if unrecognized:
        blockers.append("unrecognized_number")

    members = list(full_in_series)
    if bare_belongs_to_series and target_series is not None:
        members.extend(bare)

    widths = sorted({item.width for item in members})
    if len(widths) > 1:
        blockers.append("ambiguous_width")
    if target_series is not None and not members:
        blockers.append("no_numbers_in_series")

    duplicates: list[dict[str, Any]] = []
    same_invoice_matches: list[dict[str, Any]] = []
    suffixes: set[int] = set()
    for suffix in sorted({item.suffix for item in members}):
        suffix_members = [item for item in members if item.suffix == suffix]
        bare_members = [item for item in suffix_members if item.is_bare]
        full_members = [item for item in suffix_members if not item.is_bare]
        full_groups = _full_logical_groups(full_members)
        conflict_reasons: list[str] = []
        if len(bare_members) > 1:
            conflict_reasons.append("multiple_bare_documents")
        if len(full_groups) > 1:
            conflict_reasons.append("multiple_full_invoices")
        if len(bare_members) == 1 and len(full_groups) == 1:
            if _same_invoice_identity(bare_members[0], full_groups[0]):
                same_invoice_matches.append(
                    {
                        "suffix": suffix,
                        "bare": _parsed_record(bare_members[0]),
                        "full": [_parsed_record(item) for item in full_groups[0]],
                        "matched_on": ["issued_on", "counterparty_id", "amount_eur_minor"],
                    }
                )
            else:
                conflict_reasons.append("bare_full_identity_mismatch")
        if conflict_reasons:
            duplicates.append(
                {
                    "suffix": suffix,
                    "reasons": conflict_reasons,
                    "observations": [_parsed_record(item) for item in suffix_members],
                }
            )
        suffixes.add(suffix)

    if duplicates:
        blockers.append("duplicate_conflict")

    ordered_suffixes = sorted(suffixes)
    gaps: list[int] = []
    if ordered_suffixes:
        present = set(ordered_suffixes)
        gaps = [
            value
            for value in range(ordered_suffixes[0], ordered_suffixes[-1] + 1)
            if value not in present
        ]
    max_suffix = ordered_suffixes[-1] if ordered_suffixes else None
    next_suffix = max_suffix + 1 if max_suffix is not None else None

    blockers = list(dict.fromkeys(blockers))
    status = "ok" if not blockers else "blocked"
    width = widths[0] if len(widths) == 1 else None
    suggestion = None
    if (
        status == "ok"
        and target_series is not None
        and next_suffix is not None
        and width is not None
    ):
        suggestion = f"{target_series}-{next_suffix:0{width}d}"

    return {
        "year": year,
        "target_series": target_series,
        "series_source": (
            "requested"
            if requested is not None
            else "inferred"
            if target_series is not None
            else "unresolved"
        ),
        "status": status,
        "blocking_reasons": blockers,
        "max_suffix": max_suffix,
        "next_suffix": next_suffix,
        "number_width": width,
        "next_number_suggestion": suggestion,
        "next_number_reserved": False,
        "in_series_suffixes": ordered_suffixes,
        "gaps": gaps,
        "same_invoice_matches": same_invoice_matches,
        "unresolved_bare": unresolved_bare,
        "duplicates": duplicates,
        "other_series": other_series,
        "unrecognized": unrecognized,
        "observed": [
            _parsed_record(item)
            for item in sorted(parsed, key=_parsed_sort_key)
        ],
    }


def _parse_observation(
    observation: InvoiceNumberObservation,
) -> tuple[ParsedInvoiceNumber | None, str | None]:
    number = observation.number.strip()
    if not number:
        return None, "empty_number"
    if observation.declared_series:
        prefix = f"{observation.declared_series}-"
        if not number.startswith(prefix):
            return None, "external_number_does_not_match_declared_series"
        raw_suffix = number[len(prefix) :]
        if _BARE_NUMBER_RE.fullmatch(raw_suffix) is None:
            return None, "non_numeric_suffix"
        return _parsed(observation, observation.declared_series, raw_suffix, is_bare=False)
    if _BARE_NUMBER_RE.fullmatch(number):
        return _parsed(observation, None, number, is_bare=True)
    match = _FULL_NUMBER_RE.fullmatch(number)
    if match is None or not match.group("series").strip():
        return None, "unsupported_number_format"
    return _parsed(observation, match.group("series").strip(), match.group("suffix"), is_bare=False)


def _parsed(
    observation: InvoiceNumberObservation,
    series: str | None,
    raw_suffix: str,
    *,
    is_bare: bool,
) -> tuple[ParsedInvoiceNumber | None, str | None]:
    suffix = int(raw_suffix)
    if suffix <= 0:
        return None, "suffix_must_be_positive"
    return (
        ParsedInvoiceNumber(
            observation=observation,
            series=series,
            suffix=suffix,
            width=len(raw_suffix),
            is_bare=is_bare,
        ),
        None,
    )


def _full_logical_groups(
    members: list[ParsedInvoiceNumber],
) -> list[list[ParsedInvoiceNumber]]:
    groups: list[list[ParsedInvoiceNumber]] = []
    assigned: set[int] = set()
    for index, item in enumerate(members):
        if index in assigned:
            continue
        group = [item]
        assigned.add(index)
        changed = True
        while changed:
            changed = False
            for other_index, other in enumerate(members):
                if other_index in assigned:
                    continue
                if any(_is_linked_representation(existing, other) for existing in group):
                    group.append(other)
                    assigned.add(other_index)
                    changed = True
        groups.append(group)
    return groups


def _is_linked_representation(
    left: ParsedInvoiceNumber,
    right: ParsedInvoiceNumber,
) -> bool:
    a = left.observation
    b = right.observation
    return bool(
        (a.source == "issued_draft" and a.linked_document_id == b.record_id)
        or (b.source == "issued_draft" and b.linked_document_id == a.record_id)
    )


def _same_invoice_identity(
    bare: ParsedInvoiceNumber,
    full_group: list[ParsedInvoiceNumber],
) -> bool:
    full = next(
        (item for item in full_group if item.observation.source == "income_invoice"),
        full_group[0],
    )
    bare_observation = bare.observation
    full_observation = full.observation
    return bool(
        bare_observation.issued_on
        and bare_observation.issued_on == full_observation.issued_on
        and bare_observation.counterparty_id
        and bare_observation.counterparty_id == full_observation.counterparty_id
        and bare_observation.amount_eur_minor is not None
        and bare_observation.amount_eur_minor == full_observation.amount_eur_minor
    )


def _observation_record(observation: InvoiceNumberObservation) -> dict[str, Any]:
    return asdict(observation)


def _parsed_record(parsed: ParsedInvoiceNumber) -> dict[str, Any]:
    record = _observation_record(parsed.observation)
    record.update(
        {
            "series": parsed.series,
            "suffix": parsed.suffix,
            "width": parsed.width,
            "is_bare": parsed.is_bare,
        }
    )
    return record


def _parsed_sort_key(item: ParsedInvoiceNumber) -> tuple[str, int, str, str]:
    return (
        item.observation.issued_on or "",
        item.suffix,
        item.observation.source,
        item.observation.record_id,
    )
