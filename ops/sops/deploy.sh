#!/usr/bin/env bash
# Install exactly one reviewed Git object into an immutable release directory.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ops_dir="$(cd -- "$script_dir/.." && pwd)"
# shellcheck source=ops/lib.sh
source "$script_dir/lib.sh"

usage() { echo "usage: $0 <full-git-sha> <full-secret-config-sha>" >&2; exit 2; }
[[ $# -eq 2 ]] || usage
sha="$1"
secret_sha="$2"
require_command python3
load_bootstrap_env
require_private_file "$(bootstrap_env_path)" "ops bootstrap environment"
validate_sha "$sha"
validate_secret_sha "$secret_sha"
require_command git
require_command systemctl
with_lock

source_repo="${AUTONOMO_DEPLOY_REPOSITORY:-$PWD}"
git -C "$source_repo" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "AUTONOMO_DEPLOY_REPOSITORY must be a Git checkout: $source_repo"
root="$(release_root)"
target="$(release_path "$sha")"
secret_root="$(secret_config_mount)"
secret_generation="$(secret_generation_path "$secret_sha")"
new_unit="$(deployment_unit_name "$sha" "$secret_sha")"
previous=""
previous_secret=""
previous_unit=""
if [[ -L "$root/current" ]]; then
  previous="$(current_release_sha)"
  previous_secret="$(current_secret_config_sha 2>/dev/null || true)"
  previous_unit="$(current_deployment_unit)"
fi
release_ref="${AUTONOMO_DEPLOY_REF:-master}"
[[ "$release_ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ && "$release_ref" != *..* ]] \
  || die "AUTONOMO_DEPLOY_REF must be a safe remote branch or ref name"
mkdir -p "$root/releases"
"$script_dir/config-sync.sh" --stage-only "$secret_sha"
"$script_dir/preflight.sh" "$secret_sha"
python3 "$ops_dir/ocr-readiness.py" --runtime-env "$secret_generation/runtime.env"
python3 "$ops_dir/backup_readiness.py" \
  --runtime-env "$secret_generation/runtime.env" \
  --private-root "$(private_root)" \
  --release-root "$(release_root)" \
  --max-age-hours "${AUTONOMO_BACKUP_MAX_AGE_HOURS:-48}"

git -C "$source_repo" fetch --quiet origin "$release_ref"
git -C "$source_repo" cat-file -e "$sha^{commit}"
git -C "$source_repo" merge-base --is-ancestor "$sha" FETCH_HEAD \
  || die "release SHA is not reachable from fetched origin/$release_ref: $sha"
if [[ ! -d "$target" ]]; then
  git -C "$source_repo" worktree add --detach "$target" "$sha"
  printf '%s\n' "$sha" > "$target/.release-sha"
  python3 -m venv "$target/.venv"
  "$target/.venv/bin/python" -m pip install --disable-pip-version-check --no-input "$target"
  "$target/.venv/bin/python" - <<'PY' > "$target/.schema-version"
from autonomo_taxes.ledger_db import LATEST_SCHEMA_VERSION
print(LATEST_SCHEMA_VERSION)
PY
fi
[[ "$(<"$target/.release-sha")" == "$sha" ]] || die "release marker mismatch: $target"
if [[ ! -s "$target/.schema-version" ]]; then
  "$target/.venv/bin/python" - <<'PY' > "$target/.schema-version"
from autonomo_taxes.ledger_db import LATEST_SCHEMA_VERSION
print(LATEST_SCHEMA_VERSION)
PY
fi
[[ -s "$target/.schema-version" ]] || die "release schema marker missing: $target"
"$target/.venv/bin/python" - "$target" "$secret_generation/config.yaml" "$(private_root)" <<'PY'
import sys
from pathlib import Path

from autonomo_taxes.local_web import load_config

config = load_config(Path(sys.argv[1]), config_path=Path(sys.argv[2]))
private_root = Path(sys.argv[3]).resolve()
if config.database != private_root / "autonomo.sqlite":
    raise SystemExit("managed config ledger_db must be AUTONOMO_PRIVATE_ROOT/autonomo.sqlite")
for field in ("inbox_root", "archive_root", "cache_root"):
    path = getattr(config, field)
    if path is None:
        continue
    try:
        path.relative_to(private_root)
    except ValueError as exc:
        raise SystemExit(f"managed config {field} must stay inside AUTONOMO_PRIVATE_ROOT") from exc
PY

units_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$units_dir"
migration_env=""
migration_pre=""
backend_pre=""
if [[ "${AUTONOMO_ENABLE_STORAGE_MIGRATION:-0}" == "1" ]]; then
  "$target/.venv/bin/autonomo-tax" storage migrate --help >/dev/null
  migration_env="Environment=AUTONOMO_REQUIRE_STORAGE_MIGRATION=1"
  migration_pre="ExecStartPre=$target/.venv/bin/autonomo-tax storage migrate --startup --require-complete --db $(private_root)/autonomo.sqlite --local-root $(private_root)"
  if [[ -n "${AUTONOMO_DRIVE_MOUNT_ROOT:-}" ]]; then
    migration_pre+=" --drive-mount-root ${AUTONOMO_DRIVE_MOUNT_ROOT}"
  fi
fi
if [[ -f "$secret_generation/storage-backends.json" ]]; then
  "$target/.venv/bin/autonomo-tax" storage backends-apply --help >/dev/null
  backend_pre="ExecStartPre=$target/.venv/bin/autonomo-tax storage backends-apply --db $(private_root)/autonomo.sqlite --input $secret_generation/storage-backends.json"
fi
render_web_unit() {
  local release_sha="$1" config_sha="$2" unit="$3" unit_migration_env="$4" unit_migration_pre="$5" unit_backend_pre="$6"
  python3 - "$script_dir/systemd/autonomo-web.service.template" "$units_dir/$unit" "$release_sha" "$config_sha" "$root" "$(private_root)" "$secret_root" "$unit_migration_env" "$unit_migration_pre" "$unit_backend_pre" <<'PY'
import sys
from pathlib import Path

template = Path(sys.argv[1])
destination = Path(sys.argv[2])
sha, secret_sha, release_root, private_root, secret_root, migration_env, migration_pre, backend_pre = sys.argv[3:]
text = template.read_text(encoding="utf-8")
for key, value in {
    "@SHA@": sha,
    "@SECRET_SHA@": secret_sha,
    "@RELEASE_ROOT@": release_root,
    "@PRIVATE_ROOT@": private_root,
    "@SECRET_CONFIG_ROOT@": secret_root,
    "@MIGRATION_ENV@": migration_env,
    "@MIGRATION_PRE@": migration_pre,
    "@BACKEND_PRE@": backend_pre,
}.items():
    if "\n" in value or (
        key in {"@RELEASE_ROOT@", "@PRIVATE_ROOT@", "@SECRET_CONFIG_ROOT@"}
        and not value.startswith("/")
    ):
        raise SystemExit(f"unsafe template value for {key}")
    text = text.replace(key, value)
if "@" in text:
    raise SystemExit("unresolved systemd template token")
destination.write_text(text, encoding="utf-8")
PY
  chmod 644 "$units_dir/$unit"
}
render_web_unit "$sha" "$secret_sha" "$new_unit" "$migration_env" "$migration_pre" "$backend_pre"
if [[ -n "$previous" && -z "$previous_secret" && "$previous" != "$sha" ]]; then
  previous_target="$(release_path "$previous")"
  "$previous_target/.venv/bin/python" - "$previous_target" "$secret_generation/config.yaml" "$(private_root)" <<'PY'
import sys
from pathlib import Path

from autonomo_taxes.local_web import load_config

config = load_config(Path(sys.argv[1]), config_path=Path(sys.argv[2]))
private_root = Path(sys.argv[3]).resolve()
if config.database != private_root / "autonomo.sqlite":
    raise SystemExit("managed config ledger_db must be AUTONOMO_PRIVATE_ROOT/autonomo.sqlite")
for field in ("inbox_root", "archive_root", "cache_root"):
    path = getattr(config, field)
    if path is None:
        continue
    try:
        path.relative_to(private_root)
    except ValueError as exc:
        raise SystemExit(f"managed config {field} must stay inside AUTONOMO_PRIVATE_ROOT") from exc
PY
  fallback_unit="$(deployment_unit_name "$previous" "$secret_sha")"
  render_web_unit "$previous" "$secret_sha" "$fallback_unit" "" "" ""
fi

if [[ "${AUTONOMO_ENABLE_STORAGE_MIGRATION:-0}" == "1" || -n "$backend_pre" ]]; then
  migration_backup_dir="$(private_root)/backups/pre-deploy"
  migration_tool="$ops_dir/backup_sqlite.py"
  [[ -f "$migration_tool" ]] || migration_tool="$ops_dir/../scripts/backup_sqlite.py"
  migration_output="$(python3 "$migration_tool" \
    --database "$(private_root)/autonomo.sqlite" \
    --backup-dir "$migration_backup_dir" \
    --keep "${AUTONOMO_PRE_MIGRATION_BACKUP_KEEP:-10}")"
  printf '%s\n' "$migration_output"
  migration_snapshot="$(printf '%s\n' "$migration_output" | sed -n 's/^backup=//p')"
  [[ -f "$migration_snapshot" ]] || die "pre-deploy backup tool did not return a snapshot"
else
  migration_snapshot=""
fi

restore_previous() {
  systemctl --user stop "$new_unit" >/dev/null 2>&1 || true
  if [[ -n "$migration_snapshot" ]]; then
    install -m 600 "$migration_snapshot" "$(private_root)/autonomo.sqlite"
  fi
  restore_secret_config "$previous_secret" >/dev/null 2>&1 || true
  if [[ -n "$previous" ]]; then
    ln -sfn "releases/$previous" "$root/current.previous"
    mv -Tf "$root/current.previous" "$root/current"
  fi
  if [[ -n "$previous_secret" ]]; then
    restore_current_deployment_record "$previous" "$previous_secret" >/dev/null 2>&1 || true
  else
    restore_current_deployment_record "" "" >/dev/null 2>&1 || true
  fi
  if [[ -n "$previous_unit" ]]; then
    systemctl --user start "$previous_unit" >/dev/null 2>&1 || true
  elif [[ -n "${AUTONOMO_LEGACY_UNIT:-}" ]]; then
    systemctl --user start "$AUTONOMO_LEGACY_UNIT" >/dev/null 2>&1 || true
  fi
}
systemctl --user daemon-reload
if [[ -n "$previous_unit" ]]; then
  systemctl --user stop "$previous_unit"
elif [[ -n "${AUTONOMO_LEGACY_UNIT:-}" ]]; then
  systemctl --user stop "$AUTONOMO_LEGACY_UNIT"
fi
if ! activate_secret_config "$secret_sha"; then
  restore_previous
  die "secret config activation failed; previous deployment was restored"
fi
if ! systemctl --user enable --now "$new_unit"; then
  restore_previous
  die "new release did not start; current link was not changed"
fi
if ! systemctl --user is-active --quiet "$new_unit"; then
  restore_previous
  die "new release is not active; current link was not changed"
fi
if [[ -n "${AUTONOMO_HEALTHCHECK_URL:-}" ]]; then
  if ! "$target/.venv/bin/python" "$ops_dir/healthcheck.py" "$AUTONOMO_HEALTHCHECK_URL"; then
    restore_previous
    die "new release failed the external health check; current link was not changed"
  fi
fi
if [[ -n "$previous" && -z "$previous_secret" && "$previous" != "$sha" ]]; then
  if ! record_release_secret_binding "$previous" "$secret_sha"; then
    restore_previous
    die "legacy rollback binding failed; previous deployment was restored"
  fi
fi
if ! { ln -sfn "releases/$sha" "$root/current.new" && mv -Tf "$root/current.new" "$root/current"; }; then
  restore_previous
  die "release link update failed; previous deployment was restored"
fi
if ! record_current_deployment "$sha" "$secret_sha"; then
  restore_previous
  die "deployment metadata update failed; previous deployment was restored"
fi
prune_secret_generations "$secret_sha" "$previous_secret" \
  || note "warning: deployed successfully but stale secret generations were not pruned"
if [[ -n "$previous_unit" && "$previous_unit" != "$new_unit" ]]; then
  systemctl --user disable "$previous_unit" || true
elif [[ -n "${AUTONOMO_LEGACY_UNIT:-}" ]]; then
  systemctl --user disable "$AUTONOMO_LEGACY_UNIT" || true
fi
printf 'deployed_sha=%s\nsecret_config_sha=%s\nprevious_sha=%s\n' "$sha" "$secret_sha" "${previous:-none}"
