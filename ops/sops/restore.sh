#!/usr/bin/env bash
# Explicitly restore one validated SQLite snapshot. Existing data is retained beside it.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ops_dir="$(cd -- "$script_dir/.." && pwd)"
source "$script_dir/lib.sh"
if [[ $# -eq 4 && "$1" == "--yes-restore-archive" ]]; then
  archive="$2"; manifest="$3"; target_root="$4"
  [[ -f "$archive" && -f "$manifest" ]] || die "archive and manifest must exist"
  restore_tool="$ops_dir/restore_private_root.py"
  [[ -f "$restore_tool" ]] || restore_tool="$ops_dir/../scripts/restore_private_root.py"
  python3 "$restore_tool" \
    --archive "$archive" --manifest "$manifest" --target-root "$target_root"
  exit 0
fi
[[ $# -eq 2 && "$1" == "--yes-restore" ]] || {
  echo "usage: $0 --yes-restore <backup.sqlite> | --yes-restore-archive <archive.tar.gz> <manifest.json> <empty-target-root>" >&2
  exit 2
}
backup="$2"; [[ -f "$backup" ]] || die "backup does not exist: $backup"
require_command python3
load_bootstrap_env
require_private_file "$(bootstrap_env_path)" "ops bootstrap environment"
require_command systemctl; with_lock
data="$(private_root)"; require_absolute_directory "$data"
python3 - "$backup" <<'PY'
import sqlite3, sys
with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as db:
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise SystemExit("backup integrity_check failed")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None: raise SystemExit("backup foreign_key_check failed")
PY
sha="$(current_release_sha)"
secret_sha="$(release_secret_config_sha "$sha")"
unit="$(current_deployment_unit)"
previous_secret="$(current_secret_config_sha 2>/dev/null || true)"
"$script_dir/config-sync.sh" --stage-only "$secret_sha"
"$script_dir/preflight.sh" "$secret_sha"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
preserved="$data/autonomo.sqlite.before-restore-$stamp"
failed_restore="$data/autonomo.sqlite.failed-restore-$stamp"
systemctl --user stop "$unit"
if ! activate_secret_config "$secret_sha"; then
  systemctl --user start "$unit" || true
  die "restore secret config activation failed; service was restarted without changing the database"
fi
[[ ! -e "$data/autonomo.sqlite" ]] || mv "$data/autonomo.sqlite" "$preserved"
install -m 600 "$backup" "$data/autonomo.sqlite"
restore_previous() {
  systemctl --user stop "$unit" >/dev/null 2>&1 || true
  if [[ -f "$data/autonomo.sqlite" ]]; then mv "$data/autonomo.sqlite" "$failed_restore"; fi
  if [[ -f "$preserved" ]]; then mv "$preserved" "$data/autonomo.sqlite"; fi
  restore_secret_config "$previous_secret" >/dev/null 2>&1 || true
  systemctl --user start "$unit" || true
}
if ! systemctl --user start "$unit"; then
  restore_previous
  die "restored database did not start; previous database and config were restored"
fi
if ! systemctl --user is-active --quiet "$unit"; then
  systemctl --user stop "$unit" >/dev/null 2>&1 || true
  restore_previous
  die "restored database service is not active; previous database and config were restored"
fi
if [[ -n "${AUTONOMO_HEALTHCHECK_URL:-}" ]] \
  && ! "$(release_path "$sha")/.venv/bin/python" "$ops_dir/healthcheck.py" "$AUTONOMO_HEALTHCHECK_URL"; then
  systemctl --user stop "$unit" >/dev/null 2>&1 || true
  restore_previous
  die "restored database failed the external health check; previous database and config were restored"
fi
printf 'restored=%s\n' "$backup"
