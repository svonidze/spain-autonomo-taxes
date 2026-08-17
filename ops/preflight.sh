#!/usr/bin/env bash
# Read-only checks required before a release can replace the running service.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ops/lib.sh
source "$script_dir/lib.sh"

require_command python3
root="$(release_root)"
data="$(private_root)"
require_absolute_directory "$root"
require_absolute_directory "$data"
[[ -d "$data" ]] || die "private root does not exist: $data"
[[ -f "$data/autonomo.sqlite" ]] || die "database does not exist: $data/autonomo.sqlite"
[[ -f "$data/config.yaml" ]] || die "private config does not exist: $data/config.yaml"
[[ ! -L "$data/autonomo.sqlite" ]] || die "database must not be a symlink"

python3 - "$data/autonomo.sqlite" <<'PY'
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1])
with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("SQLite integrity_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise SystemExit("SQLite foreign_key_check failed")
PY

if [[ -n "${AUTONOMO_MIGRATE_COMMAND:-}" ]]; then
  note "migration command is configured"
else
  note "no AUTONOMO_MIGRATE_COMMAND configured; deploy will perform no schema migration"
fi
note "preflight passed"
