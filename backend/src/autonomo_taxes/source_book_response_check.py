from __future__ import annotations

import csv
from pathlib import Path
import re
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
        required_for = _periods_for_year(periods, year)
        rows.extend(
            [
                _deliverable_row(
                    check="ingresos_book",
                    scope=year,
                    matches=_matching_files(files, lambda item, year=year: _matches_ingresos_book(item.normalized, year)),
                    required_for=required_for,
                    missing_action=f"Ask Xolo for the official Libro registro de ingresos for {year}.",
                ),
                _deliverable_row(
                    check="gastos_book",
                    scope=year,
                    matches=_matching_files(files, lambda item, year=year: _matches_gastos_book(item.normalized, year)),
                    required_for=required_for,
                    missing_action=f"Ask Xolo for the official Libro registro de gastos for {year}.",
                ),
                _deliverable_row(
                    check="bienes_inversion_book",
                    scope=year,
                    matches=_matching_files(files, lambda item, year=year: _matches_bienes_inversion_book(item.normalized, year)),
                    required_for=required_for,
                    missing_action=f"Ask Xolo for the official Libro registro de bienes de inversion for {year}.",
                ),
                _deliverable_row(
                    check="provisiones_suplidos_book",
                    scope=year,
                    matches=_matching_files(files, lambda item, year=year: _matches_provisiones_suplidos_book(item.normalized, year)),
                    required_for=required_for,
                    missing_action=(
                        "Ask Xolo for the official Libro registro de provisiones de fondos y suplidos "
                        f"for {year}, even if empty or not applicable."
                    ),
                ),
            ]
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
        "# Xolo Official Register Response Package Check",
        "",
        "This report checks whether a received Xolo response folder contains the official IRPF register exports needed to continue the chronological Modelo 130 audit.",
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


def _periods_for_year(periods: list[str], year: str) -> str:
    return ", ".join(period for period in periods if period.startswith(year))


def _scan_files(root: Path) -> list[_FileItem]:
    if not root.exists():
        return []
    return [_FileItem(path, root) for path in root.rglob("*") if path.is_file()]


def _matching_files(files: list[_FileItem], predicate) -> list[_FileItem]:
    return [item for item in files if predicate(item)]


def _matches_ingresos_book(name: str, year: str) -> bool:
    if _matches_xolo_combined_accounting_book(name, year):
        return True
    has_book = _has_any(name, ["libro", "registro", "register", "book", "ledger"])
    has_income = _has_any(
        name,
        [
            "ingresos",
            "ventas",
            "ventas ingresos",
            "facturas expedidas",
            "issued invoices",
            "income",
            "revenue",
            "sales",
        ],
    )
    if not (has_book and has_income):
        return False
    if _has_any(name, ["gastos", "compras", "expenses", "purchases", "recibidas"]):
        return False
    return _has_year_scope(name, year, allow_unscoped=True)


def _matches_gastos_book(name: str, year: str) -> bool:
    if _matches_xolo_combined_accounting_book(name, year):
        return True
    has_book = _has_any(name, ["libro", "registro", "register", "book", "ledger"])
    has_expense = _has_any(
        name,
        [
            "compras",
            "gastos",
            "expense",
            "expenses",
            "purchase",
            "purchases",
            "facturas recibidas",
            "received invoices",
        ],
    )
    if not (has_book and has_expense):
        return False
    return _has_year_scope(name, year, allow_unscoped=True)


def _matches_bienes_inversion_book(name: str, year: str) -> bool:
    if _matches_xolo_combined_accounting_book(name, year):
        return True
    has_book = _has_any(name, ["libro", "registro", "register", "book", "ledger"])
    has_asset = _has_any(
        name,
        [
            "bienes inversion",
            "bienes de inversion",
            "investment goods",
            "asset schedule",
            "fixed asset",
            "fixed assets",
            "amortizacion",
            "amortization",
            "depreciation",
        ],
    )
    if not (has_book or _has_any(name, ["asset schedule", "amortization schedule", "depreciation schedule"])):
        return False
    if not has_asset:
        return False
    return _has_year_scope(name, year, allow_unscoped=True)


def _matches_provisiones_suplidos_book(name: str, year: str) -> bool:
    if _matches_xolo_combined_accounting_book(name, year):
        return True
    has_book = _has_any(name, ["libro", "registro", "register", "book", "ledger"])
    has_provisions = _has_any(
        name,
        [
            "provisiones",
            "provision",
            "provisions",
            "provisiones de fondos",
            "suplidos",
            "funds",
            "disbursements",
            "advances",
        ],
    )
    if not (has_book and has_provisions):
        return False
    return _has_year_scope(name, year, allow_unscoped=True)


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
    return any(_has_token_sequence(value, needle) for needle in needles)


def _has_all_year_scope(value: str) -> bool:
    return _has_any(
        value,
        [
            "all years",
            "allyears",
            "full history",
            "complete",
            "todo",
            "todos",
            "historico",
        ],
    )


def _has_year_scope(value: str, year: str, *, allow_unscoped: bool = False) -> bool:
    if year in value or _has_year_range_scope(value):
        return True
    if _contains_year(value):
        return False
    if _has_all_year_scope(value):
        return True
    if allow_unscoped:
        return True
    return False


def _matches_xolo_combined_accounting_book(value: str, year: str) -> bool:
    has_xolo_name = _has_any(value, ["libros contables", "libro contable"])
    return has_xolo_name and _has_year_scope(value, year, allow_unscoped=False)


def _has_year_range_scope(value: str) -> bool:
    return _has_any(value, ["2023 2026", "2023 to 2026", "2023 2024 2025 2026"])


def _contains_year(value: str) -> bool:
    return re.search(r"20\d{2}", value) is not None


def _has_token_sequence(value: str, needle: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
    if re.search(pattern, value):
        return True
    if " " not in needle:
        escaped = re.escape(needle)
        if re.search(rf"(?<![a-z0-9]){escaped}(?=20\d{{2}})", value):
            return True
        if re.search(rf"(?<=20\d{{2}}){escaped}(?![a-z0-9])", value):
            return True
    return False


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    lowered = ascii_text.lower()
    return " ".join(lowered.replace("_", " ").replace("-", " ").split())


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
