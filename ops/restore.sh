#!/usr/bin/env bash
# Explicitly restore one validated SQLite snapshot. Existing data is retained beside it.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/lib.sh"
if [[ $# -eq 4 && "$1" == "--yes-restore-archive" ]]; then
  archive="$2"; manifest="$3"; target_root="$4"
  [[ -f "$archive" && -f "$manifest" ]] || die "archive and manifest must exist"
  python3 "$script_dir/restore_private_root.py" \
    --archive "$archive" --manifest "$manifest" --target-root "$target_root"
  exit 0
fi
[[ $# -eq 2 && "$1" == "--yes-restore" ]] || {
  echo "usage: $0 --yes-restore <backup.sqlite> | --yes-restore-archive <archive.tar.gz> <manifest.json> <empty-target-root>" >&2
  exit 2
}
backup="$2"; [[ -f "$backup" ]] || die "backup does not exist: $backup"
require_command python3; require_command systemctl; with_lock
data="$(private_root)"; require_absolute_directory "$data"
python3 - "$backup" <<'PY'
import sqlite3, sys
with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as db:
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise SystemExit("backup integrity_check failed")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None: raise SystemExit("backup foreign_key_check failed")
PY
sha="$(current_release_sha)"
systemctl --user stop "autonomo-web-$sha.service"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
[[ ! -e "$data/autonomo.sqlite" ]] || mv "$data/autonomo.sqlite" "$data/autonomo.sqlite.before-restore-$stamp"
install -m 600 "$backup" "$data/autonomo.sqlite"
systemctl --user start "autonomo-web-$sha.service"
systemctl --user is-active --quiet "autonomo-web-$sha.service"
printf 'restored=%s\n' "$backup"
