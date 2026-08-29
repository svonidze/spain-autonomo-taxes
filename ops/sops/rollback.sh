#!/usr/bin/env bash
# Move the active release back to an already installed immutable SHA.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ops_dir="$(cd -- "$script_dir/.." && pwd)"
source "$script_dir/lib.sh"
[[ $# -eq 1 ]] || { echo "usage: $0 <installed-full-git-sha>" >&2; exit 2; }
require_command python3
load_bootstrap_env
require_private_file "$(bootstrap_env_path)" "ops bootstrap environment"
target_sha="$1"; validate_sha "$target_sha"
require_command systemctl; with_lock
root="$(release_root)"; target="$(release_path "$target_sha")"
[[ -f "$target/.release-sha" && "$(<"$target/.release-sha")" == "$target_sha" ]] || die "release is not installed: $target_sha"
previous="$(current_release_sha)"
previous_secret="$(current_secret_config_sha 2>/dev/null || true)"
previous_unit="$(current_deployment_unit)"
target_secret="$(release_secret_config_sha "$target_sha")"
target_unit="$(deployment_unit_name "$target_sha" "$target_secret")"
"$script_dir/config-sync.sh" --stage-only "$target_secret"
"$script_dir/preflight.sh" "$target_secret"
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
snapshot="${AUTONOMO_ROLLBACK_SNAPSHOT:-}"
if [[ -n "$snapshot" ]]; then
  [[ -f "$snapshot" && ! -L "$snapshot" ]] \
    || die "AUTONOMO_ROLLBACK_SNAPSHOT does not exist or is unsafe: $snapshot"
fi
systemctl --user daemon-reload
snapshot_tool="$ops_dir/backup_sqlite.py"
[[ -f "$snapshot_tool" ]] || snapshot_tool="$ops_dir/../scripts/backup_sqlite.py"
snapshot_output="$(python3 "$snapshot_tool" --database "$data/autonomo.sqlite" --backup-dir "$data/backups/rollback" --keep "${AUTONOMO_ROLLBACK_BACKUP_KEEP:-10}")"
current_snapshot="$(printf '%s\n' "$snapshot_output" | sed -n 's/^backup=//p')"
[[ -f "$current_snapshot" ]] || die "rollback safety backup did not complete"
systemctl --user stop "$previous_unit" || true
if ! activate_secret_config "$target_secret"; then
  systemctl --user start "$previous_unit" || true
  die "rollback secret config activation failed; previous service was restarted"
fi
if [[ -n "$snapshot" ]]; then
  install -m 600 "$snapshot" "$(private_root)/autonomo.sqlite"
fi
restore_previous() {
  install -m 600 "$current_snapshot" "$data/autonomo.sqlite"
  restore_secret_config "$previous_secret" >/dev/null 2>&1 || true
  ln -sfn "releases/$previous" "$root/current.previous"
  mv -Tf "$root/current.previous" "$root/current"
  if [[ -n "$previous_secret" ]]; then
    restore_current_deployment_record "$previous" "$previous_secret" >/dev/null 2>&1 || true
  else
    restore_current_deployment_record "" "" >/dev/null 2>&1 || true
  fi
  systemctl --user start "$previous_unit" || true
}
if ! systemctl --user enable --now "$target_unit"; then
  restore_previous
  die "rollback target did not start; current link was not changed"
fi
if ! systemctl --user is-active --quiet "$target_unit"; then
  systemctl --user stop "$target_unit" >/dev/null 2>&1 || true
  restore_previous
  die "rollback target is not active; previous deployment was restored"
fi
if [[ -n "${AUTONOMO_HEALTHCHECK_URL:-}" ]] \
  && ! "$target/.venv/bin/python" "$ops_dir/healthcheck.py" "$AUTONOMO_HEALTHCHECK_URL"; then
  systemctl --user stop "$target_unit" >/dev/null 2>&1 || true
  restore_previous
  die "rollback target failed the external health check; previous deployment was restored"
fi
if ! { ln -sfn "releases/$target_sha" "$root/current.new" && mv -Tf "$root/current.new" "$root/current"; }; then
  systemctl --user stop "$target_unit" >/dev/null 2>&1 || true
  restore_previous
  die "rollback release link update failed; previous deployment was restored"
fi
if ! record_current_deployment "$target_sha" "$target_secret"; then
  systemctl --user stop "$target_unit" >/dev/null 2>&1 || true
  restore_previous
  die "rollback metadata update failed; previous deployment was restored"
fi
prune_secret_generations "$target_secret" "$previous_secret" \
  || note "warning: rollback succeeded but stale secret generations were not pruned"
[[ "$previous_unit" == "$target_unit" ]] || systemctl --user disable "$previous_unit" || true
printf 'rolled_back_to=%s\nsecret_config_sha=%s\n' "$target_sha" "$target_secret"
