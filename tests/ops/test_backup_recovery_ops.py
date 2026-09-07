from __future__ import annotations
from autonomo_test_support.paths import REPO_ROOT

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest


ROOT = REPO_ROOT
OPS = ROOT / "ops"


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


backup_tool = _module("backup_private_root_for_recovery_tests", ROOT / "scripts" / "backup_private_root.py")
state_tool = _module("backup_state", OPS / "backup_state.py")


@pytest.fixture
def backup_pair(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    database = private / "autonomo.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA user_version = 23")
        db.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")
        db.execute("INSERT INTO sample VALUES (1)")
    database.chmod(0o600)
    output = private / "backups" / "private-root"
    archive, manifest = backup_tool.create_backup(private_root=private, database=database, output_dir=output, keep=5)
    return private, database, archive, manifest


def _marker(private, database, archive, manifest, backup_class="daily"):
    marker = private / "backups" / f"last-backup-{backup_class}.json"
    payload = state_tool.build_local_marker(
        backup_class=backup_class, archive=archive, manifest=manifest,
        app_sha="a" * 40, database=database, keep=5, remote_configured=True,
    )
    state_tool.atomic_json(marker, payload)
    state_tool.update_offsite(marker, "acknowledged")
    return marker


def test_deploy_readiness_uses_only_verified_local_pair(backup_pair, tmp_path):
    private, database, archive, manifest = backup_pair
    _marker(private, database, archive, manifest)
    release_root = tmp_path / "releases"
    release = release_root / "releases" / ("a" * 40)
    release.mkdir(parents=True)
    (release_root / "current").symlink_to(release)
    runtime = tmp_path / "runtime.env"
    runtime.write_text(f"AUTONOMO_BACKUP_DIR={archive.parent}\n")
    runtime.chmod(0o600)

    completed = subprocess.run([
        sys.executable, str(OPS / "backup_readiness.py"), "--runtime-env", str(runtime),
        "--private-root", str(private), "--release-root", str(release_root),
    ], text=True, capture_output=True, check=False)

    assert completed.returncode == 0, completed.stderr
    assert "local backup readiness passed" in completed.stdout

    state_tool.update_offsite(private / "backups" / "last-backup-daily.json", "failed")
    remote_failed = subprocess.run(completed.args, text=True, capture_output=True, check=False)
    assert remote_failed.returncode == 0
    assert "offsite upload is not acknowledged" in remote_failed.stderr


def test_deploy_readiness_rejects_corrupt_local_pair(backup_pair, tmp_path):
    private, database, archive, manifest = backup_pair
    _marker(private, database, archive, manifest)
    archive.write_bytes(archive.read_bytes() + b"corrupt")
    release_root = tmp_path / "releases"
    release = release_root / "releases" / ("a" * 40)
    release.mkdir(parents=True)
    (release_root / "current").symlink_to(release)
    runtime = tmp_path / "runtime.env"
    runtime.write_text(f"AUTONOMO_BACKUP_DIR={archive.parent}\n")

    completed = subprocess.run([
        sys.executable, str(OPS / "backup_readiness.py"), "--runtime-env", str(runtime),
        "--private-root", str(private), "--release-root", str(release_root),
    ], text=True, capture_output=True, check=False)

    assert completed.returncode != 0
    assert "local backup readiness failed" in completed.stderr


def test_deploy_readiness_rejects_legacy_marker(backup_pair, tmp_path):
    private, database, archive, manifest = backup_pair
    marker = _marker(private, database, archive, manifest)
    payload = json.loads(marker.read_text())
    payload["format"] = 1
    state_tool.atomic_json(marker, payload)
    release_root = tmp_path / "releases"
    release = release_root / "releases" / ("a" * 40)
    release.mkdir(parents=True)
    (release_root / "current").symlink_to(release)
    runtime = tmp_path / "runtime.env"
    runtime.write_text(f"AUTONOMO_BACKUP_DIR={archive.parent}\n")

    completed = subprocess.run([
        sys.executable, str(OPS / "backup_readiness.py"), "--runtime-env", str(runtime),
        "--private-root", str(private), "--release-root", str(release_root),
    ], text=True, capture_output=True, check=False)

    assert completed.returncode != 0
    assert "not deployment-ready" in completed.stderr


