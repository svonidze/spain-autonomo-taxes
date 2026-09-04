#!/usr/bin/env bash
# Read-only checks required before a release can replace the running service.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ops/lib.sh
source "$script_dir/lib.sh"

require_command python3
load_runtime_env
root="$(release_root)"
data="$(private_root)"
require_absolute_directory "$root"
require_absolute_directory "$data"
[[ -d "$data" ]] || die "private root does not exist: $data"
[[ -f "$data/autonomo.sqlite" ]] || die "database does not exist: $data/autonomo.sqlite"
[[ -f "$data/config.yaml" ]] || die "private config does not exist: $data/config.yaml"
require_private_file "$data/autonomo.sqlite" "database"
require_private_file "$data/config.yaml" "private config"
if [[ -e "$data/account-backup-settings.json" || -L "$data/account-backup-settings.json" ]]; then
  [[ -f "$script_dir/backup_settings.py" ]] || die "installed backup settings reader is missing"
  python3 "$script_dir/backup_settings.py" --private-root "$data"
fi
if [[ -n "${AUTONOMO_RCLONE_CONFIG:-}" ]]; then
  require_private_file "$AUTONOMO_RCLONE_CONFIG" "rclone config"
fi
if [[ -n "${AUTONOMO_SESSION_PRINCIPAL_SECRET_FILE:-}" ]]; then
  require_private_file "$AUTONOMO_SESSION_PRINCIPAL_SECRET_FILE" "session principal secret"
fi

python3 - "$data/autonomo.sqlite" <<'PY'
import os
import sqlite3
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("SQLite integrity_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise SystemExit("SQLite foreign_key_check failed")
    has_storage_backends = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'storage_backends'"
    ).fetchone()
    credential_refs = (
        connection.execute(
            "SELECT credential_ref FROM storage_backends "
            "WHERE enabled = 1 AND credential_ref LIKE 'file:%'"
        ).fetchall()
        if has_storage_backends
        else []
    )
for (credential_ref,) in credential_refs:
    credential = Path(str(credential_ref).removeprefix("file:"))
    try:
        info = credential.lstat()
    except FileNotFoundError as exc:
        raise SystemExit(f"storage credential does not exist: {credential}") from exc
    if credential.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise SystemExit(f"storage credential must be a regular non-symlink file: {credential}")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise SystemExit(f"storage credential must be owned by the service user with mode 0600: {credential}")
PY

if [[ -n "${AUTONOMO_MIGRATE_COMMAND:-}" ]]; then
  note "migration command is configured"
else
  note "no AUTONOMO_MIGRATE_COMMAND configured; deploy will perform no schema migration"
fi
note "preflight passed"
