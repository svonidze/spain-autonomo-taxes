from __future__ import annotations

import csv
from pathlib import Path
import re
import unicodedata


TAX_REPORT_SEQUENCE_FIELDS = [
    "period",
    "expected_label",
    "status",
    "matched_count",
    "matched_files",
    "evidence",
    "next_action",
]


def build_tax_report_sequence(
    *,
    tax_report_dir: Path,
    start_year: int,
    start_quarter: int,
    end_year: int,
    end_quarter: int,
) -> list[dict[str, str]]:
    periods = _periods(start_year, start_quarter, end_year, end_quarter)
    files = _scan_files(tax_report_dir)
    rows = [_period_row(period, files) for period in periods]
    rows.append(_verdict_row(rows, tax_report_dir))
    return rows


def write_tax_report_sequence_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TAX_REPORT_SEQUENCE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_tax_report_sequence_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    verdict = next((row for row in rows if row["period"] == "sequence_verdict"), {})
    period_rows = [row for row in rows if row["period"] != "sequence_verdict"]
    missing = [row for row in period_rows if row["status"] == "missing"]
    duplicate = [row for row in period_rows if row["status"] == "duplicate"]
    lines = [
        "# Modelo 130 Tax Report Sequence",
        "",
        "This report verifies that submitted Modelo 130 PDFs are present for the chronological audit range.",
        "It is a filing-PDF inventory only; it does not prove source-book row treatment or asset amortization.",
        "",
        "## Verdict",
        "",
        f"- Status: `{verdict.get('status', 'unknown')}`.",
        f"- Expected quarters: `{len(period_rows)}`.",
        f"- Missing quarters: `{len(missing)}`.",
        f"- Duplicate quarters: `{len(duplicate)}`.",
        f"- Next action: {verdict.get('next_action', '')}",
        "",
        "## Sequence",
        "",
        "| Period | Expected label | Status | Matches | Files | Next action |",
        "|---|---|---|---:|---|---|",
    ]
    for row in period_rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["period"]),
                    _cell(row["expected_label"]),
                    _cell(row["status"]),
                    _cell(row["matched_count"]),
                    _cell(row["matched_files"]),
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


class _FileItem:
    def __init__(self, path: Path, root: Path) -> None:
        self.path = path
        self.display = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        self.normalized = _normalize(path.name)


def _period_row(period: str, files: list[_FileItem]) -> dict[str, str]:
    year, quarter = _split_period(period)
    matches = [item for item in files if _matches_modelo130_period(item.normalized, year, quarter)]
    if not matches:
        status = "missing"
        evidence = "No matching Modelo 130 PDF filename was found."
        next_action = f"Locate or export the submitted Modelo 130 PDF for {_expected_label(year, quarter)}."
    elif len(matches) == 1:
        status = "found"
        evidence = "Exactly one submitted Modelo 130 PDF filename matches this period."
        next_action = "Use this filed PDF as the submitted quarter target; source-book evidence is still required separately."
    else:
        status = "duplicate"
        evidence = "More than one Modelo 130 PDF filename matches this period."
        next_action = "Inspect duplicates and choose the filed declaration before using the quarter target."
    return {
        "period": period,
        "expected_label": _expected_label(year, quarter),
        "status": status,
        "matched_count": str(len(matches)),
        "matched_files": "; ".join(item.display for item in matches),
        "evidence": evidence,
        "next_action": next_action,
    }


def _verdict_row(rows: list[dict[str, str]], tax_report_dir: Path) -> dict[str, str]:
    missing = [row for row in rows if row["status"] == "missing"]
    duplicate = [row for row in rows if row["status"] == "duplicate"]
    if missing:
        status = "missing_reports"
        next_action = f"Do not claim full chronological coverage; add {len(missing)} missing Modelo 130 PDF(s) to {tax_report_dir}."
    elif duplicate:
        status = "duplicate_reports"
        next_action = "Resolve duplicate Modelo 130 PDFs before treating the sequence as authoritative."
    else:
        status = "complete"
        next_action = "Use this sequence as the filed-PDF baseline for the chronological audit; continue with source-book checks."
    return {
        "period": "sequence_verdict",
        "expected_label": "",
        "status": status,
        "matched_count": str(sum(int(row["matched_count"]) for row in rows)),
        "matched_files": "",
        "evidence": f"Checked {len(rows)} expected Modelo 130 quarters.",
        "next_action": next_action,
    }


def _scan_files(root: Path) -> list[_FileItem]:
    if not root.exists():
        return []
    return [_FileItem(path, root) for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"]


def _matches_modelo130_period(name: str, year: int, quarter: int) -> bool:
    if str(year) not in name:
        return False
    if not _is_modelo130(name):
        return False
    quarter_tokens = [f"{quarter}t", f"q{quarter}", f"{quarter} trimestre", f"trimestre {quarter}"]
    return any(token in name for token in quarter_tokens)


def _is_modelo130(name: str) -> bool:
    return bool(re.search(r"\bm(?:od(?:elo)?)?\s*130\b|\bmodelo\s*130\b", name))


def _periods(start_year: int, start_quarter: int, end_year: int, end_quarter: int) -> list[str]:
    if not 1 <= start_quarter <= 4 or not 1 <= end_quarter <= 4:
        raise ValueError("quarters must be between 1 and 4")
    start = start_year * 4 + start_quarter
    end = end_year * 4 + end_quarter
    if start > end:
        raise ValueError("start period must not be after end period")
    periods: list[str] = []
    for marker in range(start, end + 1):
        year = (marker - 1) // 4
        quarter = marker - year * 4
        periods.append(f"{year}-Q{quarter}")
    return periods


def _split_period(period: str) -> tuple[int, int]:
    year, quarter = period.split("-Q")
    return int(year), int(quarter)


def _expected_label(year: int, quarter: int) -> str:
    return f"M130 {quarter}T {year}"


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    lowered = ascii_text.lower()
    return " ".join(lowered.replace("_", " ").replace("-", " ").split())


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
