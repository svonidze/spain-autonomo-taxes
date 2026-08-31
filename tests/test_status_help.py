import subprocess
from pathlib import Path


def test_status_explanations_effective_scripts():
    subprocess.run(["node", str(Path(__file__).with_suffix(".js"))], check=True)
