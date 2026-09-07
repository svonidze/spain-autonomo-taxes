"""Exercise release selection against Git objects, including a different checkout."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_layout_gate", ROOT / "ops/prepare_ui_release.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
ENGINES = {"node": "24.20.0", "npm": "11.19.0"}
UI = "\n[tool.autonomo.web-ui]\nbuild-contract = 1\n"


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def commit(root, files):
    root.mkdir(exist_ok=True)
    if not (root / ".git").exists():
        git(root, "init", "-q")
        git(root, "config", "user.name", "Fixture")
        git(root, "config", "user.email", "synthetic@example.invalid")
    for path, body in files.items():
        target = root / path
        if body is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
    git(root, "add", ".")
    git(root, "commit", "-qm", "Synthetic release")
    return git(root, "rev-parse", "HEAD")


def files(version, *, ui=True):
    prefix = "frontend/" if version == 2 else ""
    project = "backend/pyproject.toml" if version == 2 else "pyproject.toml"
    metadata = '[project]\nname = "synthetic-release"\n'
    if version == 2:
        metadata += "\n[tool.autonomo.release]\nlayout-version = 2\n"
    return {
        project: metadata + (UI if ui else ""),
        prefix + "package.json": json.dumps({"engines": ENGINES}),
        prefix + ".nvmrc": ENGINES["node"] + "\n",
        prefix + "package-lock.json": '{"synthetic": true}\n',
    }


@pytest.mark.parametrize("version,ui", [(1, False), (1, True), (2, True)])
def test_project_path_and_contract_include_pre_vue_releases(tmp_path, capsys, version, ui):
    source = tmp_path / "source repository"
    sha = commit(source, files(version, ui=ui))
    target = tmp_path / "release with spaces"
    gate.main(["python-project-path", str(source), sha, str(target)])
    assert capsys.readouterr().out.strip() == str(target / ("backend" if version == 2 else ""))
    assert gate.contract(source, sha) == int(ui)
    if ui:
        assert gate.expected_tools(source, sha) == ENGINES
    assert not target.exists()


def test_requested_git_object_wins_over_current_checkout(tmp_path):
    source = tmp_path / "source"
    old = commit(source, files(1))
    new_files = files(2)
    new_files["pyproject.toml"] = None
    new = commit(source, new_files)
    # The source checkout is layout 2, but an earlier release still uses root metadata.
    assert gate.layout(source, old).python_root == Path(".")
    assert gate.layout(source, new).python_root == Path("backend")
    (source / "backend/pyproject.toml").write_text("not even TOML")
    assert gate.expected_tools(source, old) == gate.expected_tools(source, new) == ENGINES


@pytest.mark.parametrize("metadata", [
    {"unrelated.txt": "no project"},
    {"pyproject.toml": UI, "backend/pyproject.toml": UI},
    {"backend/pyproject.toml": UI},
    {"backend/pyproject.toml": '[tool.autonomo.release]\nlayout-version=2\n'},
    {"pyproject.toml": '[tool.autonomo.release]\nlayout-version=2\n' + UI},
    {"pyproject.toml": '[tool.autonomo.release]\nlayout-version=99\n'},
    {"pyproject.toml": '[tool.autonomo.release]\nlayout-version=true\n'},
    {"pyproject.toml": 'tool = "invalid table"\n'},
    {"pyproject.toml": '[tool.autonomo.web-ui]\nbuild-contract=99\n'},
    {"pyproject.toml": 'invalid TOML'},
])
def test_invalid_layout_stops_before_any_target_or_tools(tmp_path, monkeypatch, metadata):
    source = tmp_path / "source"
    sha = commit(source, metadata)
    target = tmp_path / "release"
    monkeypatch.setattr(gate, "tools", lambda *_: pytest.fail("invalid layout reached tools"))
    with pytest.raises((RuntimeError, ValueError)):
        gate.main(["preflight", str(source), sha, str(target)])
    assert not target.exists()


@pytest.mark.parametrize("version", [1, 2])
def test_build_uses_selected_node_root_and_preserves_completed_release(tmp_path, monkeypatch, version):
    source = tmp_path / "source"
    sha = commit(source, files(version))
    monkeypatch.setattr(gate, "tools", lambda *_: ("node", "npm", {"CI": "true"}))
    calls = []
    real_run = subprocess.run

    def run(args, **kwargs):
        if args[:2] == ["node", "npm"]:
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0)
        return real_run(args, **kwargs)

    monkeypatch.setattr(gate.subprocess, "run", run)
    gate.main(["build", str(source), sha, str(source)])
    node_root = source / ("frontend" if version == 2 else "")
    assert [args[2] for args, _ in calls] == ["ci", "run"]
    assert all(options["cwd"] == node_root for _, options in calls)
    assert all(options["env"] == {"CI": "true"} for _, options in calls)
    assert (source / ".ui-build-started").read_text().strip() == sha
    with pytest.raises(RuntimeError, match="already started"):
        gate.main(["build", str(source), sha, str(source)])
    (source / ".ui-install-complete.json").write_text("preserve receipt")
    with pytest.raises(RuntimeError, match="completed immutable release"):
        gate.main(["build", str(source), sha, str(source)])
    assert len(calls) == 2
    assert (source / ".ui-install-complete.json").read_text() == "preserve receipt"


@pytest.mark.parametrize("version", [1, 2])
def test_installation_checks_the_selected_lock_digest(tmp_path, monkeypatch, version):
    source = tmp_path / "source"
    sha = commit(source, files(version))
    (source / ".release-sha").write_text(sha)
    (source / ".schema-version").write_text("24")
    entrypoint = source / ".venv/bin/autonomo-web"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.touch()
    lock = source / ("frontend" if version == 2 else "") / "package-lock.json"
    result = {"schema": "24", "build": {"source_sha": sha, "source_dirty": False,
              "lock_sha256": gate.digest(lock.read_bytes()), **ENGINES}}
    monkeypatch.setattr(gate, "installed_manifest", lambda *_: result)
    assert gate.installation(source, sha, source) == result
    result["build"]["lock_sha256"] = "incorrect"
    with pytest.raises(RuntimeError, match="lockfile mismatch"):
        gate.installation(source, sha, source)


@pytest.mark.parametrize("script", ["ops/deploy.sh", "ops/sops/deploy.sh"])
def test_deploy_installs_the_selected_python_project(script):
    source = (ROOT / script).read_text()
    assert source.index('prepare_ui_release.py" python-project-path') < source.index('-m pip install')
    assert '--no-input "$python_project"' in source
