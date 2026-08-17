from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import os
from pathlib import Path, PurePosixPath
from shutil import copy2
import sqlite3
import sys
from typing import Any, Iterable
import unicodedata


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autonomo_taxes.legacy_paths import LegacyPathResolver
from autonomo_taxes.private_paths import default_private_root


SQLITE_SIDE_SUFFIXES = ("-wal", "-shm", "-journal")
ACTIVE_PATH_COLUMNS = {
    "documents": ("document_id", "source_path"),
    "document_sources": ("document_source_id", "source_file"),
    "import_batches": ("import_batch_id", "source_name"),
    "counterparty_identities": ("counterparty_identity_id", "source_reference"),
    "obligations": ("obligation_id", "source_citation"),
}
IMMUTABLE_WINDOWS_PATH_ALLOWLIST = {
    ("filing_snapshots", "manifest_path"),
    ("filing_snapshots", "payload_json"),
    ("filing_snapshots", "source_reference"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value.strip())


def copy_tree_verified(
    source: Path,
    destination: Path,
    *,
    ignore_names: set[str] | None = None,
) -> int:
    copied = 0
    if not source.is_dir():
        return copied
    ignored = ignore_names or set()
    for source_file in sorted(path for path in source.rglob("*") if path.is_file()):
        if source_file.name in ignored or any(
            source_file.name.endswith(suffix) for suffix in SQLITE_SIDE_SUFFIXES
        ):
            continue
        relative = source_file.relative_to(source)
        destination_file = destination / relative
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        source_hash = sha256(source_file)
        if destination_file.exists():
            if sha256(destination_file) != source_hash:
                raise RuntimeError(f"Private-root collision for {relative}")
        else:
            copy2(source_file, destination_file)
        if sha256(destination_file) != source_hash:
            raise RuntimeError(f"Private-root verification failed for {relative}")
        copied += 1
    return copied


def backup_sqlite_snapshot(source_database: Path, target_database: Path) -> Path:
    source_path = source_database.expanduser().resolve(strict=True)
    target_path = target_database.expanduser().resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = target_path.with_name(f".{target_path.name}.tmp-{os.getpid()}")
    try:
        with closing(
            sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
        ) as source, closing(sqlite3.connect(temporary)) as target:
            source.execute("PRAGMA query_only = ON")
            source_audit = _database_audit(source)
            source.backup(target)
            target.commit()
            target_audit = _database_audit(target)
            if source_audit != target_audit:
                raise RuntimeError("SQLite backup validation failed")
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, target_path)
    return target_path


def _database_audit(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]
    row_counts: dict[str, int] = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        row_counts[table] = int(
            connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
        )
    return {
        "integrity_check": connection.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_key_violations": len(
            connection.execute("PRAGMA foreign_key_check").fetchall()
        ),
        "user_version": int(connection.execute("PRAGMA user_version").fetchone()[0]),
        "row_counts": row_counts,
    }


def update_config(
    config_path: Path,
    database: Path,
    *,
    legacy_path_map_file: Path | None = None,
) -> None:
    import yaml

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise RuntimeError("Private config must contain a mapping")
    private_root = database.parent.resolve()
    payload["ledger_db"] = str(database.resolve())
    payload["inbox_root"] = str((private_root / "inbox").resolve())
    payload["archive_root"] = str((private_root / "evidence").resolve())
    payload["drive_evidence_dir"] = str((private_root / "evidence").resolve())
    payload["drive_backup_dir"] = str((private_root / "backups").resolve())
    payload["review_export_dir"] = str((private_root / "review-exports").resolve())
    payload["cache_root"] = str((private_root / "cache" / "web" / "dashboard").resolve())
    if "xolo_root" in payload:
        payload["xolo_root"] = str((private_root / "imports" / "xolo").resolve())
    if "xolo_expense_ledger" in payload:
        payload["xolo_expense_ledger"] = str(
            (private_root / "imports" / "xolo-expenses.csv").resolve()
        )
    if legacy_path_map_file is not None:
        payload["legacy_path_map_file"] = str(legacy_path_map_file.resolve())
    config_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def migrate(
    source_root: Path,
    target_root: Path,
    *,
    legacy_path_map_file: Path | None = None,
) -> dict[str, int | str]:
    source_root = source_root.resolve()
    target_root = target_root.resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    counts = {
        "local_files": copy_tree_verified(
            source_root / ".local",
            target_root,
            ignore_names={"autonomo.sqlite", "autonomo.sqlite-wal", "autonomo.sqlite-shm"},
        ),
        "data_files": copy_tree_verified(source_root / "data", target_root / "data"),
        "run_files": copy_tree_verified(source_root / "runs", target_root / "runs"),
        "evidence_files": copy_tree_verified(source_root / "evidence", target_root / "evidence"),
    }
    database = backup_sqlite_snapshot(
        source_root / ".local" / "autonomo.sqlite",
        target_root / "autonomo.sqlite",
    )
    config = target_root / "config.yaml"
    if not config.is_file():
        raise RuntimeError("Private root must contain config.yaml")
    installed_map_file = None
    if legacy_path_map_file is not None:
        installed_map_file = target_root / legacy_path_map_file.name
        copy2(legacy_path_map_file, installed_map_file)
    counts["database_paths_rewritten"] = rewrite_database_paths(
        database,
        source_root,
        target_root,
        legacy_path_map_file=legacy_path_map_file,
    )
    update_config(config, database, legacy_path_map_file=installed_map_file)
    return {"private_root": str(target_root), **counts}


