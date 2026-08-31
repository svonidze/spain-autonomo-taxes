#!/usr/bin/env bash
# Read-only checks required before a release can replace the running service.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ops/lib.sh
source "$script_dir/lib.sh"

require_command python3
load_bootstrap_env
require_private_file "$(bootstrap_env_path)" "ops bootstrap environment"
[[ $# -le 1 ]] || { echo "usage: $0 [full-secret-config-sha]" >&2; exit 2; }
root="$(release_root)"
data="$(private_root)"
require_absolute_directory "$root"
require_absolute_directory "$data"
[[ -d "$data" ]] || die "private root does not exist: $data"
[[ -f "$data/autonomo.sqlite" ]] || die "database does not exist: $data/autonomo.sqlite"
[[ ! -L "$data/autonomo.sqlite" ]] || die "database must not be a symlink"
if [[ -e "$data/account-backup-settings.json" || -L "$data/account-backup-settings.json" ]]; then
  [[ -f "$script_dir/../backup_settings.py" ]] || die "installed backup settings reader is missing"
  python3 "$script_dir/../backup_settings.py" --private-root "$data"
fi

sync_required="${AUTONOMO_SECRET_SYNC_REQUIRED:-1}"
[[ "$sync_required" == "0" || "$sync_required" == "1" ]] \
  || die "AUTONOMO_SECRET_SYNC_REQUIRED must be 0 or 1"
secret_sha="${1:-}"
if [[ -z "$secret_sha" ]]; then secret_sha="$(current_secret_config_sha 2>/dev/null || true)"; fi
if [[ "$sync_required" == "1" ]]; then
  [[ -n "$secret_sha" ]] || die "a staged secret config SHA is required"
  validate_secret_sha "$secret_sha"
  require_command sops
  age_key="${AUTONOMO_AGE_PRIVATE_KEY_PATH:?AUTONOMO_AGE_PRIVATE_KEY_PATH must be configured}"
  require_private_file "$age_key" "SOPS age identity"
  if [[ "${AUTONOMO_SECRET_CONFIG_REPO:-}" == git@* || "${AUTONOMO_SECRET_CONFIG_REPO:-}" == ssh://* || "${AUTONOMO_SECRET_CONFIG_REPO:-}" == github.com:* ]]; then
    require_private_file "${AUTONOMO_SECRET_DEPLOY_KEY_PATH:?AUTONOMO_SECRET_DEPLOY_KEY_PATH must be configured}" "read-only secrets repository deploy key"
    require_owned_file "${AUTONOMO_SECRET_KNOWN_HOSTS_PATH:?AUTONOMO_SECRET_KNOWN_HOSTS_PATH must be configured}" "SSH known_hosts"
  fi
  validate_secret_generation "$secret_sha"
  generation="$(secret_generation_path "$secret_sha")"
  mount="$(secret_config_mount)"
  python3 - "$generation/runtime.env" "$data" "$mount" <<'PY'
import sys
from pathlib import Path

runtime = {}
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if line and not line.startswith("#"):
        key, value = line.split("=", 1)
        runtime[key] = value
private_root = str(Path(sys.argv[2]).resolve())
secret_mount = str(Path(sys.argv[3]).resolve())
if runtime.get("AUTONOMO_PRIVATE_ROOT") != private_root:
    raise SystemExit("runtime.env AUTONOMO_PRIVATE_ROOT does not match ops-bootstrap.env")
expected_rclone = f"{secret_mount}/current/credentials/rclone.conf"
if runtime.get("AUTONOMO_RCLONE_CONFIG") != expected_rclone:
    raise SystemExit("runtime.env AUTONOMO_RCLONE_CONFIG must use the active generation path")
PY
else
  [[ -f "$data/config.yaml" ]] || die "legacy private config does not exist: $data/config.yaml"
fi

python3 - "$data/autonomo.sqlite" <<'PY'
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1])
with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("SQLite integrity_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise SystemExit("SQLite foreign_key_check failed")
PY

if [[ "${AUTONOMO_ENABLE_STORAGE_MIGRATION:-0}" == "1" ]]; then
  note "storage migration is enabled for this deployment"
else
  note "storage migration is disabled for this deployment"
fi
printf 'preflight=passed\nsecret_config_sha=%s\n' "${secret_sha:-legacy}"
