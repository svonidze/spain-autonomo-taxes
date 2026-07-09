from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile


SOURCE_BOOK_CONTENT_CHECK_FIELDS = [
    "check",
    "scope",
    "status",
    "matched_count",
    "content_ready_count",
    "matched_files",
    "content_ready_files",
    "required_for",
    "missing_required_groups",
    "observed_headers",
    "evidence",
    "next_action",
]

REQUIRED_GROUPS = {
    "compras_gastos_book": {
        "date": ["date", "fecha", "invoice date", "booking date", "posting date"],
        "counterparty": ["supplier", "proveedor", "recipient", "vendor", "party", "counterparty", "emisor"],
        "document_number": ["number", "invoice number", "invoice_number", "factura", "document number", "numero"],
        "included_period": ["period", "quarter", "trimestre", "deducted in period", "modelo 130 period", "inclusion quarter"],
        "irpf_deductible_eur": [
            "irpf deductible eur",
            "deductible eur",
            "deductible base eur",
            "tax deductible eur",
            "importe deducible",
            "casilla 02",
        ],
    },
    "bienes_inversion_or_asset_schedule": {
        "asset_id": ["asset id", "asset", "fixed asset", "bien inversion", "bien de inversion", "investment good"],
        "acquisition_date": ["acquisition date", "purchase date", "fecha adquisicion", "date"],
        "amortizable_base_eur": ["amortizable base eur", "base amortizable", "cost basis", "acquisition cost"],
        "amortization_period": ["period", "quarter", "trimestre", "amortization period"],
        "amortization_amount_eur": [
            "amortization amount eur",
            "amortizacion eur",
            "depreciation amount",
            "deducted amortization",
        ],
        "rate_or_life": ["rate", "coefficient", "coeficiente", "useful life", "vida util", "percent", "%"],
    },
    "modelo130_source_book_tieout": {
        "period": ["period", "quarter", "trimestre", "modelo 130 period"],
        "casilla01_ytd": ["casilla 01", "casilla01", "sales ytd", "income ytd", "ingresos"],
        "casilla02_ytd": ["casilla 02", "casilla02", "expenses ytd", "gastos deducibles"],
        "casilla07": ["casilla 07", "casilla07", "payment due before reductions"],
        "casilla19": ["casilla 19", "casilla19", "payable", "amount due", "resultado"],
    },
}


@dataclass(frozen=True)
class _Inspection:
    path: str
    status: str
    file_format: str
    row_count: int
    headers: list[str]
    missing_groups: list[str]
    error: str = ""


def build_source_book_content_check(
    *,
    response_root: Path,
    source_book_response_check_csv: Path,
) -> list[dict[str, str]]:
    response_rows = _load_rows(source_book_response_check_csv)
    deliverables = [row for row in response_rows if row.get("check") != "package_verdict"]
    rows = [_deliverable_content_row(response_root, row) for row in deliverables]
    rows.append(_verdict_row(rows, response_rows))
    return rows


def write_source_book_content_check_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_BOOK_CONTENT_CHECK_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_source_book_content_check_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    verdict = next((row for row in rows if row["check"] == "content_verdict"), {})
    deliverables = [row for row in rows if row["check"] != "content_verdict"]
    ready = [row for row in deliverables if row["status"] == "content_ready"]
    attention = [row for row in deliverables if row["status"] != "content_ready"]
    lines = [
        "# Xolo Source-Book Content Check",
        "",
        "This report checks whether matched Xolo source-book files have the columns needed for row-level Modelo 130 reconciliation.",
        "It does not interpret the accounting treatment; it only blocks import when required structure is missing.",
        "",
        "## Verdict",
        "",
        f"- Status: `{verdict.get('status', 'unknown')}`.",
        f"- Content-ready deliverables: `{len(ready)}`.",
        f"- Deliverables needing attention: `{len(attention)}`.",
        f"- Next action: {verdict.get('next_action', '')}",
        "",
        "## Deliverables",
        "",
        "| Check | Scope | Status | Ready files | Missing required groups | Required for | Next action |",
        "|---|---|---|---:|---|---|---|",
    ]
    for row in deliverables:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["check"]),
                    _cell(row["scope"]),
                    _cell(row["status"]),
                    _cell(row["content_ready_count"]),
                    _cell(row["missing_required_groups"]),
                    _cell(row["required_for"]),
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )

    if ready:
        lines.extend(["", "## Content-Ready Files", "", "| Check | Scope | Files |", "|---|---|---|"])
        for row in ready:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(row["check"]),
                        _cell(row["scope"]),
                        _cell(row["content_ready_files"]),
                    ]
                )
                + " |"
            )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _deliverable_content_row(response_root: Path, row: dict[str, str]) -> dict[str, str]:
    check = row.get("check", "")
    files = _matched_paths(response_root, row.get("matched_files", ""))
    inspections = [_inspect_file(path, check) for path in files]
    ready = [item for item in inspections if item.status == "content_ready"]
    if not files:
        status = "missing_response_file"
        missing = sorted(REQUIRED_GROUPS.get(check, {}))
        evidence = "No matched response file was listed by audit-source-book-response-check."
        next_action = row.get("next_action", "Add the missing Xolo source-book file.")
    elif ready:
        status = "content_ready"
        missing = []
        evidence = _inspection_evidence(inspections)
        next_action = "Import and reconcile this deliverable against answer-intake rows."
    elif inspections and all(item.status == "unsupported_format" for item in inspections):
        status = "unsupported_content_format"
        missing = sorted(REQUIRED_GROUPS.get(check, {}))
        evidence = _inspection_evidence(inspections)
        next_action = "Ask Xolo for CSV/XLSX source-book exports with machine-readable headers."
    else:
        status = "content_incomplete"
        missing = _least_missing_groups(inspections, check)
        evidence = _inspection_evidence(inspections)
        next_action = "Ask Xolo to include the missing required fields or map them explicitly before import."
    return {
        "check": check,
        "scope": row.get("scope", ""),
        "status": status,
        "matched_count": str(len(files)),
        "content_ready_count": str(len(ready)),
        "matched_files": row.get("matched_files", ""),
        "content_ready_files": "; ".join(item.path for item in ready),
        "required_for": row.get("required_for", ""),
        "missing_required_groups": "; ".join(missing),
        "observed_headers": " || ".join(_headers_summary(item) for item in inspections),
        "evidence": evidence,
        "next_action": next_action,
    }


