from __future__ import annotations

from collections import Counter
import csv
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

from .parsers import quarter_end


SUMMARY_FIELDS = ["metric", "value", "status", "notes"]
QUARTER_FIELDS = ["period", "row_count"]


@dataclass(frozen=True)
class XoloApiCoverage:
    summary: list[dict[str, str]]
    quarters: list[dict[str, str]]
    status: str
    warnings: list[str]


def build_xolo_api_coverage(
    raw_json: Path,
    raw_csv: Path,
    activity_start_year: int = 2023,
    activity_start_quarter: int = 2,
) -> XoloApiCoverage:
    pages = _load_pages(raw_json)
    csv_rows = _load_csv_rows(raw_csv)
    json_rows = sum(len(page.get("data") or []) for page in pages)
    records_total_values = sorted({str(page.get("recordsTotal")) for page in pages if page.get("recordsTotal") is not None})
    records_filtered_values = sorted(
        {str(page.get("recordsFiltered")) for page in pages if page.get("recordsFiltered") is not None}
    )
    expected_total = _single_int(records_filtered_values) or _single_int(records_total_values)

    ids = [(row.get("xolo_id") or "").strip() for row in csv_rows]
    dates = [_parse_date(row.get("date") or "") for row in csv_rows]
    valid_dates = [item for item in dates if item is not None]
    duplicate_ids = sorted(key for key, count in Counter(item for item in ids if item).items() if count > 1)
    blank_ids = sum(1 for item in ids if not item)
    blank_dates = sum(1 for item in dates if item is None)
    first_date = min(valid_dates) if valid_dates else None
    last_date = max(valid_dates) if valid_dates else None
    start_cutoff = quarter_end(activity_start_year, activity_start_quarter)
    covers_start = first_date is not None and first_date <= start_cutoff

    warnings: list[str] = []
    if expected_total is not None and json_rows != expected_total:
        warnings.append(f"JSON page rows {json_rows} do not match recordsFiltered/recordsTotal {expected_total}")
    if len(csv_rows) != json_rows:
        warnings.append(f"CSV rows {len(csv_rows)} do not match JSON page rows {json_rows}")
    if blank_ids:
        warnings.append(f"{blank_ids} CSV rows have blank Xolo IDs")
    if blank_dates:
        warnings.append(f"{blank_dates} CSV rows have blank or invalid dates")
    if duplicate_ids:
        warnings.append(f"{len(duplicate_ids)} duplicate Xolo IDs: {', '.join(duplicate_ids[:10])}")
    if not covers_start:
        warnings.append(f"Raw export does not prove coverage from {activity_start_year}-Q{activity_start_quarter}")

    status = "complete_raw_snapshot" if not warnings else "attention_required"
    summary = [
        _row("status", status, "ok" if status == "complete_raw_snapshot" else "attention_required"),
        _row("raw_json", str(raw_json), "info"),
        _row("raw_csv", str(raw_csv), "info"),
        _row("json_sha256", _sha256(raw_json), "info"),
        _row("csv_sha256", _sha256(raw_csv), "info"),
        _row("json_pages", str(len(pages)), "ok" if pages else "attention_required"),
        _row("json_page_row_counts", ", ".join(str(len(page.get("data") or [])) for page in pages), "info"),
        _row("json_rows", str(json_rows), "ok"),
        _row("csv_rows", str(len(csv_rows)), "ok" if len(csv_rows) == json_rows else "attention_required"),
        _row("records_total_values", ", ".join(records_total_values), "info"),
        _row("records_filtered_values", ", ".join(records_filtered_values), "info"),
        _row(
            "expected_total_match",
            "yes" if expected_total is not None and expected_total == json_rows == len(csv_rows) else "no",
            "ok" if expected_total is not None and expected_total == json_rows == len(csv_rows) else "attention_required",
        ),
        _row("earliest_expense_date", first_date.isoformat() if first_date else "", "ok" if first_date else "attention_required"),
        _row("latest_expense_date", last_date.isoformat() if last_date else "", "ok" if last_date else "attention_required"),
        _row(
            "covers_activity_start",
            "yes" if covers_start else "no",
            "ok" if covers_start else "attention_required",
            f"activity start gate is {activity_start_year}-Q{activity_start_quarter}, cutoff {start_cutoff.isoformat()}",
        ),
        _row("blank_xolo_ids", str(blank_ids), "ok" if blank_ids == 0 else "attention_required"),
        _row("blank_dates", str(blank_dates), "ok" if blank_dates == 0 else "attention_required"),
        _row("duplicate_xolo_ids", str(len(duplicate_ids)), "ok" if not duplicate_ids else "attention_required"),
    ]
    if warnings:
        summary.append(_row("warnings", " | ".join(warnings), "attention_required"))

    return XoloApiCoverage(summary=summary, quarters=_quarter_counts(valid_dates), status=status, warnings=warnings)


def write_xolo_api_coverage_csv(path: Path, coverage: XoloApiCoverage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(coverage.summary)


def write_xolo_api_quarter_counts_csv(path: Path, coverage: XoloApiCoverage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUARTER_FIELDS)
        writer.writeheader()
        writer.writerows(coverage.quarters)


def write_xolo_api_coverage_markdown(path: Path, coverage: XoloApiCoverage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Xolo API Raw Export Coverage",
        "",
        "This report validates the raw Xolo expense DataTables export used by the historical Modelo 130 audit.",
        "It does not prove Xolo's submitted Modelo 130 register or asset amortization schedule.",
        "",
        "## Summary",
        "",
    ]
    for row in coverage.summary:
        notes = f" ({row['notes']})" if row.get("notes") else ""
        lines.append(f"- `{row['metric']}`: `{row['value']}` - {row['status']}{notes}")
    lines.extend(["", "## Quarter Row Counts", ""])
    lines.append("| Period | Rows |")
    lines.append("|---|---:|")
    for row in coverage.quarters:
        lines.append(f"| {row['period']} | {row['row_count']} |")
    if coverage.warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in coverage.warnings:
            lines.append(f"- {warning}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _load_pages(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict) and isinstance(payload.get("pages"), list):
        pages = payload["pages"]
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        pages = [payload]
    elif isinstance(payload, list):
        pages = payload
    else:
        raise ValueError(f"Unsupported raw Xolo JSON shape in {path}")
    output: list[dict[str, Any]] = []
    for index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            raise ValueError(f"Raw Xolo JSON page {index} in {path} is not an object")
        output.append(page)
    return output


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _single_int(values: list[str]) -> int | None:
    if len(values) != 1:
        return None
    try:
        return int(values[0])
    except ValueError:
        return None


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _quarter_counts(dates: list[date]) -> list[dict[str, str]]:
    counts: Counter[str] = Counter()
    for item in dates:
        quarter = (item.month - 1) // 3 + 1
        counts[f"{item.year}-Q{quarter}"] += 1
    return [{"period": period, "row_count": str(counts[period])} for period in sorted(counts)]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row(metric: str, value: str, status: str, notes: str = "") -> dict[str, str]:
    return {"metric": metric, "value": value, "status": status, "notes": notes}
