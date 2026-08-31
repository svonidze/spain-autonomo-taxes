from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Mapping, Sequence


TAX_FORM_KEYS = {
    "130": "modelo130",
    "303": "modelo303",
}
AUTHORITATIVE_SNAPSHOT_STATUSES = ("baseline", "filed", "submitted", "final")
SNAPSHOT_STATUS_RANKS = {
    "baseline": 1,
    "submitted": 2,
    "filed": 3,
    "final": 4,
}


def _normalize_form_code(raw: Any) -> str:
    return str(raw or "").lower().replace("modelo", "").strip()


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalize_filed_on(raw: Any) -> str | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(raw or ""))
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(0)).isoformat()
    except ValueError:
        return None


def coerce_money_values(
    raw_values: Any,
    *,
    blank_keys: Iterable[Any] = (),
) -> dict[str, str]:
    if not isinstance(raw_values, Mapping):
        raw_values = {}
    values: dict[str, str] = {}
    for key, value in raw_values.items():
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not amount.is_finite():
            continue
        values[str(key)] = f"{amount:.2f}"
    if not isinstance(blank_keys, (list, tuple, set)):
        blank_keys = ()
    for key in blank_keys:
        values.setdefault(str(key), "0.00")
    return values


def _headline_fields(
    form_code: str,
    values: Mapping[str, str],
) -> tuple[str | None, str | None]:
    normalized_form_code = _normalize_form_code(form_code)
    if normalized_form_code == "130":
        return values.get("19"), None
    headline_value = (
        values.get("result")
        or values.get("71")
        or values.get("69")
        or values.get("46")
    )
    headline_detail = (
        values.get("compensation_carryforward") or values.get("72")
    )
    return headline_value, headline_detail


def _snapshot_sort_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
    raw_filed_on = str(row.get("filed_on") or "")
    normalized_filed_on = row.get("normalized_filed_on")
    return (
        SNAPSHOT_STATUS_RANKS.get(str(row.get("status") or ""), 0),
        1 if normalized_filed_on else 0,
        normalized_filed_on or "",
        raw_filed_on,
        str(row.get("created_at") or ""),
        int(row.get("snapshot_rowid") or 0),
    )


def _extract_modelo130_values(payload: Mapping[str, Any]) -> dict[str, str]:
    filed_values = payload.get("filed_values")
    if isinstance(filed_values, Mapping):
        values = coerce_money_values(filed_values)
        if values:
            return values
    values = payload.get("values")
    if isinstance(values, Mapping):
        return coerce_money_values(values)
    return {}


def _modelo303_extraction_rank(row: Mapping[str, Any]) -> int:
    payload = row["payload"]
    receipt = payload.get("receipt_verification")
    if not isinstance(receipt, Mapping):
        receipt = {}
    values = payload.get("values")
    schema = str(payload.get("value_extraction_schema") or "")
    filed_values = payload.get("filed_values")
    if receipt.get("status") == "matched" and isinstance(values, Mapping):
        return 4
    if schema == "modelo303_v4" and isinstance(filed_values, Mapping):
        return 3
    if isinstance(values, Mapping):
        return 2
    if isinstance(filed_values, Mapping):
        return 1
    return 0


def _extract_modelo303_values(payload: Mapping[str, Any]) -> dict[str, str]:
    receipt = payload.get("receipt_verification")
    if not isinstance(receipt, Mapping):
        receipt = {}
    values = payload.get("values")
    schema = str(payload.get("value_extraction_schema") or "")
    filed_values = payload.get("filed_values")
    if receipt.get("status") == "matched" and isinstance(values, Mapping):
        return coerce_money_values(values)
    if schema == "modelo303_v4" and isinstance(filed_values, Mapping):
        return coerce_money_values(
            filed_values,
            blank_keys=payload.get("blank_casillas") or (),
        )
    if isinstance(values, Mapping):
        return coerce_money_values(values)
    if isinstance(filed_values, Mapping):
        return coerce_money_values(filed_values)
    return {}


def compact_tax_preview(cached: Mapping[str, Any]) -> dict[str, Any]:
    if not cached:
        return {}
    preview = (
        cached.get("tax_arithmetic_preview", {})
        .get("projected_reviewed", {})
    )
    modelo130 = preview.get("modelo130", {})
    modelo303 = preview.get("modelo303", {})
    return {
        "modelo130": {
            "blocked": bool(modelo130.get("blocked")),
            "values": modelo130.get("values", {}),
            "warnings": modelo130.get("warnings", []),
        },
        "modelo303": {
            "blocked": bool(modelo303.get("blocked")),
            "values": modelo303.get("values", {}),
            "warnings": modelo303.get("warnings", []),
        },
    }


