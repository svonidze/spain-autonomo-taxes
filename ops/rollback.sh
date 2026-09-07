#!/usr/bin/env bash
# Move the active release back to an already installed immutable SHA.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/lib.sh"
[[ $# -eq 1 ]] || { echo "usage: $0 <installed-full-git-sha>" >&2; exit 2; }
target_sha="$1"; validate_sha "$target_sha"
require_command python3; require_command systemctl
load_runtime_env
with_lock
root="$(release_root)"; target="$(release_path "$target_sha")"
[[ -f "$target/.release-sha" && "$(<"$target/.release-sha")" == "$target_sha" ]] || die "release is not installed: $target_sha"
previous="$(current_release_sha)"
[[ "$target_sha" != "$previous" ]] || die "rollback target is already active: $target_sha"
target_schema="$(<"$target/.schema-version")"
data="$(private_root)"
live_schema="$(python3 - "$data/autonomo.sqlite" <<'PY'
import sqlite3
import sys
with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as db:
    print(db.execute("PRAGMA user_version").fetchone()[0])
PY
)"
snapshot="${AUTONOMO_ROLLBACK_SNAPSHOT:-}"
if (( target_schema < live_schema )); then
  [[ -n "$snapshot" ]] || die "cross-schema rollback requires AUTONOMO_ROLLBACK_SNAPSHOT"
fi
if [[ -n "$snapshot" ]]; then
  require_private_file "$snapshot" "rollback snapshot"
fi

snapshot_tool="$script_dir/backup_sqlite.py"
[[ -f "$snapshot_tool" ]] || snapshot_tool="$script_dir/backup/backup_sqlite.py"
snapshot_output="$(python3 "$snapshot_tool" --database "$data/autonomo.sqlite" --backup-dir "$data/backups/rollback" --keep "${AUTONOMO_ROLLBACK_BACKUP_KEEP:-10}")"
current_snapshot="$(printf '%s\n' "$snapshot_output" | sed -n 's/^backup=//p')"
[[ -f "$current_snapshot" ]] || die "rollback safety backup did not complete"

restore_previous() {
  systemctl --user stop "autonomo-web-$target_sha.service" >/dev/null 2>&1 || true
  install -m 600 "$current_snapshot" "$data/autonomo.sqlite"
  ln -sfn "releases/$previous" "$root/current.previous"
  mv -Tf "$root/current.previous" "$root/current"
  systemctl --user start "autonomo-web-$previous.service" >/dev/null 2>&1 || true
}

systemctl --user daemon-reload
systemctl --user stop "autonomo-web-$previous.service"
if [[ -n "$snapshot" ]]; then install -m 600 "$snapshot" "$data/autonomo.sqlite"; fi
if ! systemctl --user enable --now "autonomo-web-$target_sha.service"; then
  restore_previous
  die "rollback target did not start; previous deployment was restored"
fi
if ! systemctl --user is-active --quiet "autonomo-web-$target_sha.service"; then
  restore_previous
  die "rollback target is not active; previous deployment was restored"
fi
if [[ -n "${AUTONOMO_HEALTHCHECK_URL:-}" ]] \
  && ! "$target/.venv/bin/python" "$script_dir/healthcheck.py" "$AUTONOMO_HEALTHCHECK_URL"; then
  restore_previous
  die "rollback target failed the external health check; previous deployment was restored"
fi
if ! { ln -sfn "releases/$target_sha" "$root/current.new" && mv -Tf "$root/current.new" "$root/current"; }; then
  restore_previous
  die "rollback release link update failed; previous deployment was restored"
fi
systemctl --user disable "autonomo-web-$previous.service" || true
printf 'rolled_back_to=%s\n' "$target_sha"
