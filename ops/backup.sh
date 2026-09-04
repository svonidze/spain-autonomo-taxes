#!/usr/bin/env bash
# Create a consistent SQLite backup using the backup implementation pinned to current release.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/lib.sh"
require_command python3
load_runtime_env
with_lock
data="$(private_root)"; sha="$(current_release_sha)"
release="$(release_path "$sha")"
tool="$script_dir/backup_private_root.py"
[[ -f "$tool" ]] || die "control-plane private-root backup tool missing: $tool"
backup_class="${AUTONOMO_BACKUP_CLASS:-daily}"
[[ "$backup_class" == "daily" || "$backup_class" == "monthly" ]] || die "AUTONOMO_BACKUP_CLASS must be daily or monthly"
if [[ "$backup_class" == "monthly" ]]; then
  backup_dir="${AUTONOMO_MONTHLY_BACKUP_DIR:-$data/backups/monthly}"
  backup_keep="${AUTONOMO_MONTHLY_BACKUP_KEEP:-13}"
  remote="${AUTONOMO_RCLONE_MONTHLY_REMOTE:-}"
else
  backup_dir="${AUTONOMO_BACKUP_DIR:-$data/backups/private-root}"
  backup_keep="${AUTONOMO_BACKUP_KEEP:-35}"
  remote="${AUTONOMO_RCLONE_REMOTE:-}"
fi
if [[ -e "$data/account-backup-settings.json" || -L "$data/account-backup-settings.json" ]]; then
  policy_tool="$script_dir/backup_settings.py"
  [[ -f "$policy_tool" ]] || die "installed backup settings reader is missing"
  backup_keep="$(python3 "$policy_tool" --private-root "$data" --backup-class "$backup_class" --fallback "$backup_keep")"
fi
require_absolute_directory "$backup_dir"
output="$(python3 "$tool" --private-root "$data" --database "$data/autonomo.sqlite" --out-dir "$backup_dir" --keep "$backup_keep")"
printf '%s\n' "$output"
backup_file="$(printf '%s\n' "$output" | sed -n 's/^archive=//p')"
manifest_file="$(printf '%s\n' "$output" | sed -n 's/^manifest=//p')"
[[ -f "$backup_file" ]] || die "backup tool did not return a file"
[[ -f "$manifest_file" ]] || die "backup tool did not return a manifest"
rclone_args=()
if [[ -n "${AUTONOMO_RCLONE_CONFIG:-}" ]]; then
  rclone_args+=(--config "$AUTONOMO_RCLONE_CONFIG")
fi
verify_crypt_remote() {
  local remote="$1" name
  [[ "$remote" == *crypt:* ]] || die "backup remote must name a crypt remote"
  [[ -n "${AUTONOMO_RCLONE_CONFIG:-}" && -f "${AUTONOMO_RCLONE_CONFIG}" ]] \
    || die "AUTONOMO_RCLONE_CONFIG must name the private rclone config"
  name="${remote%%:*}"
  rclone "${rclone_args[@]}" config show "$name" | grep -Eq '^type = crypt$' \
    || die "rclone remote is not configured as crypt: $name"
}
verify_uploaded_file() {
  local local_file="$1" remote_file="$2" expected_bytes result
  expected_bytes="$(wc -c < "$local_file" | tr -d '[:space:]')"
  result="$(rclone "${rclone_args[@]}" size --json "$remote_file")"
  python3 - "$expected_bytes" "$result" <<'PY'
import json
import sys

expected_bytes = int(sys.argv[1])
payload = json.loads(sys.argv[2])
if payload.get("count") != 1 or payload.get("bytes") != expected_bytes:
    raise SystemExit("uploaded backup object did not match the local file")
PY
}
if [[ -n "$remote" ]]; then
  require_command rclone
  verify_crypt_remote "$remote"
  backup_remote_file="${remote%/}/$(basename "$backup_file")"
  manifest_remote_file="${remote%/}/$(basename "$manifest_file")"
  rclone "${rclone_args[@]}" copyto --immutable "$backup_file" "$backup_remote_file"
  rclone "${rclone_args[@]}" copyto --immutable "$manifest_file" "$manifest_remote_file"
  verify_uploaded_file "$backup_file" "$backup_remote_file"
  verify_uploaded_file "$manifest_file" "$manifest_remote_file"
fi
if [[ -n "${AUTONOMO_RCLONE_EVIDENCE_REMOTE:-}" && -d "$data/evidence" ]]; then
  require_command rclone
  verify_crypt_remote "$AUTONOMO_RCLONE_EVIDENCE_REMOTE"
  rclone "${rclone_args[@]}" copy --immutable "$data/evidence" "${AUTONOMO_RCLONE_EVIDENCE_REMOTE%/}"
fi
if [[ -n "${AUTONOMO_RECONCILE_BACKENDS:-}" ]]; then
  IFS=',' read -r -a reconcile_backends <<< "$AUTONOMO_RECONCILE_BACKENDS"
  for backend in "${reconcile_backends[@]}"; do
    [[ -n "$backend" ]] || continue
    "$release/.venv/bin/autonomo-tax" storage reconcile --db "$data/autonomo.sqlite" --backend "$backend"
  done
fi

# set -e means this line is reached only when every step above succeeded, so the
# marker records a genuinely complete backup. It lives under backups/, which the
# archive perimeter excludes, and gives preflight and any operator one file to
# read instead of reconstructing the outcome from the journal.
record_state "last-backup-$backup_class.json" \
  "class=$backup_class" \
  "keep=$backup_keep" \
  "settings_format=1" \
  "archive=$(basename "$backup_file")" \
  "offsite=$([[ -n "$remote" ]] && printf yes || printf no)"
