#!/usr/bin/env python3
"""Install the repository privacy guard as a pre-commit hook.

Usage::

    python scripts/install_privacy_hook.py

An existing non-matching hook is preserved. Pass ``--force`` only after
reviewing that hook and deciding to replace it.
"""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


HOOK_CONTENT = """#!/bin/sh
repo_root=$(git rev-parse --show-toplevel) || exit 2
exec python "$repo_root/scripts/privacy_guard.py"
"""


def _git_path(repo: Path, name: str) -> Path:
    try:
        completed = subprocess.run(
            ["git", "-C", os.fspath(repo), "rev-parse", "--git-path", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            text=True,
            encoding="utf-8",
        )
    except OSError as exc:
        raise RuntimeError("unable to execute Git") from exc
    if completed.returncode != 0:
        raise RuntimeError("not a Git repository")
    result = Path(completed.stdout.strip())
    return result if result.is_absolute() else repo / result


def install(repo: Path, *, force: bool = False) -> Path:
    hook = _git_path(repo, "hooks") / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    if hook.exists():
        try:
            existing = hook.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError("unable to read existing pre-commit hook") from exc
        if existing == HOOK_CONTENT:
            return hook
        if not force:
            raise RuntimeError("a different pre-commit hook already exists; use --force to replace it")

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=hook.parent,
            prefix="privacy-hook-",
            delete=False,
        ) as temporary:
            temporary.write(HOOK_CONTENT)
            temporary_name = temporary.name
        os.chmod(temporary_name, os.stat(temporary_name).st_mode | stat.S_IXUSR)
        os.replace(temporary_name, hook)
    except OSError as exc:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
        raise RuntimeError("unable to install pre-commit hook") from exc
    return hook


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root")
    parser.add_argument("--force", action="store_true", help="replace a different existing hook")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        hook = install(args.repo, force=args.force)
    except RuntimeError as exc:
        print(f"privacy hook: ERROR ({exc})", file=sys.stderr)
        return 1
    print(f"privacy hook: installed at {hook}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
