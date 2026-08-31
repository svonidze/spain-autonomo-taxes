"""Private backup policy reader, also installed standalone in the ops directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

SETTINGS_FILENAME = "account-backup-settings.json"
MAX_SETTINGS_BYTES = 16 * 1024
LIMITS = {"daily_keep": (7, 365), "monthly_keep": (3, 120)}


class BackupSettingsError(ValueError):
    """Only a fixed, non-sensitive message may cross the ops/web boundary."""

    def __init__(self) -> None:
        super().__init__("Backup settings are invalid or unavailable; operator repair is required.")


def validate_policy(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {"format", *LIMITS}:
        raise BackupSettingsError()
    if type(value["format"]) is not int or value["format"] != 1:
        raise BackupSettingsError()
    for key, (minimum, maximum) in LIMITS.items():
        count = value[key]
        if count is not None and (type(count) is not int or not minimum <= count <= maximum):
            raise BackupSettingsError()
    return dict(value)


def read_private_bytes(path: Path, *, private_mode: bool = False) -> bytes:
    """Refuse links, devices and oversized files before reading untrusted contents."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise BackupSettingsError()
    if private_mode and os.name != "nt" and (
        stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid()
    ):
        raise BackupSettingsError()
    if info.st_size > MAX_SETTINGS_BYTES:
        raise BackupSettingsError()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            raise BackupSettingsError()
        data = stream.read(MAX_SETTINGS_BYTES + 1)
    if len(data) > MAX_SETTINGS_BYTES:
        raise BackupSettingsError()
    return data


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise BackupSettingsError()
        result[key] = value
    return result


def read_policy(private_root: Path) -> tuple[dict, str]:
    path = Path(private_root) / SETTINGS_FILENAME
    try:
        data = read_private_bytes(path, private_mode=True)
    except FileNotFoundError:
        # A dangling symlink is never equivalent to an absent policy.
        if path.is_symlink():
            raise BackupSettingsError() from None
        return {"format": 1, "daily_keep": None, "monthly_keep": None}, "missing"
    except (OSError, ValueError):
        raise BackupSettingsError() from None
    try:
        value = json.loads(data, object_pairs_hook=_unique_object)
        return validate_policy(value), hashlib.sha256(data).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise BackupSettingsError() from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate private account backup policy")
    parser.add_argument("--private-root", type=Path, required=True)
    parser.add_argument("--backup-class", choices=("daily", "monthly"))
    parser.add_argument("--fallback")
    args = parser.parse_args(argv)
    try:
        policy, _ = read_policy(args.private_root)
        if args.backup_class:
            count = policy[f"{args.backup_class}_keep"]
            if count is None:
                if not args.fallback or not args.fallback.isascii() or not args.fallback.isdigit():
                    raise BackupSettingsError()
                count = int(args.fallback)
                if count < 1:
                    raise BackupSettingsError()
            print(count)
    except (BackupSettingsError, OSError, ValueError):
        print(str(BackupSettingsError()), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
