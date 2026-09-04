#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/lib.sh"
[[ $# -eq 1 && "$1" == monthly ]] || { echo "usage: $0 monthly" >&2; exit 2; }
require_command python3
require_command rclone
load_runtime_env
with_lock
data="$(private_root)"
remote="${AUTONOMO_RCLONE_MONTHLY_REMOTE:-}"
[[ -n "$remote" ]] || die "monthly crypt backup remote is not configured"
[[ "$remote" == *crypt:* ]] || die "monthly backup remote must be crypt"
[[ -n "${AUTONOMO_RCLONE_CONFIG:-}" ]] || die "rclone config is not configured"
require_private_file "$AUTONOMO_RCLONE_CONFIG" "rclone config"
backup_dir="${AUTONOMO_MONTHLY_BACKUP_DIR:-$data/backups/monthly}"
python3 "$script_dir/verify_backup.py" \
  --private-root "$data" \
  --backup-dir "$backup_dir" \
  --remote "$remote" \
  --rclone-config "$AUTONOMO_RCLONE_CONFIG" \
  --restore-script "$script_dir/restore_private_root.py" \
  --max-age-hours "${AUTONOMO_MONTHLY_VERIFY_MAX_AGE_HOURS:-72}"