def _verdict_row(rows: list[dict[str, str]], response_rows: list[dict[str, str]]) -> dict[str, str]:
    deliverables = [row for row in rows if row.get("check") != "content_verdict"]
    ready = [row for row in deliverables if row.get("status") == "content_ready"]
    response_verdict = next((row for row in response_rows if row.get("check") == "package_verdict"), {})
    complete = bool(deliverables) and len(ready) == len(deliverables)
    status = "ready_for_import" if complete else "content_attention"
    if status == "ready_for_import":
        next_action = "Import source-book rows into answer-intake/register review and rerun quarter acceptance."
    else:
        next_action = f"Resolve {len(deliverables) - len(ready)} source-book content deliverable(s) before import."
    return {
        "check": "content_verdict",
        "scope": response_verdict.get("scope", ""),
        "status": status,
        "matched_count": str(len(deliverables)),
        "content_ready_count": str(len(ready)),
        "matched_files": "",
        "content_ready_files": "",
        "required_for": "all quarters in source-book response check",
        "missing_required_groups": "",
        "observed_headers": "",
        "evidence": f"{len(ready)}/{len(deliverables)} deliverables content-ready.",
        "next_action": next_action,
    }


def _inspect_file(path: Path, check: str) -> _Inspection:
    suffix = path.suffix.lower()
    try:
        if suffix in {".csv", ".tsv"}:
            headers, row_count = _read_delimited_headers(path, "\t" if suffix == ".tsv" else None)
            return _inspection(path, check, suffix.removeprefix("."), headers, row_count)
        if suffix == ".xlsx":
            return _best_xlsx_inspection(path, check)
        return _Inspection(str(path), "unsupported_format", suffix.removeprefix(".") or "unknown", 0, [], sorted(REQUIRED_GROUPS.get(check, {})))
    except Exception as exc:  # pragma: no cover - exact parser failures are environment/file dependent.
        return _Inspection(str(path), "unreadable", suffix.removeprefix(".") or "unknown", 0, [], sorted(REQUIRED_GROUPS.get(check, {})), str(exc))


def _inspection(path: Path, check: str, file_format: str, headers: list[str], row_count: int) -> _Inspection:
    missing = _missing_groups(check, headers)
    if not headers:
        status = "empty_or_no_header"
    elif row_count <= 0:
        status = "empty_or_no_data_rows"
    elif missing:
        status = "content_incomplete"
    else:
        status = "content_ready"
    return _Inspection(str(path), status, file_format, row_count, headers, missing)


def _missing_groups(check: str, headers: list[str]) -> list[str]:
    mapping = required_group_header_map(check, headers)
    return [group for group in REQUIRED_GROUPS.get(check, {}) if group not in mapping]


def required_group_header_map(check: str, headers: list[str]) -> dict[str, str]:
    groups = REQUIRED_GROUPS.get(check, {})
    normalized_headers = [_normalize(header) for header in headers]
    compact_headers = [_compact(header) for header in normalized_headers]
    mapping: dict[str, str] = {}
    for group, aliases in groups.items():
        match = _matched_header(aliases, headers, normalized_headers, compact_headers)
        if match:
            mapping[group] = match
    return mapping


def _matched_header(
    aliases: list[str],
    headers: list[str],
    normalized_headers: list[str],
    compact_headers: list[str],
) -> str:
    for alias in aliases:
        for header, normalized_header, compact_header in zip(headers, normalized_headers, compact_headers, strict=True):
            if _alias_matches(alias, [normalized_header], [compact_header]):
                return header
    return ""


