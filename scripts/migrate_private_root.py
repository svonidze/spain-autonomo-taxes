from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Iterable

import yaml


PATH_COLUMNS = {
    "documents": ("document_id", "source_path", "source_hash"),
    "document_sources": ("document_source_id", "source_file", "source_hash"),
    "filing_snapshots": ("filing_snapshot_id", "manifest_path", None),
    "import_batches": ("import_batch_id", "source_name", "source_hash"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_tree_verified(source: Path, destination: Path) -> int:
    copied = 0
    if not source.is_dir():
        return copied
    for source_file in sorted(path for path in source.rglob("*") if path.is_file()):
        relative = source_file.relative_to(source)
        destination_file = destination / relative
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        source_hash = sha256(source_file)
        if destination_file.exists():
            if sha256(destination_file) != source_hash:
                raise RuntimeError(f"Private-root collision for {relative}")
        else:
            shutil.copy2(source_file, destination_file)
        if sha256(destination_file) != source_hash:
            raise RuntimeError(f"Private-root verification failed for {relative}")
        copied += 1
    return copied


def _under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _migrated_path(
    value: str,
    source_root: Path,
    target_root: Path,
) -> tuple[Path, Path] | None:
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        return None
    resolved = candidate.resolve(strict=False)
    source_evidence = (source_root / "evidence").resolve(strict=False)
    if not _under(resolved, source_evidence):
        return None
    return resolved, target_root / "evidence" / resolved.relative_to(source_evidence)


def rewrite_database_paths(database: Path, source_root: Path, target_root: Path) -> int:
    rewritten = 0
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table, (key_column, path_column, hash_column) in PATH_COLUMNS.items():
                columns = [key_column, path_column]
                if hash_column:
                    columns.append(hash_column)
                query = f"SELECT {', '.join(columns)} FROM {table} WHERE {path_column} IS NOT NULL"
                for row in connection.execute(query).fetchall():
                    key, value = row[0], row[1]
                    path_pair = _migrated_path(value, source_root, target_root)
                    if path_pair is None:
                        continue
                    source, migrated = path_pair
                    if not source.is_file():
                        raise RuntimeError(f"Original private file is missing for {table}.{path_column}")
                    if not migrated.is_file():
                        raise RuntimeError(f"Migrated private file is missing for {table}.{path_column}")
                    if sha256(source) != sha256(migrated):
                        raise RuntimeError(f"Copied file hash mismatch for {table}.{path_column}")
                    connection.execute(
                        f"UPDATE {table} SET {path_column} = ? WHERE {key_column} = ?",
                        (str(migrated.resolve()), key),
                    )
                    rewritten += 1
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return rewritten


def update_config(config_path: Path, database: Path) -> None:
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError("Private config must contain a mapping")
    payload["ledger_db"] = str(database.resolve())
    config_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def migrate(source_root: Path, target_root: Path) -> dict[str, int | str]:
    source_root = source_root.resolve()
    target_root = target_root.resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    counts = {
        "local_files": copy_tree_verified(source_root / ".local", target_root),
        "data_files": copy_tree_verified(source_root / "data", target_root / "data"),
        "run_files": copy_tree_verified(source_root / "runs", target_root / "runs"),
        "evidence_files": copy_tree_verified(source_root / "evidence", target_root / "evidence"),
    }
    database = target_root / "autonomo.sqlite"
    config = target_root / "config.yaml"
    if not database.is_file() or not config.is_file():
        raise RuntimeError("Private root must contain autonomo.sqlite and config.yaml")
    counts["database_paths_rewritten"] = rewrite_database_paths(database, source_root, target_root)
    update_config(config, database)
    return {"private_root": str(target_root), **counts}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copy and verify private runtime data outside the Git worktree")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument(
        "--target-root",
        type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local")) / "spain-autonomo-taxes",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = migrate(args.source_root, args.target_root)
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
