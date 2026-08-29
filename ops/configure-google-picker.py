#!/usr/bin/env python3
"""Atomically install restricted Google Picker values in a private YAML config."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat

import yaml


APP_ID_RE = re.compile(r"[0-9]{6,20}")
DEVELOPER_KEY_RE = re.compile(r"[A-Za-z0-9_-]{20,128}")
PICKER_LINE_RE = re.compile(
    r"^(?:google_picker_developer_key|google_picker_app_id):.*(?:\n|$)",
    re.MULTILINE,
)


def _private_file(path: Path, label: str) -> bytes:
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError(f"{label} must be owned by the current user with mode 0600")
    if info.st_nlink != 1:
        raise ValueError(f"{label} must not have hard links")
    return path.read_bytes()


def _exclusive_write(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def configure(config: Path, developer_key_file: Path, app_id: str) -> dict[str, str]:
    if not APP_ID_RE.fullmatch(app_id):
        raise ValueError("app ID must be a numeric Google Cloud project number")
    original = _private_file(config, "private config")
    key = _private_file(developer_key_file, "developer key file").decode("utf-8").strip()
    if not DEVELOPER_KEY_RE.fullmatch(key):
        raise ValueError("developer key has an invalid format")
    text = original.decode("utf-8")
    loaded = yaml.safe_load(text) or {}
    if not isinstance(loaded, dict):
        raise ValueError("private config must contain a YAML mapping")
    retained = PICKER_LINE_RE.sub("", text).rstrip()
    updated = (
        retained
        + "\n"
        + f"google_picker_developer_key: {json.dumps(key)}\n"
        + f"google_picker_app_id: {json.dumps(app_id)}\n"
    )
    verified = yaml.safe_load(updated)
    if not isinstance(verified, dict):
        raise ValueError("updated private config must contain a YAML mapping")
    if verified.get("google_picker_developer_key") != key or str(
        verified.get("google_picker_app_id")
    ) != app_id:
        raise ValueError("updated Picker configuration did not validate")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = config.with_name(f"{config.name}.before-picker-{stamp}")
    temporary = config.with_name(f".{config.name}.picker-{os.getpid()}")
    _exclusive_write(backup, original)
    try:
        _exclusive_write(temporary, updated.encode("utf-8"))
        os.replace(temporary, config)
        directory = os.open(config.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {"config": str(config), "backup": str(backup), "app_id": app_id}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--developer-key-file", type=Path, required=True)
    parser.add_argument("--app-id", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = configure(
            Path(os.path.abspath(args.config.expanduser())),
            Path(os.path.abspath(args.developer_key_file.expanduser())),
            args.app_id.strip(),
        )
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
