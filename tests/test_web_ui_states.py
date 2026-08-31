"""Run the node-based checks for the shared UI state helpers."""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tests" / "test_web_ui_states.js"


def _require_node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed in this environment")
    return node


def test_web_ui_state_helpers_behave_deterministically() -> None:
    node = _require_node()
    result = subprocess.run(
        [node, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert '"ok":true' in result.stdout.replace(" ", "")
