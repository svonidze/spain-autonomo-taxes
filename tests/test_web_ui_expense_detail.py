from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


def test_expense_detail_ui_behaviors_run_under_node() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the expense detail UI checks")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, str(root / "tests" / "test_web_ui_expense_detail.js")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
