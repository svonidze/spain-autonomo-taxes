from pathlib import Path
import subprocess


def test_counterparty_name_editor_behavior():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["node", str(root / "tests/test_web_ui_counterparty_names.js")], cwd=root, check=True)
