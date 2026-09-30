"""RETA bracket check: the year-to-date view of the TGSS annual regularisation.

Decision support only (see DESIGN.md, "RETA bracket check"). Its amounts are
never added to or netted against tax cash due, and missing inputs give
``unknown``, never ``ok``. The arithmetic follows RD 2064/1995 (BOE-A-1996-1579)
art. 44.2 and 46.2 and is exact (``Fraction``); rounding is for display only.
"""
from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from calendar import monthrange
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

WORKER_KINDS = ("individual", "societario", "colaborador")
REGIMES = ("base", "tarifa_plana")
# Reasons only the ledger-side caller can know; each one also makes the YTD
# income (or its day divisor) untrustworthy, so income-derived values go null.
CALLER_REASONS = frozenset({
    "ledger_blocked",
    "period_missing",
    "tarifa_plana_extension_unverified",
    "unposted_rows_in_window",
    "worker_kind_changed",
})
_INCOME_BLOCKERS = CALLER_REASONS | {"table_unavailable"}
# ponytail: fixed 10 % of the tramo width; tune if users find it noisy.
BOUNDARY_SENSITIVITY_BP = 1000
_PRIMARY_SOURCE_PREFIX = "https://www.boe.es/"


@dataclass(frozen=True)
class RetaTramo:
    table: str
    tramo: int
    lower_minor: int | None
    lower_inclusive: bool | None
    upper_minor: int | None
    upper_inclusive: bool | None
    min_base_minor: int
    max_base_minor: int


@dataclass(frozen=True)
class RetaTable:
    year: int
    source_file_hash: str
    source_checked_on: date
    maximum_base_minor: int
    # Full coverage: IT covered here (art. 18.2.a) and AT/EP covered (art. 18.2.b).
    total_rate_bp: int
    generic_deduction_bp: Mapping[str, int]
    tramos: tuple[RetaTramo, ...]


@dataclass(frozen=True)
class BaseElection:
    effective_from: date
    regime: str
    monthly_base_minor: int | None


def load_reta_table(path: Path) -> RetaTable:
    return parse_reta_table(path.read_bytes())


