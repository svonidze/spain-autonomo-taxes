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
require_command python3; require_command systemctl
load_runtime_env
with_lock
data="$(private_root)"; require_absolute_directory "$data"
python3 - "$backup" <<'PY'
import sqlite3
import sys
with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as db:
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("backup integrity_check failed")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise SystemExit("backup foreign_key_check failed")
PY
sha="$(current_release_sha)"
unit="autonomo-web-$sha.service"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
preserved="$data/autonomo.sqlite.before-restore-$stamp"
failed_restore="$data/autonomo.sqlite.failed-restore-$stamp"

restore_previous() {
  systemctl --user stop "$unit" >/dev/null 2>&1 || true
  if [[ -f "$data/autonomo.sqlite" ]]; then mv "$data/autonomo.sqlite" "$failed_restore"; fi
  if [[ -f "$preserved" ]]; then mv "$preserved" "$data/autonomo.sqlite"; fi
  systemctl --user start "$unit" >/dev/null 2>&1 || true
}

systemctl --user stop "$unit"
if [[ -e "$data/autonomo.sqlite" ]]; then
  if ! mv "$data/autonomo.sqlite" "$preserved"; then
    systemctl --user start "$unit" >/dev/null 2>&1 || true
    die "live database could not be preserved; service was restarted"
  fi
fi
if ! install -m 600 "$backup" "$data/autonomo.sqlite"; then
  restore_previous
  die "backup could not be installed; previous database was restored"
fi
if ! systemctl --user start "$unit"; then
  restore_previous
  die "restored database did not start; previous database was restored"
fi
if ! systemctl --user is-active --quiet "$unit"; then
  restore_previous
  die "restored database service is not active; previous database was restored"
fi
if [[ -n "${AUTONOMO_HEALTHCHECK_URL:-}" ]] \
  && ! "$(release_path "$sha")/.venv/bin/python" "$script_dir/healthcheck.py" "$AUTONOMO_HEALTHCHECK_URL"; then
  restore_previous
  die "restored database failed the external health check; previous database was restored"
fi
printf 'restored=%s\npreserved=%s\n' "$backup" "$preserved"
