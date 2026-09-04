from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest


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


def test_default_installer_delivers_service_environment_wrapper() -> None:
    installer = (OPS / "install-systemd-user-units.sh").read_text(encoding="utf-8")

    assert (OPS / "run-with-service-env.sh").is_file()
    assert 'install -m 755 "$script_dir"/*.sh "$ops_root/"' in installer


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
        f"AUTONOMO_RELEASE_ROOT={release_root}\n"
        "TZ=UTC\n"
        "LANG=C.UTF-8\n"
        "PYTHONUTF8=1\n"
        "PATH=/usr/bin:/bin\n",
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


def _ocr_preflight_environment(tmp_path: Path, *, ready: bool) -> tuple[dict[str, str], str]:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    releases = tmp_path / "releases"
    releases.mkdir()
    with sqlite3.connect(private / "autonomo.sqlite") as db:
        db.execute("PRAGMA user_version = 18")
    (private / "autonomo.sqlite").chmod(0o600)
    (private / "config.yaml").write_text("year: 2032\n")
    (private / "config.yaml").chmod(0o600)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    engine = binaries / "tesseract"
    engine.write_text(f"#!/bin/sh\nprintf '{'eng' if ready else 'spa'}\\n'\n")
    engine.chmod(0o755)
    for command in ("sops", "flock"):
        stub = binaries / command
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    runtime = tmp_path / "runtime.env"
    runtime.write_text(
        f"AUTONOMO_PRIVATE_ROOT={private}\nAUTONOMO_RELEASE_ROOT={releases}\n"
        f"PATH={binaries}:/usr/bin:/bin\n"
    )
    runtime.chmod(0o600)
    secret_sha = "a" * 40
    mount = private / "runtime-config"
    generation = mount / "generations" / secret_sha
    generation.mkdir(parents=True, mode=0o700)
    (generation / "credentials").mkdir(mode=0o700)
    contents = {
        ".secret-config-sha": secret_sha,
        "runtime.env": runtime.read_text() + f"AUTONOMO_RCLONE_CONFIG={mount}/current/credentials/rclone.conf\n",
        "config.yaml": "year: 2032\n",
        "credentials/google-drive-reader-service-account.json": "{}",
        "credentials/google-drive-oauth-token.json": "{}",
        "credentials/rclone.conf": "[synthetic]\ntype = local\n",
        "storage-backends.json": json.dumps({"backends": [{"backend_key": "synthetic"}]}),
    }
    manifest = {}
    for relative, content in contents.items():
        target = generation / relative
        target.write_text(content)
        target.chmod(0o600)
        if relative != ".secret-config-sha":
            manifest[relative] = {"byte_size": len(content.encode()), "sha256": hashlib.sha256(content.encode()).hexdigest()}
    manifest_file = generation / ".manifest.json"
    manifest_file.write_text(json.dumps({
        "format": "autonomo-secret-generation/v1", "secret_config_sha": secret_sha, "files": manifest,
    }))
    manifest_file.chmod(0o600)
    age = tmp_path / "synthetic-age.key"
    age.write_text("synthetic fixture, not an identity")
    age.chmod(0o600)
    bootstrap = tmp_path / "bootstrap.env"
    bootstrap.write_text(
        f"AUTONOMO_PRIVATE_ROOT={private}\nAUTONOMO_RELEASE_ROOT={releases}\n"
        f"AUTONOMO_SECRET_CONFIG_MOUNT={mount}\nAUTONOMO_AGE_PRIVATE_KEY_PATH={age}\n"
        "AUTONOMO_SECRET_SYNC_REQUIRED=1\n"
    )
    bootstrap.chmod(0o600)
    env = {key: value for key, value in os.environ.items() if not key.startswith("AUTONOMO_")}
    env.update({
        "PATH": f"{binaries}:/usr/bin:/bin",
        "AUTONOMO_RUNTIME_ENV_PATH": str(runtime),
        "AUTONOMO_BOOTSTRAP_ENV_PATH": str(bootstrap),
    })
    return env, secret_sha


