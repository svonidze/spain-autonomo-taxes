from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil


PACKAGE_MANIFEST_FIELDS = [
    "package_path",
    "source_path",
    "role",
    "status",
    "size",
    "sha256",
    "notes",
]

ALLOWED_ATTACHMENT_SUFFIXES = {".md", ".txt"}


@dataclass(frozen=True)
class PackageItem:
    package_path: str
    source_path: str
    role: str
    status: str
    size: str
    sha256: str
    notes: str


def build_source_book_request_package(
    *,
    message_md: Path,
    attachments: list[Path],
    out_dir: Path,
) -> list[dict[str, str]]:
    _validate_required_message(message_md)
    _prepare_out_dir(out_dir)
    rows: list[PackageItem] = []
    rows.append(_copy_required_message(message_md, out_dir / "message_to_xolo.md"))

    attachment_dir = out_dir / "attachments"
    attachment_dir.mkdir(parents=True, exist_ok=True)
    seen_names: set[str] = set()
    for attachment in attachments:
        rows.append(_copy_attachment(attachment, attachment_dir, seen_names))

    dict_rows = [_as_dict(row) for row in rows]
    write_source_book_request_package_manifest_csv(out_dir / "MANIFEST.csv", dict_rows)
    write_source_book_request_package_manifest_markdown(out_dir / "MANIFEST.md", dict_rows)
    return dict_rows


def _validate_required_message(source: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Required message file is missing: {source}")
    if source.suffix.lower() not in ALLOWED_ATTACHMENT_SUFFIXES:
        raise ValueError(
            "Required message must be markdown/text so it cannot hide spreadsheet target-fitting data: "
            + str(source)
        )


def _prepare_out_dir(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("message_to_xolo.md", "MANIFEST.csv", "MANIFEST.md"):
        path = out_dir / name
        if path.is_file():
            path.unlink()
    attachment_dir = out_dir / "attachments"
    if attachment_dir.exists():
        for path in attachment_dir.iterdir():
            if path.is_file():
                path.unlink()


def write_source_book_request_package_manifest_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=PACKAGE_MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_source_book_request_package_manifest_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    included = [row for row in rows if row["status"] == "included"]
    safety_excluded = [row for row in rows if row["status"].startswith("excluded_")]
    missing_or_broken = [row for row in rows if row["status"].startswith("missing_")]
    lines = [
        "# Xolo Source-Book Request Package Manifest",
        "",
        f"Created at: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "This package is for requesting Xolo's official IRPF register exports.",
        "It intentionally excludes spreadsheet/data attachment formats by default.",
        "Allowed markdown/text attachments still require human content review before sending.",
        "",
        "## Summary",
        "",
        f"- Included files: `{len(included)}`.",
        f"- Excluded for safety: `{len(safety_excluded)}`.",
        f"- Missing or broken inputs: `{len(missing_or_broken)}`.",
        "",
        "## Files",
        "",
        "| Status | Role | Package path | Source path | SHA256 | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["status"]),
                    _cell(row["role"]),
                    _cell(row["package_path"]),
                    _cell(row["source_path"]),
                    _cell(row["sha256"]),
                    _cell(row["notes"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Send Guidance",
            "",
            "Start with `message_to_xolo.md`.",
            "Attach markdown reports only if Xolo asks for supporting context.",
            "Do not attach CSV/JSON/XLSX target-fitting outputs unless Xolo explicitly asks for local calculations; official registers should come from Xolo's accounting records.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _copy_required_message(source: Path, destination: Path) -> PackageItem:
    shutil.copyfile(source, destination)
    return _included_item(source, destination, "message", "Primary text to paste into Xolo support.")


def _copy_attachment(source: Path, attachment_dir: Path, seen_names: set[str]) -> PackageItem:
    if not source.exists():
        return PackageItem(
            package_path="",
            source_path=str(source),
            role="optional_attachment",
            status="missing_attachment",
            size="",
            sha256="",
            notes="Attachment path does not exist.",
        )
    if source.suffix.lower() not in ALLOWED_ATTACHMENT_SUFFIXES:
        return PackageItem(
            package_path="",
            source_path=str(source),
            role="optional_attachment",
            status="excluded_disallowed_suffix",
            size=str(source.stat().st_size),
            sha256=_sha256(source),
            notes="Only markdown/text attachments are included by default to avoid sending target-fitting spreadsheets.",
        )
    destination = attachment_dir / _unique_name(source.name, seen_names)
    shutil.copyfile(source, destination)
    return _included_item(source, destination, "optional_attachment", "Markdown/text support context.")


def _included_item(source: Path, destination: Path, role: str, notes: str) -> PackageItem:
    return PackageItem(
        package_path=str(destination),
        source_path=str(source),
        role=role,
        status="included",
        size=str(destination.stat().st_size),
        sha256=_sha256(destination),
        notes=notes,
    )


def _unique_name(name: str, seen_names: set[str]) -> str:
    candidate = name
    stem = Path(name).stem
    suffix = Path(name).suffix
    index = 2
    while candidate.lower() in seen_names:
        candidate = f"{stem}-{index}{suffix}"
        index += 1
    seen_names.add(candidate.lower())
    return candidate


def _as_dict(item: PackageItem) -> dict[str, str]:
    return {
        "package_path": item.package_path,
        "source_path": item.source_path,
        "role": item.role,
        "status": item.status,
        "size": item.size,
        "sha256": item.sha256,
        "notes": item.notes,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
