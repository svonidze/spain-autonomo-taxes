from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from backup_state import _safe_basename, _secure_regular, atomic_json, read_json, sha256_file


class VerificationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _state(private_root: Path, name: str, payload: dict) -> None:
    atomic_json(private_root / "backups" / name, {
        "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "format": 1,
        **payload,
    })


def verify(*, private_root: Path, backup_dir: Path, remote: str, config: Path, restore_script: Path, max_age_hours: float) -> None:
    attempt_name = "last-backup-verification-attempt-monthly.json"
    success_name = "last-backup-verified-monthly.json"
    identity = {"class": "monthly", "status": "running"}
    _state(private_root, attempt_name, identity)
    try:
        marker_path = private_root / "backups" / "last-backup-monthly.json"
        _secure_regular(marker_path)
        marker = read_json(marker_path)
        if marker.get("format") != 2 or marker.get("class") != "monthly" or marker.get("local_status") != "verified":
            raise VerificationError("invalid_monthly_marker")
        created = datetime.strptime(str(marker["backup_created_at"]), "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age = (now - created).total_seconds() / 3600
        if age < 0 or age > max_age_hours or (created.year, created.month) != (now.year, now.month):
            raise VerificationError("monthly_marker_not_current")
        if marker.get("offsite_status") != "acknowledged" or marker.get("offsite") != "yes":
            raise VerificationError("monthly_upload_not_acknowledged")
        archive_name = _safe_basename(marker["archive"], ".tar.gz")
        manifest_name = _safe_basename(marker["manifest"], ".manifest.json")
        if manifest_name != f"{archive_name.removesuffix('.tar.gz')}.manifest.json":
            raise VerificationError("unsafe_backup_name")
        identity["archive_sha256"] = str(marker["archive_sha256"])
        required = int(marker["archive_bytes"]) + int(marker["manifest_bytes"]) + int(marker["member_bytes"]) + 100 * 1024 * 1024
        if shutil.disk_usage(tempfile.gettempdir()).free < required:
            raise VerificationError("insufficient_scratch_space")
        with tempfile.TemporaryDirectory(prefix="autonomo-recovery-verify-") as raw:
            scratch = Path(raw)
            os.chmod(scratch, 0o700)
            archive = scratch / archive_name
            manifest = scratch / manifest_name
            for source, destination in ((f"{remote.rstrip('/')}/{archive_name}", archive), (f"{remote.rstrip('/')}/{manifest_name}", manifest)):
                result = subprocess.run(["rclone", "--config", str(config), "copyto", source, str(destination)], text=True, capture_output=True, check=False)
                if result.returncode:
                    print(result.stderr, file=sys.stderr, end="")
                    raise VerificationError("remote_download_failed")
                os.chmod(destination, 0o600)
            if sha256_file(archive) != marker["archive_sha256"] or sha256_file(manifest) != marker["manifest_sha256"]:
                raise VerificationError("download_hash_mismatch")
            restored = scratch / "restored"
            result = subprocess.run([sys.executable, str(restore_script), "--archive", str(archive), "--manifest", str(manifest), "--target-root", str(restored), "--private-root", str(private_root)], text=True, capture_output=True, check=False)
            if result.returncode:
                print(result.stderr, file=sys.stderr, end="")
                raise VerificationError("archive_validation_failed")
            try:
                with sqlite3.connect((restored / "autonomo.sqlite").resolve().as_uri() + "?mode=ro", uri=True) as database:
                    if database.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or database.execute("PRAGMA foreign_key_check").fetchone() is not None:
                        raise VerificationError("sqlite_validation_failed")
                    schema = int(database.execute("PRAGMA user_version").fetchone()[0])
            except sqlite3.Error as exc:
                raise VerificationError("sqlite_validation_failed") from exc
            if schema != int(marker["sqlite_schema"]):
                raise VerificationError("sqlite_schema_mismatch")
        verified = {**identity, "status": "success", "sqlite_schema": int(marker["sqlite_schema"])}
        _state(private_root, success_name, verified)
        _state(private_root, attempt_name, verified)
    except VerificationError as exc:
        _state(private_root, attempt_name, {**identity, "status": "failed", "failure_code": exc.code})
        raise SystemExit(f"monthly backup verification failed: {exc.code}") from exc
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        _state(private_root, attempt_name, {**identity, "status": "failed", "failure_code": "internal_error"})
        raise SystemExit("monthly backup verification failed: internal_error") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a monthly backup through isolated remote recovery")
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--remote", required=True)
    parser.add_argument("--rclone-config", type=Path, required=True)
    parser.add_argument("--restore-script", type=Path, required=True)
    parser.add_argument("--max-age-hours", type=float, default=72)
    args = parser.parse_args()
    verify(private_root=args.private_root.resolve(), backup_dir=args.backup_dir.resolve(), remote=args.remote, config=args.rclone_config.resolve(), restore_script=args.restore_script.resolve(), max_age_hours=args.max_age_hours)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
