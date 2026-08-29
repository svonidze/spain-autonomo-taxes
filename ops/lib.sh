#!/usr/bin/env bash
# Shared, deliberately small safety helpers for production operations.
set -euo pipefail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*" >&2; }

require_command() { command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"; }

runtime_env_path() {
  printf '%s\n' "${AUTONOMO_RUNTIME_ENV_PATH:-${XDG_CONFIG_HOME:-$HOME/.config}/autonomo-tax/runtime.env}"
}

require_private_file() {
  local path="$1" label="${2:-private file}"
  require_command python3
  python3 - "$path" "$label" <<'PY'
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
label = sys.argv[2]
try:
    info = path.lstat()
except FileNotFoundError as exc:
    raise SystemExit(f"{label} does not exist: {path}") from exc
if path.is_symlink() or not stat.S_ISREG(info.st_mode):
    raise SystemExit(f"{label} must be a regular non-symlink file: {path}")
if info.st_uid != os.geteuid():
    raise SystemExit(f"{label} must be owned by the service user: {path}")
if stat.S_IMODE(info.st_mode) != 0o600:
    raise SystemExit(f"{label} must have mode 0600: {path}")
if info.st_nlink != 1:
    raise SystemExit(f"{label} must not have hard links: {path}")
PY
}

load_runtime_env() {
  local path line key value current
  path="$(runtime_env_path)"
  require_private_file "$path" "runtime environment"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" =~ ^(AUTONOMO_[A-Z0-9_]+)=(.*)$ ]] \
      || die "invalid runtime environment assignment in $path"
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    current="${!key:-}"
    if [[ -z "$current" ]]; then export "$key=$value"; fi
  done < "$path"
}

require_absolute_directory() {
  local path="$1"
  [[ "$path" = /* ]] || die "path must be absolute: $path"
  [[ "$path" != / && "$path" != /srv && "$path" != /var && "$path" != /home ]] || die "refusing broad path: $path"
}

release_root() { printf '%s\n' "${AUTONOMO_RELEASE_ROOT:-/srv/spain-autonomo-taxes}"; }
private_root() { printf '%s\n' "${AUTONOMO_PRIVATE_ROOT:?AUTONOMO_PRIVATE_ROOT must name the private data directory}"; }

validate_sha() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]] || die "release must be a full lowercase 40-character Git SHA"
}

release_path() {
  local sha="$1" root
  validate_sha "$sha"
  root="$(release_root)"
  require_absolute_directory "$root"
  printf '%s/releases/%s\n' "$root" "$sha"
}

current_release_sha() {
  local root target
  root="$(release_root)"
  target="$(readlink -f "$root/current" 2>/dev/null || true)"
  [[ "$target" =~ /releases/([0-9a-f]{40})$ ]] || die "current is not a managed release"
  printf '%s\n' "${BASH_REMATCH[1]}"
}

with_lock() {
  local root lock
  root="$(release_root)"
  mkdir -p "$root"
  lock="$root/.operations.lock"
  require_command flock
  exec 9>"$lock"
  flock -n 9 || die "another deploy, backup, or restore is running"
}
