#!/usr/bin/env bash
# Install templates only; activation is intentionally performed by deploy.sh.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source_ops_dir="$(cd -- "$script_dir/.." && pwd)"
source "$script_dir/lib.sh"
require_command python3
load_bootstrap_env
require_private_file "$(bootstrap_env_path)" "ops bootstrap environment"
require_command systemctl
units_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$units_dir"
ops_root="${AUTONOMO_OPS_ROOT:-$HOME/.local/lib/autonomo-ops}"
require_absolute_directory "$ops_root"
private_root="${AUTONOMO_PRIVATE_ROOT:?AUTONOMO_PRIVATE_ROOT must be set before installing units}"
require_absolute_directory "$private_root"
secret_config_root="$(secret_config_mount)"
active_secret_sha="$(current_secret_config_sha)" \
  || die "activate a verified secret config generation before installing systemd units"
validate_secret_generation "$active_secret_sha"
mkdir -p "$ops_root"
sops_root="$ops_root/sops"
install -d -m 700 "$sops_root" "$sops_root/systemd"
install -m 644 "$source_ops_dir/ocr-readiness.py" "$ops_root/ocr-readiness.py"
install -m 755 "$script_dir"/*.sh "$sops_root/"
install -m 755 "$source_ops_dir/backup.sh" "$source_ops_dir/verify-backup.sh" "$source_ops_dir/alert.sh" "$source_ops_dir/lib.sh" "$ops_root/"
install -m 644 "$source_ops_dir/healthcheck.py" "$ops_root/healthcheck.py"
install -m 644 "$source_ops_dir/prepare_ui_release.py" "$ops_root/prepare_ui_release.py"
install -m 644 "$source_ops_dir/backup_state.py" "$source_ops_dir/backup_readiness.py" "$source_ops_dir/verify_backup.py" "$ops_root/"
install -m 644 "$source_ops_dir/backup/backup_sqlite.py" "$ops_root/backup_sqlite.py"
install -m 644 "$source_ops_dir/backup/backup_private_root.py" "$ops_root/backup_private_root.py"
install -m 644 "$source_ops_dir/../backend/src/autonomo_taxes/backup_settings.py" "$ops_root/backup_settings.py"
install -m 644 "$source_ops_dir/backup/restore_private_root.py" "$ops_root/restore_private_root.py"
install -m 644 "$script_dir/systemd"/*.template "$sops_root/systemd/"
render_unit() {
  local template="$1" destination="$2"
  python3 - "$template" "$destination" "$ops_root" "$private_root" "$secret_config_root" <<'PY'
import sys
from pathlib import Path

template, destination, ops_root, private_root, secret_config_root = map(Path, sys.argv[1:])
text = template.read_text(encoding="utf-8")
for token, value in {
    "@OPS_ROOT@": str(ops_root),
    "@PRIVATE_ROOT@": str(private_root),
    "@SECRET_CONFIG_ROOT@": str(secret_config_root),
}.items():
    if not value.startswith("/") or "\n" in value:
        raise SystemExit("unsafe systemd template value")
    text = text.replace(token, value)
if "@" in text:
    raise SystemExit("unresolved systemd template token")
Path(destination).write_text(text, encoding="utf-8")
PY
  chmod 644 "$destination"
}
render_unit "$script_dir/systemd/autonomo-backup.service.template" "$units_dir/autonomo-backup.service"
install -m 644 "$source_ops_dir/systemd/autonomo-backup.timer" "$units_dir/autonomo-backup.timer"
render_unit "$script_dir/systemd/autonomo-backup-monthly.service.template" "$units_dir/autonomo-backup-monthly.service"
install -m 644 "$source_ops_dir/systemd/autonomo-backup-monthly.timer" "$units_dir/autonomo-backup-monthly.timer"
render_unit "$script_dir/systemd/autonomo-backup-verification-monthly.service.template" "$units_dir/autonomo-backup-verification-monthly.service"
install -m 644 "$source_ops_dir/systemd/autonomo-backup-verification-monthly.timer" "$units_dir/autonomo-backup-verification-monthly.timer"
render_unit "$script_dir/systemd/autonomo-backup-verification-alert.service.template" "$units_dir/autonomo-backup-verification-alert.service"
render_unit "$script_dir/systemd/autonomo-alert.service.template" "$units_dir/autonomo-alert.service"
systemctl --user daemon-reload
systemctl --user enable --now autonomo-backup.timer
systemctl --user enable --now autonomo-backup-monthly.timer
systemctl --user enable --now autonomo-backup-verification-monthly.timer
note "SOPS ops install completed; active_secret_sha=$active_secret_sha AUTONOMO_OPS_ROOT=$ops_root"
