from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPS = PROJECT_ROOT / "ops"


def test_default_deploy_is_single_sha_and_has_no_sops_dependency() -> None:
    deploy = (OPS / "deploy.sh").read_text(encoding="utf-8")
    preflight = (OPS / "preflight.sh").read_text(encoding="utf-8")
    unit = (OPS / "systemd" / "autonomo-web.service.template").read_text(
        encoding="utf-8"
    )

    assert "usage: $0 <full-git-sha>" in deploy
    assert "[[ $# -eq 1 ]]" in deploy
    assert "config-sync" not in deploy
    assert "secret_sha" not in deploy
    assert "sops" not in preflight.casefold()
    assert "EnvironmentFile=%h/.config/autonomo-tax/runtime.env" in unit
    assert "@SECRET_" not in unit


def test_sops_deploy_remains_an_explicit_two_sha_mode() -> None:
    deploy = (OPS / "sops" / "deploy.sh").read_text(encoding="utf-8")
    installer = (OPS / "install-systemd-user-units.sh").read_text(encoding="utf-8")

    assert "usage: $0 <full-git-sha> <full-secret-config-sha>" in deploy
    assert "[[ $# -eq 2 ]]" in deploy
    assert '"$script_dir/config-sync.sh" --stage-only "$secret_sha"' in deploy
    assert 'install -m 755 "$script_dir/sops"/*.sh "$ops_root/sops/"' in installer
    assert "active_secret_sha" not in installer
    for template_name in (
        "autonomo-backup.service.template",
        "autonomo-backup-monthly.service.template",
    ):
        template = (OPS / "sops" / "systemd" / template_name).read_text(encoding="utf-8")
        assert (
            "Environment=AUTONOMO_RUNTIME_ENV_PATH="
            "@SECRET_CONFIG_ROOT@/current/runtime.env"
        ) in template


def test_default_recovery_scripts_keep_compensation_before_commit() -> None:
    deploy = (OPS / "deploy.sh").read_text(encoding="utf-8")
    rollback = (OPS / "rollback.sh").read_text(encoding="utf-8")
    restore = (OPS / "restore.sh").read_text(encoding="utf-8")

    assert "release link update failed; previous deployment was restored" in deploy
    assert rollback.index('require_private_file "$snapshot"') < rollback.index(
        'systemctl --user stop "autonomo-web-$previous.service"'
    )
    assert rollback.index("is-active --quiet") < rollback.index('ln -sfn "releases/$target_sha"')
    assert "rollback target failed the external health check" in rollback
    assert "restore_previous()" in restore
    assert "previous database was restored" in restore
    assert "restored database failed the external health check" in restore


def test_default_preflight_passes_without_sops_or_bootstrap_file(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    release_root = tmp_path / "releases"
    runtime_env = tmp_path / "runtime.env"
    private_root.mkdir()
    release_root.mkdir()
    (private_root / "config.yaml").write_text(
        f"ledger_db: {private_root / 'autonomo.sqlite'}\n",
        encoding="utf-8",
    )
    (private_root / "config.yaml").chmod(0o600)
    with sqlite3.connect(private_root / "autonomo.sqlite") as database:
        database.execute("PRAGMA user_version = 18")
    (private_root / "autonomo.sqlite").chmod(0o600)
    runtime_env.write_text(
        f"AUTONOMO_PRIVATE_ROOT={private_root}\n"
        f"AUTONOMO_RELEASE_ROOT={release_root}\n",
        encoding="utf-8",
    )
    runtime_env.chmod(0o600)
    env = os.environ.copy()
    env.pop("AUTONOMO_PRIVATE_ROOT", None)
    env.pop("AUTONOMO_RELEASE_ROOT", None)
    env.update(
        {
            "AUTONOMO_RUNTIME_ENV_PATH": str(runtime_env),
            "PATH": "/usr/bin:/bin",
        }
    )

    completed = subprocess.run(
        [str(OPS / "preflight.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "preflight passed" in completed.stderr


def test_one_shot_runtime_override_wins_over_runtime_file(tmp_path: Path) -> None:
    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text("AUTONOMO_ENABLE_STORAGE_MIGRATION=0\n", encoding="utf-8")
    runtime_env.chmod(0o600)
    env = os.environ.copy()
    env.update(
        {
            "AUTONOMO_RUNTIME_ENV_PATH": str(runtime_env),
            "AUTONOMO_ENABLE_STORAGE_MIGRATION": "1",
        }
    )
    command = (
        f'source "{OPS / "lib.sh"}"; '
        'load_runtime_env; printf "%s\\n" "$AUTONOMO_ENABLE_STORAGE_MIGRATION"'
    )

    completed = subprocess.run(
        ["bash", "-c", command],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "1\n"


def test_inherited_loaded_marker_cannot_bypass_runtime_file_permissions(
    tmp_path: Path,
) -> None:
    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text("AUTONOMO_PRIVATE_ROOT=/private\n", encoding="utf-8")
    runtime_env.chmod(0o644)
    env = os.environ.copy()
    env.update(
        {
            "AUTONOMO_RUNTIME_ENV_PATH": str(runtime_env),
            "AUTONOMO_RUNTIME_ENV_LOADED": "1",
        }
    )
    command = f'source "{OPS / "lib.sh"}"; load_runtime_env'

    completed = subprocess.run(
        ["bash", "-c", command],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "mode 0600" in completed.stderr


def test_sops_runtime_path_loads_without_default_runtime_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    generation = tmp_path / "private" / "runtime-config" / "generations" / ("a" * 40)
    generation.mkdir(parents=True)
    runtime_env = generation / "runtime.env"
    runtime_env.write_text("AUTONOMO_PRIVATE_ROOT=/sops-private\n", encoding="utf-8")
    runtime_env.chmod(0o600)
    current = generation.parent.parent / "current"
    current.symlink_to(Path("generations") / generation.name)
    env = os.environ.copy()
    env.pop("AUTONOMO_PRIVATE_ROOT", None)
    env.update(
        {
            "HOME": str(home),
            "AUTONOMO_RUNTIME_ENV_PATH": str(current / "runtime.env"),
        }
    )
    command = (
        f'source "{OPS / "lib.sh"}"; '
        'load_runtime_env; printf "%s\\n" "$AUTONOMO_PRIVATE_ROOT"'
    )

    completed = subprocess.run(
        ["bash", "-c", command],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "/sops-private\n"
