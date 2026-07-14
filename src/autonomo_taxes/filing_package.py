from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from .books import BookExportResult, write_accounting_books
from .ledger_db import LedgerDB


MANIFEST_FILENAME = "filing-package-manifest.json"
FINAL_SNAPSHOT_STATUSES = {"filed", "submitted", "final"}


def write_filing_package(database: LedgerDB, output_dir: str | Path, *, period_key: str) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    export = write_accounting_books(database, root, period_key=period_key)
    manifest = _build_manifest(database, export)
    manifest_path = root / MANIFEST_FILENAME
    manifest_path.write_text(_serialize_json(manifest), encoding="utf-8")
    return manifest_path


def verify_filing_package(database: LedgerDB, manifest_path: str | Path) -> dict[str, Any]:
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    missing_artifacts: list[str] = []
    mismatched_artifacts: list[str] = []
    for artifact in manifest.get("artifacts", []):
        artifact_path = manifest_file.parent / artifact["filename"]
        if not artifact_path.is_file():
            missing_artifacts.append(artifact["filename"])
            continue
        actual_hash = _sha256_file(artifact_path)
        if actual_hash != artifact["sha256"]:
            mismatched_artifacts.append(artifact["filename"])

    with tempfile.TemporaryDirectory() as tmp:
        regenerated_path = write_filing_package(database, Path(tmp), period_key=manifest["period"]["period_key"])
        regenerated = json.loads(regenerated_path.read_text(encoding="utf-8"))

    manifest_matches = regenerated == manifest
    ok = manifest_matches and not missing_artifacts and not mismatched_artifacts
    return {
        "ok": ok,
        "submission_ready": bool(regenerated.get("submission_ready")),
        "readiness_blockers": regenerated.get("readiness_blockers", []),
        "manifest_matches": manifest_matches,
        "missing_artifacts": missing_artifacts,
        "mismatched_artifacts": mismatched_artifacts,
        "expected_manifest": regenerated,
    }


def _build_manifest(database: LedgerDB, export: BookExportResult) -> dict[str, Any]:
    connection = database.connection
    period = connection.execute(
        "SELECT period_key, starts_on, ends_on, status, source_hash, row_version FROM periods WHERE period_key = ?",
        (export.period_key,),
    ).fetchone()
    if period is None:
        raise ValueError(f"Unknown period: {export.period_key}")

    artifacts = []
    for name in ("assets", "expense", "income", "payments"):
        path = export.files[name]
        artifacts.append(
            {
                "book": name,
                "filename": path.name,
                "row_count": export.row_counts[name],
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )

    rule_versions = [
        {
            "rule_version_id": row["rule_version_id"],
            "rule_name": row["rule_name"],
            "version": row["version"],
            "activated_at": row["activated_at"] or "",
            "source_hash": row["source_hash"],
        }
        for row in connection.execute(
            """
            SELECT DISTINCT rv.rule_version_id, rv.rule_name, rv.version, rv.activated_at, rv.source_hash
            FROM rule_versions rv
            WHERE rv.rule_version_id IN (
                SELECT tt.rule_version_id
                FROM tax_treatments tt
                JOIN transactions t ON t.transaction_id = tt.transaction_id
                JOIN periods p ON p.period_id = t.period_id
                WHERE p.period_key = ? AND tt.rule_version_id IS NOT NULL
                UNION
                SELECT fx.rule_version_id
                FROM fx_rates fx
                JOIN transactions t ON t.fx_rate_id = fx.fx_rate_id
                JOIN periods p ON p.period_id = t.period_id
                WHERE p.period_key = ? AND fx.rule_version_id IS NOT NULL
            )
            ORDER BY rv.rule_name, rv.version, rv.rule_version_id
            """,
            (export.period_key, export.period_key),
        ).fetchall()
    ]

    snapshot_refs = [
        {
            "filing_snapshot_id": row["filing_snapshot_id"],
            "snapshot_hash": row["snapshot_hash"],
            "filed_on": row["filed_on"],
            "status": row["status"],
            "manifest_path": row["manifest_path"] or "",
            "source_hash": row["source_hash"],
        }
        for row in connection.execute(
            """
            SELECT
                fs.filing_snapshot_id,
                fs.snapshot_hash,
                fs.filed_on,
                fs.status,
                fs.manifest_path,
                fs.source_hash
            FROM filing_snapshots fs
            JOIN periods p ON p.period_id = fs.period_id
            WHERE p.period_key = ?
            ORDER BY filed_on, filing_snapshot_id
            """,
            (export.period_key,),
        ).fetchall()
    ]
    readiness_reasons: list[str] = []
    if period["status"] not in {"closed", "amended"}:
        readiness_reasons.append("period_not_closed")
    if not any(row["status"] in FINAL_SNAPSHOT_STATUSES for row in snapshot_refs):
        readiness_reasons.append("final_filing_snapshot_missing")

    return {
        "manifest_version": 2,
        "package_type": "ledgerdb_export_only",
        "package_stage": "submission_ready" if not readiness_reasons else "draft_review",
        "submission_ready": not readiness_reasons,
        "readiness_blockers": readiness_reasons,
        "period": {
            "period_key": period["period_key"],
            "starts_on": period["starts_on"],
            "ends_on": period["ends_on"],
            "status": period["status"],
            "row_version": period["row_version"],
            "source_hash": period["source_hash"],
        },
        "artifacts": artifacts,
        "rule_versions": rule_versions,
        "snapshot_refs": snapshot_refs,
    }


def _serialize_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
