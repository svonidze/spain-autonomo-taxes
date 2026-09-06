import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest


def project(root: Path, *, corrupt: bool = False, missing: bool = False) -> None:
    source = Path(__file__).resolve().parents[1]
    (root / "setup.py").write_text((source / "setup.py").read_text())
    (root / "pyproject.toml").write_text((source / "pyproject.toml").read_text())
    (root / "README.md").write_text("Synthetic packaging fixture")
    package = root / "src/autonomo_taxes"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    if missing:
        return
    dist = package / "web_ui/dist"
    (dist / "ui-assets").mkdir(parents=True)
    files = {"index.html": b'<script src="/ui-assets/current.js"></script>', "ui-assets/current.js": b"export {};"}
    for name, body in files.items():
        (dist / name).write_bytes(body)
    checks = {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}
    if corrupt:
        checks["ui-assets/current.js"] = "0" * 64
    (dist / "build-manifest.json").write_text(json.dumps({"contract": 1, "files": checks}))


@pytest.mark.parametrize("missing", [True, False])
def test_distributable_refuses_missing_or_corrupt_frontend(tmp_path, missing):
    project(tmp_path, missing=missing, corrupt=not missing)
    result = subprocess.run([sys.executable, "-m", "build", "--wheel"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Run npm ci --include=dev and npm run build" in result.stderr + result.stdout
    assert not list((tmp_path / "dist").glob("*.whl"))


def test_wheel_does_not_retain_an_obsolete_cached_bundle(tmp_path):
    project(tmp_path)
    cached = tmp_path / "build/lib/autonomo_taxes/web_ui/dist/ui-assets"
    cached.mkdir(parents=True)
    (cached / "obsolete.js").write_text("old code")
    result = subprocess.run([sys.executable, "-m", "build", "--wheel"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    wheel, = (tmp_path / "dist").glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        resources = [name for name in archive.namelist() if "/web_ui/" in name]
        assert set(resources) == {"autonomo_taxes/web_ui/dist/" + name for name in ("index.html", "build-manifest.json", "ui-assets/current.js")}