def rewrite_database_paths(
    database: Path,
    source_root: Path,
    target_root: Path,
    *,
    legacy_path_map_file: Path | None = None,
) -> int:
    resolver = LegacyPathResolver.from_json_file(legacy_path_map_file)
    rewritten = 0
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table, (key_column, path_column) in ACTIVE_PATH_COLUMNS.items():
                if not _table_has_column(connection, table, path_column):
                    continue
                query = f"SELECT {key_column}, {path_column} FROM {table} WHERE {path_column} IS NOT NULL"
                for key, value in connection.execute(query).fetchall():
                    rewritten_value = _rewrite_path_value(
                        str(value),
                        source_root,
                        target_root,
                        resolver,
                    )
                    if rewritten_value is None:
                        if _looks_like_absolute_path(str(value)):
                            raise RuntimeError(f"Unmapped active path in {table}.{path_column}")
                        continue
                    if rewritten_value != str(value):
                        connection.execute(
                            f"UPDATE {table} SET {path_column} = ? WHERE {key_column} = ?",
                            (rewritten_value, key),
                        )
                        rewritten += 1
            _assert_no_unmapped_windows_paths(connection, target_root=target_root)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return rewritten


def _rewrite_path_value(
    value: str,
    source_root: Path,
    target_root: Path,
    resolver: LegacyPathResolver,
) -> str | None:
    raw = _normalize_text(value)
    if not raw or not _looks_like_absolute_path(raw):
        return None
    destination = _rewrite_from_staged_runtime(raw, source_root, target_root) or resolver.resolve(raw)
    if destination is None:
        return None
    source_candidate = Path(raw).resolve(strict=False)
    if _is_relative_to(destination, target_root):
        if source_candidate.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            copy2(source_candidate, destination)
    destination_is_private = _is_relative_to(
        destination.resolve(strict=False), target_root.resolve(strict=False)
    )
    if destination_is_private and not destination.exists():
        raise RuntimeError(f"Mapped private file is missing: {destination}")
    if (
        source_candidate.is_file()
        and destination.is_file()
        and sha256(source_candidate) != sha256(destination)
    ):
        raise RuntimeError("Copied file hash mismatch for mapped path")
    return str(destination.resolve(strict=False))


def _rewrite_from_staged_runtime(
    value: str,
    source_root: Path,
    target_root: Path,
) -> Path | None:
    normalized = _normalize_text(value)
    native_candidate = Path(normalized).resolve(strict=False)
    native_evidence_root = (source_root / "evidence").resolve(strict=False)
    if _is_relative_to(native_candidate, native_evidence_root):
        return target_root / "evidence" / native_candidate.relative_to(native_evidence_root)
    if _looks_like_windows_path(normalized):
        return None
    candidate = PurePosixPath(normalized)
    evidence_root = PurePosixPath((source_root / "evidence").resolve(strict=False).as_posix())
    try:
        relative = candidate.relative_to(evidence_root)
    except ValueError:
        return None
    if str(relative) == ".":
        relative = PurePosixPath()
    if relative.parts:
        return target_root / "evidence" / Path(*relative.parts)
    return target_root / "evidence"


def _assert_no_unmapped_windows_paths(
    connection: sqlite3.Connection,
    *,
    target_root: Path,
) -> None:
    allowed_native_roots = (
        target_root.resolve(strict=False),
        (target_root / "mnt").resolve(strict=False),
        (target_root / "evidence").resolve(strict=False),
    )
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    ]
    for table in tables:
        columns = [
            row[1]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            if str(row[2]).upper() in {"TEXT", "VARCHAR", "CLOB"}
        ]
        for column in columns:
            if (table, column) in IMMUTABLE_WINDOWS_PATH_ALLOWLIST:
                continue
            for (value,) in connection.execute(
                f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"
            ).fetchall():
                if not _looks_like_windows_path(str(value)):
                    continue
                if os.name == "nt":
                    candidate = Path(str(value)).resolve(strict=False)
                    if any(
                        _is_relative_to(candidate, allowed_root)
                        for allowed_root in allowed_native_roots
                    ):
                        continue
                raise RuntimeError(f"Unmapped Windows path remains in {table}.{column}")


def _table_has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    return any(
        row[1] == column for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    )


def _looks_like_windows_path(value: str) -> bool:
    normalized = value.strip()
    return normalized.startswith("\\\\") or (
        len(normalized) >= 3 and normalized[1:3] in {":\\", ":/"}
    )


def _looks_like_absolute_path(value: str) -> bool:
    normalized = value.strip()
    return normalized.startswith("/") or _looks_like_windows_path(normalized)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy, snapshot, and rewrite an extracted private runtime into the final private root"
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, default=default_private_root())
    parser.add_argument("--legacy-path-map-file", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = migrate(
        args.source_root,
        args.target_root,
        legacy_path_map_file=args.legacy_path_map_file,
    )
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
