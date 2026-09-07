from __future__ import annotations
from autonomo_test_support.paths import REPO_ROOT

import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess

import pytest


PROJECT_ROOT = REPO_ROOT
CONFIG_SYNC = PROJECT_ROOT / "ops" / "sops" / "config-sync.sh"
PREFLIGHT = PROJECT_ROOT / "ops" / "sops" / "preflight.sh"
OPS_LIB = PROJECT_ROOT / "ops" / "sops" / "lib.sh"
SERVER_RECIPIENT = "age1serverrecipient000000000000000000000000000000000000000000"
RECOVERY_RECIPIENT = "age1recoveryrecipient000000000000000000000000000000000000000"


def _run(*args: str | Path, env: dict[str, str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(value) for value in args],
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def _write_secret_tree(work: Path, private_root: Path, *, failing_file: str | None = None) -> None:
    prod = work / "prod"
    prod.mkdir(parents=True, exist_ok=True)
    dotenv = (
        "SYNTHETIC=ENC[AES256_GCM,data:synthetic]\n"
        f"sops_age__list_0__map_recipient={SERVER_RECIPIENT}\n"
        f"sops_age__list_1__map_recipient={RECOVERY_RECIPIENT}\n"
    )
    yaml = (
        "synthetic: ENC[AES256_GCM,data:synthetic]\n"
        "sops:\n"
        "    age:\n"
        "        - enc: synthetic\n"
        f"          recipient: {SERVER_RECIPIENT}\n"
        "        - enc: synthetic\n"
        f"          recipient: {RECOVERY_RECIPIENT}\n"
    )
    json_document = {
        "synthetic": "ENC[AES256_GCM,data:synthetic]",
        "sops": {
            "age": [
                {"recipient": SERVER_RECIPIENT, "enc": "synthetic"},
                {"recipient": RECOVERY_RECIPIENT, "enc": "synthetic"},
            ]
        },
    }
    ini = (
        "[synthetic]\nvalue = ENC[AES256_GCM,data:synthetic]\n"
        "[sops]\n"
        f"age__list_0__map_recipient = {SERVER_RECIPIENT}\n"
        f"age__list_1__map_recipient = {RECOVERY_RECIPIENT}\n"
    )
    values = {
        "runtime.sops.env": dotenv,
        "config.sops.yaml": yaml,
        "google-drive-reader-service-account.sops.json": json.dumps(json_document),
        "google-drive-oauth-token.sops.json": json.dumps(json_document),
        "rclone.sops.ini": ini,
        "storage-backends.sops.json": json.dumps(json_document),
    }
    for name, content in values.items():
        if name == failing_file:
            if name.endswith(".json"):
                document = json.loads(content)
                document["FAIL_DECRYPT"] = True
                content = json.dumps(document)
            else:
                content += "FAIL_DECRYPT=1\n"
        (prod / name).write_text(content, encoding="utf-8")


def _commit_and_push(work: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(work), "add", "prod"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-m", message], check=True, capture_output=True)
    sha = subprocess.check_output(["git", "-C", str(work), "rev-parse", "HEAD"], text=True).strip()
    subprocess.run(["git", "-C", str(work), "push", "origin", "HEAD:main"], check=True, capture_output=True)
    return sha


def _fixture(tmp_path: Path) -> tuple[dict[str, str], Path, Path, str]:
    remote = tmp_path / "secrets.git"
    work = tmp_path / "secrets-work"
    private_root = tmp_path / "private"
    fake_bin = tmp_path / "fake-bin"
    config_home = tmp_path / "config-home"
    ssh_dir = tmp_path / "ssh"
    remote.mkdir()
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(work)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "Synthetic Operator"], check=True)
    subprocess.run(
        ["git", "-C", str(work), "config", "user.email", "operator" + chr(64) + "example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(remote)], check=True)
    _write_secret_tree(work, private_root)
    sha = _commit_and_push(work, "synthetic encrypted config")

    fake_bin.mkdir()
    fake_sops = fake_bin / "sops"
    fake_outputs = {
        "runtime.sops.env": (
            f"AUTONOMO_PRIVATE_ROOT={private_root}\n"
            f"AUTONOMO_RCLONE_CONFIG={private_root}/runtime-config/current/credentials/rclone.conf\n"
            "AUTONOMO_ALERT_WEBHOOK=TOP_SECRET_ALERT_VALUE\n"
        ),
        "config.sops.yaml": "ledger_db: /private/autonomo.sqlite\ngoogle_picker_developer_key: TOP_SECRET_PICKER_VALUE\n",
        "google-drive-reader-service-account.sops.json": '{"synthetic_identity":"reader-at-example.invalid"}\n',
        "google-drive-oauth-token.sops.json": '{"synthetic":"oauth-value"}\n',
        "rclone.sops.ini": "[yandex-evidence-crypt]\ntype = crypt\n",
        "storage-backends.sops.json": '{"backends":[{"backend_key":"local","display_name":"Local","driver_key":"filesystem","provider_key":"local","access_mode":"read_write","config":{"schema_version":1,"root":"/private"},"credential_ref":null}]}\n',
    }
    fake_sops.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import sys\n"
        "text = Path(sys.argv[-1]).read_text(encoding='utf-8')\n"
        "if 'FAIL_DECRYPT' in text:\n"
        "    raise SystemExit('synthetic decrypt failure')\n"
        f"outputs = {fake_outputs!r}\n"
        "print(outputs[Path(sys.argv[-1]).name], end='')\n",
        encoding="utf-8",
    )
    fake_sops.chmod(0o755)
    fake_flock = fake_bin / "flock"
    fake_flock.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_flock.chmod(0o755)

    config_home.mkdir()
    ssh_dir.mkdir()
    private_root.mkdir()
    age_key = config_home / "sops-age.key"
    age_key.write_text("AGE-SECRET-KEY-1SYNTHETIC\n", encoding="utf-8")
    age_key.chmod(0o600)
    bootstrap = config_home / "ops-bootstrap.env"
    bootstrap.write_text(
        f"AUTONOMO_PRIVATE_ROOT={private_root}\n"
        f"AUTONOMO_RELEASE_ROOT={tmp_path / 'releases'}\n"
        f"AUTONOMO_SECRET_CONFIG_REPO={remote}\n"
        "AUTONOMO_SECRET_CONFIG_BRANCH=main\n"
        f"AUTONOMO_SECRET_CONFIG_MOUNT={private_root / 'runtime-config'}\n"
        f"AUTONOMO_AGE_PRIVATE_KEY_PATH={age_key}\n"
        f"AUTONOMO_EXPECTED_AGE_RECIPIENTS={SERVER_RECIPIENT},{RECOVERY_RECIPIENT}\n"
        "AUTONOMO_SECRET_ALLOW_LOCAL_REPO=1\n"
        "AUTONOMO_SECRET_SYNC_REQUIRED=1\n",
        encoding="utf-8",
    )
    bootstrap.chmod(0o600)
    env = os.environ.copy()
    env.update(
        {
            "AUTONOMO_BOOTSTRAP_ENV_PATH": str(bootstrap),
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
        }
    )
    return env, work, private_root, sha


def test_config_sync_stages_and_activates_one_immutable_generation(tmp_path: Path) -> None:
    env, _work, private_root, sha = _fixture(tmp_path)

    completed = _run(CONFIG_SYNC, "--activate", sha, env=env, check=True)

    generation = private_root / "runtime-config" / "generations" / sha
    assert f"config_sha={sha}" in completed.stdout
    assert "TOP_SECRET" not in completed.stdout + completed.stderr
    assert (private_root / "runtime-config" / "current").resolve() == generation
    assert (generation / "config.yaml").read_text(encoding="utf-8").endswith(
        "google_picker_developer_key: TOP_SECRET_PICKER_VALUE\n"
    )
    expected_files = {
        generation / "runtime.env",
        generation / "config.yaml",
        generation / "credentials" / "google-drive-reader-service-account.json",
        generation / "credentials" / "google-drive-oauth-token.json",
        generation / "credentials" / "rclone.conf",
    }
    for path in expected_files:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(generation.stat().st_mode) == 0o700
    assert stat.S_IMODE((generation / "credentials").stat().st_mode) == 0o700


def test_failed_new_generation_keeps_the_active_generation(tmp_path: Path) -> None:
    env, work, private_root, first_sha = _fixture(tmp_path)
    _run(CONFIG_SYNC, "--activate", first_sha, env=env, check=True)
    _write_secret_tree(work, private_root, failing_file="google-drive-oauth-token.sops.json")
    second_sha = _commit_and_push(work, "failing config")

    completed = _run(CONFIG_SYNC, "--stage-only", second_sha, env=env)

    assert completed.returncode != 0
    assert "synthetic decrypt failure" in completed.stderr
    assert (private_root / "runtime-config" / "current").resolve().name == first_sha
    assert not (private_root / "runtime-config" / "generations" / second_sha).exists()


def test_existing_generation_digest_drift_fails_closed(tmp_path: Path) -> None:
    env, _work, private_root, sha = _fixture(tmp_path)
    _run(CONFIG_SYNC, "--activate", sha, env=env, check=True)
    config = private_root / "runtime-config" / "generations" / sha / "config.yaml"
    config.write_text("ledger_db: /tampered.sqlite\n", encoding="utf-8")
    config.chmod(0o600)

    completed = _run(CONFIG_SYNC, "--stage-only", sha, env=env)

    assert completed.returncode != 0
    assert "mismatch: config.yaml" in completed.stderr


def test_sync_rejects_file_without_recovery_recipient(tmp_path: Path) -> None:
    env, work, _private_root, _first_sha = _fixture(tmp_path)
    token = work / "prod" / "google-drive-oauth-token.sops.json"
    token.write_text(
        token.read_text(encoding="utf-8").replace(RECOVERY_RECIPIENT, SERVER_RECIPIENT),
        encoding="utf-8",
    )
    sha = _commit_and_push(work, "missing recovery recipient")

    completed = _run(CONFIG_SYNC, "--stage-only", sha, env=env)

    assert completed.returncode != 0
    assert "missing 1 expected age recipient" in completed.stderr


def test_sync_fails_closed_without_sops_or_age_identity(tmp_path: Path) -> None:
    env, _work, _private_root, sha = _fixture(tmp_path)
    fake_sops = Path(env["PATH"].split(os.pathsep)[0]) / "sops"
    fake_sops.rename(fake_sops.with_suffix(".disabled"))

    # Do not accidentally discover a real sops installed on the test host.
    no_sops_bin = tmp_path / "no-sops-bin"
    no_sops_bin.mkdir()
    for command in ("bash", "dirname", "python3", "git"):
        executable = shutil.which(command)
        assert executable is not None
        (no_sops_bin / command).symlink_to(executable)
    missing_sops = _run(CONFIG_SYNC, "--stage-only", sha, env={**env, "PATH": str(no_sops_bin)})

    assert missing_sops.returncode != 0
    assert "required command is unavailable: sops" in missing_sops.stderr

    fake_sops.with_suffix(".disabled").rename(fake_sops)
    bootstrap = Path(env["AUTONOMO_BOOTSTRAP_ENV_PATH"])
    age_key = next(
        Path(line.split("=", 1)[1])
        for line in bootstrap.read_text(encoding="utf-8").splitlines()
        if line.startswith("AUTONOMO_AGE_PRIVATE_KEY_PATH=")
    )
    age_key.unlink()
    missing_key = _run(CONFIG_SYNC, "--stage-only", sha, env=env)

    assert missing_key.returncode != 0
    assert "SOPS age identity does not exist" in missing_key.stderr


def test_preflight_validates_active_secret_paths_before_service_work(tmp_path: Path) -> None:
    env, _work, private_root, sha = _fixture(tmp_path)
    _run(CONFIG_SYNC, "--activate", sha, env=env, check=True)
    with sqlite3.connect(private_root / "autonomo.sqlite") as database:
        database.execute("PRAGMA user_version = 18")

    completed = _run(PREFLIGHT, sha, env=env, check=True)

    assert "preflight=passed" in completed.stdout
    assert f"secret_config_sha={sha}" in completed.stdout


def test_deployment_metadata_binds_application_and_secret_revisions(tmp_path: Path) -> None:
    env, _work, _private_root, secret_sha = _fixture(tmp_path)
    release_sha = "a" * 40
    bootstrap = Path(env["AUTONOMO_BOOTSTRAP_ENV_PATH"])
    release_root = next(
        Path(line.split("=", 1)[1])
        for line in bootstrap.read_text(encoding="utf-8").splitlines()
        if line.startswith("AUTONOMO_RELEASE_ROOT=")
    )
    release = release_root / "releases" / release_sha
    release.mkdir(parents=True)
    (release_root / "current").symlink_to(Path("releases") / release_sha)
    command = (
        f'source "{OPS_LIB}"; load_bootstrap_env; '
        f'record_current_deployment "{release_sha}" "{secret_sha}"; '
        "current_deployment_unit; "
        f'release_secret_config_sha "{release_sha}"'
    )

    completed = _run("bash", "-c", command, env=env, check=True)

    assert f"autonomo-web-{release_sha}-{secret_sha}.service" in completed.stdout
    assert completed.stdout.rstrip().endswith(secret_sha)
    assert stat.S_IMODE((release_root / "current-deployment").stat().st_mode) == 0o600
    assert stat.S_IMODE((release_root / "release-config" / release_sha).stat().st_mode) == 0o600


def test_ops_scripts_prepare_secrets_before_stopping_services() -> None:
    deploy = (PROJECT_ROOT / "ops" / "sops" / "deploy.sh").read_text(encoding="utf-8")
    rollback = (PROJECT_ROOT / "ops" / "sops" / "rollback.sh").read_text(encoding="utf-8")
    restore = (PROJECT_ROOT / "ops" / "sops" / "restore.sh").read_text(encoding="utf-8")

    assert deploy.index('config-sync.sh" --stage-only') < deploy.index("systemctl --user stop")
    assert rollback.index('config-sync.sh" --stage-only') < rollback.index("systemctl --user stop")
    assert restore.index('config-sync.sh" --stage-only') < restore.index("systemctl --user stop")
    assert "restore_secret_config" in deploy
    assert "restore_secret_config" in rollback
    assert "restore_secret_config" in restore


@pytest.mark.skipif(
    shutil.which("sops") is None or shutil.which("age-keygen") is None,
    reason="real SOPS/age smoke requires optional local binaries",
)
def test_real_sops_age_roundtrip(tmp_path: Path) -> None:
    sops = shutil.which("sops")
    age_keygen = shutil.which("age-keygen")
    assert sops is not None and age_keygen is not None
    remote = tmp_path / "real-secrets.git"
    work = tmp_path / "real-secrets-work"
    private_root = tmp_path / "private"
    config_home = tmp_path / "config-home"
    fake_bin = tmp_path / "fake-bin"
    for directory in (private_root, config_home, fake_bin):
        directory.mkdir()
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(work)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "Synthetic Operator"], check=True)
    subprocess.run(
        ["git", "-C", str(work), "config", "user.email", "operator" + chr(64) + "example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", str(remote)], check=True)
    server_key = config_home / "server.key"
    recovery_key = config_home / "recovery.key"
    subprocess.run([age_keygen, "-o", str(server_key)], check=True, capture_output=True)
    subprocess.run([age_keygen, "-o", str(recovery_key)], check=True, capture_output=True)
    server_key.chmod(0o600)
    recovery_key.chmod(0o600)
    server_recipient = subprocess.check_output([age_keygen, "-y", str(server_key)], text=True).strip()
    recovery_recipient = subprocess.check_output([age_keygen, "-y", str(recovery_key)], text=True).strip()
    prod = work / "prod"
    prod.mkdir()
    plaintext = {
        "runtime.sops.env": (
            f"AUTONOMO_PRIVATE_ROOT={private_root}\n"
            f"AUTONOMO_RCLONE_CONFIG={private_root}/runtime-config/current/credentials/rclone.conf\n"
        ),
        "config.sops.yaml": f"ledger_db: {private_root}/autonomo.sqlite\n",
        "google-drive-reader-service-account.sops.json": '{"synthetic_identity":"reader-at-example.invalid"}\n',
        "google-drive-oauth-token.sops.json": '{"synthetic":"oauth-value"}\n',
        "rclone.sops.ini": "[yandex-evidence-crypt]\ntype = crypt\n",
        "storage-backends.sops.json": '{"backends":[{"backend_key":"local","display_name":"Local","driver_key":"filesystem","provider_key":"local","access_mode":"read_write","config":{"schema_version":1,"root":"/private"},"credential_ref":null}]}\n',
    }
    for name, content in plaintext.items():
        path = prod / name
        path.write_text(content, encoding="utf-8")
        subprocess.run(
            [sops, "encrypt", "--age", f"{server_recipient},{recovery_recipient}", "--in-place", str(path)],
            check=True,
            capture_output=True,
        )
    sha = _commit_and_push(work, "real SOPS encrypted config")
    bootstrap = config_home / "ops-bootstrap.env"
    bootstrap.write_text(
        f"AUTONOMO_PRIVATE_ROOT={private_root}\n"
        f"AUTONOMO_RELEASE_ROOT={tmp_path / 'releases'}\n"
        f"AUTONOMO_SECRET_CONFIG_REPO={remote}\n"
        "AUTONOMO_SECRET_CONFIG_BRANCH=main\n"
        f"AUTONOMO_SECRET_CONFIG_MOUNT={private_root / 'runtime-config'}\n"
        f"AUTONOMO_AGE_PRIVATE_KEY_PATH={server_key}\n"
        f"AUTONOMO_EXPECTED_AGE_RECIPIENTS={server_recipient},{recovery_recipient}\n"
        "AUTONOMO_SECRET_ALLOW_LOCAL_REPO=1\n"
        "AUTONOMO_SECRET_SYNC_REQUIRED=1\n",
        encoding="utf-8",
    )
    bootstrap.chmod(0o600)
    fake_flock = fake_bin / "flock"
    fake_flock.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_flock.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "AUTONOMO_BOOTSTRAP_ENV_PATH": str(bootstrap),
            "PATH": f"{fake_bin}{os.pathsep}{Path(sops).parent}{os.pathsep}{env['PATH']}",
        }
    )

    completed = _run(CONFIG_SYNC, "--activate", sha, env=env, check=True)

    generation = private_root / "runtime-config" / "generations" / sha
    assert "status=staged" in completed.stdout
    assert (generation / "storage-backends.json").is_file()
    assert json.loads((generation / "storage-backends.json").read_text(encoding="utf-8"))["backends"][0]["backend_key"] == "local"
