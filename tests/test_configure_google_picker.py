from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess
import sys

import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "configure-google-picker.py"


def test_configure_google_picker_updates_atomically_without_printing_key(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.yaml"
    original = "# private config\nledger_db: /private/autonomo.sqlite\n"
    config.write_text(original, encoding="utf-8")
    config.chmod(0o600)
    key_file = tmp_path / "picker.key"
    key = "synthetic_restricted_picker_key_1234567890"
    key_file.write_text(key + "\n", encoding="utf-8")
    key_file.chmod(0o600)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(config),
            "--developer-key-file",
            str(key_file),
            "--app-id",
            "344327133225",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert key not in completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    backup = Path(result["backup"])
    assert backup.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    updated = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert updated["google_picker_developer_key"] == key
    assert str(updated["google_picker_app_id"]) == "344327133225"
    assert config.read_text(encoding="utf-8").startswith("# private config\n")


def test_configure_google_picker_rejects_broad_key_permissions(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("ledger_db: /private/autonomo.sqlite\n", encoding="utf-8")
    config.chmod(0o600)
    key_file = tmp_path / "picker.key"
    key_file.write_text("synthetic_restricted_picker_key_1234567890\n", encoding="utf-8")
    key_file.chmod(0o644)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(config),
            "--developer-key-file",
            str(key_file),
            "--app-id",
            "344327133225",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "mode 0600" in completed.stderr
    assert yaml.safe_load(config.read_text(encoding="utf-8")) == {
        "ledger_db": "/private/autonomo.sqlite"
    }


def test_configure_google_picker_rejects_symlinked_config(tmp_path: Path) -> None:
    target = tmp_path / "actual-config.yaml"
    target.write_text("ledger_db: /private/autonomo.sqlite\n", encoding="utf-8")
    target.chmod(0o600)
    config = tmp_path / "config.yaml"
    config.symlink_to(target)
    key_file = tmp_path / "picker.key"
    key_file.write_text("synthetic_restricted_picker_key_1234567890\n", encoding="utf-8")
    key_file.chmod(0o600)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--config",
            str(config),
            "--developer-key-file",
            str(key_file),
            "--app-id",
            "344327133225",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "non-symlink" in completed.stderr
    assert yaml.safe_load(target.read_text(encoding="utf-8")) == {
        "ledger_db": "/private/autonomo.sqlite"
    }
