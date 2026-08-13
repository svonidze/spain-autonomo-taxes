from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
import hashlib
from pathlib import Path
import re


DOCUMENT_INDEX_FIELDS = [
    "doc_key",
    "category",
    "year",
    "period",
    "source_system",
    "original_name",
    "drive_relative_path",
    "drive_url",
    "local_source_path",
    "mime_type",
    "file_size",
    "sha256",
    "received_at",
    "indexed_at",
    "status",
    "notes",
]

SOURCE_BOOK_INDEX_FIELDS = [
    "year",
    "book_type",
    "workbook_name",
    "drive_url",
    "sha256",
    "scope_start",
    "scope_end",
    "content_status",
    "imported_rows",
    "reconciliation_status",
    "notes",
]

XOLO_CORRESPONDENCE_FIELDS = [
    "date",
    "direction",
    "subject",
    "item",
    "drive_relative_path",
    "drive_url",
    "local_source_path",
    "status",
    "notes",
]

DRIVE_MAP_FIELDS = ["local_source_path", "drive_relative_path", "drive_url", "drive_id"]

AUDIT_OUTPUT_NAMES = [
    "modelo130_goal_status.csv",
    "modelo130_goal_status.md",
    "modelo130_quarter_acceptance.csv",
    "modelo130_quarter_acceptance.md",
    "modelo303_vat_crosscheck.csv",
    "modelo303_vat_crosscheck.md",
    "xolo_source_book_content_check.csv",
    "xolo_source_book_content_check.md",
    "xolo_source_book_reconciliation.csv",
    "xolo_source_book_reconciliation.md",
    "xolo_source_book_refresh.csv",
    "xolo_source_book_refresh.md",
    "xolo_source_book_rows.csv",
    "xolo_source_book_rows.md",
]

CORRESPONDENCE_SOURCE_NAMES = ["MANIFEST.csv", "MANIFEST.md", "message_to_xolo.md"]

ARCHIVED_EVIDENCE_CATEGORIES = {
    "raw_exports": "raw_export",
    "expense_evidence": "expense_evidence",
    "tax_reports": "tax_report",
}


@dataclass(frozen=True)
class DriveMapEntry:
    local_source_path: str
    drive_relative_path: str
    drive_url: str
    drive_id: str


def load_drive_map(path: Path | None) -> dict[str, DriveMapEntry]:
    if path is None or not path.exists():
        return {}
    output: dict[str, DriveMapEntry] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            entry = DriveMapEntry(
                local_source_path=row.get("local_source_path", ""),
                drive_relative_path=row.get("drive_relative_path", ""),
                drive_url=row.get("drive_url", ""),
                drive_id=row.get("drive_id", ""),
            )
            for key in _drive_map_keys(entry):
                if key:
                    output[key] = entry
    return output


def build_document_index(
    *,
    xolo_root: Path,
    source_books_root: Path,
    runs_root: Path,
    archive_root: Path | None = None,
    drive_map: dict[str, DriveMapEntry] | None = None,
    xolo_export_folder_url: str = "",
    archive_folder_url: str = "",
    indexed_at: str | None = None,
) -> list[dict[str, str]]:
    drive_map = drive_map or {}
    indexed_at = indexed_at or date.today().isoformat()
    rows: list[dict[str, str]] = []
    rows.extend(_xolo_export_rows(xolo_root, drive_map, xolo_export_folder_url, indexed_at))
    rows.extend(_source_book_rows(source_books_root, drive_map, archive_folder_url, indexed_at))
    rows.extend(_archived_evidence_rows(archive_root, drive_map, archive_folder_url, indexed_at))
    rows.extend(_audit_output_rows(runs_root, drive_map, archive_folder_url, indexed_at))
    rows.extend(_correspondence_document_rows(runs_root, drive_map, archive_folder_url, indexed_at))
    return sorted(rows, key=lambda row: (row["category"], row["year"], row["period"], row["drive_relative_path"]))


