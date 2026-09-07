from __future__ import annotations
from autonomo_test_support.paths import REPO_ROOT

import os
from pathlib import Path
import subprocess


ROOT = REPO_ROOT
WRAPPER = ROOT / "ops" / "run-with-service-env.sh"


def _script(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_wrapper_uses_runtime_path_and_passes_one_shot_overrides(tmp_path: Path) -> None:
    caller_bin = tmp_path / "caller-bin"
    runtime_bin = tmp_path / "runtime-bin"
    caller_bin.mkdir()
    runtime_bin.mkdir()
    received = tmp_path / "systemd-run.args"
    runtime = tmp_path / "runtime.env"
    runtime.write_text(
        f"PATH={runtime_bin}:/usr/bin:/bin\n"
        "AUTONOMO_PRIVATE_ROOT=/private\n"
        "AUTONOMO_DEPLOY_REF=runtime-default\n"
        "AUTONOMO_ENABLE_STORAGE_MIGRATION=0\n",
        encoding="utf-8",
    )
    runtime.chmod(0o600)

    _script(
        caller_bin / "systemd-run",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"received={received!s}\n"
        "printf '%s\\n' \"$@\" > \"$received\"\n"
        "runtime=\"\"\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  case \"$1\" in\n"
        "    --property=EnvironmentFile=*) runtime=${1#--property=EnvironmentFile=} ;;\n"
        "    --setenv=*) ;;\n"
        "    --) shift; break ;;\n"
        "  esac\n"
        "  shift\n"
        "done\n"
        "while IFS= read -r line || [[ -n $line ]]; do\n"
        "  case \"$line\" in ''|'#'*) continue ;; esac\n"
        "  export \"$line\"\n"
        "done < \"$runtime\"\n"
        "exec \"$@\"\n",
    )
    _script(runtime_bin / "rclone", "#!/bin/sh\nprintf 'runtime-rclone\\n'\n")
    consumer = tmp_path / "consumer"
    _script(
        consumer,
        "#!/bin/sh\n"
        "printf 'rclone='\n"
        "rclone\n"
        "IFS= read -r payload\n"
        "printf 'stdin=%s\\n' \"$payload\"\n"
        "printf 'deploy_ref=%s\\n' \"$AUTONOMO_DEPLOY_REF\"\n"
        "printf 'migration=%s\\n' \"$AUTONOMO_ENABLE_STORAGE_MIGRATION\"\n"
        "printf 'runtime_env=%s\\n' \"$AUTONOMO_RUNTIME_ENV_PATH\"\n",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{caller_bin}:/usr/bin:/bin",
            "AUTONOMO_RUNTIME_ENV_PATH": str(runtime),
        }
    )
    assert subprocess.run(
        ["sh", "-c", "command -v rclone"], env=env, capture_output=True, text=True
    ).returncode != 0

    result = subprocess.run(
        [
            str(WRAPPER),
            "--setenv",
            "AUTONOMO_DEPLOY_REF=master",
            "--setenv",
            "AUTONOMO_ENABLE_STORAGE_MIGRATION=1",
            "--",
            str(consumer),
        ],
        env=env,
        input="stdin-roundtrip\n",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "rclone=runtime-rclone\n"
        "stdin=stdin-roundtrip\n"
        "deploy_ref=master\n"
        "migration=1\n"
        f"runtime_env={runtime}\n"
    )
    arguments = received.read_text(encoding="utf-8")
    for expected in (
        "--user",
        "--wait",
        "--collect",
        "--pipe",
        "--quiet",
        f"--property=EnvironmentFile={runtime}",
        "/usr/bin/env",
        f"AUTONOMO_RUNTIME_ENV_PATH={runtime}",
        "AUTONOMO_DEPLOY_REF=master",
        "AUTONOMO_ENABLE_STORAGE_MIGRATION=1",
        str(consumer),
    ):
        assert expected in arguments


def test_wrapper_rejects_non_autonomo_override_and_relative_command(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.env"
    runtime.write_text("PATH=/usr/bin:/bin\n", encoding="utf-8")
    runtime.chmod(0o600)
    env = os.environ | {"AUTONOMO_RUNTIME_ENV_PATH": str(runtime)}

    unsafe = subprocess.run(
        [str(WRAPPER), "--setenv", "PATH=/tmp", "--", "/bin/true"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    relative = subprocess.run(
        [str(WRAPPER), "--", "backup.sh"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert unsafe.returncode != 0
    assert "AUTONOMO_NAME=value" in unsafe.stderr
    assert relative.returncode != 0
    assert "absolute path" in relative.stderr
