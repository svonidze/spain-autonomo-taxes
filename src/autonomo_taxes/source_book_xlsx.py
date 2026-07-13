from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile


@dataclass(frozen=True)
class XlsxTable:
    file_format: str
    headers: list[str]
    rows: list[list[str]]


def read_xlsx_tables(path: Path) -> list[XlsxTable]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _shared_strings(archive)
        sheet_names = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
        tables: list[XlsxTable] = []
        for sheet_name in sheet_names:
            root = ET.fromstring(archive.read(sheet_name))
            rows = [_xlsx_row_values(row, shared_strings) for row in root.findall(".//{*}sheetData/{*}row")]
            rows = [_trim_trailing_empty(row) for row in rows if any(cell.strip() for cell in row)]
            sheet_id = Path(sheet_name).stem
            if not rows:
                tables.append(XlsxTable(f"xlsx:{sheet_id}", [], []))
                continue
            tables.extend(_candidate_tables(sheet_id, rows))
        return tables


def _candidate_tables(sheet_id: str, rows: list[list[str]]) -> list[XlsxTable]:
    tables: list[XlsxTable] = []
    for header_index, header_row in enumerate(rows):
        headers = _dedupe_headers(header_row)
        tables.append(XlsxTable(f"xlsx:{sheet_id}:r{header_index + 1}", headers, rows[header_index + 1 :]))
        if header_index + 1 >= len(rows):
            continue
        combined = _dedupe_headers(_combine_header_rows(header_row, rows[header_index + 1]))
        tables.append(XlsxTable(f"xlsx:{sheet_id}:r{header_index + 1}-r{header_index + 2}", combined, rows[header_index + 2 :]))
    return tables


def _combine_header_rows(parent_row: list[str], child_row: list[str]) -> list[str]:
    width = max(len(parent_row), len(child_row))
    headers: list[str] = []
    active_parent = ""
    for index in range(width):
        parent = _cell(parent_row, index)
        child = _cell(child_row, index)
        if parent:
            active_parent = parent
        if child and active_parent and child != active_parent:
            headers.append(f"{active_parent} {child}")
        else:
            headers.append(child or parent or active_parent)
    return headers


def _dedupe_headers(headers: list[str]) -> list[str]:
    output: list[str] = []
    seen: dict[str, int] = {}
    for index, header in enumerate(headers, start=1):
        value = " ".join((header or "").replace("\r", " ").replace("\n", " ").split())
        if not value:
            value = f"Unnamed:{index}"
        count = seen.get(value, 0) + 1
        seen[value] = count
        output.append(value if count == 1 else f"{value} #{count}")
    return output


def _cell(row: list[str], index: int) -> str:
    return row[index].strip() if index < len(row) else ""


def _trim_trailing_empty(row: list[str]) -> list[str]:
    end = len(row)
    while end > 0 and not row[end - 1].strip():
        end -= 1
    return row[:end]


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
    return _normalize_numeric_raw(raw)


def _normalize_numeric_raw(raw: str) -> str:
    if not re.fullmatch(r"-?\d+\.\d+", raw):
        return raw
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return raw
    if len(raw.rsplit(".", 1)[1]) > 6:
        return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):f}"
    normalized = value.normalize()
    return f"{normalized:f}"


def _column_index(cell_reference: str) -> int:
    letters = re.match(r"([A-Z]+)", cell_reference.upper())
    if not letters:
        return 1
    index = 0
    for char in letters.group(1):
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index
