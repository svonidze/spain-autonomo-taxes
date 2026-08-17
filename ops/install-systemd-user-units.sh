#!/usr/bin/env bash
# Install templates only; activation is intentionally performed by deploy.sh.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/lib.sh"
require_command systemctl
units_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$units_dir"
ops_root="${AUTONOMO_OPS_ROOT:-$HOME/.local/lib/autonomo-ops}"
require_absolute_directory "$ops_root"
mkdir -p "$ops_root"
install -m 755 "$script_dir"/*.sh "$ops_root/"
install -m 644 "$script_dir/healthcheck.py" "$ops_root/healthcheck.py"
install -m 644 "$script_dir/../scripts/backup_sqlite.py" "$ops_root/backup_sqlite.py"
install -m 644 "$script_dir/../scripts/backup_private_root.py" "$ops_root/backup_private_root.py"
install -m 644 "$script_dir/../scripts/restore_private_root.py" "$ops_root/restore_private_root.py"
install -m 644 "$script_dir/systemd/autonomo-backup.service" "$units_dir/autonomo-backup.service"
install -m 644 "$script_dir/systemd/autonomo-backup.timer" "$units_dir/autonomo-backup.timer"
install -m 644 "$script_dir/systemd/autonomo-backup-monthly.service" "$units_dir/autonomo-backup-monthly.service"
install -m 644 "$script_dir/systemd/autonomo-backup-monthly.timer" "$units_dir/autonomo-backup-monthly.timer"
install -m 644 "$script_dir/systemd/autonomo-alert.service" "$units_dir/autonomo-alert.service"
systemctl --user daemon-reload
systemctl --user enable --now autonomo-backup.timer
systemctl --user enable --now autonomo-backup-monthly.timer
note "install completed; set $HOME/.config/autonomo-tax/runtime.env with 0600 permissions and AUTONOMO_OPS_ROOT=$ops_root"
