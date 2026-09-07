"""Refuse a distributable with a missing or stale frontend build."""
from pathlib import Path
import hashlib
import json
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithFrontend(build_py):
    def run(self):
        root = Path(__file__).parent / "src/autonomo_taxes/web_ui/dist"
        try:
            manifest = json.loads((root / "build-manifest.json").read_text())
            files = manifest["files"]
            if manifest["contract"] != 1 or not isinstance(files, dict) or "index.html" not in files:
                raise ValueError("Invalid frontend manifest")
            if manifest.get("test_only"):
                raise ValueError("Test-only frontend cannot be packaged")
            for name, checksum in files.items():
                target = (root / name).resolve()
                if not target.is_relative_to(root.resolve()) or not target.is_file():
                    raise ValueError("Invalid frontend resource path")
                if hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
                    raise ValueError("Invalid frontend resource checksum")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError("Run npm --prefix frontend ci --include=dev and npm --prefix frontend run build from the repository root before building the Python package") from exc
        # setuptools reuses build/lib; do not carry an earlier hashed bundle into
        # the next wheel. Only clear this command's generated UI copy.
        output = Path(self.build_lib) / "autonomo_taxes/web_ui"
        if output.resolve().is_relative_to((Path(__file__).parent / "src").resolve()):
            raise RuntimeError("build_lib must not overwrite the source tree")
        if output.exists():
            shutil.rmtree(output)
        super().run()


setup(cmdclass={"build_py": BuildWithFrontend})