def build_source_books_index(
    *,
    content_check_csv: Path,
    imported_rows_csv: Path,
    reconciliation_csv: Path,
    drive_map: dict[str, DriveMapEntry] | None = None,
) -> list[dict[str, str]]:
    drive_map = drive_map or {}
    content_rows = _read_csv(content_check_csv)
    imported_counts = _imported_source_book_counts(imported_rows_csv)
    reconciled_years = _reconciled_years(reconciliation_csv)
    rows: list[dict[str, str]] = []
    for row in content_rows:
        if row.get("status") != "content_ready":
            continue
        paths = _split_paths(row.get("content_ready_files", ""))
        for source_path in paths:
            source = Path(source_path)
            year = _year_from_scope(row.get("scope", "")) or _infer_year_period(source.name)[0]
            entry = _lookup_drive_map(drive_map, source, f"Xolo evidence archive/source_books/{source.name}")
            periods = _periods_from_text(row.get("required_for", ""))
            workbook_name = source.name
            imported_key = (_canonical_book_type(row.get("check", "")), workbook_name)
            rows.append(
                {
                    "year": year,
                    "book_type": row.get("check", ""),
                    "workbook_name": workbook_name,
                    "drive_url": entry.drive_url if entry else "",
                    "sha256": _sha256(source) if source.exists() else "",
                    "scope_start": min(periods) if periods else "",
                    "scope_end": max(periods) if periods else "",
                    "content_status": row.get("status", ""),
                    "imported_rows": str(imported_counts.get(imported_key, 0)),
                    "reconciliation_status": _year_reconciliation_status(year, reconciled_years),
                    "notes": row.get("evidence", ""),
                }
            )
    return rows


def build_xolo_correspondence_index(
    *,
    runs_root: Path,
    source_books_root: Path,
    drive_map: dict[str, DriveMapEntry] | None = None,
    indexed_at: str | None = None,
) -> list[dict[str, str]]:
    drive_map = drive_map or {}
    indexed_at = indexed_at or date.today().isoformat()
    rows: list[dict[str, str]] = []
    package = runs_root / "xolo_source_book_request_package"
    for name in CORRESPONDENCE_SOURCE_NAMES:
        path = package / name
        if not path.exists():
            continue
        entry = _lookup_drive_map(drive_map, path, f"Xolo evidence archive/correspondence/xolo_source_books/{path.name}")
        rows.append(
            {
                "date": "2026-07-10",
                "direction": "outbound",
                "subject": "Request Xolo official source books",
                "item": path.name,
                "drive_relative_path": entry.drive_relative_path if entry else f"Xolo evidence archive/correspondence/xolo_source_books/{path.name}",
                "drive_url": entry.drive_url if entry else "",
                "local_source_path": str(path),
                "status": "prepared_request",
                "notes": "Local request package used to ask Xolo for official accounting books.",
            }
        )
    for path in sorted(source_books_root.glob("Libros_contables_*.xlsx")):
        year = _infer_year_period(path.name)[0]
        entry = _lookup_drive_map(drive_map, path, f"Xolo evidence archive/source_books/{path.name}")
        rows.append(
            {
                "date": indexed_at,
                "direction": "inbound",
                "subject": "Xolo source books received",
                "item": path.name,
                "drive_relative_path": entry.drive_relative_path if entry else f"Xolo evidence archive/source_books/{path.name}",
                "drive_url": entry.drive_url if entry else "",
                "local_source_path": str(path),
                "status": "received_and_archived" if entry and entry.drive_url else "received_pending_drive_url",
                "notes": f"Official Xolo accounting workbook for {year}.",
            }
        )
    return rows


def write_document_index_csv(path: Path, rows: list[dict[str, str]]) -> None:
    _write_csv(path, DOCUMENT_INDEX_FIELDS, rows)


