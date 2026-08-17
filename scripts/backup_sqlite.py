from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from typing import Iterable


def backup_database(database: Path, backup_dir: Path, *, keep: int = 14) -> Path:
    if keep < 1:
        raise ValueError("keep must be at least 1")
    source_path = database.expanduser().resolve(strict=True)
    backup_root = backup_dir.expanduser().resolve()
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = backup_root / f"autonomo-{stamp}.sqlite"
    temporary = backup_root / f".{destination.name}.tmp-{os.getpid()}"
    try:
        with closing(
            sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
        ) as source, closing(sqlite3.connect(temporary)) as target:
            source.execute("PRAGMA query_only = ON")
            source.backup(target)
            target.commit()
            integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_key_violations = target.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != "ok" or foreign_key_violations:
                raise RuntimeError("SQLite backup validation failed")
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    backups = sorted(
        backup_root.glob("autonomo-*.sqlite"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for expired in backups[keep:]:
        expired.unlink()
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create and rotate a validated SQLite backup")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--keep", type=int, default=14)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    destination = backup_database(args.database, args.backup_dir, keep=args.keep)
    print(f"backup={destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
