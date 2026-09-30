"""Reusable synthetic fixtures shared by backend test suites."""

from __future__ import annotations
from dataclasses import replace
from datetime import date
import errno
import hashlib
from http.client import HTTPConnection
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import types
import pytest
import autonomo_taxes.local_web as local_web
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.local_web import (
    LocalAccountingApp,
    LocalAccountingServer,
    LocalWebApiError,
    LocalWebConfig,
    LocalWebError,
    _cli_error_detail,
    _host_header_parts,
    _review_summary,
    _store_upload,
    normalize_google_drive_url,
)
from autonomo_test_support.paths import REPO_ROOT
DRIVE_URL = "https://" + "drive.google.com"
DOCS_URL = "https://" + "docs.google.com"

def _config(
    tmp_path: Path,
    *,
    trusted_proxy_mode: str | None = None,
    allowed_tailscale_logins: tuple[str, ...] = (),
    read_only_document_roots: tuple[Path, ...] = (),
    legacy_path_map_file: Path | None = None,
) -> LocalWebConfig:
    static_root = (
        REPO_ROOT
        / "backend/src"
        / "autonomo_taxes"
        / "web_ui"
    )
    return LocalWebConfig(
        project_root=tmp_path,
        database=tmp_path / "autonomo.sqlite",
        inbox_root=tmp_path / "Inbox",
        archive_root=tmp_path / "Evidence",
        cache_root=tmp_path / "cache",
        static_root=static_root,
        trusted_proxy_mode=trusted_proxy_mode,
        allowed_tailscale_logins=allowed_tailscale_logins,
        read_only_document_roots=read_only_document_roots,
        legacy_path_map_file=legacy_path_map_file,
    )
