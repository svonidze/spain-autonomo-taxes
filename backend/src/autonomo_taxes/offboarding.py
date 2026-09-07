from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Protocol

from pypdf import PdfReader

from .filing_evidence import parse_filing_metadata
from .tax_rules import recognize_tax_form_filename


REQUIRED_OFFBOARDING_CATEGORIES = (
    "exports",
    "books",
    "forms",
    "justificantes_csv_nrc",
)

OPTIONAL_OFFBOARDING_CATEGORIES = (
    "invoice_channel_evidence",
    "presenter_role",
    "rectification_docs",
    "advisor_signoff",
)

OFFBOARDING_CATEGORIES = REQUIRED_OFFBOARDING_CATEGORIES + OPTIONAL_OFFBOARDING_CATEGORIES

_IGNORED_SYSTEM_FILENAMES = {".ds_store", "desktop.ini", "thumbs.db"}
_NON_EVIDENCE_DIRECTORIES = {"audit_outputs"}

_CATEGORY_PATTERNS = {
    "exports": re.compile(r"(xolo[_ -]?export|data[_ -]?export|export[_ -]?bundle|standard[_ -]?export)", re.I),
    "books": re.compile(r"(libro|book|ledger|register|registro)", re.I),
    "forms": re.compile(
        r"((?<![a-z0-9])(?:modelo|mod|m)[_ -]?\d+|"
        r"tax[_ -]?(?:form|report)|return[_ -]?pdf)",
        re.I,
    ),
    "justificantes_csv_nrc": re.compile(
        r"(justificant|nrc|csv[_ -]?(?:aeat|tax|payment)|(?:aeat|tax)[_ -]?(?:payment|receipt|reference))",
        re.I,
    ),
    "presenter_role": re.compile(
        r"(presenter|representante|apoderamiento|authorization|"
        r"power[_ -]?of[_ -]?attorney)",
        re.I,
    ),
    "rectification_docs": re.compile(r"(rectif|complementaria|sustitutiva)", re.I),
    "invoice_channel_evidence": re.compile(
        r"(invoice[_ -]?channel|billing[_ -]?channel|invoice[_ -]?delivery|"
        r"(?:invoice|factura)[_ -]?(?:issuer|sif|verifactu)|evidence[_ -]?email)",
        re.I,
    ),
    "advisor_signoff": re.compile(r"(asesor|advisor|tax[_ -]?signoff|fiscal[_ -]?signoff|written[_ -]?opinion)", re.I),
}

_TAX_CONTEXT_TOKENS = {
    "aeat",
    "agencia",
    "tributaria",
    "tax",
    "modelo",
}
_TAX_EVIDENCE_TOKENS = {
    "adeudo",
    "cargo",
    "csv",
    "debit",
    "justificante",
    "nrc",
    "pago",
    "payment",
    "receipt",
    "reference",
}
_INVOICE_CHANNEL_TOKENS = {
    "billing",
    "channel",
    "delivery",
    "email",
    "issuer",
    "sif",
    "verifactu",
}
_NON_FILED_FORM_TOKENS = {
    "borrador",
    "draft",
    "preview",
    "provisional",
    "request",
    "solicitud",
}


