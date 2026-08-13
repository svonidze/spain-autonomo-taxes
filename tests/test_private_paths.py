from __future__ import annotations

import json
from pathlib import Path

import pytest

from autonomo_taxes.cli import main
from autonomo_taxes.local_web import load_config
from autonomo_taxes.private_paths import (
    LegacyPrivateConfigWarning,
    PrivatePathError,
    resolve_private_paths,
)


def test_explicit_config_wins_and_relative_values_use_config_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    working = tmp_path / "working"
    working.mkdir()
    config_dir = tmp_path / "private-config"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"
    config_file.write_text("ledger_db: data/ledger.sqlite\n", encoding="utf-8")
    monkeypatch.chdir(working)
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "ignored-env-root"))

    assert main(["--config", str(config_file), "db", "init"]) == 0

    assert (config_dir / "data" / "ledger.sqlite").is_file()
    emitted = json.loads(capsys.readouterr().out)
    assert Path(emitted["database"]) == (config_dir / "data" / "ledger.sqlite").resolve()


def test_relative_cli_override_keeps_working_directory_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    working = tmp_path / "working"
    working.mkdir()
    config_dir = tmp_path / "private-config"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"
    config_file.write_text("ledger_db: configured.sqlite\n", encoding="utf-8")
    monkeypatch.chdir(working)

    assert main(["--config", str(config_file), "db", "init", "--db", "explicit.sqlite"]) == 0

    assert (working / "explicit.sqlite").is_file()
    assert not (config_dir / "configured.sqlite").exists()


def test_absolute_env_root_disables_legacy_fallback(tmp_path: Path) -> None:
    project = tmp_path / "project"
    legacy = project / ".local" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("ledger_db: legacy.sqlite\n", encoding="utf-8")
    env_root = tmp_path / "private"

    resolved = resolve_private_paths(
        project_root=project,
        environ={"AUTONOMO_PRIVATE_ROOT": str(env_root)},
    )

    assert resolved.root == env_root.resolve()
    assert resolved.config_path is None
    assert resolved.config_source == "environment"


def test_relative_env_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PrivatePathError, match="must be an absolute path"):
        resolve_private_paths(
            project_root=tmp_path,
            environ={"AUTONOMO_PRIVATE_ROOT": "relative/private"},
        )


def test_default_config_precedes_legacy_config(tmp_path: Path) -> None:
    project = tmp_path / "project"
    legacy = project / ".local" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("ledger_db: legacy.sqlite\n", encoding="utf-8")
    local_app_data = tmp_path / "app-data"
    default_config = local_app_data / "spain-autonomo-taxes" / "config.yaml"
    default_config.parent.mkdir(parents=True)
    default_config.write_text("ledger_db: current.sqlite\n", encoding="utf-8")

    resolved = resolve_private_paths(
        project_root=project,
        environ={"LOCALAPPDATA": str(local_app_data)},
    )

    assert resolved.config_path == default_config.resolve()
    assert resolved.config_source == "default"


def test_legacy_config_warns_without_moving_or_changing_default_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    legacy = project / ".local" / "config.yaml"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("ledger_db: legacy.sqlite\n", encoding="utf-8")
    local_app_data = tmp_path / "app-data"

    with pytest.warns(LegacyPrivateConfigWarning, match="no files are moved automatically"):
        resolved = resolve_private_paths(
            project_root=project,
            environ={"LOCALAPPDATA": str(local_app_data)},
        )

    assert resolved.config_path == legacy.resolve()
    assert resolved.root == (local_app_data / "spain-autonomo-taxes").resolve()
    assert not (local_app_data / "spain-autonomo-taxes").exists()


def test_web_defaults_are_outside_project_and_config_paths_are_relative_to_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    config_file = private / "config.yaml"
    config_file.write_text(
        "ledger_db: db/ledger.sqlite\ninbox_root: intake\ndrive_evidence_dir: evidence-store\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "ignored"))

    config = load_config(project, config_path=config_file)

    assert config.database == (private / "db" / "ledger.sqlite").resolve()
    assert config.inbox_root == (private / "intake").resolve()
    assert config.archive_root == (private / "evidence-store").resolve()
    assert config.cache_root == (private / "cache" / "web" / "dashboard").resolve()