def test_monthly_verifier_downloads_and_restores_exact_pair(backup_pair, tmp_path, monkeypatch):
    private, database, archive, manifest = backup_pair
    monthly_dir = private / "backups" / "monthly"
    monthly_dir.mkdir()
    monthly_archive = monthly_dir / archive.name
    monthly_manifest = monthly_dir / manifest.name
    shutil.copy2(archive, monthly_archive)
    shutil.copy2(manifest, monthly_manifest)
    marker = _marker(private, database, monthly_archive, monthly_manifest, "monthly")
    payload = json.loads(marker.read_text())
    payload["backup_created_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    state_tool.atomic_json(marker, payload)
    remote = tmp_path / "remote"
    remote.mkdir()
    shutil.copy2(monthly_archive, remote / monthly_archive.name)
    shutil.copy2(monthly_manifest, remote / monthly_manifest.name)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    rclone = binaries / "rclone"
    rclone.write_text("#!/bin/sh\nif [ \"$1\" = --config ]; then shift 2; fi\n[ \"$1\" = copyto ] || exit 2\ncp \"$FAKE_REMOTE/$(basename \"$2\")\" \"$3\"\n")
    rclone.chmod(0o755)
    config = tmp_path / "rclone.conf"
    config.write_text("[crypt]\ntype = crypt\n")
    monkeypatch.setenv("PATH", f"{binaries}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_REMOTE", str(remote))

    completed = subprocess.run([
        sys.executable, str(OPS / "verify_backup.py"), "--private-root", str(private),
        "--backup-dir", str(monthly_dir), "--remote", "crypt:monthly",
        "--rclone-config", str(config), "--restore-script", str(ROOT / "scripts" / "restore_private_root.py"),
    ], text=True, capture_output=True, check=False, env=os.environ.copy())

    assert completed.returncode == 0, completed.stderr
    attempt = json.loads((private / "backups" / "last-backup-verification-attempt-monthly.json").read_text())
    success = json.loads((private / "backups" / "last-backup-verified-monthly.json").read_text())
    assert attempt["status"] == success["status"] == "success"


def test_monthly_verifier_records_failure_without_erasing_success(backup_pair, tmp_path, monkeypatch):
    private, database, archive, manifest = backup_pair
    monthly_dir = private / "backups" / "monthly"
    monthly_dir.mkdir()
    monthly_archive = monthly_dir / archive.name
    monthly_manifest = monthly_dir / manifest.name
    shutil.copy2(archive, monthly_archive)
    shutil.copy2(manifest, monthly_manifest)
    marker = _marker(private, database, monthly_archive, monthly_manifest, "monthly")
    payload = json.loads(marker.read_text())
    payload["backup_created_at"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    state_tool.atomic_json(marker, payload)
    previous = private / "backups" / "last-backup-verified-monthly.json"
    previous.write_text('{"class":"monthly","status":"success","recorded_at":"2026-01-01T00:00:00Z"}\n')
    previous_bytes = previous.read_bytes()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    rclone = binaries / "rclone"
    rclone.write_text("#!/bin/sh\nexit 3\n")
    rclone.chmod(0o755)
    config = tmp_path / "rclone.conf"
    config.write_text("[crypt]\ntype = crypt\n")
    monkeypatch.setenv("PATH", f"{binaries}:{os.environ['PATH']}")

    completed = subprocess.run([
        sys.executable, str(OPS / "verify_backup.py"), "--private-root", str(private),
        "--backup-dir", str(monthly_dir), "--remote", "crypt:monthly",
        "--rclone-config", str(config), "--restore-script", str(ROOT / "scripts" / "restore_private_root.py"),
    ], text=True, capture_output=True, check=False, env=os.environ.copy())

    assert completed.returncode != 0
    attempt = json.loads((private / "backups" / "last-backup-verification-attempt-monthly.json").read_text())
    assert attempt["status"] == "failed"
    assert attempt["failure_code"] == "remote_download_failed"
    assert previous.read_bytes() == previous_bytes
