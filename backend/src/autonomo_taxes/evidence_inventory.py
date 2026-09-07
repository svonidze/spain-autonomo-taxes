from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re
import zipfile

from .tax_rules import recognize_tax_form_filename


@dataclass(frozen=True)
class EvidenceFile:
    relative_path: str
    name: str
    category: str
    period: str
    size_bytes: int


REGISTER_PATTERN = re.compile(r"(libro|ledger|register|registro)", re.I)
ASSET_PATTERN = re.compile(r"(amort|activo|asset|bienes|invers|fixed|depreci)", re.I)
ARCHIVE_SUFFIXES = {".zip"}
SOURCE_BOOK_CATEGORY = "candidate_source_book_row_evidence"
ASSET_CATEGORY = "candidate_asset_schedule"


def build_evidence_inventory(xolo_root: Path) -> list[dict[str, str]]:
    local_paths = sorted(p for p in xolo_root.rglob("*") if p.is_file())
    files = [_classify_file(xolo_root, path) for path in local_paths]
    files = [file for file in files if file is not None]
    zip_member_count = 0
    archive_errors: list[EvidenceFile] = []
    for path in local_paths:
        if path.suffix.lower() not in ARCHIVE_SUFFIXES:
            continue
        archive_members, member_count, errors = _classify_archive_members(xolo_root, path)
        files.extend(archive_members)
        zip_member_count += member_count
        archive_errors.extend(errors)
    files.extend(archive_errors)
    rows: list[dict[str, str]] = []
    by_category: dict[str, list[EvidenceFile]] = {}
    for file in files:
        by_category.setdefault(file.category, []).append(file)
    for category in sorted(by_category):
        category_files = by_category[category]
        periods = sorted({file.period for file in category_files if file.period})
        rows.append(
            {
                "category": category,
                "period": "; ".join(periods),
                "count": str(len(category_files)),
                "paths": "; ".join(file.relative_path for file in category_files),
            }
        )
    for expected in (SOURCE_BOOK_CATEGORY, ASSET_CATEGORY):
        if expected not in by_category:
            rows.append({"category": expected, "period": "", "count": "0", "paths": ""})
    rows.extend(
        [
            {"category": "scan_local_files", "period": "", "count": str(len(local_paths)), "paths": ""},
            {"category": "scan_zip_members", "period": "", "count": str(zip_member_count), "paths": ""},
        ]
    )
    return sorted(rows, key=lambda row: row["category"])


