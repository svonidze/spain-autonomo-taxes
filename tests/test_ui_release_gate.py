import importlib.util
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ui_release_gate", ROOT / "ops/prepare_ui_release.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
SHA = "a" * 40


def test_legacy_releases_do_not_require_new_tools_or_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "contract", lambda *_: 0)
    monkeypatch.setattr(gate, "tools", lambda *_: pytest.fail("legacy called build tools"))
    for phase in ("preflight", "build", "receipt", "verify"):
        gate.main([phase, str(tmp_path), SHA, str(tmp_path / "release")])


def test_new_target_preflights_tools_without_creating_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "contract", lambda *_: 1)
    calls = []
    monkeypatch.setattr(gate, "tools", lambda *_: calls.append("tools"))
    target = tmp_path / "release"
    gate.main(["preflight", str(tmp_path), SHA, str(target)])
    assert calls == ["tools"] and not target.exists()


def test_existing_partial_release_is_preserved_and_cannot_be_repaired(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "contract", lambda *_: 1)
    monkeypatch.setattr(gate, "tools", lambda *_: pytest.fail("partial target ran build tools"))
    target = tmp_path / "release"
    target.mkdir()
    (target / "partial").write_text("keep")
    with pytest.raises(RuntimeError, match="Incomplete frontend release"):
        gate.main(["preflight", str(tmp_path), SHA, str(target)])
    assert (target / "partial").read_text() == "keep"


def test_complete_target_can_be_reused_without_node(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "contract", lambda *_: 1)
    monkeypatch.setattr(gate, "installation", lambda *_: {"verified": True})
    monkeypatch.setattr(gate, "tools", lambda *_: pytest.fail("complete target ran build tools"))
    gate.write_receipt(tmp_path, SHA, tmp_path)
    gate.main(["preflight", str(tmp_path), SHA, str(tmp_path)])
    with pytest.raises(RuntimeError, match="Refusing"):
        gate.write_receipt(tmp_path, SHA, tmp_path)


def test_npm_failure_stops_before_build_and_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "contract", lambda *_: 1)
    monkeypatch.setattr(gate, "verify_tree", lambda *_: None)
    monkeypatch.setattr(gate, "tools", lambda *_: ("node", "npm", {"CI": "true"}))
    calls = []
    def fail(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(gate.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        gate.main(["build", str(tmp_path), SHA, str(tmp_path)])
    assert len(calls) == 1 and "--include=dev" in calls[0]
    assert not (tmp_path / ".ui-install-complete.json").exists()
    with pytest.raises(RuntimeError, match="already started"):
        gate.main(["build", str(tmp_path), SHA, str(tmp_path)])
    assert len(calls) == 1


def test_build_environment_does_not_inherit_service_secrets(monkeypatch):
    for name in ("VITE_SECRET", "AUTONOMO_SECRET", "NODE_OPTIONS", "NODE_ENV", "SOPS_AGE_KEY"):
        monkeypatch.setenv(name, "synthetic-sensitive-value")
    env = gate.build_environment("/synthetic/node/bin/node")
    assert all(value != "synthetic-sensitive-value" for value in env.values())
    assert env["PATH"].startswith("/synthetic/node/bin:")


@pytest.mark.parametrize("path", ["ops/deploy.sh", "ops/sops/deploy.sh"])
def test_both_modes_gate_installation_before_downtime(path):
    source = (ROOT / path).read_text()
    preflight = source.index('prepare_ui_release.py" preflight')
    create = source.index('worktree add --detach')
    build = source.index('prepare_ui_release.py" build')
    install = source.index('prepare_ui_release.py" install')
    receipt = source.index('prepare_ui_release.py" receipt')
    verify = source.index('prepare_ui_release.py" verify')
    stop = source.index('systemctl --user stop')
    assert preflight < create < build < install < receipt < verify < stop
    assert "set -euo pipefail" in source
