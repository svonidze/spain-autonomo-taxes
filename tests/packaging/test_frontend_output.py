"""A frontend build may clear only its generated output, never a linked directory."""
from pathlib import Path
import shutil
import subprocess

import pytest

from autonomo_test_support.paths import REPO_ROOT


def project(tmp_path):
    root = tmp_path / "repository"
    frontend = root / "frontend"
    (frontend / "scripts").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "frontend/vite.config.mts", frontend / "vite.config.mts")
    (frontend / "scripts/shell_locales.mts").write_text("export const localizeShell = (html: string) => html;\n")
    (frontend / "node_modules").symlink_to(REPO_ROOT / "frontend/node_modules", target_is_directory=True)
    (frontend / "clear.mts").write_text(
        'import config from "./vite.config.mts";\n'
        'const plugin = config.plugins.find(item => item.name === "clear-generated-ui-output");\n'
        'plugin.buildStart();\n'
    )
    package = root / "backend/src/autonomo_taxes"
    package.parent.mkdir(parents=True)
    return root, frontend, package


def clear(frontend):
    node = shutil.which("node")
    assert node is not None, "Frontend packaging tests require Node from frontend/.nvmrc"
    return subprocess.run([node, str(frontend / "clear.mts")], cwd=frontend,
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("redirect", ["package-inside", "package-outside", "web_ui", "dist"])
def test_output_cleanup_refuses_symlink_redirects(tmp_path, redirect):
    root, frontend, package = project(tmp_path)
    target = (root if redirect == "package-inside" else tmp_path) / "preserve"
    target.mkdir()
    if redirect.startswith("package"):
        package.symlink_to(target, target_is_directory=True)
        output = target / "web_ui/dist"
    elif redirect == "web_ui":
        package.mkdir()
        (package / "web_ui").symlink_to(target, target_is_directory=True)
        output = target / "dist"
    else:
        (package / "web_ui").mkdir(parents=True)
        (package / "web_ui/dist").symlink_to(target, target_is_directory=True)
        output = target
    output.mkdir(parents=True, exist_ok=True)
    marker = output / "preserve.txt"
    marker.write_text("Synthetic file must survive")
    result = clear(frontend)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Refusing" in result.stderr
    assert marker.read_text() == "Synthetic file must survive"


def test_output_cleanup_creates_only_the_generated_directory(tmp_path):
    _, frontend, package = project(tmp_path)
    package.mkdir()
    marker = package / "__init__.py"
    marker.write_text("# Synthetic package\n")
    for _ in range(2):
        result = clear(frontend)
        assert result.returncode == 0, result.stdout + result.stderr
        output = package / "web_ui/dist"
        assert output.is_dir() and list(output.iterdir()) == []
        (output / "stale.js").write_text("stale bundle")
    assert marker.read_text() == "# Synthetic package\n"