def write_evidence_inventory_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["category", "period", "count", "paths"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_evidence_inventory_markdown(path: Path, rows: list[dict[str, str]], xolo_root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_category = {row["category"]: row for row in rows}
    m130_periods = _periods(by_category.get("modelo130_report", {}))
    source_book_count = _count(by_category, SOURCE_BOOK_CATEGORY)
    asset_count = _count(by_category, ASSET_CATEGORY)
    lines = [
        "# Modelo 130 Evidence Inventory",
        "",
        f"Scanned root: `{xolo_root}`",
        "",
        "## Conclusion",
        "",
        f"- Modelo 130 reports found: `{_count(by_category, 'modelo130_report')}` ({', '.join(m130_periods)}).",
        f"- Local files scanned: `{_count(by_category, 'scan_local_files')}`.",
        f"- ZIP members scanned without extraction: `{_count(by_category, 'scan_zip_members')}`.",
        f"- Candidate source-book row-evidence files found: `{source_book_count}`.",
        f"- Candidate asset/amortization schedule files found: `{asset_count}`.",
        "- ZIP member inspection is filename-only; it does not inspect PDF/image contents inside archives.",
    ]
    if source_book_count == 0 and asset_count == 0:
        lines.append(
            "- Local archive still does not contain the source-book row export or asset schedule needed to close quarter row treatment."
        )
    lines.extend(
        [
            "",
            "## Category Summary",
            "",
            "| Category | Count | Periods |",
            "|---|---:|---|",
        ]
    )
    for row in rows:
        lines.append(f"| {row['category']} | {row['count']} | {row['period']} |")
    lines.extend(["", "## Candidate Source-Book Or Asset Files", ""])
    candidate_rows = [
        row
        for row in rows
        if row["category"] in {SOURCE_BOOK_CATEGORY, ASSET_CATEGORY} and row["count"] != "0"
    ]
    if not candidate_rows:
        lines.append("none")
    else:
        for row in candidate_rows:
            lines.append(f"- `{row['category']}`: {row['paths']}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _classify_file(root: Path, path: Path) -> EvidenceFile | None:
    name = path.name
    rel = path.relative_to(root).as_posix()
    if name.lower() == "desktop.ini":
        return None
    category, period = _tax_report_category(name)
    if category is None:
        category = _supporting_category(path, root)
        period = ""
    return EvidenceFile(relative_path=rel, name=name, category=category, period=period, size_bytes=path.stat().st_size)


def _tax_report_category(name: str) -> tuple[str | None, str]:
    recognized = recognize_tax_form_filename(name)
    if recognized is None:
        return None, ""
    return recognized.category, recognized.period


def _supporting_category(path: Path, root: Path) -> str:
    name = path.name
    rel_parts = {part.upper() for part in path.relative_to(root).parts[:-1]}
    if _is_candidate_asset_schedule(name):
        return ASSET_CATEGORY
    if _is_known_register_false_positive(name):
        return "source_book_keyword_false_positive"
    if _is_candidate_source_book(name):
        return SOURCE_BOOK_CATEGORY
    if path.suffix.lower() in ARCHIVE_SUFFIXES:
        return "archive_file"
    if "EXPENSE" in rel_parts:
        return "expense_document"
    if "INVOICE" in rel_parts:
        return "income_invoice"
    if "COMPANY" in rel_parts:
        return "company_document"
    return "other_document"


def _is_candidate_source_book(name: str) -> bool:
    if _is_known_register_false_positive(name):
        return False
    return bool(REGISTER_PATTERN.search(name))


def _is_known_register_false_positive(name: str) -> bool:
    lowered = name.lower()
    return "justificante_registro" in lowered or "registro_electronico" in lowered


def _is_candidate_asset_schedule(name: str) -> bool:
    return bool(ASSET_PATTERN.search(name))


def _classify_archive_members(root: Path, archive_path: Path) -> tuple[list[EvidenceFile], int, list[EvidenceFile]]:
    rel_archive = archive_path.relative_to(root).as_posix()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = sorted(name for name in archive.namelist() if not name.endswith("/"))
    except zipfile.BadZipFile:
        return [], 0, [_archive_error(archive_path, root, "bad_zip")]
    except OSError:
        return [], 0, [_archive_error(archive_path, root, "unreadable_zip")]

    rows: list[EvidenceFile] = []
    for name in names:
        member_name = Path(name).name
        category, period = _archive_member_candidate_category(member_name)
        if category is None:
            continue
        rows.append(
            EvidenceFile(
                relative_path=f"{rel_archive}!/{name}",
                name=member_name,
                category=category,
                period=period,
                size_bytes=0,
            )
        )
    return rows, len(names), []


def _archive_member_candidate_category(name: str) -> tuple[str | None, str]:
    recognized = recognize_tax_form_filename(name)
    if recognized is not None:
        return recognized.category, recognized.period
    if _is_candidate_asset_schedule(name):
        return ASSET_CATEGORY, ""
    if _is_known_register_false_positive(name):
        return "archive_source_book_keyword_false_positive", ""
    if _is_candidate_source_book(name):
        return SOURCE_BOOK_CATEGORY, ""
    return None, ""


def _archive_error(path: Path, root: Path, category: str) -> EvidenceFile:
    return EvidenceFile(
        relative_path=path.relative_to(root).as_posix(),
        name=path.name,
        category=f"archive_error_{category}",
        period="",
        size_bytes=path.stat().st_size,
    )


def _count(by_category: dict[str, dict[str, str]], category: str) -> int:
    row = by_category.get(category)
    return int(row["count"]) if row else 0


def _periods(row: dict[str, str]) -> list[str]:
    periods = row.get("period", "")
    return [period for period in periods.split("; ") if period]
