#!/usr/bin/env python3
"""Check the local OCR dependency without reading accounting documents."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys


class OcrReadinessError(ValueError):
    pass


def runtime_environment(path: Path | None) -> dict[str, str]:
    if path is None:
        return dict(os.environ)
    environment: dict[str, str] = {}
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise OcrReadinessError("runtime file must be owned by this user with mode 0600, without links")
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise OcrReadinessError("runtime file could not be read") from exc
    for number, line in enumerate(lines, 1):
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
        if (
            match is None
            or match[2] != match[2].strip()
            or any(character in match[2] for character in ("'", '"', "\\", "\x00"))
        ):
            raise OcrReadinessError(f"runtime file has an invalid plain assignment at line {number}")
        # Do not inherit operator-only OCR settings such as TESSDATA_PREFIX.
        # Never source, expand or print this file: it may contain secrets.
        environment[match[1]] = match[2]
    if not environment.get("PATH"):
        raise OcrReadinessError("runtime file must declare a non-empty PATH")
    return environment


def check_readiness(environment: dict[str, str], *, timeout_seconds: float) -> None:
    executable = shutil.which("tesseract", path=environment.get("PATH", os.defpath))
    if executable is None:
        raise OcrReadinessError("tesseract was not found in the runtime PATH; install tesseract-ocr")
    for option in ("--version", "--list-langs"):
        try:
            result = subprocess.run(
                [executable, option],
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrReadinessError(f"tesseract {option} timed out") from exc
        except OSError as exc:
            raise OcrReadinessError(f"tesseract {option} could not start") from exc
        if result.returncode != 0:
            raise OcrReadinessError(f"tesseract {option} failed (exit {result.returncode})")
        if option == "--list-langs" and "eng" not in result.stdout.splitlines():
            raise OcrReadinessError("eng language data is unavailable; install tesseract-ocr-eng")


def positive_timeout(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number")
    return seconds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-env", type=Path)
    parser.add_argument("--timeout-seconds", type=positive_timeout, default=10.0)
    args = parser.parse_args()
    try:
        check_readiness(runtime_environment(args.runtime_env), timeout_seconds=args.timeout_seconds)
    except OcrReadinessError as exc:
        print(f"error: OCR readiness: {exc}", file=sys.stderr)
        return 1
    print("ocr=ready language=eng")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