class ObligationReader(Protocol):
    def list_obligations(
        self,
        *,
        period_key: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def list_filing_snapshots(self) -> list[dict[str, Any]]: ...

    def list_obligation_evidence(
        self,
        *,
        obligation_id: str | None = None,
    ) -> list[dict[str, Any]]: ...


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
            candidates = (
                candidate for candidate in path.rglob("*") if candidate.is_file()
            )
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
        category = overrides.get(
            str(resolved).casefold()
        ) or classify_offboarding_artifact(path)
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
    database: ObligationReader | None = None,
    category_overrides: Mapping[str, str] | None = None,
    excluded_paths: Iterable[Path] = (),
) -> dict[str, object]:
    source_paths = [Path(path) for path in paths]
    rows = build_offboarding_manifest(
        source_paths,
        category_overrides=category_overrides,
        excluded_paths=excluded_paths,
    )
    document: dict[str, object] = {
        "schema_version": 3 if database is not None else 2,
        "report_type": "xolo_offboarding_manifest",
        "generated_on": generated_on.isoformat(),
        "source_roots": sorted(
            {str(path.resolve()) for path in source_paths},
            key=str.casefold,
        ),
        "rows": rows,
    }
    if database is not None:
        document["expected_filings"] = expected_filings_from_database(database)
        document["final_export_expected_on"] = generated_on.isoformat()
        document["accepted_filing_hashes"] = accepted_filing_hashes_from_database(
            database
        )
        document["unresolved_required_obligations"] = (
            unresolved_required_obligations_from_database(
                database,
                generated_on=generated_on,
                manifest_rows=rows,
            )
        )
    return document


def expected_filings_from_database(
    database: ObligationReader,
) -> list[dict[str, str]]:
    expected = {
        (
            str(row["obligation_code"]),
            str(row["period_key"]),
        )
        for row in database.list_obligations()
        if str(row.get("filing_status", "")) == "filed"
    }
    return [
        {"form_code": form_code, "period_key": period_key}
        for form_code, period_key in sorted(
            expected,
            key=lambda item: (item[1], item[0]),
        )
    ]


def accepted_filing_hashes_from_database(
    database: ObligationReader,
) -> dict[str, list[str]]:
    accepted_statuses = {
        "baseline",
        "filed",
        "final",
        "submitted",
    }
    grouped: dict[str, set[str]] = {}
    for row in database.list_filing_snapshots():
        form_code = str(row.get("form_code") or "").strip()
        period_key = str(row.get("period_key") or "").strip()
        source_hash = str(row.get("source_hash") or "").strip().casefold()
        if (
            str(row.get("status") or "") not in accepted_statuses
            or not form_code
            or not period_key
            or re.fullmatch(r"[0-9a-f]{64}", source_hash) is None
        ):
            continue
        grouped.setdefault(_filing_identity_key(form_code, period_key), set()).add(
            source_hash
        )
    return {
        key: sorted(values)
        for key, values in sorted(grouped.items(), key=lambda item: item[0])
    }


def unresolved_required_obligations_from_database(
    database: ObligationReader,
    *,
    generated_on: date,
    manifest_rows: Iterable[Mapping[str, str]] = (),
) -> list[dict[str, object]]:
    required_evidence_kinds = {
        "aeat_account_check",
        "independent_calculation",
    }
    archived_hashes = {
        str(row.get("sha256") or "").casefold()
        for row in manifest_rows
        if re.fullmatch(
            r"[0-9a-f]{64}",
            str(row.get("sha256") or "").casefold(),
        )
        is not None
    }
    evidence_by_obligation: dict[str, set[str]] = {}
    for evidence in database.list_obligation_evidence():
        evidence_path = Path(str(evidence.get("source_reference") or ""))
        expected_hash = str(evidence.get("source_hash") or "").casefold()
        if re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None:
            continue
        direct_source_matches = (
            evidence_path.is_file()
            and sha256_file(evidence_path) == expected_hash
        )
        if not direct_source_matches and expected_hash not in archived_hashes:
            continue
        evidence_by_obligation.setdefault(
            str(evidence["obligation_id"]),
            set(),
        ).add(str(evidence["evidence_kind"]))
    unresolved: list[dict[str, object]] = []
    for row in database.list_obligations():
        period_key = str(row.get("period_key") or "")
        if (
            str(row.get("obligation_code") or "") != "347"
            or len(period_key) != 4
            or not period_key.isdigit()
            or int(period_key) >= generated_on.year
        ):
            continue
        determination = str(row.get("determination") or "unknown")
        filing_status = str(row.get("filing_status") or "unknown")
        is_filed = filing_status == "filed"
        is_confirmed_not_due = (
            determination == "not_due" and filing_status == "waived"
        )
        available_evidence = evidence_by_obligation.get(
            str(row["obligation_id"]),
            set(),
        )
        missing_evidence_kinds = sorted(
            required_evidence_kinds - available_evidence
        )
        if is_filed or (is_confirmed_not_due and not missing_evidence_kinds):
            continue
        unresolved.append(
            {
                "form_code": "347",
                "period_key": period_key,
                "determination": determination,
                "filing_status": filing_status,
                "missing_evidence_kinds": missing_evidence_kinds,
            }
        )
    return sorted(
        unresolved,
        key=lambda row: (row["period_key"], row["form_code"]),
    )


