from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Iterable, Mapping


REQUIRED_OFFBOARDING_CATEGORIES = (
    "exports",
    "books",
    "forms",
    "justificantes_csv_nrc",
    "invoice_channel_evidence",
)

OPTIONAL_OFFBOARDING_CATEGORIES = (
    "presenter_role",
    "rectification_docs",
    "advisor_signoff",
)

OFFBOARDING_CATEGORIES = REQUIRED_OFFBOARDING_CATEGORIES + OPTIONAL_OFFBOARDING_CATEGORIES

_CATEGORY_PATTERNS = {
    "exports": re.compile(r"(xolo[_ -]?export|data[_ -]?export|export[_ -]?bundle|standard[_ -]?export)", re.I),
    "books": re.compile(r"(libro|book|ledger|register|registro)", re.I),
    "forms": re.compile(r"((?:modelo|mod)[_ -]?\d+|tax[_ -]?(?:form|report)|return[_ -]?pdf)", re.I),
    "justificantes_csv_nrc": re.compile(r"(justificant|receipt|nrc|csv[_ -]?payment|payment[_ -]?reference)", re.I),
    "presenter_role": re.compile(r"(presenter|representante|apoderamiento|authorization|power[_ -]?of[_ -]?attorney)", re.I),
    "rectification_docs": re.compile(r"(rectif|complementaria|sustitutiva)", re.I),
    "invoice_channel_evidence": re.compile(r"(invoice[_ -]?channel|billing[_ -]?channel|invoice[_ -]?delivery|factura|evidence[_ -]?email)", re.I),
    "advisor_signoff": re.compile(r"(asesor|advisor|tax[_ -]?signoff|fiscal[_ -]?signoff|written[_ -]?opinion)", re.I),
}


def build_offboarding_manifest(
    paths: Iterable[Path],
    *,
    category_overrides: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    overrides = {
        str(Path(key).resolve()).casefold(): value
        for key, value in (category_overrides or {}).items()
    }
    rows: list[dict[str, str]] = []
    expanded: dict[str, Path] = {}
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            candidates = (candidate for candidate in path.rglob("*") if candidate.is_file())
        elif path.is_file():
            candidates = (path,)
        else:
            raise FileNotFoundError(f"Offboarding artifact is missing: {path}")
        for candidate in candidates:
            expanded[str(candidate.resolve()).casefold()] = candidate

    for path in sorted(expanded.values(), key=lambda item: str(item).lower()):
        resolved = path.resolve()
        category = overrides.get(str(resolved).casefold()) or classify_offboarding_artifact(path)
        rows.append(
            {
                "path": str(resolved),
                "name": resolved.name,
                "category": category,
                "size_bytes": str(resolved.stat().st_size),
                "sha256": sha256_file(resolved),
            }
        )
    return rows


def verify_offboarding_manifest(rows: Iterable[Mapping[str, str]]) -> dict[str, object]:
    normalized_rows = [dict(row) for row in rows]
    present_categories = {row.get("category", "") for row in normalized_rows}
    missing_categories = [category for category in REQUIRED_OFFBOARDING_CATEGORIES if category not in present_categories]
    invalid_hashes = sorted(
        row.get("path", "")
        for row in normalized_rows
        if not re.fullmatch(r"[0-9a-f]{64}", row.get("sha256", ""))
    )
    missing_paths: list[str] = []
    hash_mismatch_paths: list[str] = []
    size_mismatch_paths: list[str] = []
    for row in normalized_rows:
        raw_path = row.get("path", "")
        path = Path(raw_path) if raw_path else None
        if path is None or not path.is_file():
            missing_paths.append(raw_path)
            continue
        if path.stat().st_size != _parse_size(row.get("size_bytes", "")):
            size_mismatch_paths.append(raw_path)
        expected_hash = row.get("sha256", "")
        if re.fullmatch(r"[0-9a-f]{64}", expected_hash) and sha256_file(path) != expected_hash:
            hash_mismatch_paths.append(raw_path)
    category_counts = {
        category: sum(1 for row in normalized_rows if row.get("category") == category)
        for category in OFFBOARDING_CATEGORIES
    }
    blocked = bool(
        missing_categories
        or invalid_hashes
        or missing_paths
        or hash_mismatch_paths
        or size_mismatch_paths
    )
    return {
        "ok": not blocked,
        "blocked": blocked,
        "missing_categories": missing_categories,
        "optional_missing_categories": [
            category
            for category in OPTIONAL_OFFBOARDING_CATEGORIES
            if category not in present_categories
        ],
        "invalid_hash_paths": invalid_hashes,
        "missing_paths": sorted(missing_paths),
        "hash_mismatch_paths": sorted(hash_mismatch_paths),
        "size_mismatch_paths": sorted(size_mismatch_paths),
        "category_counts": category_counts,
    }


def classify_offboarding_artifact(path: Path) -> str:
    resolved = Path(path)
    candidates = [resolved.name, *(parent.name for parent in resolved.parents)]
    for candidate in candidates:
        for category, pattern in _CATEGORY_PATTERNS.items():
            if pattern.search(candidate):
                return category
    return "uncategorized"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _parse_size(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1