def write_source_books_index_csv(path: Path, rows: list[dict[str, str]]) -> None:
    _write_csv(path, SOURCE_BOOK_INDEX_FIELDS, rows)


def write_xolo_correspondence_index_csv(path: Path, rows: list[dict[str, str]]) -> None:
    _write_csv(path, XOLO_CORRESPONDENCE_FIELDS, rows)


def _xolo_export_rows(
    xolo_root: Path,
    drive_map: dict[str, DriveMapEntry],
    xolo_export_folder_url: str,
    indexed_at: str,
) -> list[dict[str, str]]:
    if not xolo_root.exists():
        return []
    rows: list[dict[str, str]] = []
    for path in sorted(item for item in xolo_root.rglob("*") if item.is_file()):
        if _is_ignored_filesystem_artifact(path):
            continue
        relative = path.relative_to(xolo_root).as_posix()
        category = _xolo_export_category(relative)
        year, period = _infer_year_period(path.name)
        drive_relative_path = f"Xolo export/{relative}"
        entry = _lookup_drive_map(drive_map, path, drive_relative_path)
        rows.append(
            _document_row(
                path=path,
                category=category,
                year=year,
                period=period,
                source_system="xolo_export",
                drive_relative_path=drive_relative_path,
                drive_url=entry.drive_url if entry else xolo_export_folder_url,
                received_at="",
                indexed_at=indexed_at,
                status="indexed_existing_drive_export",
                notes=(
                    "Existing Google Drive export file; URL points to containing export folder."
                    if xolo_export_folder_url and not entry
                    else ""
                ),
            )
        )
    return rows