def form_preview_from_cache(cached: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    preview = compact_tax_preview(cached)
    preview_as_of = cached.get("as_of") if cached else None
    result: dict[str, dict[str, Any]] = {}
    for form_code, form_key in TAX_FORM_KEYS.items():
        values = coerce_money_values(preview.get(form_key, {}).get("values"))
        headline_value, headline_detail = _headline_fields(form_code, values)
        result[form_key] = {
            "form_code": form_code,
            "display_state": "preview" if values else "unavailable",
            "filed_on": None,
            "snapshot_status": None,
            "snapshot_hash": None,
            "extraction_status": None,
            "values": values,
            "headline_value": headline_value,
            "headline_detail": headline_detail,
            "preview_as_of": preview_as_of if values else None,
        }
    return result


def select_filed_form_snapshot(
    connection: sqlite3.Connection,
    *,
    period_key: str,
    form_code: str,
) -> dict[str, Any] | None:
    normalized_form_code = _normalize_form_code(form_code)
    authoritative_statuses = ", ".join(
        f"'{status}'" for status in AUTHORITATIVE_SNAPSHOT_STATUSES
    )
    rows = connection.execute(
        f"""
        SELECT
            fs.rowid AS snapshot_rowid,
            fs.snapshot_hash,
            fs.filed_on,
            fs.status,
            fs.created_at,
            fs.form_code,
            fs.payload_json
        FROM filing_snapshots fs
        JOIN periods p ON p.period_id = fs.period_id
        WHERE p.period_key = ?
          AND fs.status IN ({authoritative_statuses})
        """,
        (period_key,),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        candidate_form = _normalize_form_code(payload.get("form") or row["form_code"] or "")
        if candidate_form != normalized_form_code:
            continue
        candidates.append(
            {
                "snapshot_rowid": int(row["snapshot_rowid"]),
                "snapshot_hash": str(row["snapshot_hash"] or ""),
                "filed_on": row["filed_on"],
                "normalized_filed_on": _normalize_filed_on(row["filed_on"]),
                "status": str(row["status"] or ""),
                "created_at": str(row["created_at"] or ""),
                "payload": payload,
            }
        )
    if not candidates:
        return None
    ordered = sorted(candidates, key=_snapshot_sort_key, reverse=True)
    authoritative = ordered[0]
    selected = authoritative
    values: dict[str, str] = {}
    if normalized_form_code == "303":
        phase_candidates = (
            [
                row
                for row in ordered
                if row["normalized_filed_on"] == authoritative["normalized_filed_on"]
            ]
            if authoritative["normalized_filed_on"]
            else [authoritative]
        )
        ranked = sorted(
            (
                (
                    _modelo303_extraction_rank(row),
                    _snapshot_sort_key(row),
                    row,
                )
                for row in phase_candidates
            ),
            key=lambda item: (item[0], item[1]),
            reverse=True,
        )
        for rank, _, row in ranked:
            if rank <= 0:
                continue
            candidate_values = _extract_modelo303_values(row["payload"])
            if candidate_values:
                selected = row
                values = candidate_values
                break
    else:
        values = _extract_modelo130_values(authoritative["payload"])
    headline_value, headline_detail = _headline_fields(form_code, values)
    payload = selected["payload"] if values else authoritative["payload"]
    return {
        "form_code": form_code,
        "filed_on": authoritative["normalized_filed_on"],
        "snapshot_status": authoritative["status"] or None,
        "snapshot_hash": (
            selected["snapshot_hash"] if values else authoritative["snapshot_hash"]
        )
        or None,
        "extraction_status": _optional_text(payload.get("extraction_status")),
        "values": values,
        "headline_value": headline_value,
        "headline_detail": headline_detail,
        "preview_as_of": None,
    }


def form_results(
    connection: sqlite3.Connection,
    *,
    period_key: str,
    obligations: list[dict[str, Any]],
    cached: Mapping[str, Any],
) -> dict[str, Any]:
    preview_forms = form_preview_from_cache(cached)
    obligations_by_code = {
        str(row.get("obligation_code") or ""): row for row in obligations
    }
    result: dict[str, Any] = {}
    for form_code, form_key in TAX_FORM_KEYS.items():
        obligation = obligations_by_code.get(form_code)
        snapshot = select_filed_form_snapshot(
            connection,
            period_key=period_key,
            form_code=form_code,
        )
        preview_form = dict(preview_forms[form_key])
        if obligation and obligation.get("filing_status") == "filed":
            if snapshot and snapshot["values"]:
                result[form_key] = {
                    **snapshot,
                    "display_state": "filed",
                }
                continue
            if snapshot:
                result[form_key] = {
                    **snapshot,
                    "display_state": "filed_without_values",
                }
                continue
        if snapshot and snapshot["values"]:
            result[form_key] = {
                **snapshot,
                "display_state": "snapshot_only",
            }
            continue
        result[form_key] = preview_form
    return result


def period_status(
    connection: sqlite3.Connection,
    period_key: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT
            p.period_key,
            p.status,
            p.starts_on,
            p.ends_on,
            p.amendment_reason,
            amendment.period_key AS amendment_period_key
        FROM periods p
        LEFT JOIN periods amendment
            ON amendment.period_id = p.amendment_period_id
        WHERE p.period_key = ? AND p.period_type = 'quarter'
        """,
        (period_key,),
    ).fetchone()
    if row is None:
        return None
    return {
        "period_key": str(row["period_key"]),
        "status": str(row["status"]),
        "starts_on": row["starts_on"],
        "ends_on": row["ends_on"],
        "amendment_period_key": row["amendment_period_key"],
        "amendment_reason": row["amendment_reason"],
    }


def year_form_results(
    connection: sqlite3.Connection,
    *,
    quarters: Sequence[str],
    load_cached: Callable[[str], Mapping[str, Any]],
    load_obligations: Callable[[str], list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for period_key in quarters:
        period = period_status(connection, period_key)
        obligations = load_obligations(period_key) if period else []
        cached = load_cached(period_key)
        results[period_key] = {
            "period": period,
            "obligations": obligations,
            "forms": form_results(
                connection,
                period_key=period_key,
                obligations=obligations,
                cached=cached,
            ),
        }
    return results
