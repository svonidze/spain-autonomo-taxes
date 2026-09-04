"""Run the node-based checks for the taxes-page summary helpers."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_web_ui_taxes_summary_behaves_deterministically() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed in this environment")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, str(root / "tests" / "test_web_ui_taxes.js")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert '"ok":true' in result.stdout.replace(" ", "")