def verify_offboarding_manifest(
    manifest: Iterable[Mapping[str, str]] | Mapping[str, object],
    *,
    required_generated_on: date | None = None,
) -> dict[str, object]:
    source_rows, metadata = _unpack_manifest(manifest)
    schema_version = int(metadata["schema_version"])
    accepted_filing_hashes = metadata["accepted_filing_hashes"]
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
    final_export_expected_on = metadata["final_export_expected_on"]
    final_export_date_consistent = (
        final_export_expected_on is None
        or final_export_expected_on == metadata["generated_on"]
    )
    final_export_paths = sorted(
        row.get("path", "")
        for row in normalized_rows
        if row.get("category", "") == "exports"
        and _row_is_present_and_nonzero(row)
        and final_export_expected_on is not None
        and _path_is_dated_raw_export(
            Path(row.get("path", "")),
            final_export_expected_on,
        )
    )
    raw_present_categories = {row.get("category", "") for row in normalized_rows}
    supported_categories_by_path = {
        row.get("path", ""): _supported_categories(
            row,
            accepted_filing_hashes=accepted_filing_hashes,
        )
        for row in normalized_rows
    }
    strict_present_categories = {
        category
        for row in normalized_rows
        if _row_is_present_and_nonzero(row)
        for category in supported_categories_by_path[row.get("path", "")]
    }
    present_categories = (
        strict_present_categories
        if schema_version == 3
        else raw_present_categories
    )
    missing_categories = [
        category
        for category in REQUIRED_OFFBOARDING_CATEGORIES
        if category not in present_categories
    ]
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
    zero_byte_required_paths = sorted(
        row.get("path", "")
        for row in normalized_rows
        if row.get("category", "") in REQUIRED_OFFBOARDING_CATEGORIES
        and _parse_size(row.get("size_bytes", "")) == 0
    )
    rejected_sensitive_paths = sorted(
        row.get("path", "")
        for row in normalized_rows
        if row.get("category", "")
        in {"forms", "justificantes_csv_nrc", "invoice_channel_evidence"}
        and row.get("category", "")
        not in supported_categories_by_path[row.get("path", "")]
    )
    mutable_export_tree_paths = sorted(
        row.get("path", "")
        for row in normalized_rows
        if _is_mutable_xolo_export_path(Path(row.get("path", "")))
    )
    recognized_form_artifacts = _recognized_form_artifacts(
        normalized_rows,
        accepted_filing_hashes=accepted_filing_hashes,
    )
    artifacts_by_filing: dict[tuple[str, str], list[str]] = {}
    for artifact in recognized_form_artifacts:
        identity = (artifact["form_code"], artifact["period_key"])
        artifacts_by_filing.setdefault(identity, []).append(artifact["path"])
    expected_filings = list(metadata["expected_filings"])
    matched_expected_filings: list[dict[str, object]] = []
    missing_expected_filings: list[dict[str, str]] = []
    for expected in expected_filings:
        identity = (expected["form_code"], expected["period_key"])
        artifact_paths = sorted(artifacts_by_filing.get(identity, []), key=str.casefold)
        if artifact_paths:
            matched_expected_filings.append(
                {**expected, "artifact_paths": artifact_paths}
            )
        else:
            missing_expected_filings.append(dict(expected))
    category_counts = {
        category: sum(1 for row in normalized_rows if row.get("category") == category)
        for category in OFFBOARDING_CATEGORIES
    }
    qualified_category_counts = {
        category: sum(
            1
            for row in normalized_rows
            if _row_is_present_and_nonzero(row)
            and category in supported_categories_by_path[row.get("path", "")]
        )
        for category in OFFBOARDING_CATEGORIES
    }
    integrity_blocked = bool(
        missing_categories
        or invalid_hashes
        or missing_paths
        or hash_mismatch_paths
        or size_mismatch_paths
        or (schema_version == 3 and zero_byte_required_paths)
        or (schema_version == 3 and rejected_sensitive_paths)
        or (schema_version == 3 and mutable_export_tree_paths)
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
    strict_readiness_reasons: list[str] = []
    if schema_version == 2:
        strict_readiness_reasons.append("schema_v2_historical_only")
    elif schema_version == 1:
        strict_readiness_reasons.append("unversioned_manifest_historical_only")
    if integrity_blocked:
        strict_readiness_reasons.append("integrity_failed")
    if schema_version == 3 and mutable_export_tree_paths:
        strict_readiness_reasons.append("mutable_xolo_export_tree_included")
    if schema_version == 3 and missing_expected_filings:
        strict_readiness_reasons.append("expected_filings_missing")
    if schema_version == 3 and not final_export_paths:
        strict_readiness_reasons.append("final_export_for_manifest_date_missing")
    if schema_version == 3 and not final_export_date_consistent:
        strict_readiness_reasons.append("final_export_expected_date_mismatch")
    unresolved_required_obligations = list(
        metadata["unresolved_required_obligations"]
    )
    if schema_version == 3 and unresolved_required_obligations:
        strict_readiness_reasons.append("required_obligation_status_unresolved")
    if manifest_fresh is False:
        strict_readiness_reasons.append("manifest_date_mismatch")
    strict_readiness = schema_version == 3 and not strict_readiness_reasons
    if schema_version == 1:
        blocked = integrity_blocked or manifest_fresh is False
    else:
        blocked = not strict_readiness
    return {
        "ok": not blocked,
        "blocked": blocked,
        "integrity_ok": not integrity_blocked,
        "strict_readiness": strict_readiness,
        "strict_readiness_reasons": strict_readiness_reasons,
        "manifest_schema_version": schema_version,
        "manifest_generated_on": metadata["generated_on"],
        "manifest_source_roots": metadata["source_roots"],
        "final_export_expected_on": final_export_expected_on,
        "final_export_date_consistent": final_export_date_consistent,
        "final_export_paths": final_export_paths,
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
        "zero_byte_required_paths": zero_byte_required_paths,
        "rejected_sensitive_paths": rejected_sensitive_paths,
        "mutable_export_tree_paths": mutable_export_tree_paths,
        "ignored_system_paths": ignored_system_paths,
        "category_counts": category_counts,
        "qualified_category_counts": qualified_category_counts,
        "expected_filings": expected_filings,
        "matched_expected_filings": matched_expected_filings,
        "missing_expected_filings": missing_expected_filings,
        "recognized_form_artifacts": recognized_form_artifacts,
        "accepted_filing_hashes": {
            key: sorted(values)
            for key, values in sorted(accepted_filing_hashes.items())
        },
        "unresolved_required_obligations": unresolved_required_obligations,
        "uncategorized_paths": sorted(
            row.get("path", "")
            for row in normalized_rows
            if row.get("category", "") == "uncategorized"
        ),
    }


def _unpack_manifest(
    manifest: Iterable[Mapping[str, str]] | Mapping[str, object],
) -> tuple[list[dict[str, str]], dict[str, object]]:
    if isinstance(manifest, Mapping):
        schema_version = manifest.get("schema_version")
        if schema_version not in {2, 3}:
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
        expected_filings = (
            _parse_expected_filings(manifest.get("expected_filings"))
            if schema_version == 3
            else []
        )
        final_export_expected_on = (
            _parse_manifest_date(
                manifest.get("final_export_expected_on"),
                field_name="final_export_expected_on",
            )
            if schema_version == 3
            else None
        )
        accepted_filing_hashes = (
            _parse_accepted_filing_hashes(manifest.get("accepted_filing_hashes"))
            if schema_version == 3
            else {}
        )
        unresolved_required_obligations = (
            _parse_unresolved_required_obligations(
                manifest.get("unresolved_required_obligations")
            )
            if schema_version == 3
            else []
        )
        source_roots = list(raw_roots)
    else:
        raw_rows = list(manifest)
        generated_on = None
        schema_version = 1
        source_roots = []
        expected_filings = []
        final_export_expected_on = None
        accepted_filing_hashes = {}
        unresolved_required_obligations = []

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
        "expected_filings": expected_filings,
        "final_export_expected_on": final_export_expected_on,
        "accepted_filing_hashes": accepted_filing_hashes,
        "unresolved_required_obligations": unresolved_required_obligations,
    }


