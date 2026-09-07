"""Repository paths shared by relocated tests."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]

FIXTURE_ROOT = REPO_ROOT / "tests/fixtures"
