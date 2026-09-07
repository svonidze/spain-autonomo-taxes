"""Exercise the browser suite against a wheel installed outside the checkout."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv
import zipfile


def checked(args: list[str], **kwargs) -> None:
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="autonomo-wheel-test-") as directory:
        temporary = Path(directory)
        wheels = temporary / "wheels"
        checked([sys.executable, "-m", "build", "--wheel", "--outdir", str(wheels)], cwd=root / "backend")
        wheel, = wheels.glob("*.whl")
        with zipfile.ZipFile(wheel) as archive:
            prefix = "autonomo_taxes/web_ui/dist/"
            manifest = json.loads(archive.read(prefix + "build-manifest.json"))
            expected = {prefix + name for name in manifest["files"]} | {prefix + "build-manifest.json"}
            actual = {name for name in archive.namelist() if name.startswith("autonomo_taxes/web_ui/")}
            if actual != expected:
                raise RuntimeError("Wheel UI inventory differs from its build (missing, stale or source files)")
        environment = temporary / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        bin_directory = environment / ("Scripts" if os.name == "nt" else "bin")
        python = bin_directory / ("python.exe" if os.name == "nt" else "python")
        checked([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--quiet", str(wheel)], cwd=temporary)
        env = dict(os.environ, PATH=str(bin_directory) + os.pathsep + os.environ["PATH"],
                   PYTHON=str(python), AUTONOMO_BROWSER_INSTALLED="1")
        env.pop("PYTHONPATH", None)
        checked(["autonomo-tax", "--help"], cwd=temporary, env=env)
        checked(["autonomo-web", "--help"], cwd=temporary, env=env)
        checked([str(python), "-I", str(root / "tests/support/verify_cli_bridge.py")], cwd=temporary, env=env)
        node = os.environ.get("NODE") or "node"
        cli = root / "frontend/node_modules/@playwright/test/cli.js"
        subprocess.run([node, str(cli), "test", "--config", str(root / "frontend/playwright.config.mts")], cwd=root, env=env, check=True)
        print("Isolated installed-wheel browser verification passed")


if __name__ == "__main__":
    main()