def _alias_matches(alias: str, normalized_headers: list[str], compact_headers: list[str]) -> bool:
    normalized_alias = _normalize(alias)
    compact_alias = _compact(normalized_alias)
    for header, compact_header in zip(normalized_headers, compact_headers, strict=True):
        if normalized_alias == header or compact_alias == compact_header:
            return True
        alias_tokens = normalized_alias.split()
        header_tokens = header.split()
        if len(alias_tokens) == 1:
            token = alias_tokens[0]
            if token in header_tokens:
                return True
            if (any(char.isdigit() for char in token) or token == "%") and compact_alias and compact_alias in compact_header:
                return True
            continue
        if normalized_alias and normalized_alias in header:
            return True
        if compact_alias and compact_alias in compact_header:
            return True
    return False


def _read_delimited_headers(path: Path, delimiter: str | None) -> tuple[list[str], int]:
    text = _read_text(path)
    sample = text[:4096]
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return [], 0
    return [cell.strip() for cell in rows[0]], max(0, len(rows) - 1)


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _best_xlsx_inspection(path: Path, check: str) -> _Inspection:
    sheets = _read_xlsx_sheets(path)
    if not sheets:
        return _inspection(path, check, "xlsx", [], 0)
    inspections = [
        _inspection(path, check, f"xlsx:{sheet_name}", headers, row_count)
        for sheet_name, headers, row_count in sheets
    ]
    return min(inspections, key=_inspection_rank)


def _inspection_rank(item: _Inspection) -> tuple[int, int, int]:
    status_rank = {
        "content_ready": 0,
        "content_incomplete": 1,
        "empty_or_no_data_rows": 2,
        "empty_or_no_header": 3,
    }.get(item.status, 9)
    return (status_rank, len(item.missing_groups), -item.row_count)


def _read_xlsx_sheets(path: Path) -> list[tuple[str, list[str], int]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _shared_strings(archive)
        sheet_names = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
        if not sheet_names:
            return []
        sheets: list[tuple[str, list[str], int]] = []
        for sheet_name in sheet_names:
            root = ET.fromstring(archive.read(sheet_name))
            rows = [_xlsx_row_values(row, shared_strings) for row in root.findall(".//{*}sheetData/{*}row")]
            rows = [row for row in rows if any(cell.strip() for cell in row)]
            if not rows:
                sheets.append((Path(sheet_name).stem, [], 0))
            else:
                sheets.append((Path(sheet_name).stem, rows[0], max(0, len(rows) - 1)))
        return sheets


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root.findall(".//{*}si"):
        values.append("".join(text.text or "" for text in item.findall(".//{*}t")).strip())
    return values


def _xlsx_row_values(row: ET.Element, shared_strings: list[str]) -> list[str]:
    values_by_col: dict[int, str] = {}
    for cell in row.findall("{*}c"):
        ref = cell.attrib.get("r", "")
        column_index = _column_index(ref)
        values_by_col[column_index] = _xlsx_cell_value(cell, shared_strings)
    if not values_by_col:
        return []
    return [values_by_col.get(index, "") for index in range(1, max(values_by_col) + 1)]


def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//{*}t")).strip()
    value = cell.find("{*}v")
    raw = value.text.strip() if value is not None and value.text else ""
    if cell_type == "s" and raw.isdigit():
        index = int(raw)
        if 0 <= index < len(shared_strings):
            return shared_strings[index]
    return raw


def _column_index(cell_reference: str) -> int:
    letters = re.match(r"([A-Z]+)", cell_reference.upper())
    if not letters:
        return 1
    index = 0
    for char in letters.group(1):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def _matched_paths(root: Path, raw: str) -> list[Path]:
    paths: list[Path] = []
    for value in [part.strip() for part in raw.split(";") if part.strip()]:
        path = Path(value)
        if not path.is_absolute():
            path = root / value
        paths.append(path)
    return paths


def _least_missing_groups(inspections: list[_Inspection], check: str) -> list[str]:
    if not inspections:
        return sorted(REQUIRED_GROUPS.get(check, {}))
    return min((item.missing_groups for item in inspections), key=len, default=sorted(REQUIRED_GROUPS.get(check, {})))


def _inspection_evidence(inspections: list[_Inspection]) -> str:
    if not inspections:
        return "No files inspected."
    pieces = []
    for item in inspections:
        detail = f"{Path(item.path).name}: status={item.status}; rows={item.row_count}; format={item.file_format}"
        if item.error:
            detail += f"; error={item.error}"
        pieces.append(detail)
    return " | ".join(pieces)


def _headers_summary(item: _Inspection) -> str:
    if not item.headers:
        return f"{Path(item.path).name}:"
    return f"{Path(item.path).name}: " + ", ".join(item.headers[:20])


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    lowered = ascii_text.lower()
    replaced = re.sub(r"[^a-z0-9%]+", " ", lowered)
    return " ".join(replaced.split())


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9%]+", "", value)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
