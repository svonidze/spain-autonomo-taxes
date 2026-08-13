from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sqlite3

import pytest
import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_private_root.py"
SPEC = importlib.util.spec_from_file_location("migrate_private_root", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


def test_migration_copies_verifies_and_rewrites_evidence_paths(tmp_path: Path) -> None:
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
    _create_database(database, evidence_file)

    result = migration.migrate(source, target)

    assert result["database_paths_rewritten"] == 4
    copied_evidence = target / "evidence" / evidence_file.name
    assert _sha256(copied_evidence) == _sha256(evidence_file)
    with sqlite3.connect(target / "autonomo.sqlite") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for table, column in (
            ("documents", "source_path"),
            ("document_sources", "source_file"),
            ("filing_snapshots", "manifest_path"),
            ("import_batches", "source_name"),
        ):
            stored = Path(connection.execute(f"SELECT {column} FROM {table}").fetchone()[0])
            assert stored == copied_evidence.resolve()
    config = yaml.safe_load((target / "config.yaml").read_text(encoding="utf-8"))
    assert Path(config["ledger_db"]) == (target / "autonomo.sqlite").resolve()


def test_copy_aborts_on_different_existing_content(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "same-name.txt").write_text("source", encoding="utf-8")
    (target / "same-name.txt").write_text("different", encoding="utf-8")

    with pytest.raises(RuntimeError, match="collision"):
        migration.copy_tree_verified(source, target)


def _create_database(path: Path, evidence_file: Path) -> None:
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
                filing_snapshot_id TEXT PRIMARY KEY, manifest_path TEXT
            );
            CREATE TABLE import_batches (
                import_batch_id TEXT PRIMARY KEY, source_name TEXT, source_hash TEXT
            );
            """
        )
        for table, key, column in (
            ("documents", "document_id", "source_path"),
            ("document_sources", "document_source_id", "source_file"),
            ("filing_snapshots", "filing_snapshot_id", "manifest_path"),
            ("import_batches", "import_batch_id", "source_name"),
        ):
            connection.execute(
                f"INSERT INTO {table} ({key}, {column}) VALUES (?, ?)",
                (f"synthetic-{table}", str(evidence_file.resolve())),
            )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
