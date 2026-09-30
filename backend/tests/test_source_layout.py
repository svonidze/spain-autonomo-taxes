import os
from pathlib import Path
import subprocess
import sys

from autonomo_taxes import local_web


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_source_imports_and_default_project_root_ignore_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", "/synthetic/existing")
    source = REPO_ROOT / "backend/src"
    assert local_web._source_import_root() == source
    assert local_web._default_project_root() == REPO_ROOT
    assert local_web._cli_environment()["PYTHONPATH"].split(os.pathsep) == [
        str(source), "/synthetic/existing",
    ]


def test_installed_package_never_injects_a_checkout_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(local_web, "__file__", str(tmp_path / ".venv/lib/python3.11/site-packages/autonomo_taxes/local_web.py"))
    monkeypatch.delenv("PYTHONPATH", raising=False)
    assert local_web._source_import_root() is None
    assert local_web._default_project_root() == tmp_path
    assert "PYTHONPATH" not in local_web._cli_environment()
    monkeypatch.setenv("PYTHONPATH", "/synthetic/existing")
    assert local_web._cli_environment()["PYTHONPATH"] == "/synthetic/existing"


def test_source_http_to_cli_refresh_outside_checkout(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "backend/src"))
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tests/support/verify_cli_bridge.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=210,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HTTP -> CLI refresh verified" in result.stdout
