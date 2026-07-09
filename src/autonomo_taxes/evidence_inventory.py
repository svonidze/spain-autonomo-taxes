from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class EvidenceFile:
    relative_path: str
    name: str
    category: str
    period: str
    size_bytes: int


M130_PATTERN = re.compile(r"(?:M130|MOD 130|Mod 130)\s+([1-4])T\s+(20\d{2})", re.I)
M303_PATTERN = re.compile(r"(?:M303|MOD 303|Mod 303)\s+([1-4])T\s+(20\d{2})", re.I)
M100_PATTERN = re.compile(r"(?:M100|MOD 100|DRAFT_MOD 100|Mod 100)\s+0A\s+(20\d{2})", re.I)
M390_PATTERN = re.compile(r"(?:M390|MOD 390|Mod 390)\s+(20\d{2})", re.I)
REGISTER_PATTERN = re.compile(r"(libro|ledger|register|registro)", re.I)
ASSET_PATTERN = re.compile(r"(amort|activo|asset|bienes|invers|fixed|depreci)", re.I)


def build_evidence_inventory(xolo_root: Path) -> list[dict[str, str]]:
    files = [_classify_file(xolo_root, path) for path in sorted(p for p in xolo_root.rglob("*") if p.is_file())]
    files = [file for file in files if file is not None]
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
    for expected in ("candidate_submitted_register", "candidate_asset_schedule"):
        if expected not in by_category:
            rows.append({"category": expected, "period": "", "count": "0", "paths": ""})
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
    register_count = _count(by_category, "candidate_submitted_register")
    asset_count = _count(by_category, "candidate_asset_schedule")
    lines = [
        "# Modelo 130 Evidence Inventory",
        "",
        f"Scanned root: `{xolo_root}`",
        "",
        "## Conclusion",
        "",
        f"- Modelo 130 reports found: `{_count(by_category, 'modelo130_report')}` ({', '.join(m130_periods)}).",
        f"- Candidate source-book/register files found: `{register_count}`.",
        f"- Candidate asset/amortization schedule files found: `{asset_count}`.",
    ]
    if register_count == 0 and asset_count == 0:
        lines.append(
            "- Local archive still does not contain the source-book/register or asset schedule export needed to close quarter row treatment."
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
    lines.extend(["", "## Candidate Register Or Asset Files", ""])
    candidate_rows = [
        row
        for row in rows
        if row["category"] in {"candidate_submitted_register", "candidate_asset_schedule"} and row["count"] != "0"
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
    for pattern, category in (
        (M130_PATTERN, "modelo130_report"),
        (M303_PATTERN, "modelo303_report"),
    ):
        match = pattern.search(name)
        if match:
            quarter, year = match.group(1), match.group(2)
            return category, f"{year}-Q{quarter}"
    match = M100_PATTERN.search(name)
    if match:
        return "modelo100_report", match.group(1)
    match = M390_PATTERN.search(name)
    if match:
        return "modelo390_report", match.group(1)
    return None, ""


def _supporting_category(path: Path, root: Path) -> str:
    name = path.name
    rel_parts = {part.upper() for part in path.relative_to(root).parts[:-1]}
    if _is_candidate_asset_schedule(name):
        return "candidate_asset_schedule"
    if _is_candidate_register(name):
        return "candidate_submitted_register"
    if "EXPENSE" in rel_parts:
        return "expense_document"
    if "INVOICE" in rel_parts:
        return "income_invoice"
    if "COMPANY" in rel_parts:
        return "company_document"
    return "other_document"


def _is_candidate_register(name: str) -> bool:
    lowered = name.lower()
    if "justificante_registro" in lowered or "registro_electronico" in lowered:
        return False
    return bool(REGISTER_PATTERN.search(name))


def _is_candidate_asset_schedule(name: str) -> bool:
    return bool(ASSET_PATTERN.search(name))


def _count(by_category: dict[str, dict[str, str]], category: str) -> int:
    row = by_category.get(category)
    return int(row["count"]) if row else 0


def _periods(row: dict[str, str]) -> list[str]:
    periods = row.get("period", "")
    return [period for period in periods.split("; ") if period]