def classify_offboarding_artifact(path: Path) -> str:
    resolved = Path(path)
    if any(parent.name.casefold() in _NON_EVIDENCE_DIRECTORIES for parent in resolved.parents):
        return "uncategorized"
    if _filename_tokens(resolved) & _NON_FILED_FORM_TOKENS:
        return "uncategorized"
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


def _is_mutable_xolo_export_path(path: Path) -> bool:
    return any(part.casefold() == "xolo export" for part in path.parts)


def _parse_size(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _parse_expected_filings(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("Offboarding manifest expected_filings must be a JSON array")
    parsed: set[tuple[str, str]] = set()
    for row in value:
        if not isinstance(row, Mapping):
            raise ValueError(
                "Offboarding manifest expected_filings must contain JSON objects"
            )
        form_code = str(row.get("form_code", "")).strip()
        period_key = str(row.get("period_key", "")).strip()
        if not form_code or not period_key:
            raise ValueError(
                "Offboarding manifest expected_filings require form_code and period_key"
            )
        parsed.add((form_code, period_key))
    return [
        {"form_code": form_code, "period_key": period_key}
        for form_code, period_key in sorted(
            parsed,
            key=lambda item: (item[1], item[0]),
        )
    ]


def _parse_manifest_date(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Offboarding manifest {field_name} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(
            f"Offboarding manifest {field_name} must be YYYY-MM-DD"
        ) from exc


def _parse_accepted_filing_hashes(value: object) -> dict[str, set[str]]:
    if not isinstance(value, Mapping):
        raise ValueError(
            "Offboarding manifest accepted_filing_hashes must be a JSON object"
        )
    parsed: dict[str, set[str]] = {}
    for raw_key, raw_hashes in value.items():
        key = str(raw_key).strip()
        if _parse_filing_identity_key(key) is None:
            raise ValueError(
                "Offboarding manifest accepted_filing_hashes contains an invalid "
                f"filing key: {key!r}"
            )
        if not isinstance(raw_hashes, list):
            raise ValueError(
                "Offboarding manifest accepted_filing_hashes values must be arrays"
            )
        hashes = {str(item).strip().casefold() for item in raw_hashes}
        if any(re.fullmatch(r"[0-9a-f]{64}", item) is None for item in hashes):
            raise ValueError(
                "Offboarding manifest accepted_filing_hashes contains an invalid "
                f"SHA-256 for {key}"
            )
        parsed[key] = hashes
    return parsed


def _parse_unresolved_required_obligations(
    value: object,
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError(
            "Offboarding manifest unresolved_required_obligations must be a JSON array"
        )
    parsed: dict[tuple[str, str, str, str], tuple[str, ...]] = {}
    for row in value:
        if not isinstance(row, Mapping):
            raise ValueError(
                "Offboarding manifest unresolved_required_obligations must contain "
                "JSON objects"
            )
        values = tuple(
            str(row.get(key, "")).strip()
            for key in (
                "form_code",
                "period_key",
                "determination",
                "filing_status",
            )
        )
        if any(not item for item in values):
            raise ValueError(
                "Offboarding manifest unresolved_required_obligations rows require "
                "form_code, period_key, determination, and filing_status"
            )
        raw_missing = row.get("missing_evidence_kinds", [])
        if not isinstance(raw_missing, list) or not all(
            isinstance(item, str) for item in raw_missing
        ):
            raise ValueError(
                "Offboarding manifest unresolved obligation evidence kinds "
                "must be a string array"
            )
        missing = tuple(sorted(set(raw_missing)))
        unsupported = set(missing) - {
            "aeat_account_check",
            "independent_calculation",
        }
        if unsupported:
            raise ValueError(
                "Offboarding manifest contains unsupported obligation evidence kinds: "
                + ", ".join(sorted(unsupported))
            )
        parsed[values] = missing
    rows: list[dict[str, object]] = []
    for identity in sorted(parsed, key=lambda item: (item[1], item[0])):
        form_code, period_key, determination, filing_status = identity
        rows.append(
            {
                "form_code": form_code,
                "period_key": period_key,
                "determination": determination,
                "filing_status": filing_status,
                "missing_evidence_kinds": list(parsed[identity]),
            }
        )
    return rows


def _supported_categories(
    row: Mapping[str, str],
    *,
    accepted_filing_hashes: Mapping[str, set[str]],
) -> set[str]:
    category = row.get("category", "")
    path = Path(row.get("path", ""))
    if category == "forms":
        if (
            _qualify_form_artifact(
                row,
                accepted_filing_hashes=accepted_filing_hashes,
            )
            is None
        ):
            return set()
        supported = {"forms"}
        if _is_tax_filing_or_payment_evidence(path):
            supported.add("justificantes_csv_nrc")
        return supported
    if category == "justificantes_csv_nrc":
        return (
            {"justificantes_csv_nrc"}
            if _is_tax_filing_or_payment_evidence(path)
            else set()
        )
    if category == "invoice_channel_evidence":
        return (
            {"invoice_channel_evidence"}
            if _is_invoice_channel_evidence(path)
            else set()
        )
    return {category} if category in OFFBOARDING_CATEGORIES else set()


def _recognized_form_artifacts(
    rows: Iterable[Mapping[str, str]],
    *,
    accepted_filing_hashes: Mapping[str, set[str]],
) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    for row in rows:
        path = Path(row.get("path", ""))
        if not _row_is_present_and_nonzero(row):
            continue
        if (
            row.get("category") != "forms"
            and recognize_tax_form_filename(path.name) is None
        ):
            continue
        qualified = _qualify_form_artifact(
            row,
            accepted_filing_hashes=accepted_filing_hashes,
        )
        if qualified is None:
            continue
        form_code, period_key, evidence_method = qualified
        artifacts.append(
            {
                "form_code": form_code,
                "period_key": period_key,
                "path": str(path),
                "evidence_method": evidence_method,
            }
        )
    return sorted(
        artifacts,
        key=lambda item: (
            item["period_key"],
            item["form_code"],
            item["path"].casefold(),
        ),
    )


def _recognize_form_identity(path: Path) -> tuple[str, str] | None:
    if path.suffix.casefold() != ".pdf" or not _is_readable_pdf(path):
        return None
    if _filename_tokens(path) & _NON_FILED_FORM_TOKENS:
        return None
    metadata = _read_pdf_filing_metadata(path)
    recognized = recognize_tax_form_filename(path.name)
    filename_form = recognized.code if recognized is not None else None
    filename_period = recognized.period if recognized is not None else None
    metadata_form = metadata.get("form_code")
    metadata_period = metadata.get("period_key")
    if filename_form and metadata_form and filename_form != metadata_form:
        return None
    if filename_period and metadata_period and filename_period != metadata_period:
        return None
    form_code = metadata_form or filename_form
    period_key = metadata_period or filename_period
    if form_code and period_key:
        return form_code, period_key
    return None


def _qualify_form_artifact(
    row: Mapping[str, str],
    *,
    accepted_filing_hashes: Mapping[str, set[str]],
) -> tuple[str, str, str] | None:
    path = Path(row.get("path", ""))
    identity = _recognize_form_identity(path)
    if identity is None:
        return None
    form_code, period_key = identity
    row_hash = str(row.get("sha256") or "").casefold()
    accepted_hashes = accepted_filing_hashes.get(
        _filing_identity_key(form_code, period_key),
        set(),
    )
    if row_hash in accepted_hashes:
        return form_code, period_key, "filing_snapshot_hash"

    metadata = _read_pdf_filing_metadata(path)
    if metadata.get("form_code") != form_code:
        return None
    if metadata.get("period_key") not in {None, period_key}:
        return None
    if any(
        metadata.get(key)
        for key in (
            "submission_reference",
            "justificante_number",
            "verification_code",
        )
    ):
        return form_code, period_key, "aeat_submission_metadata"
    return None


def _is_tax_filing_or_payment_evidence(path: Path) -> bool:
    if path.suffix.casefold() == ".pdf" and not _is_readable_pdf(path):
        return False
    tokens = _filename_tokens(path)
    if "nrc" in tokens:
        return True
    has_tax_context = bool(tokens & _TAX_CONTEXT_TOKENS) or any(
        token.startswith("mod") and token[3:].isdigit()
        for token in tokens
    )
    if has_tax_context and bool(tokens & _TAX_EVIDENCE_TOKENS):
        return True
    recognized = _recognize_form_identity(path)
    if recognized is None:
        return False
    metadata = _read_pdf_filing_metadata(path)
    return any(
        metadata.get(key)
        for key in (
            "submission_reference",
            "justificante_number",
            "verification_code",
        )
    )


def _is_invoice_channel_evidence(path: Path) -> bool:
    tokens = _filename_tokens(path)
    has_invoice_context = bool({"invoice", "factura", "billing"} & tokens)
    return has_invoice_context and bool(tokens & _INVOICE_CHANNEL_TOKENS)


def _filename_tokens(path: Path) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", path.stem.casefold())
        if token
    }


def _path_is_dated_raw_export(path: Path, expected_date: str) -> bool:
    if path.suffix.casefold() != ".zip":
        return False
    parts = [part.casefold() for part in path.parts]
    expected = expected_date.casefold()
    return any(
        component == "raw_exports"
        and index + 1 < len(parts)
        and parts[index + 1] == expected
        for index, component in enumerate(parts)
    )


def _filing_identity_key(form_code: str, period_key: str) -> str:
    return f"{period_key}:{form_code}"


def _parse_filing_identity_key(value: str) -> tuple[str, str] | None:
    if ":" not in value:
        return None
    period_key, form_code = value.rsplit(":", 1)
    if not period_key or not form_code or not form_code.isdigit():
        return None
    if not (
        (len(period_key) == 4 and period_key.isdigit())
        or re.fullmatch(r"20\d{2}-Q[1-4]", period_key)
    ):
        return None
    return form_code, period_key


def _read_pdf_filing_metadata(path: Path) -> dict[str, str]:
    if not _is_readable_pdf(path):
        return {}
    try:
        text = "\n".join(
            page.extract_text() or ""
            for page in PdfReader(str(path)).pages
        )
    except Exception:
        return {}
    return parse_filing_metadata(text)


def _is_readable_pdf(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                return False
        return len(PdfReader(str(path)).pages) > 0
    except Exception:
        return False


def _row_is_present_and_nonzero(row: Mapping[str, str]) -> bool:
    raw_path = row.get("path", "")
    path = Path(raw_path) if raw_path else None
    return bool(
        path is not None
        and path.is_file()
        and path.stat().st_size > 0
        and _parse_size(row.get("size_bytes", "")) > 0
    )
