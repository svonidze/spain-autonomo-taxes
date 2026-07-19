from __future__ import annotations

from datetime import date
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

_IGNORED_SYSTEM_FILENAMES = {".ds_store", "desktop.ini", "thumbs.db"}

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
    excluded_paths: Iterable[Path] = (),
) -> list[dict[str, str]]:
    overrides = {
        str(Path(key).resolve()).casefold(): value
        for key, value in (category_overrides or {}).items()
    }
    excluded = {str(Path(path).resolve()).casefold() for path in excluded_paths}
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
            if str(candidate.resolve()).casefold() in excluded:
                continue
            if _is_ignored_system_file(candidate):
                continue
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


def build_offboarding_manifest_document(
    paths: Iterable[Path],
    *,
    generated_on: date,
    category_overrides: Mapping[str, str] | None = None,
    excluded_paths: Iterable[Path] = (),
) -> dict[str, object]:
    source_paths = [Path(path) for path in paths]
    rows = build_offboarding_manifest(
        source_paths,
        category_overrides=category_overrides,
        excluded_paths=excluded_paths,
    )
    return {
        "schema_version": 2,
        "report_type": "xolo_offboarding_manifest",
        "generated_on": generated_on.isoformat(),
        "source_roots": sorted(
            {str(path.resolve()) for path in source_paths},
            key=str.casefold,
        ),
        "rows": rows,
    }


def verify_offboarding_manifest(
    manifest: Iterable[Mapping[str, str]] | Mapping[str, object],
    *,
    required_generated_on: date | None = None,
) -> dict[str, object]:
    source_rows, metadata = _unpack_manifest(manifest)
    ignored_system_paths = sorted(
        row.get("path", "")
        for row in source_rows
        if _is_ignored_system_file(Path(row.get("path", "")))
    )
    normalized_rows = [
        row
        for row in source_rows
        if not _is_ignored_system_file(Path(row.get("path", "")))
    ]
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
    integrity_blocked = bool(
        missing_categories
        or invalid_hashes
        or missing_paths
        or hash_mismatch_paths
        or size_mismatch_paths
    )
    manifest_fresh: bool | None = None
    freshness_reason = "not_required"
    if required_generated_on is not None:
        generated_on = metadata["generated_on"]
        if generated_on is None:
            manifest_fresh = False
            freshness_reason = "generated_on_missing"
        elif generated_on != required_generated_on.isoformat():
            manifest_fresh = False
            freshness_reason = "generated_on_mismatch"
        else:
            manifest_fresh = True
            freshness_reason = "generated_on_matches"
    blocked = integrity_blocked or manifest_fresh is False
    return {
        "ok": not blocked,
        "blocked": blocked,
        "integrity_ok": not integrity_blocked,
        "manifest_schema_version": metadata["schema_version"],
        "manifest_generated_on": metadata["generated_on"],
        "manifest_source_roots": metadata["source_roots"],
        "required_generated_on": (
            required_generated_on.isoformat()
            if required_generated_on is not None
            else None
        ),
        "manifest_fresh": manifest_fresh,
        "freshness_reason": freshness_reason,
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
        "ignored_system_paths": ignored_system_paths,
        "category_counts": category_counts,
    }


def _unpack_manifest(
    manifest: Iterable[Mapping[str, str]] | Mapping[str, object],
) -> tuple[list[dict[str, str]], dict[str, object]]:
    if isinstance(manifest, Mapping):
        if manifest.get("schema_version") != 2:
            raise ValueError("Unsupported offboarding manifest schema_version")
        if manifest.get("report_type") != "xolo_offboarding_manifest":
            raise ValueError("Unsupported offboarding manifest report_type")
        generated_on = manifest.get("generated_on")
        if not isinstance(generated_on, str):
            raise ValueError("Offboarding manifest generated_on must be YYYY-MM-DD")
        try:
            date.fromisoformat(generated_on)
        except ValueError as exc:
            raise ValueError(
                "Offboarding manifest generated_on must be YYYY-MM-DD"
            ) from exc
        raw_rows = manifest.get("rows")
        if not isinstance(raw_rows, list):
            raise ValueError("Offboarding manifest rows must be a JSON array")
        raw_roots = manifest.get("source_roots", [])
        if not isinstance(raw_roots, list) or not all(
            isinstance(value, str) for value in raw_roots
        ):
            raise ValueError("Offboarding manifest source_roots must be a string array")
        schema_version = 2
        source_roots = list(raw_roots)
    else:
        raw_rows = list(manifest)
        generated_on = None
        schema_version = 1
        source_roots = []

    if not all(isinstance(row, Mapping) for row in raw_rows):
        raise ValueError("Offboarding manifest rows must contain JSON objects")
    rows = [
        {str(key): str(value) for key, value in row.items()}
        for row in raw_rows
    ]
    return rows, {
        "schema_version": schema_version,
        "generated_on": generated_on,
        "source_roots": source_roots,
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


def _is_ignored_system_file(path: Path) -> bool:
    name = path.name.casefold()
    return name in _IGNORED_SYSTEM_FILENAMES or name.startswith("~$")


def _parse_size(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1
