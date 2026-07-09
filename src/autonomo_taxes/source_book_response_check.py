from __future__ import annotations

import csv
from pathlib import Path
import unicodedata


SOURCE_BOOK_RESPONSE_CHECK_FIELDS = [
    "check",
    "scope",
    "status",
    "matched_count",
    "matched_files",
    "required_for",
    "next_action",
]


def build_source_book_response_check(
    *,
    response_root: Path,
    quarter_acceptance_csv: Path,
) -> list[dict[str, str]]:
    periods = _periods_from_acceptance(quarter_acceptance_csv)
    if not periods:
        return [
            {
                "check": "package_verdict",
                "scope": "",
                "status": "invalid_quarter_acceptance",
                "matched_count": "0",
                "matched_files": "",
                "required_for": "all quarters in quarter acceptance matrix",
                "next_action": "Regenerate quarter acceptance with a period column before checking Xolo response files.",
            }
        ]
    years = sorted({period.split("-")[0] for period in periods})
    files = _scan_files(response_root)

    rows: list[dict[str, str]] = []
    for year in years:
        rows.append(
            _deliverable_row(
                check="compras_gastos_book",
                scope=year,
                matches=_matching_files(files, lambda item, year=year: _matches_compras_gastos(item.normalized, year)),
                required_for=", ".join(period for period in periods if period.startswith(year)),
                missing_action=f"Ask Xolo for libro registro de compras y gastos for {year}, with IRPF deductible EUR per row.",
            )
        )
    for year in years:
        rows.append(
            _deliverable_row(
                check="bienes_inversion_or_asset_schedule",
                scope=year,
                matches=_matching_files(files, lambda item, year=year: _matches_asset_schedule(item.normalized, year)),
                required_for=", ".join(period for period in periods if period.startswith(year)),
                missing_action=f"Ask Xolo for libro registro de bienes de inversion / asset amortization schedule covering {year}.",
            )
        )
    for period in periods:
        rows.append(
            _deliverable_row(
                check="modelo130_source_book_tieout",
                scope=period,
                matches=_matching_files(files, lambda item, period=period: _matches_quarter_tieout(item.normalized, period)),
                required_for=period,
                missing_action=f"Ask Xolo for a source-book tie-out to filed Modelo 130 casillas for {period}.",
            )
        )

    rows.append(_verdict_row(rows, periods, response_root))
    return rows


def write_source_book_response_check_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_BOOK_RESPONSE_CHECK_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_source_book_response_check_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    verdict = next((row for row in rows if row["check"] == "package_verdict"), {})
    missing = [
        row for row in rows if row["check"] != "package_verdict" and row["status"] not in {"found", "ready_for_intake"}
    ]
    found = [row for row in rows if row["status"] == "found"]
    lines = [
        "# Xolo Source-Book Response Package Check",
        "",
        "This report checks whether a received Xolo response folder contains the source-book deliverables needed to continue the chronological Modelo 130 audit.",
        "It is a filename-level intake check only; matched files still need row-level import and reconciliation.",
        "",
        "## Verdict",
        "",
        f"- Status: `{verdict.get('status', 'unknown')}`.",
        f"- Found deliverables: `{len(found)}`.",
        f"- Missing deliverables: `{len(missing)}`.",
        f"- Next action: {verdict.get('next_action', '')}",
        "",
        "## Deliverables",
        "",
        "| Check | Scope | Status | Matches | Required for | Next action |",
        "|---|---|---|---:|---|---|",
    ]
    for row in rows:
        if row["check"] == "package_verdict":
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["check"]),
                    _cell(row["scope"]),
                    _cell(row["status"]),
                    _cell(row["matched_count"]),
                    _cell(row["required_for"]),
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )

    matched_rows = [row for row in rows if row["status"] == "found" and row["matched_files"]]
    if matched_rows:
        lines.extend(["", "## Matched Files", "", "| Check | Scope | Files |", "|---|---|---|"])
        for row in matched_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(row["check"]),
                        _cell(row["scope"]),
                        _cell(row["matched_files"]),
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
        self.normalized = _normalize(self.display)


def _periods_from_acceptance(path: Path) -> list[str]:
    rows = _load_rows(path)
    periods = [row.get("period", "") for row in rows if row.get("period", "").count("-") == 1]
    return sorted(dict.fromkeys(periods))


def _scan_files(root: Path) -> list[_FileItem]:
    if not root.exists():
        return []
    return [_FileItem(path, root) for path in root.rglob("*") if path.is_file()]


def _matching_files(files: list[_FileItem], predicate) -> list[_FileItem]:
    return [item for item in files if predicate(item)]


def _matches_compras_gastos(name: str, year: str) -> bool:
    has_year_scope = year in name or _has_all_year_scope(name)
    if not has_year_scope:
        return False
    has_book = _has_any(name, ["libro", "registro", "register", "book"])
    has_expense = _has_any(name, ["compras", "gastos", "expense", "expenses", "purchase", "purchases"])
    return has_book and has_expense


def _matches_asset_schedule(name: str, year: str) -> bool:
    has_year_scope = year in name or _has_all_year_scope(name)
    if not has_year_scope:
        return False
    return _has_any(
        name,
        [
            "bienes inversion",
            "bienes de inversion",
            "investment goods",
            "asset schedule",
            "fixed asset",
            "amortizacion",
            "amortization",
            "depreciation",
        ],
    )


def _matches_quarter_tieout(name: str, period: str) -> bool:
    year, quarter = period.split("-")
    quarter_number = quarter.removeprefix("Q")
    quarter_tokens = [
        f"{quarter_number}t",
        f"q{quarter_number}",
        f"{quarter_number} trimestre",
        f"trimestre {quarter_number}",
        f"quarter {quarter_number}",
    ]
    if year not in name or not _has_any(name, quarter_tokens):
        return False
    return _has_any(name, ["tieout", "tie out", "casilla", "calculo", "calculation", "source book"])


def _deliverable_row(
    *,
    check: str,
    scope: str,
    matches: list[_FileItem],
    required_for: str,
    missing_action: str,
) -> dict[str, str]:
    status = "found" if matches else "missing"
    return {
        "check": check,
        "scope": scope,
        "status": status,
        "matched_count": str(len(matches)),
        "matched_files": "; ".join(item.display for item in matches),
        "required_for": required_for,
        "next_action": "Import and reconcile this deliverable against answer-intake rows." if matches else missing_action,
    }


def _verdict_row(rows: list[dict[str, str]], periods: list[str], response_root: Path) -> dict[str, str]:
    missing = [row for row in rows if row["status"] == "missing"]
    status = "ready_for_intake" if not missing else "missing_required_deliverables"
    if status == "ready_for_intake":
        next_action = "Import Xolo rows into runs/modelo130_register_answer_intake.csv and rerun first-gate answer check."
    else:
        next_action = f"Do not rebuild quarters yet; request or add {len(missing)} missing deliverable(s) to {response_root}."
    return {
        "check": "package_verdict",
        "scope": f"{periods[0]}..{periods[-1]}" if periods else "",
        "status": status,
        "matched_count": str(len(rows) - len(missing)),
        "matched_files": "",
        "required_for": "all quarters in quarter acceptance matrix",
        "next_action": next_action,
    }


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _has_any(value: str, needles: list[str]) -> bool:
    return any(needle in value for needle in needles)


def _has_all_year_scope(value: str) -> bool:
    return _has_any(value, ["all years", "allyears", "full history", "complete", "2023 2026", "2023 to 2026"])


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    lowered = ascii_text.lower()
    return " ".join(lowered.replace("_", " ").replace("-", " ").split())


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
