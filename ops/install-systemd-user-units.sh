#!/usr/bin/env bash
# Install templates only; activation is intentionally performed by deploy.sh.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/lib.sh"
require_command python3
load_runtime_env
require_command systemctl
units_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$units_dir"
ops_root="${AUTONOMO_OPS_ROOT:-$HOME/.local/lib/autonomo-ops}"
require_absolute_directory "$ops_root"
private_root="${AUTONOMO_PRIVATE_ROOT:?AUTONOMO_PRIVATE_ROOT must be set before installing units}"
require_absolute_directory "$private_root"
mkdir -p "$ops_root"
install -m 644 "$script_dir/ocr-readiness.py" "$ops_root/ocr-readiness.py"
install -m 755 "$script_dir"/*.sh "$ops_root/"
install -m 644 "$script_dir/healthcheck.py" "$ops_root/healthcheck.py"
install -m 644 "$script_dir/prepare_ui_release.py" "$ops_root/prepare_ui_release.py"
install -m 644 "$script_dir/backup_state.py" "$script_dir/backup_readiness.py" "$script_dir/verify_backup.py" "$ops_root/"
install -m 755 "$script_dir/configure-google-picker.py" "$ops_root/configure-google-picker.py"
install -m 644 "$script_dir/../scripts/backup_sqlite.py" "$ops_root/backup_sqlite.py"
install -m 644 "$script_dir/../scripts/backup_private_root.py" "$ops_root/backup_private_root.py"
install -m 644 "$script_dir/../src/autonomo_taxes/backup_settings.py" "$ops_root/backup_settings.py"
install -m 644 "$script_dir/../scripts/restore_private_root.py" "$ops_root/restore_private_root.py"
install -d -m 700 "$ops_root/systemd"
install -m 644 "$script_dir/systemd/autonomo-web.service.template" "$ops_root/systemd/autonomo-web.service.template"
if [[ -d "$script_dir/sops" ]]; then
  install -d -m 700 "$ops_root/sops" "$ops_root/sops/systemd"
  install -m 755 "$script_dir/sops"/*.sh "$ops_root/sops/"
  install -m 644 "$script_dir/sops"/*.example "$ops_root/sops/"
  install -m 644 "$script_dir/sops/systemd"/*.template "$ops_root/sops/systemd/"
fi
render_unit() {
  local template="$1" destination="$2"
  python3 - "$template" "$destination" "$ops_root" "$private_root" <<'PY'
import sys
from pathlib import Path

template, destination, ops_root, private_root = map(Path, sys.argv[1:])
text = template.read_text(encoding="utf-8")
for token, value in {"@OPS_ROOT@": str(ops_root), "@PRIVATE_ROOT@": str(private_root)}.items():
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
install -m 644 "$script_dir/systemd/autonomo-backup.timer" "$units_dir/autonomo-backup.timer"
render_unit "$script_dir/systemd/autonomo-backup-monthly.service.template" "$units_dir/autonomo-backup-monthly.service"
install -m 644 "$script_dir/systemd/autonomo-backup-monthly.timer" "$units_dir/autonomo-backup-monthly.timer"
render_unit "$script_dir/systemd/autonomo-backup-verification-monthly.service.template" "$units_dir/autonomo-backup-verification-monthly.service"
install -m 644 "$script_dir/systemd/autonomo-backup-verification-monthly.timer" "$units_dir/autonomo-backup-verification-monthly.timer"
render_unit "$script_dir/systemd/autonomo-backup-verification-alert.service.template" "$units_dir/autonomo-backup-verification-alert.service"
render_unit "$script_dir/systemd/autonomo-alert.service.template" "$units_dir/autonomo-alert.service"
systemctl --user daemon-reload
systemctl --user enable --now autonomo-backup.timer
systemctl --user enable --now autonomo-backup-monthly.timer
systemctl --user enable --now autonomo-backup-verification-monthly.timer
note "install completed; set $HOME/.config/autonomo-tax/runtime.env with 0600 permissions and AUTONOMO_OPS_ROOT=$ops_root"
