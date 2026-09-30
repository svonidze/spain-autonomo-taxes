from autonomo_test_support.paths import REPO_ROOT
import hashlib
import json
from pathlib import Path

import pytest

from autonomo_taxes.ui_assets import UiAssets


def fixture(root: Path) -> Path:
    dist = root / "dist"
    (dist / "ui-assets").mkdir(parents=True)
    files = {"index.html": b'<script type="module" src="/ui-assets/app-123.js"></script>', "ui-assets/app-123.js": b'export {};'}
    for name, body in files.items():
        (dist / name).write_bytes(body)
    (dist / "build-manifest.json").write_text(json.dumps({"contract": 1, "files": {name: hashlib.sha256(body).hexdigest() for name, body in files.items()}}))
    return dist


def test_manifest_is_an_allowlist_and_detects_missing_corrupt_builds(tmp_path):
    dist = fixture(tmp_path)
    assets = UiAssets(tmp_path)
    assert assets.path("index.html") == dist / "index.html"
    for name in ("build-manifest.json", "../outside.js", "ui-assets/missing.js", "ui-assets/app-123.js.map"):
        with pytest.raises(FileNotFoundError):
            assets.path(name)
    (dist / "ui-assets/app-123.js").write_text("changed")
    with pytest.raises(RuntimeError, match="does not match"):
        assets.verify()
    (dist / "build-manifest.json").unlink()
    with pytest.raises(RuntimeError, match="build is missing"):
        UiAssets(tmp_path)


def test_build_cannot_serve_symlink_escape(tmp_path):
    dist = fixture(tmp_path)
    outside = tmp_path / "outside.js"
    outside.write_bytes((dist / "ui-assets/app-123.js").read_bytes())
    (dist / "ui-assets/app-123.js").unlink()
    (dist / "ui-assets/app-123.js").symlink_to(outside)
    with pytest.raises(FileNotFoundError, match="Invalid frontend"):
        UiAssets(tmp_path)


def test_packaged_resource_contract_is_complete():
    assets = UiAssets(REPO_ROOT / "backend/src/autonomo_taxes/web_ui")
    assert assets.manifest["source_sha"]
    assert any(name.endswith(".css") for name in assets.files)
    assert all(name == "index.html" or name.startswith("ui-assets/") for name in assets.files)


def test_pseudolocale_requires_the_explicit_synthetic_test_host(tmp_path):
    dist = fixture(tmp_path)
    path = dist / "build-manifest.json"
    manifest = json.loads(path.read_text())
    manifest["test_only"] = True
    path.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="test-only"):
        UiAssets(tmp_path)
    assert UiAssets(tmp_path, allow_test=True).files
