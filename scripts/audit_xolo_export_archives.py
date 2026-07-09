from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autonomo_taxes.xolo_dataexport_inventory import KEYWORDS, build_xolo_dataexport_inventory  # noqa: E402


FIELDNAMES = [
    "archive",
    "bytes",
    "company_files",
    "expense_files",
    "invoice_files",
    "tax_report_files",
    "candidate_source_book_or_asset_files",
    *[f"keyword_{keyword}" for keyword in KEYWORDS],
    "candidate_paths",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare filename inventories across Xolo dataexport ZIP archives")
    parser.add_argument("--zip-dir", type=Path, required=True)
    parser.add_argument("--out-csv", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    rows = build_rows(args.zip_dir)
    write_csv(args.out_csv, rows)
    write_markdown(args.out_md, rows, args.zip_dir)
    print(f"Compared {len(rows)} Xolo dataexport archives into {args.out_csv} and {args.out_md}")
    return 0


def build_rows(zip_dir: Path) -> list[dict[str, str]]:
    if not zip_dir.exists():
        raise FileNotFoundError(f"Xolo dataexport ZIP directory not found: {zip_dir}")
    rows: list[dict[str, str]] = []
    for zip_path in sorted(zip_dir.glob("dataexport_*.zip"), key=lambda path: path.stat().st_mtime, reverse=True):
        inventory = build_xolo_dataexport_inventory(zip_path)
        by_kind_key = {(row["kind"], row["key"]): row for row in inventory}
        row = {
            "archive": zip_path.name,
            "bytes": str(zip_path.stat().st_size),
            "company_files": _count(by_kind_key, "top_level", "COMPANY"),
            "expense_files": _count(by_kind_key, "top_level", "EXPENSE"),
            "invoice_files": _count(by_kind_key, "top_level", "INVOICE"),
            "tax_report_files": _count(by_kind_key, "top_level", "TAX_REPORT"),
            "candidate_source_book_or_asset_files": _count(
                by_kind_key, "conclusion", "candidate_source_book_or_asset_schedule"
            ),
            "candidate_paths": by_kind_key.get(("conclusion", "candidate_source_book_or_asset_schedule"), {}).get(
                "paths", ""
            ),
        }
        for keyword in KEYWORDS:
            row[f"keyword_{keyword}"] = _count(by_kind_key, "keyword", keyword)
        rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: list[dict[str, str]], zip_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate_archives = [row for row in rows if row["candidate_source_book_or_asset_files"] != "0"]
    lines = [
        "# Xolo Data Export Archive Comparison",
        "",
        f"Scanned directory: `{zip_dir}`",
        "",
        "## Conclusion",
        "",
        f"- Archives scanned: `{len(rows)}`.",
        f"- Archives with filename-level source-book/asset/amortization candidates: `{len(candidate_archives)}`.",
    ]
    if not candidate_archives:
        lines.append(
            "- None of the historical ZIP archives expose source-book row evidence or an asset amortization schedule by filename."
        )
    lines.append("- This is a filename-only scan; it does not inspect PDF or image contents inside the archives.")
    lines.extend(
        [
            "",
            "## Archives",
            "",
            "| Archive | Size bytes | COMPANY | EXPENSE | INVOICE | TAX_REPORT | Candidate source books/assets |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["archive"],
                    row["bytes"],
                    row["company_files"],
                    row["expense_files"],
                    row["invoice_files"],
                    row["tax_report_files"],
                    row["candidate_source_book_or_asset_files"],
                ]
            )
            + " |"
        )
    lines.extend(["", "## Keyword Counts", "", "| Archive | register | ledger | libro | registro | asset | amort | depre | fixed |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["archive"],
                    row["keyword_register"],
                    row["keyword_ledger"],
                    row["keyword_libro"],
                    row["keyword_registro"],
                    row["keyword_asset"],
                    row["keyword_amort"],
                    row["keyword_depre"],
                    row["keyword_fixed"],
                ]
            )
            + " |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _count(by_kind_key: dict[tuple[str, str], dict[str, str]], kind: str, key: str) -> str:
    return by_kind_key.get((kind, key), {}).get("count", "0")


if __name__ == "__main__":
    raise SystemExit(main())
