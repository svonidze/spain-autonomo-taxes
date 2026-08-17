#!/usr/bin/env bash
# Create a consistent SQLite backup using the backup implementation pinned to current release.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/lib.sh"
require_command python3; with_lock
root="$(release_root)"; data="$(private_root)"; sha="$(current_release_sha)"
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
if [[ -n "$remote" ]]; then
  require_command rclone
  verify_crypt_remote "$remote"
  rclone "${rclone_args[@]}" copyto --immutable "$backup_file" "${remote%/}/$(basename "$backup_file")"
  rclone "${rclone_args[@]}" copyto --immutable "$manifest_file" "${remote%/}/$(basename "$manifest_file")"
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
