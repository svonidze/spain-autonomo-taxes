#!/usr/bin/env python3
"""Keep existing pre-commit hooks working after developer tools moved."""
from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "dev/privacy_guard.py"), run_name="__main__")