@pytest.mark.parametrize("sops", [False, True])
@pytest.mark.parametrize("ready", [False, True])
def test_recovery_preflights_remain_usable_without_ocr(tmp_path: Path, sops: bool, ready: bool) -> None:
    env, sha = _ocr_preflight_environment(tmp_path, ready=ready)
    command = [str(OPS / "sops" / "preflight.sh"), sha] if sops else [str(OPS / "preflight.sh")]
    result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert "ocr=ready" not in result.stdout


def test_sops_checks_staged_runtime_not_operator_or_current_path(tmp_path: Path) -> None:
    env, sha = _ocr_preflight_environment(tmp_path, ready=True)
    staged = tmp_path / "private" / "runtime-config" / "generations" / sha
    runtime = staged / "runtime.env"
    runtime.write_text(runtime.read_text().replace(f"PATH={tmp_path / 'bin'}:/usr/bin:/bin", "PATH=/missing-synthetic-bin"))
    manifest_path = staged / ".manifest.json"
    manifest = json.loads(manifest_path.read_text())
    content = runtime.read_bytes()
    manifest["files"]["runtime.env"] = {"byte_size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    manifest_path.write_text(json.dumps(manifest))
    command, events = _deployment_harness(tmp_path, env, sha, sops=True)
    result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=10, check=False)
    assert result.returncode != 0
    assert "not found in the runtime PATH" in result.stderr
    assert not events.exists()


def _deployment_harness(tmp_path: Path, env: dict[str, str], sha: str, *, sops: bool) -> tuple[list[str], Path]:
    binaries = tmp_path / "bin"
    events = tmp_path / "events.txt"
    for name in ("git", "systemctl"):
        stub = binaries / name
        stub.write_text(
            "#!/bin/sh\n"
            'case "$*" in *--is-inside-work-tree*) exit 0;; esac\n'
            f'printf "{name} call\\n" >> "$OCR_TEST_EVENTS"\nexit 88\n'
        )
        stub.chmod(0o755)
    env.update({"OCR_TEST_EVENTS": str(events), "AUTONOMO_DEPLOY_REPOSITORY": str(tmp_path)})
    if sops:
        # Only secret transport is stubbed. The deployed script, its library,
        # staged-generation validation, and OCR readiness are real code.
        staged_ops = tmp_path / "ops"
        staged_sops = staged_ops / "sops"
        staged_sops.mkdir(parents=True)
        shutil.copy2(OPS / "ocr-readiness.py", staged_ops / "ocr-readiness.py")
        for name in ("deploy.sh", "lib.sh", "preflight.sh"):
            shutil.copy2(OPS / "sops" / name, staged_sops / name)
        sync = staged_sops / "config-sync.sh"
        sync.write_text("#!/bin/sh\nexit 0\n")
        sync.chmod(0o755)
        command = [str(staged_sops / "deploy.sh"), "b" * 40, sha]
    else:
        command = [str(OPS / "deploy.sh"), "b" * 40]
    return command, events


@pytest.mark.parametrize("sops", [False, True])
@pytest.mark.parametrize("ready", [False, True])
def test_deploy_checks_ocr_before_git_fetch_or_systemctl(tmp_path: Path, sops: bool, ready: bool) -> None:
    env, sha = _ocr_preflight_environment(tmp_path, ready=ready)
    command, events = _deployment_harness(tmp_path, env, sha, sops=sops)
    result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=10, check=False)
    if ready:
        assert result.returncode == 88, result.stderr
        assert "ocr=ready" in result.stdout
        assert events.read_text() == "git call\n"
    else:
        assert result.returncode != 0
        assert "eng language data is unavailable" in result.stderr
        assert not events.exists()


@pytest.mark.parametrize("sops", [False, True])
def test_installers_deliver_helper_before_deploy(sops: bool) -> None:
    installer = OPS / "sops" / "install-systemd-user-units.sh" if sops else OPS / "install-systemd-user-units.sh"
    contents = installer.read_text()
    assert contents.index('"$ops_root/ocr-readiness.py"') < contents.index('/*.sh')


def test_ocr_gate_is_deployment_only_not_a_rollback_dependency() -> None:
    for directory in (OPS, OPS / "sops"):
        assert "ocr-readiness.py" in (directory / "deploy.sh").read_text()
        for name in ("rollback.sh", "restore.sh", "preflight.sh"):
            assert "ocr-readiness.py" not in (directory / name).read_text()
