from pathlib import Path
import subprocess


def test_contact_page_and_menu_behavior():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["node", str(root / "tests/test_web_ui_contacts.js")], cwd=root, check=True)
