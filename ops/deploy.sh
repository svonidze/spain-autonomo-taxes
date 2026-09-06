#!/usr/bin/env bash
# Install exactly one reviewed Git object into an immutable release directory.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ops/lib.sh
source "$script_dir/lib.sh"

usage() { echo "usage: $0 <full-git-sha>" >&2; exit 2; }
[[ $# -eq 1 ]] || usage
sha="$1"
validate_sha "$sha"
require_command git
require_command python3
require_command systemctl
load_runtime_env
with_lock

source_repo="${AUTONOMO_DEPLOY_REPOSITORY:-$PWD}"
git -C "$source_repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "AUTONOMO_DEPLOY_REPOSITORY must be a Git checkout: $source_repo"
root="$(release_root)"
target="$(release_path "$sha")"
release_ref="${AUTONOMO_DEPLOY_REF:-master}"
[[ "$release_ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ && "$release_ref" != *..* ]] \
  || die "AUTONOMO_DEPLOY_REF must be a safe remote branch or ref name"
mkdir -p "$root/releases"
"$script_dir/preflight.sh"
python3 "$script_dir/ocr-readiness.py" --runtime-env "$(runtime_env_path)"
python3 "$script_dir/backup_readiness.py" \
  --runtime-env "$(runtime_env_path)" \
  --private-root "$(private_root)" \
  --release-root "$(release_root)" \
  --max-age-hours "${AUTONOMO_BACKUP_MAX_AGE_HOURS:-48}"

git -C "$source_repo" fetch --quiet origin "$release_ref"
git -C "$source_repo" cat-file -e "$sha^{commit}"
git -C "$source_repo" merge-base --is-ancestor "$sha" FETCH_HEAD \
  || die "release SHA is not reachable from fetched origin/$release_ref: $sha"
python3 "$script_dir/prepare_ui_release.py" preflight "$source_repo" "$sha" "$target"
if [[ ! -d "$target" ]]; then
  git -C "$source_repo" worktree add --detach "$target" "$sha"
  printf '%s\n' "$sha" > "$target/.release-sha"
  python3 "$script_dir/prepare_ui_release.py" build "$source_repo" "$sha" "$target"
  python3 -m venv "$target/.venv"
  "$target/.venv/bin/python" -m pip install --disable-pip-version-check --no-input "$target"
  "$target/.venv/bin/python" - <<'PY' > "$target/.schema-version"
from autonomo_taxes.ledger_db import LATEST_SCHEMA_VERSION
print(LATEST_SCHEMA_VERSION)
PY
  python3 "$script_dir/prepare_ui_release.py" receipt "$source_repo" "$sha" "$target"
fi
python3 "$script_dir/prepare_ui_release.py" verify "$source_repo" "$sha" "$target"
[[ "$(<"$target/.release-sha")" == "$sha" ]] || die "release marker mismatch: $target"
if [[ ! -s "$target/.schema-version" ]]; then
  "$target/.venv/bin/python" - <<'PY' > "$target/.schema-version"
from autonomo_taxes.ledger_db import LATEST_SCHEMA_VERSION
print(LATEST_SCHEMA_VERSION)
PY
fi
[[ -s "$target/.schema-version" ]] || die "release schema marker missing: $target"

units_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$units_dir"
migration_env=""
migration_pre=""
if [[ "${AUTONOMO_ENABLE_STORAGE_MIGRATION:-0}" == "1" ]]; then
  "$target/.venv/bin/autonomo-tax" storage migrate --help >/dev/null
  migration_env="Environment=AUTONOMO_REQUIRE_STORAGE_MIGRATION=1"
  migration_pre="ExecStartPre=$target/.venv/bin/autonomo-tax storage migrate --startup --require-complete --db $(private_root)/autonomo.sqlite --local-root $(private_root)"
  if [[ -n "${AUTONOMO_DRIVE_MOUNT_ROOT:-}" ]]; then
    migration_pre+=" --drive-mount-root ${AUTONOMO_DRIVE_MOUNT_ROOT}"
  fi
fi
python3 - "$script_dir/systemd/autonomo-web.service.template" "$units_dir/autonomo-web-$sha.service" "$sha" "$root" "$(private_root)" "$migration_env" "$migration_pre" <<'PY'
import sys
from pathlib import Path

template = Path(sys.argv[1])
destination = Path(sys.argv[2])
sha, release_root, private_root, migration_env, migration_pre = sys.argv[3:]
text = template.read_text(encoding="utf-8")
for key, value in {
    "@SHA@": sha,
    "@RELEASE_ROOT@": release_root,
    "@PRIVATE_ROOT@": private_root,
    "@MIGRATION_ENV@": migration_env,
    "@MIGRATION_PRE@": migration_pre,
}.items():
    if "\n" in value or (key in {"@RELEASE_ROOT@", "@PRIVATE_ROOT@"} and not value.startswith("/")):
        raise SystemExit(f"unsafe template value for {key}")
    text = text.replace(key, value)
if "@" in text:
    raise SystemExit("unresolved systemd template token")
destination.write_text(text, encoding="utf-8")
PY

if [[ "${AUTONOMO_ENABLE_STORAGE_MIGRATION:-0}" == "1" ]]; then
  migration_backup_dir="$(private_root)/backups/pre-migration"
  migration_tool="$script_dir/backup_sqlite.py"
  [[ -f "$migration_tool" ]] || migration_tool="$script_dir/../scripts/backup_sqlite.py"
  migration_output="$(python3 "$migration_tool" \
    --database "$(private_root)/autonomo.sqlite" \
    --backup-dir "$migration_backup_dir" \
    --keep "${AUTONOMO_PRE_MIGRATION_BACKUP_KEEP:-10}")"
  printf '%s\n' "$migration_output"
  migration_snapshot="$(printf '%s\n' "$migration_output" | sed -n 's/^backup=//p')"
  [[ -f "$migration_snapshot" ]] || die "pre-migration backup tool did not return a snapshot"
else
  migration_snapshot=""
fi

previous=""
if [[ -L "$root/current" ]]; then previous="$(current_release_sha)"; fi
restore_previous() {
  systemctl --user stop "autonomo-web-$sha.service" >/dev/null 2>&1 || true
  if [[ -n "$migration_snapshot" ]]; then
    install -m 600 "$migration_snapshot" "$(private_root)/autonomo.sqlite"
  fi
  if [[ -n "$previous" && "$previous" != "$sha" ]]; then
    systemctl --user start "autonomo-web-$previous.service" >/dev/null 2>&1 || true
  elif [[ -n "${AUTONOMO_LEGACY_UNIT:-}" ]]; then
    systemctl --user start "$AUTONOMO_LEGACY_UNIT" >/dev/null 2>&1 || true
  fi
}
systemctl --user daemon-reload
if [[ -n "$previous" && "$previous" != "$sha" ]]; then
  systemctl --user stop "autonomo-web-$previous.service"
elif [[ -n "${AUTONOMO_LEGACY_UNIT:-}" ]]; then
  systemctl --user stop "$AUTONOMO_LEGACY_UNIT"
fi
if ! systemctl --user enable --now "autonomo-web-$sha.service"; then
  restore_previous
  die "new release did not start; current link was not changed"
fi
if ! systemctl --user is-active --quiet "autonomo-web-$sha.service"; then
  restore_previous
  die "new release is not active; current link was not changed"
fi
if [[ -n "${AUTONOMO_HEALTHCHECK_URL:-}" ]]; then
  if ! "$target/.venv/bin/python" "$script_dir/healthcheck.py" "$AUTONOMO_HEALTHCHECK_URL"; then
    restore_previous
    die "new release failed the external health check; current link was not changed"
  fi
fi
if ! { ln -sfn "releases/$sha" "$root/current.new" && mv -Tf "$root/current.new" "$root/current"; }; then
  restore_previous
  die "release link update failed; previous deployment was restored"
fi
if [[ -n "$previous" && "$previous" != "$sha" ]]; then
  systemctl --user disable "autonomo-web-$previous.service" || true
elif [[ -n "${AUTONOMO_LEGACY_UNIT:-}" ]]; then
  systemctl --user disable "$AUTONOMO_LEGACY_UNIT" || true
fi
printf 'deployed_sha=%s\nprevious_sha=%s\n' "$sha" "${previous:-none}"
