from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3


SCRIPT = Path(__file__).parents[1] / "scripts" / "backup_sqlite.py"
SPEC = importlib.util.spec_from_file_location("backup_sqlite", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_backup_database_validates_and_rotates(tmp_path: Path) -> None:
    database = tmp_path / "autonomo.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("CREATE TABLE values_table (value TEXT NOT NULL)")
        connection.execute("INSERT INTO values_table VALUES ('preserved')")

    backup_dir = tmp_path / "backups"
    first = MODULE.backup_database(database, backup_dir, keep=1)
    second = MODULE.backup_database(database, backup_dir, keep=1)

    assert not first.exists()
    assert second.is_file()
    assert list(backup_dir.glob("autonomo-*.sqlite")) == [second]
    with sqlite3.connect(f"file:{second.as_posix()}?mode=ro", uri=True) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM values_table").fetchone()[0] == "preserved"


def test_backup_database_rejects_invalid_retention(tmp_path: Path) -> None:
    database = tmp_path / "autonomo.sqlite"
    database.touch()

    try:
        MODULE.backup_database(database, tmp_path / "backups", keep=0)
    except ValueError as exc:
        assert str(exc) == "keep must be at least 1"
    else:
        raise AssertionError("expected invalid retention to fail")
