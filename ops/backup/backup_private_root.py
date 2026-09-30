from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
from typing import Iterable


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_database(source_path: Path, destination: Path) -> None:
    source_uri = f"file:{source_path.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source, closing(
        sqlite3.connect(destination)
    ) as target:
        source.execute("PRAGMA query_only = ON")
        source.backup(target)
        target.commit()
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = target.execute("PRAGMA foreign_key_check").fetchall()
    if integrity != "ok" or foreign_keys:
        raise RuntimeError("SQLite snapshot verification failed")


def create_backup(
    *,
    private_root: Path,
    database: Path,
    output_dir: Path,
    keep: int,
) -> tuple[Path, Path]:
    if keep < 1:
        raise ValueError("keep must be at least 1")
    root = private_root.expanduser().resolve(strict=True)
    db = database.expanduser().resolve(strict=True)
    try:
        db.relative_to(root)
    except ValueError as exc:
        raise ValueError("database must be inside private_root") from exc
    out = output_dir.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    archive = out / f"private-root-{stamp}.tar.gz"
    manifest = out / f"private-root-{stamp}.manifest.json"
    excluded_roots = {"backups", "cache", "browser"}
    with tempfile.TemporaryDirectory(dir=out, prefix=".backup-") as temporary_dir:
        temporary = Path(temporary_dir)
        snapshot = temporary / "autonomo.sqlite"
        _snapshot_database(db, snapshot)
        temporary_archive = temporary / archive.name
        with tarfile.open(temporary_archive, "w:gz", format=tarfile.PAX_FORMAT) as tar:
            for path in sorted(root.rglob("*")):
                relative = path.relative_to(root)
                if not relative.parts:
                    continue
                if relative.parts[0] in excluded_roots:
                    continue
                if path == db or path.name in {
                    f"{db.name}-wal",
                    f"{db.name}-shm",
                    f"{db.name}-journal",
                }:
                    continue
                if path.is_symlink() or not path.is_file():
                    continue
                arcname = relative.as_posix()
                tar.add(path, arcname=arcname, recursive=False)
            tar.add(snapshot, arcname="autonomo.sqlite", recursive=False)
        os.chmod(temporary_archive, 0o600)
        os.replace(temporary_archive, archive)
    files = _archive_members(archive)
    payload = {
        "format": 1,
        "created_at": stamp,
        "archive": archive.name,
        "archive_sha256": _sha256(archive),
        "files": files,
    }
    manifest.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(manifest, 0o600)
    archives = sorted(out.glob("private-root-*.tar.gz"), key=lambda path: path.name, reverse=True)
    for expired in archives[keep:]:
        expired.unlink()
        expired.with_suffix("").with_suffix(".manifest.json").unlink(missing_ok=True)
    return archive, manifest


def _archive_members(archive: Path) -> list[dict[str, str | int]]:
    """Build the manifest from the exact bytes committed to the archive."""
    rows: list[dict[str, str | int]] = []
    with tarfile.open(archive, "r:gz") as tar:
        for member in sorted((item for item in tar.getmembers() if item.isfile()), key=lambda item: item.name):
            source = tar.extractfile(member)
            if source is None:
                raise RuntimeError(f"Archive member could not be read: {member.name}")
            digest = hashlib.sha256()
            size = 0
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
            rows.append({"path": member.name, "sha256": digest.hexdigest(), "size": size})
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a verified private-root archive backup")
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--keep", type=int, default=35)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    archive, manifest = create_backup(
        private_root=args.private_root,
        database=args.database,
        output_dir=args.out_dir,
        keep=args.keep,
    )
    print(f"archive={archive}")
    print(f"manifest={manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
