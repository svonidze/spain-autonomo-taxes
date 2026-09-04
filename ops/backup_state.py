from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile


SHA_RE = re.compile(r"^[0-9a-f]{64}$")
APP_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
OFFSITE_STATUSES = {"disabled", "pending", "acknowledged", "failed"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("state must be a JSON object")
    return payload


def _safe_basename(value: object, suffix: str) -> str:
    name = str(value or "")
    if not name or name != Path(name).name or not name.startswith("private-root-") or not name.endswith(suffix):
        raise ValueError("unsafe backup basename")
    return name


def _secure_regular(path: Path, *, mode: int = 0o600) -> None:
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ValueError("backup state must reference regular files")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != mode or info.st_nlink != 1:
        raise ValueError("backup file ownership or permissions are invalid")


def build_local_marker(
    *, backup_class: str, archive: Path, manifest: Path, app_sha: str,
    database: Path, keep: int, remote_configured: bool,
) -> dict:
    if backup_class not in {"daily", "monthly"} or not APP_SHA_RE.fullmatch(app_sha) or keep < 1:
        raise ValueError("invalid backup state input")
    archive = archive.resolve(strict=True)
    manifest = manifest.resolve(strict=True)
    _secure_regular(archive)
    _secure_regular(manifest)
    archive_name = _safe_basename(archive.name, ".tar.gz")
    manifest_name = _safe_basename(manifest.name, ".manifest.json")
    if manifest_name != f"{archive_name.removesuffix('.tar.gz')}.manifest.json":
        raise ValueError("archive and manifest basenames do not match")
    document = read_json(manifest)
    if document.get("format") != 1 or document.get("archive") != archive_name:
        raise ValueError("unsupported backup manifest")
    archive_sha = sha256_file(archive)
    if document.get("archive_sha256") != archive_sha:
        raise ValueError("archive hash does not match manifest")
    members = document.get("files")
    if not isinstance(members, list) or not any(isinstance(row, dict) and row.get("path") == "autonomo.sqlite" for row in members):
        raise ValueError("manifest does not contain the database snapshot")
    member_bytes = sum(int(row["size"]) for row in members if isinstance(row, dict))
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "format": 2,
        "class": backup_class,
        "recorded_at": now,
        "backup_created_at": str(document["created_at"]),
        "archive": archive_name,
        "manifest": manifest_name,
        "archive_sha256": archive_sha,
        "manifest_sha256": sha256_file(manifest),
        "archive_bytes": archive.stat().st_size,
        "manifest_bytes": manifest.stat().st_size,
        "member_bytes": member_bytes,
        "app_sha": app_sha,
        "sqlite_schema": schema,
        "keep": keep,
        "settings_format": 1,
        "local_status": "verified",
        "offsite": "no",
        "offsite_status": "pending" if remote_configured else "disabled",
    }


def update_offsite(marker: Path, status_value: str) -> dict:
    if status_value not in OFFSITE_STATUSES:
        raise ValueError("invalid offsite status")
    payload = read_json(marker)
    if payload.get("format") != 2 or payload.get("local_status") != "verified":
        raise ValueError("invalid local backup marker")
    payload["offsite_status"] = status_value
    payload["offsite"] = "yes" if status_value == "acknowledged" else "no"
    payload["recorded_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    atomic_json(marker, payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write durable backup state without exposing private paths")
    sub = parser.add_subparsers(dest="command", required=True)
    local = sub.add_parser("local")
    local.add_argument("--marker", type=Path, required=True)
    local.add_argument("--class", dest="backup_class", choices=("daily", "monthly"), required=True)
    local.add_argument("--archive", type=Path, required=True)
    local.add_argument("--manifest", type=Path, required=True)
    local.add_argument("--app-sha", required=True)
    local.add_argument("--database", type=Path, required=True)
    local.add_argument("--keep", type=int, required=True)
    local.add_argument("--remote-configured", choices=("yes", "no"), required=True)
    remote = sub.add_parser("offsite")
    remote.add_argument("--marker", type=Path, required=True)
    remote.add_argument("--status", choices=tuple(sorted(OFFSITE_STATUSES)), required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "local":
        payload = build_local_marker(
            backup_class=args.backup_class, archive=args.archive, manifest=args.manifest,
            app_sha=args.app_sha, database=args.database, keep=args.keep,
            remote_configured=args.remote_configured == "yes",
        )
        atomic_json(args.marker, payload)
    else:
        update_offsite(args.marker, args.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
