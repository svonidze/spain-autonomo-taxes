from __future__ import annotations

import csv
import json
from pathlib import Path

from .register_answer_intake import REGISTER_ANSWER_INTAKE_FIELDS


def import_register_answer_sheet_json(sheet_json: Path) -> list[dict[str, str]]:
    values = json.loads(sheet_json.read_text(encoding="utf-8-sig"))
    if not isinstance(values, list) or not values:
        raise ValueError("Sheet JSON must be a non-empty 2D array exported from the register_answer_intake tab.")
    header = values[0]
    if not isinstance(header, list):
        raise ValueError("Sheet JSON first row must contain headers.")
    missing = [field for field in REGISTER_ANSWER_INTAKE_FIELDS if field not in header]
    if missing:
        raise ValueError("Sheet JSON is missing required register_answer_intake columns: " + ", ".join(missing))
    indexes = {field: header.index(field) for field in REGISTER_ANSWER_INTAKE_FIELDS}

    rows: list[dict[str, str]] = []
    for raw_row in values[1:]:
        if not isinstance(raw_row, list):
            continue
        normalized = ["" if value is None else str(value) for value in raw_row]
        if not any(cell.strip() for cell in normalized):
            continue
        row = {field: _cell_at(normalized, indexes[field]) for field in REGISTER_ANSWER_INTAKE_FIELDS}
        if row["queue_rank"].strip():
            rows.append(row)
    _validate_unique_ranks(rows)
    return rows


def write_imported_register_answer_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTER_ANSWER_INTAKE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _cell_at(row: list[str], index: int) -> str:
    if index >= len(row):
        return ""
    return row[index].strip()


def _validate_unique_ranks(rows: list[dict[str, str]]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        rank = row["queue_rank"].strip()
        if rank in seen:
            duplicates.add(rank)
        seen.add(rank)
    if duplicates:
        raise ValueError("Sheet JSON contains duplicate queue_rank values: " + ", ".join(sorted(duplicates)))
