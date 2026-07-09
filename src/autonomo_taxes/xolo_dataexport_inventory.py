from __future__ import annotations

import csv
from pathlib import Path
import zipfile
from collections import Counter


KEYWORDS = [
    "register",
    "ledger",
    "libro",
    "registro",
    "asset",
    "amort",
    "depre",
    "inversion",
    "fixed",
    "expense",
    "invoice",
    "tax_report",
    "130",
]

FIELDNAMES = ["kind", "key", "count", "paths"]


def build_xolo_dataexport_inventory(zip_path: Path) -> list[dict[str, str]]:
    if not zip_path.exists():
        raise FileNotFoundError(f"Xolo data export ZIP not found: {zip_path}")
    with zipfile.ZipFile(zip_path) as archive:
        names = sorted(name for name in archive.namelist() if not name.endswith("/"))

    rows: list[dict[str, str]] = []
    top_level = Counter(_top_level(name) for name in names)
    extensions = Counter(Path(name).suffix.lower() or "<none>" for name in names)

    rows.extend(_counter_rows("top_level", top_level))
    rows.extend(_counter_rows("extension", extensions))
    for keyword in KEYWORDS:
        lowered = keyword.lower()
        hits = [name for name in names if lowered in name.lower().replace(" ", "_")]
        rows.append(
            {
                "kind": "keyword",
                "key": keyword,
                "count": str(len(hits)),
                "paths": "; ".join(hits[:50]),
            }
        )
    candidate_paths = _candidate_paths(names)
    rows.append(
        {
            "kind": "conclusion",
            "key": "candidate_submitted_register_or_asset_schedule",
            "count": str(len(candidate_paths)),
            "paths": "; ".join(candidate_paths[:50]),
        }
    )
    return rows


def write_xolo_dataexport_inventory_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in FIELDNAMES})


def write_xolo_dataexport_inventory_markdown(path: Path, rows: list[dict[str, str]], zip_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_kind_key = {(row["kind"], row["key"]): row for row in rows}
    candidate_count = int(
        by_kind_key.get(("conclusion", "candidate_submitted_register_or_asset_schedule"), {}).get("count", "0")
    )
    lines = [
        "# Xolo Data Export Inventory",
        "",
        f"Scanned ZIP: `{zip_path}`",
        "",
        "## Conclusion",
        "",
        f"- Candidate source-book/register or asset/amortization files found: `{candidate_count}`.",
    ]
    if candidate_count == 0:
        lines.append(
            "- Filename-only scan found no register or asset/amortization schedule candidates. "
            "The downloaded Xolo data export appears to be an evidence-document archive, but this check does not inspect PDF/image contents."
        )
    lines.extend(["", "## Top Level", "", "| Folder | Count |", "|---|---:|"])
    for row in rows:
        if row["kind"] == "top_level":
            lines.append(f"| {row['key']} | {row['count']} |")
    lines.extend(["", "## Extensions", "", "| Extension | Count |", "|---|---:|"])
    for row in rows:
        if row["kind"] == "extension":
            lines.append(f"| {row['key']} | {row['count']} |")
    lines.extend(["", "## Keywords", "", "| Keyword | Count | Example paths |", "|---|---:|---|"])
    for row in rows:
        if row["kind"] == "keyword":
            paths = row["paths"] or ""
            lines.append(f"| {row['key']} | {row['count']} | {paths} |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _counter_rows(kind: str, counter: Counter[str]) -> list[dict[str, str]]:
    return [{"kind": kind, "key": key, "count": str(counter[key]), "paths": ""} for key in sorted(counter)]


def _top_level(name: str) -> str:
    return name.split("/", 1)[0] if "/" in name else "<root>"


def _candidate_paths(names: list[str]) -> list[str]:
    candidate_keywords = ("register", "ledger", "libro", "registro", "asset", "amort", "depre", "inversion", "fixed")
    return [
        name
        for name in names
        if any(keyword in name.lower().replace(" ", "_") for keyword in candidate_keywords)
    ]
