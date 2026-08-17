#!/usr/bin/env bash
# Shared, deliberately small safety helpers for production operations.
set -euo pipefail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*" >&2; }

require_command() { command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"; }

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
