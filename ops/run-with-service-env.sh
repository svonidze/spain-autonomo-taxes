#!/usr/bin/env bash
# Run one default-mode operation with the exact EnvironmentFile used by services.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ops/lib.sh
source "$script_dir/lib.sh"

usage() {
  echo "usage: $0 [--setenv AUTONOMO_NAME=value]... -- /absolute/command [args...]" >&2
  exit 2
}

runtime_env="$(runtime_env_path)"
setenv_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --setenv)
      [[ $# -ge 2 ]] || usage
      assignment="$2"
      [[ "$assignment" =~ ^AUTONOMO_[A-Z0-9_]+=[^[:space:]]+$ ]] \
        || die "--setenv accepts nonempty AUTONOMO_NAME=value assignments only"
      [[ "$assignment" != AUTONOMO_RUNTIME_ENV_PATH=* ]] \
        || die "AUTONOMO_RUNTIME_ENV_PATH is selected by the wrapper"
      setenv_args+=("--setenv=$assignment")
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      usage
      ;;
  esac
done

[[ $# -gt 0 && "$1" == /* ]] || die "command must be an absolute path"
require_private_file "$runtime_env" "runtime environment"
require_command systemd-run

exec systemd-run --user --wait --collect --pipe --quiet \
  "--property=EnvironmentFile=$runtime_env" \
  "--setenv=AUTONOMO_RUNTIME_ENV_PATH=$runtime_env" \
  "${setenv_args[@]}" -- "$@"
