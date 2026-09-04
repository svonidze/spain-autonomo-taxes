from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3

from backup_state import _safe_basename, _secure_regular, read_json, sha256_file


def _runtime_backup_dir(runtime_env: Path, private_root: Path) -> Path:
    configured = None
    for line in runtime_env.read_text(encoding="utf-8").splitlines():
        if line.startswith("AUTONOMO_BACKUP_DIR="):
            configured = line.split("=", 1)[1]
    result = Path(configured) if configured else private_root / "backups" / "private-root"
    if not result.is_absolute():
        raise ValueError("backup directory must be absolute")
    return result


def validate_readiness(*, runtime_env: Path, private_root: Path, release_root: Path, max_age_hours: float) -> None:
    current = release_root / "current"
    if not current.is_symlink():
        print("backup readiness skipped for initial managed deployment")
        return
    target = current.resolve(strict=True)
    match = re.search(r"/releases/([0-9a-f]{40})$", str(target))
    if match is None:
        raise ValueError("current release link is invalid")
    active_sha = match.group(1)
    marker = private_root / "backups" / "last-backup-daily.json"
    _secure_regular(marker)
    payload = read_json(marker)
    required = {
        "format": 2, "class": "daily", "local_status": "verified", "app_sha": active_sha,
    }
    if any(payload.get(key) != value for key, value in required.items()):
        raise ValueError("daily backup marker is not deployment-ready")
    created = datetime.strptime(str(payload["backup_created_at"]), "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - created).total_seconds() / 3600
    if age < 0 or age > max_age_hours:
        raise ValueError("daily local backup is outside the allowed age")
    backup_dir = _runtime_backup_dir(runtime_env, private_root).resolve()
    archive_name = _safe_basename(payload.get("archive"), ".tar.gz")
    manifest_name = _safe_basename(payload.get("manifest"), ".manifest.json")
    if manifest_name != f"{archive_name.removesuffix('.tar.gz')}.manifest.json":
        raise ValueError("daily backup pair names do not match")
    archive = backup_dir / archive_name
    manifest = backup_dir / manifest_name
    _secure_regular(archive)
    _secure_regular(manifest)
    if archive.stat().st_size != int(payload["archive_bytes"]) or manifest.stat().st_size != int(payload["manifest_bytes"]):
        raise ValueError("daily backup pair size mismatch")
    if sha256_file(archive) != payload.get("archive_sha256") or sha256_file(manifest) != payload.get("manifest_sha256"):
        raise ValueError("daily backup pair hash mismatch")
    document = read_json(manifest)
    if document.get("archive") != archive_name or document.get("archive_sha256") != payload.get("archive_sha256"):
        raise ValueError("daily backup manifest binding mismatch")
    if not any(isinstance(row, dict) and row.get("path") == "autonomo.sqlite" for row in document.get("files", [])):
        raise ValueError("daily backup manifest lacks SQLite")
    with sqlite3.connect((private_root / "autonomo.sqlite").resolve().as_uri() + "?mode=ro", uri=True) as connection:
        live_schema = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if int(payload["sqlite_schema"]) != live_schema:
        raise ValueError("daily backup schema does not match live SQLite")
    if payload.get("offsite_status") != "acknowledged":
        print("warning: offsite upload is not acknowledged; local backup remains deployment-ready", file=os.sys.stderr)
    verified = private_root / "backups" / "last-backup-verified-monthly.json"
    if not verified.is_file():
        print("warning: monthly recovery verification has not succeeded yet", file=os.sys.stderr)
    print(f"local backup readiness passed; age={age:.1f}h")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate local backup readiness without contacting remote storage")
    parser.add_argument("--runtime-env", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--max-age-hours", type=float, default=48)
    args = parser.parse_args()
    try:
        validate_readiness(runtime_env=args.runtime_env, private_root=args.private_root.resolve(), release_root=args.release_root.resolve(), max_age_hours=args.max_age_hours)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, sqlite3.Error) as exc:
        raise SystemExit(f"local backup readiness failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
