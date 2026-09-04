"""Run installed ops against disposable data; never use a real runtime environment."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile

import pytest

from autonomo_taxes.account_settings import save_backups
from autonomo_taxes.backup_settings import SETTINGS_FILENAME
from autonomo_taxes.ledger_db import LedgerDB

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def installed(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    database = private / "autonomo.sqlite"
    with LedgerDB.initialize(database):
        pass
    database.chmod(0o600)
    (private / "config.yaml").write_text("synthetic: true\n")
    (private / "config.yaml").chmod(0o600)
    ops = tmp_path / "ops"
    ops.mkdir()
    for filename in ("lib.sh", "backup.sh", "preflight.sh"):
        shutil.copy2(ROOT / "ops" / filename, ops / filename)
    shutil.copy2(ROOT / "scripts" / "backup_private_root.py", ops / "backup_private_root.py")
    shutil.copy2(ROOT / "src" / "autonomo_taxes" / "backup_settings.py", ops / "backup_settings.py")
    (ops / "sops").mkdir()
    for filename in ("lib.sh", "preflight.sh"):
        shutil.copy2(ROOT / "ops" / "sops" / filename, ops / "sops" / filename)
    releases = tmp_path / "releases"
    old_release = releases / "releases" / ("a" * 40)
    old_release.mkdir(parents=True)
    (releases / "current").symlink_to(old_release)
    # Intentionally no venv/package in this old release: the installed reader survives rollback.
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "python3").symlink_to(sys.executable)
    if not shutil.which("flock"):
        (binaries / "flock").write_text("#!/bin/sh\nexit 0\n")
        (binaries / "flock").chmod(0o755)
    runtime = tmp_path / "runtime.env"
    runtime.write_text(f"AUTONOMO_PRIVATE_ROOT={private}\nAUTONOMO_RELEASE_ROOT={releases}\nAUTONOMO_BACKUP_KEEP=12\nAUTONOMO_MONTHLY_BACKUP_KEEP=12\n")
    runtime.chmod(0o600)
    bootstrap = tmp_path / "bootstrap.env"
    bootstrap.write_text(f"AUTONOMO_PRIVATE_ROOT={private}\nAUTONOMO_RELEASE_ROOT={releases}\nAUTONOMO_SECRET_SYNC_REQUIRED=0\n")
    bootstrap.chmod(0o600)
    env = {key: value for key, value in os.environ.items() if not key.startswith("AUTONOMO_")}
    env.update({"AUTONOMO_RUNTIME_ENV_PATH": str(runtime), "AUTONOMO_BOOTSTRAP_ENV_PATH": str(bootstrap), "PATH": f"{binaries}:{os.environ['PATH']}"})
    return private, database, ops, env


def _run(script, env):
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=30, check=False)


def _seed_archives(private, database, backup_class):
    output = private / "backups" / ("private-root" if backup_class == "daily" else "monthly")
    spec = importlib.util.spec_from_file_location("synthetic_backup", ROOT / "scripts" / "backup_private_root.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for _ in range(8):
        module.create_backup(private_root=private, database=database, output_dir=output, keep=12)
    return output


@pytest.mark.parametrize("backup_class,keep", [("daily", 7), ("monthly", 3)])
def test_installed_ops_applies_saved_policy_and_prunes_only_when_run(installed, backup_class, keep):
    private, database, ops, env = installed
    output = _seed_archives(private, database, backup_class)
    save_backups(database, private, {"expected_revision": "missing", "daily_keep": 7, "monthly_keep": 3, "confirm_local_pruning": True})
    assert len(list(output.glob("*.tar.gz"))) == 8
    env["AUTONOMO_BACKUP_CLASS"] = backup_class
    result = _run(ops / "backup.sh", env)
    assert result.returncode == 0, result.stderr
    archives = sorted(output.glob("*.tar.gz"))
    assert len(archives) == keep
    assert len(list(output.glob("*.manifest.json"))) == keep
    with tarfile.open(archives[-1]) as archive:
        assert SETTINGS_FILENAME in archive.getnames()
        snapshot = archive.extractfile("autonomo.sqlite").read()
    restored = private.parent / "snapshot.sqlite"
    restored.write_bytes(snapshot)
    with sqlite3.connect(restored) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    marker = json.loads((private / "backups" / f"last-backup-{backup_class}.json").read_text())
    assert marker["keep"] == str(keep)
    assert marker["settings_format"] == "1"
    assert marker["offsite"] == "no"


def test_invalid_saved_policy_leaves_archives_and_success_marker_untouched(installed):
    private, database, ops, env = installed
    output = _seed_archives(private, database, "daily")
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    marker = private / "backups" / "last-backup-daily.json"
    marker.write_text("existing fixture marker")
    path = private / SETTINGS_FILENAME
    path.write_text('{"private":"must not appear in errors"')
    path.chmod(0o600)
    result = _run(ops / "backup.sh", env)
    assert result.returncode != 0
    assert "operator repair" in result.stderr
    assert "must not appear" not in result.stderr
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before
    assert marker.read_text() == "existing fixture marker"


def test_no_policy_does_not_need_installed_reader(installed):
    private, _, ops, env = installed
    (ops / "backup_settings.py").unlink()
    result = _run(ops / "backup.sh", env)
    assert result.returncode == 0, result.stderr
    marker = json.loads((private / "backups" / "last-backup-daily.json").read_text())
    assert marker["keep"] == "12"


def test_offsite_marker_requires_byte_exact_remote_readback(installed, tmp_path):
    private, _, ops, env = installed
    config = tmp_path / "rclone.conf"
    config.write_text("[crypt]\ntype = crypt\n")
    config.chmod(0o600)
    rclone_state = tmp_path / "rclone-state"
    rclone_state.mkdir()
    rclone = Path(env["PATH"].split(":", 1)[0]) / "rclone"
    rclone.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = --config ]; then shift 2; fi\n"
        "case \"$1\" in\n"
        "  config) printf 'type = crypt\\n' ;;\n"
        "  copyto) if [ \"$2\" = --immutable ]; then cp \"$3\" \"$FAKE_RCLONE_STATE/$(basename \"$4\")\"; "
        "else cp \"$FAKE_RCLONE_STATE/$(basename \"$2\")\" \"$3\"; fi ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n"
    )
    rclone.chmod(0o755)
    runtime = Path(env["AUTONOMO_RUNTIME_ENV_PATH"])
    runtime.write_text(
        runtime.read_text()
        + f"AUTONOMO_RCLONE_CONFIG={config}\nAUTONOMO_RCLONE_REMOTE=crypt:daily\n"
    )
    env["FAKE_RCLONE_STATE"] = str(rclone_state)

    result = _run(ops / "backup.sh", env)

    assert result.returncode == 0, result.stderr
    marker = json.loads((private / "backups" / "last-backup-daily.json").read_text())
    assert marker["offsite"] == "yes"


def test_offsite_marker_is_not_written_when_remote_readback_is_corrupt(installed, tmp_path):
    private, _, ops, env = installed
    config = tmp_path / "rclone.conf"
    config.write_text("[crypt]\ntype = crypt\n")
    config.chmod(0o600)
    rclone_state = tmp_path / "rclone-state"
    rclone_state.mkdir()
    rclone = Path(env["PATH"].split(":", 1)[0]) / "rclone"
    rclone.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = --config ]; then shift 2; fi\n"
        "case \"$1\" in\n"
        "  config) printf 'type = crypt\\n' ;;\n"
        "  copyto) if [ \"$2\" = --immutable ]; then cp \"$3\" \"$FAKE_RCLONE_STATE/$(basename \"$4\")\"; "
        "else printf corrupt > \"$3\"; fi ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n"
    )
    rclone.chmod(0o755)
    runtime = Path(env["AUTONOMO_RUNTIME_ENV_PATH"])
    runtime.write_text(
        runtime.read_text()
        + f"AUTONOMO_RCLONE_CONFIG={config}\nAUTONOMO_RCLONE_REMOTE=crypt:daily\n"
    )
    marker = private / "backups" / "last-backup-daily.json"
    marker.parent.mkdir(exist_ok=True)
    marker.write_text("previous marker")
    env["FAKE_RCLONE_STATE"] = str(rclone_state)

    result = _run(ops / "backup.sh", env)

    assert result.returncode != 0
    assert "uploaded backup archive did not match after remote readback" in result.stderr
    assert marker.read_text() == "previous marker"


@pytest.mark.parametrize("sops", [False, True])
def test_both_preflights_fail_closed_for_invalid_policy(installed, sops):
    private, _, ops, env = installed
    path = private / SETTINGS_FILENAME
    path.write_text('{"format":1,"daily_keep":0,"monthly_keep":3}')
    path.chmod(0o600)
    script = ops / "sops" / "preflight.sh" if sops else ops / "preflight.sh"
    result = _run(script, env)
    assert result.returncode != 0
    assert "operator repair" in result.stderr
    assert not (private / "backups").exists()


@pytest.mark.parametrize("sops", [False, True])
def test_both_installers_deliver_the_standalone_reader(sops):
    path = ROOT / "ops" / "sops" / "install-systemd-user-units.sh" if sops else ROOT / "ops" / "install-systemd-user-units.sh"
    installer = path.read_text()
    source = "$source_ops_dir" if sops else "$script_dir"
    assert f'install -m 644 "{source}/../src/autonomo_taxes/backup_settings.py" "$ops_root/backup_settings.py"' in installer
