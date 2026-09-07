from __future__ import annotations
from autonomo_test_support.paths import REPO_ROOT

import importlib.util
import json
from pathlib import Path
import tarfile

import pytest

from autonomo_taxes.ledger_db import initialize


def _module() -> object:
    path = REPO_ROOT / "scripts" / "backup_private_root.py"
    spec = importlib.util.spec_from_file_location("backup_private_root", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _restore_module() -> object:
    path = REPO_ROOT / "scripts" / "restore_private_root.py"
    spec = importlib.util.spec_from_file_location("restore_private_root", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_root_backup_includes_evidence_and_verified_sqlite_snapshot(tmp_path: Path) -> None:
    private_root = tmp_path / "private"
    private_root.mkdir()
    database = private_root / "autonomo.sqlite"
    with initialize(database):
        pass
    (private_root / "config.yaml").write_text("ledger_db: autonomo.sqlite\n", encoding="utf-8")
    evidence = private_root / "evidence" / "2026-Q3" / "invoice.pdf"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"invoice")
    cache = private_root / "cache" / "dashboard.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("discard", encoding="utf-8")
    browser = private_root / "browser" / "token.txt"
    browser.parent.mkdir(parents=True)
    browser.write_text("exclude", encoding="utf-8")

    module = _module()
    archive, manifest = module.create_backup(
        private_root=private_root,
        database=database,
        output_dir=private_root / "backups" / "private-root",
        keep=3,
    )

    assert archive.is_file()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["archive"] == archive.name
    with tarfile.open(archive, "r:gz") as tar:
        names = set(tar.getnames())
    assert {"autonomo.sqlite", "config.yaml", "evidence/2026-Q3/invoice.pdf"}.issubset(names)
    assert not any(name.startswith("cache/") or name.startswith("browser/") for name in names)

    restored = _restore_module().restore_backup(
        archive=archive,
        manifest=manifest,
        target_root=tmp_path / "restored",
    )
    assert (restored / "evidence" / "2026-Q3" / "invoice.pdf").read_bytes() == b"invoice"


def _archive_pair(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build one real backup pair and return it with its private root."""
    private_root = tmp_path / "private"
    private_root.mkdir()
    database = private_root / "autonomo.sqlite"
    with initialize(database):
        pass
    archive, manifest = _module().create_backup(
        private_root=private_root,
        database=database,
        output_dir=tmp_path / "out",
        keep=3,
    )
    return private_root, archive, manifest


@pytest.mark.parametrize("relative", ["restore-drill", "nested/restore-drill", "."])
def test_restore_refuses_a_target_inside_the_private_root(tmp_path: Path, relative: str) -> None:
    private_root, archive, manifest = _archive_pair(tmp_path)

    with pytest.raises(ValueError, match="must not be inside the private root"):
        _restore_module().restore_backup(
            archive=archive,
            manifest=manifest,
            target_root=private_root / relative,
            private_root=private_root,
        )

    assert not (private_root / "config.yaml").exists()


def test_restore_refuses_a_target_that_would_contain_the_private_root(tmp_path: Path) -> None:
    private_root, archive, manifest = _archive_pair(tmp_path)

    with pytest.raises(ValueError, match="must not contain the private root"):
        _restore_module().restore_backup(
            archive=archive,
            manifest=manifest,
            target_root=private_root.parent,
            private_root=private_root,
        )


def test_restore_allows_a_target_outside_the_private_root(tmp_path: Path) -> None:
    private_root, archive, manifest = _archive_pair(tmp_path)

    restored = _restore_module().restore_backup(
        archive=archive,
        manifest=manifest,
        target_root=tmp_path / "drill",
        private_root=private_root,
    )

    assert (restored / "autonomo.sqlite").is_file()


def test_restore_cli_reads_the_private_root_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_root, archive, manifest = _archive_pair(tmp_path)
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(private_root))

    with pytest.raises(ValueError, match="must not be inside the private root"):
        _restore_module().main(
            [
                "--archive",
                str(archive),
                "--manifest",
                str(manifest),
                "--target-root",
                str(private_root / "restore-drill"),
            ]
        )