def parse_reta_table(raw_bytes: bytes) -> RetaTable:
    payload = json.loads(raw_bytes.decode("utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("RETA table input must be a JSON object")
    year = _int(payload.get("year"), "year")
    if not 2000 <= year <= 2100:
        raise ValueError("year is outside the supported range")
    try:
        source_checked_on = date.fromisoformat(str(payload.get("source_checked_on")))
    except ValueError as exc:
        raise ValueError("source_checked_on must be an ISO date") from exc
    _source({**payload, "source_kind": "primary"}, "RETA table")
    maximum_base = _int(payload.get("maximum_base_minor"), "maximum_base_minor")

    raw_tramos = payload.get("tramos")
    if not isinstance(raw_tramos, list) or not raw_tramos:
        raise ValueError("RETA table must contain a non-empty tramos list")
    tramos: list[RetaTramo] = []
    for index, raw in enumerate(raw_tramos, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"RETA tramo {index} must be an object")
        field = f"RETA tramo {index}"
        row = RetaTramo(
            table=str(raw.get("table")),
            tramo=_int(raw.get("tramo"), f"{field} number"),
            **_bound(raw, "lower", field),
            **_bound(raw, "upper", field),
            min_base_minor=_int(raw.get("min_base_minor"), f"{field} min_base_minor"),
            max_base_minor=_int(raw.get("max_base_minor"), f"{field} max_base_minor"),
        )
        previous = tramos[-1] if tramos else None
        expected = (
            ("reduced", 1) if previous is None
            else (previous.table, previous.tramo + 1) if row.table == previous.table
            else ("general", 1) if previous.table == "reduced"
            else None
        )
        if (row.table, row.tramo) != expected:
            raise ValueError(f"{field} must be {expected}, got {(row.table, row.tramo)}")
        if previous is None:
            if row.lower_minor is not None:
                raise ValueError("The first RETA tramo must be open below")
        elif (
            previous.upper_minor is None
            or row.lower_minor != previous.upper_minor
            or row.lower_inclusive == previous.upper_inclusive
        ):
            raise ValueError(f"{field} is not contiguous with the previous tramo")
        if row.lower_minor is not None and row.upper_minor is not None and row.upper_minor <= row.lower_minor:
            raise ValueError(f"{field} ends before it starts")
        if not 0 < row.min_base_minor <= row.max_base_minor <= maximum_base:
            raise ValueError(f"{field} bases must satisfy 0 < min <= max <= maximum_base_minor")
        tramos.append(row)
    if tramos[-1].upper_minor is not None:
        raise ValueError("The last RETA tramo must be open above")

    components = payload.get("rate_components")
    if not isinstance(components, list) or not components:
        raise ValueError("RETA table must contain a non-empty rate_components list")
    names: set[str] = set()
    for index, component in enumerate(components, start=1):
        if not isinstance(component, dict) or not str(component.get("component") or "").strip():
            raise ValueError(f"RETA rate component {index} must name its component")
        name = component["component"]
        if name in names:
            raise ValueError(f"Duplicate RETA rate component {name}")
        names.add(name)
        if _int(component.get("rate_bp"), f"rate component {name} rate_bp") <= 0:
            raise ValueError(f"RETA rate component {name} rate_bp must be positive")
        _source(component, f"RETA rate component {name}")
    total_rate_bp = _int(payload.get("total_rate_bp"), "total_rate_bp")
    component_sum = sum(component["rate_bp"] for component in components)
    if component_sum != total_rate_bp:
        raise ValueError(f"RETA rate components sum to {component_sum} bp, not total_rate_bp {total_rate_bp}")

    deductions = payload.get("generic_deduction_bp")
    if not isinstance(deductions, dict) or set(deductions) != set(WORKER_KINDS):
        raise ValueError(f"generic_deduction_bp must list exactly {WORKER_KINDS}")
    for kind, value in deductions.items():
        if not 0 <= _int(value, f"generic_deduction_bp.{kind}") < 10000:
            raise ValueError(f"generic_deduction_bp.{kind} must be between 0 and 9999")
    _source(payload.get("generic_deduction_source"), "generic_deduction_source")

    return RetaTable(
        year=year,
        source_file_hash=hashlib.sha256(raw_bytes).hexdigest(),
        source_checked_on=source_checked_on,
        maximum_base_minor=maximum_base,
        total_rate_bp=total_rate_bp,
        generic_deduction_bp=dict(deductions),
        tramos=tuple(tramos),
    )


def find_tramo(table: RetaTable, monthly_income_minor: Fraction | int) -> RetaTramo:
    """Exact lookup: 670.001 EUR is already "> 670", so no rounding first."""
    for row in table.tramos:
        if (
            row.upper_minor is None
            or monthly_income_minor < row.upper_minor
            or (row.upper_inclusive and monthly_income_minor == row.upper_minor)
        ):
            return row
    raise AssertionError("load_reta_table guarantees an open last tramo")


def next_base_change_date(as_of: date) -> date:
    # RD 2064/1995 art. 45.1: a request made in Jan-Feb takes effect 1 Mar,
    # Mar-Apr 1 May, ..., Nov-Dec 1 Jan of the next year. The limit of six
    # changes a year is not modelled.
    month = (as_of.month - 1) // 2 * 2 + 3
    return date(as_of.year + 1, 1, 1) if month > 12 else date(as_of.year, month, 1)


def bracket_check(
    table: RetaTable | None,
    *,
    year: int,
    as_of: date,
    through: date,
    irpf_net_income_minor: int | None,
    contributions_added_back_minor: int | None,
    worker_kind: str,
    alta_periods: Sequence[tuple[date, date | None]],
    elections: Sequence[BaseElection],
    reasons: Sequence[str] = (),
) -> dict[str, Any]:
    """Estimate supported RETA exposure for 1 January..``through`` without projecting.

    Regularisable days are alta days (``alta_periods``, inclusive, ``None`` =
    ongoing) less tarifa plana days (RD art. 46.2 regla 1.ª). Monthly income is
    the full YTD income after the generic deduction x 30 / those natural days
    (RD art. 44.2.a, regla 2.ª). Bases compare as totals (LGSS art. 308.1.c
    reglas 3.ª-4.ª): a full month weighs 1 at its monthly base, a partial month
    d/30 (RD art. 47.1), and the tramo limits are prorated alike (regla 5.ª.d).
    Inside the tramo nothing is due; otherwise the monthly differences to the
    minimum (or maximum), positive and negative, are netted (regla 5.ª b/c).
    ``reasons`` carries ledger-side blockers.
    """
    if through.year != year or through.day != monthrange(year, through.month)[1]:
        raise ValueError("through must be a month end inside the checked year")
    if as_of < through:
        raise ValueError("as_of cannot be before through")
    if worker_kind not in WORKER_KINDS:
        raise ValueError(f"worker_kind must be one of {WORKER_KINDS}")
    if any(end is not None and end < start for start, end in alta_periods):
        raise ValueError("An alta period cannot end before it starts")
    found = list(dict.fromkeys(reasons))
    if not set(found) <= CALLER_REASONS:
        raise ValueError(f"Unsupported caller reasons: {sorted(set(found) - CALLER_REASONS)}")
    _validate_elections(elections)

    next_change = next_base_change_date(as_of)
    # Months a request made on as_of can no longer reach.
    locked_until = 12 if next_change.year > year else next_change.month - 1
    all_months = _months(year, locked_until, alta_periods, elections)
    window = all_months[: through.month]
    regularisable_days = sum(row["regularisable_days"] for row in window)

    table_ok = table is not None and table.year == year
    if not table_ok:
        found.append("table_unavailable")
    if irpf_net_income_minor is None or contributions_added_back_minor is None:
        found.append("ledger_blocked")
    if worker_kind != "individual":
        # 305.2.b/e and 305.2.k workers have a grupo 7 floor (art. 44.3.b) and
        # 305.2.b/e also add entity income (LGSS 308.1.c regla 1.ª); neither is here.
        found.append("worker_kind_floor_unverified")
    if any(row["missing_base_days"] for row in window):
        found.append("base_missing")
    if regularisable_days == 0:
        found.append("window_empty")
    used_bases = {base for row in all_months for base in row["bases"]}
    if table_ok:
        if any(base > table.maximum_base_minor for base in used_bases):
            # A table re-import can lower the maximum under a recorded base.
            found.append("base_above_table_maximum")
        if any(base < table.tramos[0].min_base_minor for base in used_bases):
            # Orden art. 18.6 / 18.11 special bases; their regularisation is not modelled.
            found.append("base_below_table_minimum")
    found = list(dict.fromkeys(found))

    deduction_bp = table.generic_deduction_bp[worker_kind] if table_ok else None
    computable = average = tramo = None
    if not set(found) & _INCOME_BLOCKERS:
        computable = Fraction(irpf_net_income_minor + contributions_added_back_minor) * (10000 - deduction_bp) / 10000
        # Societario income also includes entity income (LGSS art. 308.1.c
        # regla 1.ª) the ledger does not hold, so no average or tramo for it.
        if regularisable_days and worker_kind != "societario":
            average = computable * 30 / regularisable_days
            tramo = find_tramo(table, average)

    status = "unknown"
    average_base = estimated_additional = estimated_refund = locked_in = None
    if not found:
        rate = Fraction(table.total_rate_bp, 10000)
        provisional = sum(row["provisional"] for row in window)
        weight = sum(row["share"] for row in window)
        floor, ceiling = tramo.min_base_minor * weight, tramo.max_base_minor * weight
        average_base = provisional / weight
        if provisional < floor:
            status, delta = "below_bracket", floor - provisional
        elif provisional > ceiling:
            status, delta = "above_bracket", ceiling - provisional
        else:
            status, delta = "ok", Fraction(0)
        # The switch to the other procedure (regla 5.ª b/c last paragraphs) can
        # only come from differences TGSS holds that this model does not; here
        # delta is positive below the tramo and negative above it.
        estimated_additional = _half_up(max(delta, 0) * rate)
        estimated_refund = _half_up(max(-delta, 0) * rate)
        locked = sum(
            tramo.min_base_minor * row["share"] - row["provisional"] for row in all_months
        )
        locked_in = _half_up(max(locked, 0) * rate)

    months = []
    for row in window:
        if not row["alta_days"]:
            continue
        known = status != "unknown" and row["regularisable_days"] > 0
        share = row["share"]
        months.append({
            "month": f"{year}-{row['month']:02d}",
            "alta_days": row["alta_days"],
            "tarifa_plana_days": row["tarifa_plana_days"],
            "regularisable_days": row["regularisable_days"],
            "provisional_base_minor": (
                _half_up(row["provisional"])
                if row["regularisable_days"] and not row["missing_base_days"] else None
            ),
            "difference_to_min_minor": _half_up(row["provisional"] - tramo.min_base_minor * share) if known else None,
            "difference_to_max_minor": _half_up(row["provisional"] - tramo.max_base_minor * share) if known else None,
        })

    return {
        "year": year,
        "as_of": as_of.isoformat(),
        "through": through.isoformat(),
        "status": status,
        "reasons": found,
        "worker_kind": worker_kind,
        "table": None if not table_ok else {
            "year": table.year,
            "source_file_hash": table.source_file_hash,
            "source_checked_on": table.source_checked_on.isoformat(),
            "total_rate_bp": table.total_rate_bp,
        },
        "income": {
            "irpf_net_income_minor": irpf_net_income_minor,
            "contributions_added_back_minor": contributions_added_back_minor,
            "generic_deduction_bp": deduction_bp,
            "rendimiento_computable_minor": None if computable is None else _half_up(computable),
            "regularisable_days": regularisable_days,
            "monthly_average_minor": None if average is None else _half_up(average),
        },
        "bracket": None if tramo is None else {
            "table": tramo.table,
            "tramo": tramo.tramo,
            "lower_minor": tramo.lower_minor,
            "lower_inclusive": tramo.lower_inclusive,
            "upper_minor": tramo.upper_minor,
            "upper_inclusive": tramo.upper_inclusive,
            "min_base_minor": tramo.min_base_minor,
            "max_base_minor": tramo.max_base_minor,
        },
        "boundary_sensitive": None if tramo is None else _boundary_sensitive(table, tramo, average),
        "average_provisional_base_minor": None if average_base is None else _half_up(average_base),
        "months": months,
        "estimated_additional_minor": estimated_additional,
        "estimated_refund_minor": estimated_refund,
        "additional_locked_in_minor": locked_in,
        "next_base_change": {
            "effective_on": next_change.isoformat(),
            "request_by": (next_change - timedelta(days=1)).isoformat(),
        },
    }


def _months(
    year: int,
    last_month: int,
    alta_periods: Sequence[tuple[date, date | None]],
    elections: Sequence[BaseElection],
) -> list[dict[str, Any]]:
    ordered = sorted(elections, key=lambda row: row.effective_from)
    starts = [row.effective_from for row in ordered]
    rows = []
    for month in range(1, last_month + 1):
        length = monthrange(year, month)[1]
        row: dict[str, Any] = {
            "month": month, "days_in_month": length, "alta_days": 0, "tarifa_plana_days": 0,
            "missing_base_days": 0, "bases": set(),
        }
        base_sum = 0
        for day in range(1, length + 1):
            current = date(year, month, day)
            if not any(start <= current and (end is None or current <= end) for start, end in alta_periods):
                continue
            row["alta_days"] += 1
            index = bisect_right(starts, current) - 1
            election = ordered[index] if index >= 0 else None
            if election is None:
                row["missing_base_days"] += 1
            elif election.regime == "tarifa_plana":
                row["tarifa_plana_days"] += 1
            else:
                base_sum += election.monthly_base_minor
                row["bases"].add(election.monthly_base_minor)
        row["regularisable_days"] = row["alta_days"] - row["tarifa_plana_days"]
        base_days = row["regularisable_days"] - row["missing_base_days"]
        # RD 2064/1995 art. 47.1: months liquidate whole; a partial month pays
        # d/30 of the monthly amount ("se dividirá por treinta en todo caso").
        full = base_days == length
        row["share"] = Fraction(1) if full else Fraction(base_days, 30)
        row["provisional"] = Fraction(base_sum, length if full else 30)
        rows.append(row)
    return rows


def _validate_elections(elections: Sequence[BaseElection]) -> None:
    seen: set[date] = set()
    for row in elections:
        if row.regime not in REGIMES:
            raise ValueError(f"Base election regime must be one of {REGIMES}")
        if row.regime == "base" and (
            isinstance(row.monthly_base_minor, bool)
            or not isinstance(row.monthly_base_minor, int)
            or row.monthly_base_minor <= 0
        ):
            raise ValueError("A base election needs a positive monthly_base_minor")
        if row.effective_from in seen:
            raise ValueError(f"Duplicate base election for {row.effective_from.isoformat()}")
        seen.add(row.effective_from)


def _boundary_sensitive(table: RetaTable, tramo: RetaTramo, income: Fraction) -> bool:
    index = table.tramos.index(tramo)
    width_row = (
        table.tramos[index + 1] if tramo.lower_minor is None
        else table.tramos[index - 1] if tramo.upper_minor is None
        else tramo
    )
    width = width_row.upper_minor - width_row.lower_minor
    distance = min(
        abs(income - bound) for bound in (tramo.lower_minor, tramo.upper_minor) if bound is not None
    )
    return distance * 10000 <= width * BOUNDARY_SENSITIVITY_BP


def _half_up(value: Fraction | int) -> int:
    magnitude = int((abs(value) * 2 + 1) // 2)
    return magnitude if value >= 0 else -magnitude


def _bound(raw: Mapping[str, Any], name: str, field: str) -> dict[str, Any]:
    value = raw.get(f"{name}_minor")
    inclusive = raw.get(f"{name}_inclusive")
    if value is None and inclusive is None:
        return {f"{name}_minor": None, f"{name}_inclusive": None}
    if not isinstance(inclusive, bool):
        raise ValueError(f"{field} {name}_inclusive must be a boolean exactly when {name}_minor is set")
    return {f"{name}_minor": _int(value, f"{field} {name}_minor"), f"{name}_inclusive": inclusive}


def _source(raw: object, field: str) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"{field} must carry source_kind, source_url and source_reference")
    kind = raw.get("source_kind")
    url = str(raw.get("source_url") or "").strip()
    reference = str(raw.get("source_reference") or "").strip()
    if kind not in {"primary", "secondary"} or not url.startswith("https://") or not reference:
        raise ValueError(f"{field} needs source_kind primary|secondary, an https source_url and a source_reference")
    if kind == "primary" and not url.startswith(_PRIMARY_SOURCE_PREFIX):
        raise ValueError(f"{field} is marked primary but its source is not on {_PRIMARY_SOURCE_PREFIX}")


def _int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value
