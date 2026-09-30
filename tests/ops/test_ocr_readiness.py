from __future__ import annotations
from autonomo_test_support.paths import REPO_ROOT

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = REPO_ROOT / "ops" / "ocr-readiness.py"


def _fake_engine(directory: Path, mode: str = "ready") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    engine = directory / "tesseract"
    engine.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        f"mode = {mode!r}\n"
        "if mode == 'timeout': time.sleep(5)\n"
        "if mode == 'error':\n"
        "    print('PRIVATE DIAGNOSTIC', file=sys.stderr)\n"
        "    sys.exit(2)\n"
        "if sys.argv[1] == '--version': print('tesseract synthetic')\n"
        "elif sys.argv[1] == '--list-langs':\n"
        "    print('List of available languages:')\n"
        "    ready = mode != 'no_eng' and (mode != 'env_models' or os.environ.get('TESSDATA_PREFIX') == 'synthetic-models')\n"
        "    print('eng' if ready else 'spa')\n",
        encoding="utf-8",
    )
    engine.chmod(0o755)
    return engine


def _run(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        env={**os.environ, "PATH": str(path)},
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


@pytest.mark.parametrize(
    ("mode", "message"),
    [("missing", "not found"), ("no_eng", "eng"), ("error", "failed"), ("timeout", "timed out")],
)
def test_readiness_fails_closed_without_private_diagnostics(
    tmp_path: Path, mode: str, message: str
) -> None:
    if mode != "missing":
        _fake_engine(tmp_path, mode)
    result = _run(tmp_path, "--timeout-seconds", "0.2" if mode == "timeout" else "2")
    assert result.returncode != 0
    assert message in result.stderr
    assert "PRIVATE DIAGNOSTIC" not in result.stdout + result.stderr
    assert "ocr=ready" not in result.stdout


def test_readiness_succeeds_without_system_tesseract(tmp_path: Path) -> None:
    _fake_engine(tmp_path)
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ocr=ready language=eng"


def test_runtime_path_overrides_interactive_path(tmp_path: Path) -> None:
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    _fake_engine(good)
    _fake_engine(bad, "no_eng")
    runtime = tmp_path / "runtime.env"
    runtime.write_text(f"PATH={bad}\nAUTONOMO_SYNTHETIC_VALUE=PRIVATE VALUE\n")
    runtime.chmod(0o600)
    failed = _run(good, "--runtime-env", str(runtime))
    assert failed.returncode != 0
    assert "eng" in failed.stderr
    assert "PRIVATE VALUE" not in failed.stdout + failed.stderr
    runtime.write_text(f"PATH={good}\n")
    passed = _run(bad, "--runtime-env", str(runtime))
    assert passed.returncode == 0, passed.stderr


def test_runtime_does_not_inherit_operator_ocr_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_engine(tmp_path, "env_models")
    monkeypatch.setenv("TESSDATA_PREFIX", "synthetic-models")
    runtime = tmp_path / "runtime.env"
    runtime.write_text(f"PATH={tmp_path}\n")
    runtime.chmod(0o600)
    failed = _run(tmp_path, "--runtime-env", str(runtime))
    assert failed.returncode != 0
    assert "eng language data is unavailable" in failed.stderr
    runtime.write_text(runtime.read_text() + "TESSDATA_PREFIX=synthetic-models\n")
    passed = _run(tmp_path, "--runtime-env", str(runtime))
    assert passed.returncode == 0, passed.stderr


def test_runtime_requires_explicit_path(tmp_path: Path) -> None:
    _fake_engine(tmp_path)
    runtime = tmp_path / "runtime.env"
    runtime.write_text("LANG=C\n")
    runtime.chmod(0o600)
    result = _run(tmp_path, "--runtime-env", str(runtime))
    assert result.returncode != 0
    assert "declare a non-empty PATH" in result.stderr


@pytest.mark.parametrize("contents", [
    "not an assignment", "PATH='quoted'", "export PATH=/bin", "PATH= /bin",
    "PATH=/bin ", "PATH=/bin\\", 'VALUE=embedded"quote', "VALUE=embedded\x00null",
])
def test_invalid_runtime_is_not_evaluated(tmp_path: Path, contents: str) -> None:
    runtime = tmp_path / "runtime.env"
    runtime.write_text(contents + "\n")
    runtime.chmod(0o600)
    result = _run(tmp_path, "--runtime-env", str(runtime))
    assert result.returncode != 0
    assert "runtime" in result.stderr
    assert contents not in result.stderr


def test_runtime_requires_private_regular_file(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.env"
    runtime.write_text("PATH=/bin\n")
    runtime.chmod(0o644)
    result = _run(tmp_path, "--runtime-env", str(runtime))
    assert result.returncode != 0
    assert "0600" in result.stderr


def test_exec_failure_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("ocr_readiness", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.shutil, "which", lambda *args, **kwargs: "/synthetic/tesseract")

    def fail(*args: object, **kwargs: object) -> None:
        raise PermissionError("PRIVATE DIAGNOSTIC")

    monkeypatch.setattr(module.subprocess, "run", fail)
    with pytest.raises(module.OcrReadinessError, match="could not start"):
        module.check_readiness(dict(os.environ), timeout_seconds=1)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_timeout_must_be_finite_and_positive(tmp_path: Path, value: str) -> None:
    result = _run(tmp_path, "--timeout-seconds", value)
    assert result.returncode != 0
    assert "finite positive" in result.stderr
