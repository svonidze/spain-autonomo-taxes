#!/usr/bin/env bash
# Move the active release back to an already installed immutable SHA.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/lib.sh"
[[ $# -eq 1 ]] || { echo "usage: $0 <installed-full-git-sha>" >&2; exit 2; }
target_sha="$1"; validate_sha "$target_sha"
require_command systemctl; with_lock
root="$(release_root)"; target="$(release_path "$target_sha")"
[[ -f "$target/.release-sha" && "$(<"$target/.release-sha")" == "$target_sha" ]] || die "release is not installed: $target_sha"
previous="$(current_release_sha)"
current_release="$(release_path "$previous")"
current_schema="$(<"$current_release/.schema-version")"
target_schema="$(<"$target/.schema-version")"
data="$(private_root)"
live_schema="$(python3 - "$data/autonomo.sqlite" <<'PY'
import sqlite3
import sys
with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as db:
    print(db.execute("PRAGMA user_version").fetchone()[0])
PY
)"
if (( target_schema < live_schema )); then
  [[ -n "${AUTONOMO_ROLLBACK_SNAPSHOT:-}" ]] \
    || die "cross-schema rollback requires AUTONOMO_ROLLBACK_SNAPSHOT"
fi
systemctl --user daemon-reload
snapshot_tool="$script_dir/backup_sqlite.py"
[[ -f "$snapshot_tool" ]] || snapshot_tool="$script_dir/../scripts/backup_sqlite.py"
snapshot_output="$(python3 "$snapshot_tool" --database "$data/autonomo.sqlite" --backup-dir "$data/backups/rollback" --keep "${AUTONOMO_ROLLBACK_BACKUP_KEEP:-10}")"
current_snapshot="$(printf '%s\n' "$snapshot_output" | sed -n 's/^backup=//p')"
[[ -f "$current_snapshot" ]] || die "rollback safety backup did not complete"
systemctl --user stop "autonomo-web-$previous.service" || true
if [[ -n "${AUTONOMO_ROLLBACK_SNAPSHOT:-}" ]]; then
  snapshot="$AUTONOMO_ROLLBACK_SNAPSHOT"
  [[ -f "$snapshot" ]] || die "AUTONOMO_ROLLBACK_SNAPSHOT does not exist: $snapshot"
  install -m 600 "$snapshot" "$(private_root)/autonomo.sqlite"
fi
if ! systemctl --user enable --now "autonomo-web-$target_sha.service"; then
  install -m 600 "$current_snapshot" "$data/autonomo.sqlite"
  systemctl --user start "autonomo-web-$previous.service" || true
  die "rollback target did not start; current link was not changed"
fi
ln -sfn "releases/$target_sha" "$root/current.new"; mv -Tf "$root/current.new" "$root/current"
systemctl --user disable "autonomo-web-$previous.service" || true
systemctl --user is-active --quiet "autonomo-web-$target_sha.service"
printf 'rolled_back_to=%s\n' "$target_sha"
