"""Prove the installed core works with no Node, UI distribution or web process."""

from pathlib import Path
import json
import shutil
import os
import subprocess
import sys
import tempfile
import venv
import zipfile


def run(args, *, cwd, env):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="autonomo-core-test-") as directory:
        scratch = Path(directory)
        tools = scratch / "tools"
        tools.mkdir()
        for name in ("python", "python3"):
            (tools / name).symlink_to(sys.executable)
        env = dict(
            os.environ, PATH=str(tools), AUTONOMO_PRIVATE_ROOT=str(scratch / "private")
        )
        env.pop("PYTHONPATH", None)
        assert not any((tools / name).exists() for name in ("node", "npm"))
        run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(scratch / "wheels"),
                str(root),
            ],
            cwd=scratch,
            env=env,
        )
        (wheel,) = (scratch / "wheels").glob("*.whl")
        with zipfile.ZipFile(wheel) as archive:
            assert not any(
                "web_ui/" in name
                or "autonomo_taxes_ui/" in name
                or name.endswith((".js", ".css", ".html"))
                for name in archive.namelist()
            )
            entry = next(
                name for name in archive.namelist() if name.endswith("entry_points.txt")
            )
            assert b"autonomo-tax" in archive.read(
                entry
            ) and b"autonomo-web" not in archive.read(entry)
        environment = scratch / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                str(wheel),
            ],
            cwd=scratch,
            env=env,
        )
        code = """
import importlib.abc, importlib.util, json, sys
assert importlib.util.find_spec('autonomo_taxes_ui') is None
class CoreOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'autonomo_taxes.local_web','autonomo_taxes.ui_assets','autonomo_taxes_ui'}:
            raise RuntimeError('Unexpected UI dependency: '+fullname)
sys.meta_path.insert(0,CoreOnly())
from autonomo_taxes.cli import main
raise SystemExit(main(sys.argv[1:]))
"""
        database = scratch / "private" / "autonomo.sqlite"
        database.parent.mkdir(mode=0o700)
        run(
            [str(python), "-I", "-c", code, "db", "init", "--db", str(database)],
            cwd=scratch,
            env=env,
        )
        result = json.loads(
            run(
                [str(python), "-I", "-c", code, "db", "status", "--db", str(database)],
                cwd=scratch,
                env=env,
            )
        )
        assert result["schema_version"] > 0
        launcher = python.parent / (
            "autonomo-tax.exe" if os.name == "nt" else "autonomo-tax"
        )
        run([str(launcher), "--help"], cwd=scratch, env=env)
        installed_status = json.loads(
            run(
                [str(launcher), "db", "status", "--db", str(database)],
                cwd=scratch,
                env=env,
            )
        )
        assert installed_status["schema_version"] == result["schema_version"]
        assert not (python.parent / "autonomo-web").exists()
        # Execute the maintained guide against the installed wheel, not checkout imports.
        guide = scratch / "agent_workflow.py"
        shutil.copyfile(root / "docs/examples/agent_workflow.py", guide)
        guard = code[: code.index("from autonomo_taxes.cli import main")]
        run(
            [
                str(python),
                "-I",
                "-c",
                guard
                + "\nimport runpy; runpy.run_path(sys.argv[1], run_name='__main__')",
                str(guide),
            ],
            cwd=scratch,
            env=env,
        )
        # Synthetic provider/clock fixtures exercise verified USD FX and later-due
        # depreciation without network, a running HTTP server or source-package imports.
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "pytest",
            ],
            cwd=scratch,
            env=env,
        )
        tests = scratch / "tests"
        tests.mkdir()
        for source in (root / "tests").glob("test_*.py"):
            shutil.copyfile(source, tests / source.name)
        configuration = scratch / "pytest.ini"
        configuration.write_text("[pytest]\n")
        selected = [
            str(tests / name)
            for name in (
                "test_toolkit_cli.py",
                "test_toolkit_expense_cli.py",
                "test_toolkit_remaining_cli.py",
            )
        ]
        result = run(
            [
                str(python),
                "-I",
                "-c",
                guard + "\nimport pytest; raise SystemExit(pytest.main(sys.argv[1:]))",
                "-c",
                str(configuration),
                "-q",
                *selected,
            ],
            cwd=scratch,
            env=env,
        )
        print(result.strip())
        print(
            "Core-only wheel: synthetic guide, verified FX, expense, later depreciation, retries and settings passed; no Node/npm/UI or web imports"
        )


if __name__ == "__main__":
    main()