def _source_book_rows(
    source_books_root: Path,
    drive_map: dict[str, DriveMapEntry],
    archive_folder_url: str,
    indexed_at: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted(source_books_root.glob("Libros_contables_*.xlsx")):
        year, period = _infer_year_period(path.name)
        drive_relative_path = f"Xolo evidence archive/source_books/{path.name}"
        entry = _lookup_drive_map(drive_map, path, drive_relative_path)
        rows.append(
            _document_row(
                path=path,
                category="source_book",
                year=year,
                period=period,
                source_system="xolo_source_book_response",
                drive_relative_path=entry.drive_relative_path if entry else drive_relative_path,
                drive_url=entry.drive_url if entry else archive_folder_url,
                received_at=indexed_at,
                indexed_at=indexed_at,
                status="saved_to_drive" if entry and entry.drive_url else "pending_drive_url",
                notes="Official Xolo accounting workbook stored as raw XLSX evidence.",
            )
        )
    return rows


def _archived_evidence_rows(
    archive_root: Path | None,
    drive_map: dict[str, DriveMapEntry],
    archive_folder_url: str,
    indexed_at: str,
) -> list[dict[str, str]]:
    if archive_root is None or not archive_root.exists():
        return []
    rows: list[dict[str, str]] = []
    for directory_name, category in ARCHIVED_EVIDENCE_CATEGORIES.items():
        evidence_root = archive_root / directory_name
        if not evidence_root.exists():
            continue
        for path in sorted(item for item in evidence_root.rglob("*") if item.is_file()):
            if _is_ignored_filesystem_artifact(path):
                continue
            relative = path.relative_to(archive_root).as_posix()
            year, period = _infer_year_period(relative)
            drive_relative_path = f"Xolo evidence archive/{relative}"
            entry = _lookup_drive_map(drive_map, path, drive_relative_path)
            rows.append(
                _document_row(
                    path=path,
                    category=category,
                    year=year,
                    period=period,
                    source_system="xolo_export_recovery",
                    drive_relative_path=(
                        entry.drive_relative_path if entry else drive_relative_path
                    ),
                    drive_url=entry.drive_url if entry else archive_folder_url,
                    received_at=indexed_at,
                    indexed_at=indexed_at,
                    status="saved_to_drive",
                    notes="Recovered from the immutable Xolo data export and stored in the controlled evidence archive.",
                )
            )
    return rows


def _audit_output_rows(
    runs_root: Path,
    drive_map: dict[str, DriveMapEntry],
    archive_folder_url: str,
    indexed_at: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name in AUDIT_OUTPUT_NAMES:
        path = runs_root / name
        if not path.exists():
            continue
        drive_relative_path = f"Xolo evidence archive/audit_outputs/modelo130/{path.name}"
        entry = _lookup_drive_map(drive_map, path, drive_relative_path)
        year, period = _infer_year_period(path.name)
        rows.append(
            _document_row(
                path=path,
                category="audit_output",
                year=year,
                period=period,
                source_system="autonomo_taxes_runs",
                drive_relative_path=entry.drive_relative_path if entry else drive_relative_path,
                drive_url=entry.drive_url if entry else archive_folder_url,
                received_at=indexed_at,
                indexed_at=indexed_at,
                status="saved_to_drive" if entry and entry.drive_url else "local_audit_output",
                notes="Derived audit trail generated from Xolo source books and filed tax reports.",
            )
        )
    return rows


def _correspondence_document_rows(
    runs_root: Path,
    drive_map: dict[str, DriveMapEntry],
    archive_folder_url: str,
    indexed_at: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    package = runs_root / "xolo_source_book_request_package"
    for name in CORRESPONDENCE_SOURCE_NAMES:
        path = package / name
        if not path.exists():
            continue
        drive_relative_path = f"Xolo evidence archive/correspondence/xolo_source_books/{path.name}"
        entry = _lookup_drive_map(drive_map, path, drive_relative_path)
        rows.append(
            _document_row(
                path=path,
                category="correspondence",
                year="2026",
                period="2026-Q3",
                source_system="autonomo_taxes_xolo_request",
                drive_relative_path=entry.drive_relative_path if entry else drive_relative_path,
                drive_url=entry.drive_url if entry else archive_folder_url,
                received_at="2026-07-10",
                indexed_at=indexed_at,
                status="saved_to_drive" if entry and entry.drive_url else "local_correspondence_artifact",
                notes="Request package for official Xolo accounting/source-book records.",
            )
        )
    return rows


def _document_row(
    *,
    path: Path,
    category: str,
    year: str,
    period: str,
    source_system: str,
    drive_relative_path: str,
    drive_url: str,
    received_at: str,
    indexed_at: str,
    status: str,
    notes: str,
) -> dict[str, str]:
    digest = _sha256(path)
    return {
        "doc_key": f"{source_system}:{drive_relative_path}",
        "category": category,
        "year": year,
        "period": period,
        "source_system": source_system,
        "original_name": path.name,
        "drive_relative_path": drive_relative_path,
        "drive_url": drive_url,
        "local_source_path": str(path),
        "mime_type": _mime_type(path),
        "file_size": str(path.stat().st_size),
        "sha256": digest,
        "received_at": received_at,
        "indexed_at": indexed_at,
        "status": status,
        "notes": notes,
    }


def _lookup_drive_map(
    drive_map: dict[str, DriveMapEntry],
    path: Path,
    drive_relative_path: str,
) -> DriveMapEntry | None:
    keys = [
        _normalize_path_key(path),
        str(path),
        path.name,
        drive_relative_path,
        drive_relative_path.replace("\\", "/"),
    ]
    for key in keys:
        entry = drive_map.get(key)
        if entry:
            return entry
    return None


def _drive_map_keys(entry: DriveMapEntry) -> list[str]:
    keys = [entry.local_source_path, entry.drive_relative_path]
    if entry.local_source_path:
        path = Path(entry.local_source_path)
        keys.extend([_normalize_path_key(path), path.name])
    if entry.drive_relative_path:
        keys.append(entry.drive_relative_path.replace("\\", "/"))
    return keys


def _xolo_export_category(relative: str) -> str:
    parts = relative.split("/", 1)
    if len(parts) == 1:
        return _root_file_category(parts[0])
    top = parts[0].strip().lower()
    return {
        "expense": "expense_evidence",
        "invoice": "income_invoice",
        "tax_report": "tax_report",
        "company": "company_document",
        "adobe_stock_2025": "adobe_stock_archive",
    }.get(top, top or "xolo_export")


def _root_file_category(name: str) -> str:
    normalized = name.lower().replace("_", " ")
    if any(token in normalized for token in ("m100", "m130", "m303", "m390", "mod 100", "mod 130", "mod 303", "mod 390")):
        return "tax_report"
    return "xolo_export_root"


def _is_ignored_filesystem_artifact(path: Path) -> bool:
    ignored_names = {"desktop.ini", "thumbs.db", ".ds_store"}
    ignored_dirs = {".git", ".omc", ".omx", "__pycache__"}
    lowered_parts = {part.lower() for part in path.parts}
    return path.name.lower() in ignored_names or bool(lowered_parts & ignored_dirs)


def _infer_year_period(name: str) -> tuple[str, str]:
    text = name.replace("_", " ")
    match = re.search(r"(?i)\b([1-4])\s*T\s*(20\d{2})\b", text)
    if match:
        year = match.group(2)
        return year, f"{year}-Q{match.group(1)}"
    match = re.search(r"(?i)\b(20\d{2})\s*[- ]?\s*Q([1-4])\b", text)
    if match:
        year = match.group(1)
        return year, f"{year}-Q{match.group(2)}"
    match = re.search(r"\b(20\d{2})[-.](\d{2})[-.](\d{2})\b", text)
    if match:
        year = match.group(1)
        month = int(match.group(2))
        return year, f"{year}-Q{((month - 1) // 3) + 1}"
    match = re.search(r"\b(20\d{2})\b", text)
    if match:
        return match.group(1), ""
    return "", ""


def _periods_from_text(value: str) -> list[str]:
    return sorted(set(re.findall(r"\b20\d{2}-Q[1-4]\b", value or "")))


def _year_from_scope(scope: str) -> str:
    match = re.search(r"\b(20\d{2})\b", scope or "")
    return match.group(1) if match else ""


def _canonical_book_type(value: str) -> str:
    return {
        "compras_gastos_book": "gastos_book",
        "bienes_inversion_or_asset_schedule": "bienes_inversion_book",
    }.get(value, value)


def _imported_source_book_counts(path: Path) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for row in _read_csv(path):
        source_file = row.get("source_file", "")
        key = (_canonical_book_type(row.get("source_book_type", "")), Path(source_file).name)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _reconciled_years(path: Path) -> dict[str, str]:
    output: dict[str, str] = {}
    for row in _read_csv(path):
        period = row.get("period") or row.get("Period") or ""
        year = _year_from_scope(period)
        status = row.get("status") or row.get("Status") or ""
        if year and status:
            existing = output.get(year, "")
            output[year] = status if not existing else _merge_reconciliation_status(existing, status)
    return output


def _year_reconciliation_status(year: str, reconciled_years: dict[str, str]) -> str:
    if not year:
        return ""
    return reconciled_years.get(year, "not_reconciled_in_current_scope")


def _merge_reconciliation_status(left: str, right: str) -> str:
    if left == right:
        return left
    if "not_reconciled" in {left, right}:
        return "mixed"
    return "all_reconciled_with_some_annual_adjustment" if "annual_adjustment" in (left + right) else "all_reconciled"


def _split_paths(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(";") if part.strip()]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _normalize_path_key(path: Path) -> str:
    try:
        return str(path.resolve()).casefold()
    except OSError:
        return str(path.absolute()).casefold()


def _mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".csv": "text/csv",
        ".eml": "message/rfc822",
        ".html": "text/html",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".json": "application/json",
        ".md": "text/markdown",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".txt": "text/plain",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".zip": "application/zip",
    }.get(suffix, "application/octet-stream")
