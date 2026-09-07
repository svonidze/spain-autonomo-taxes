from __future__ import annotations
from autonomo_test_support.paths import REPO_ROOT

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest
import yaml


SCRIPT = REPO_ROOT / "scripts" / "migrate_private_root.py"
SPEC = importlib.util.spec_from_file_location("migrate_private_root", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def test_migration_copies_verifies_and_rewrites_only_active_paths(tmp_path: Path) -> None:
    source = tmp_path / "project"
    target = tmp_path / "private"
    local = source / ".local"
    evidence = source / "evidence"
    local.mkdir(parents=True)
    evidence.mkdir(parents=True)
    source_evidence_file = evidence / "synthetic-receipt.txt"
    source_evidence_file.write_text("synthetic evidence", encoding="utf-8")
    source_drive_file = source / "gdrive-source.pdf"
    source_drive_file.write_text("drive evidence", encoding="utf-8")
    target_drive_root = target / "mnt" / "gdrive-espana"
    target_drive_file = target_drive_root / "2026" / "drive-source.pdf"
    target_drive_file.parent.mkdir(parents=True, exist_ok=True)
    target_drive_file.write_text("drive evidence", encoding="utf-8")
    (local / "config.yaml").write_text("ledger_db: old.sqlite\n", encoding="utf-8")
    map_file = tmp_path / "legacy-path-map.json"
    legacy_evidence_path = r"C:\projects\spain-autonomo-taxes\evidence\synthetic-receipt.txt"
    legacy_drive_path = r"G:\My Drive\Family\Испания\2026\drive-source.pdf"
    map_file.write_text(
        json.dumps(
            {
                r"C:\projects\spain-autonomo-taxes\evidence": str(target / "evidence"),
                r"G:\My Drive\Family\Испания": str(target_drive_root),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    database = local / "autonomo.sqlite"
    _create_database(
        database,
        source_evidence=legacy_evidence_path,
        drive_source=legacy_drive_path,
        evidence_hash=_sha256(source_evidence_file),
        drive_hash=_sha256(source_drive_file),
    )

    result = migration.migrate(source, target, legacy_path_map_file=map_file)

    assert result["database_paths_rewritten"] == 3
    copied_evidence = target / "evidence" / source_evidence_file.name
    assert _sha256(copied_evidence) == _sha256(source_evidence_file)
    with sqlite3.connect(target / "autonomo.sqlite") as connection:
        document_path = Path(
            connection.execute("SELECT source_path FROM documents").fetchone()[0]
        )
        document_source_path = Path(
            connection.execute("SELECT source_file FROM document_sources").fetchone()[0]
        )
        import_batch_path = Path(
            connection.execute("SELECT source_name FROM import_batches").fetchone()[0]
        )
        manifest_path = connection.execute(
            "SELECT manifest_path FROM filing_snapshots"
        ).fetchone()[0]
        payload_json = connection.execute(
            "SELECT payload_json FROM filing_snapshots"
        ).fetchone()[0]
        assert document_path == copied_evidence.resolve()
        assert document_source_path == target_drive_file.resolve()
        assert import_batch_path == target_drive_file.resolve()
        assert manifest_path == legacy_drive_path
        assert json.loads(payload_json)["document_path"] == legacy_drive_path
    config = yaml.safe_load((target / "config.yaml").read_text(encoding="utf-8"))
    assert Path(config["ledger_db"]) == (target / "autonomo.sqlite").resolve()
    assert Path(config["legacy_path_map_file"]) == (target / map_file.name).resolve()


def test_migration_aborts_on_unmapped_active_path(tmp_path: Path) -> None:
    source = tmp_path / "project"
    target = tmp_path / "private"
    local = source / ".local"
    evidence = source / "evidence"
    local.mkdir(parents=True)
    evidence.mkdir(parents=True)
    evidence_file = evidence / "synthetic-receipt.txt"
    evidence_file.write_text("synthetic evidence", encoding="utf-8")
    (local / "config.yaml").write_text("ledger_db: old.sqlite\n", encoding="utf-8")
    database = local / "autonomo.sqlite"
    _create_database(
        database,
        source_evidence=r"H:\Unknown\receipt.pdf",
        drive_source=r"G:\My Drive\Family\Испания\2026\drive-source.pdf",
        evidence_hash=_sha256(evidence_file),
        drive_hash=_sha256(evidence_file),
    )
    map_file = tmp_path / "legacy-path-map.json"
    map_file.write_text(
        json.dumps(
            {r"G:\My Drive\Family\Испания": str(target / "mnt" / "gdrive-espana")},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Unmapped active path"):
        migration.migrate(source, target, legacy_path_map_file=map_file)


def test_copy_aborts_on_different_existing_content(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "same-name.txt").write_text("source", encoding="utf-8")
    (target / "same-name.txt").write_text("different", encoding="utf-8")

    with pytest.raises(RuntimeError, match="collision"):
        migration.copy_tree_verified(source, target)


def test_rewrite_from_staged_runtime_uses_posix_paths_without_windows_resolution(
    tmp_path: Path,
) -> None:
    source = tmp_path / "project"
    target = tmp_path / "private"
    evidence_file = source / "evidence" / "nested" / "receipt.pdf"
    evidence_file.parent.mkdir(parents=True)
    evidence_file.write_text("synthetic evidence", encoding="utf-8")

    rewritten = migration._rewrite_from_staged_runtime(
        evidence_file.resolve().as_posix(),
        source,
        target,
    )

    assert rewritten == target / "evidence" / "nested" / "receipt.pdf"


def test_schema_wide_windows_path_check_rejects_non_allowlisted_columns(tmp_path: Path) -> None:
    database = tmp_path / "autonomo.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE stray_paths (path_value TEXT)")
        connection.execute(
            "INSERT INTO stray_paths (path_value) VALUES (?)",
            (r"C:\projects\spain-autonomo-taxes\evidence\receipt.pdf",),
        )

        with pytest.raises(RuntimeError, match="Unmapped Windows path remains"):
            migration._assert_no_unmapped_windows_paths(connection, target_root=tmp_path)


def _create_database(
    path: Path,
    *,
    source_evidence: str,
    drive_source: str,
    evidence_hash: str,
    drive_hash: str,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                document_id TEXT PRIMARY KEY, source_path TEXT, source_hash TEXT
            );
            CREATE TABLE document_sources (
                document_source_id TEXT PRIMARY KEY, source_file TEXT, source_hash TEXT
            );
            CREATE TABLE filing_snapshots (
                filing_snapshot_id TEXT PRIMARY KEY, manifest_path TEXT, payload_json TEXT
            );
            CREATE TABLE import_batches (
                import_batch_id TEXT PRIMARY KEY, source_name TEXT, source_hash TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO documents (document_id, source_path, source_hash) VALUES (?, ?, ?)",
            ("synthetic-document", source_evidence, evidence_hash),
        )
        connection.execute(
            "INSERT INTO document_sources (document_source_id, source_file, source_hash) VALUES (?, ?, ?)",
            ("synthetic-source", drive_source, drive_hash),
        )
        connection.execute(
            "INSERT INTO filing_snapshots (filing_snapshot_id, manifest_path, payload_json) VALUES (?, ?, ?)",
            (
                "synthetic-snapshot",
                drive_source,
                json.dumps({"document_path": drive_source}, ensure_ascii=False),
            ),
        )
        connection.execute(
            "INSERT INTO import_batches (import_batch_id, source_name, source_hash) VALUES (?, ?, ?)",
            ("synthetic-import", drive_source, drive_hash),
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
