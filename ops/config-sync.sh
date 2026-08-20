#!/usr/bin/env bash
# Materialize one immutable, reviewed SOPS revision inside the private root.
set -euo pipefail
umask 077
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=ops/lib.sh
source "$script_dir/lib.sh"

usage() {
  echo "usage: $0 --stage-only|--activate <full-secret-config-sha>" >&2
  exit 2
}
[[ $# -eq 2 ]] || usage
mode="$1"
secret_sha="$2"
[[ "$mode" == "--stage-only" || "$mode" == "--activate" ]] || usage

require_command python3
load_bootstrap_env
require_private_file "$(bootstrap_env_path)" "ops bootstrap environment"
validate_secret_sha "$secret_sha"
require_command git
require_command sops
require_command flock

repo_url="${AUTONOMO_SECRET_CONFIG_REPO:?AUTONOMO_SECRET_CONFIG_REPO must be configured}"
branch="${AUTONOMO_SECRET_CONFIG_BRANCH:-main}"
[[ "$branch" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ && "$branch" != *..* ]] \
  || die "AUTONOMO_SECRET_CONFIG_BRANCH must be a safe branch name"

age_key="${AUTONOMO_AGE_PRIVATE_KEY_PATH:?AUTONOMO_AGE_PRIVATE_KEY_PATH must be configured}"
[[ "$age_key" = /* ]] || die "AUTONOMO_AGE_PRIVATE_KEY_PATH must be absolute"
require_private_file "$age_key" "SOPS age identity"

expected_recipients="${AUTONOMO_EXPECTED_AGE_RECIPIENTS:?AUTONOMO_EXPECTED_AGE_RECIPIENTS must list server and recovery recipients}"
IFS=',' read -r -a recipients <<< "$expected_recipients"
[[ ${#recipients[@]} -ge 2 ]] || die "at least server and recovery age recipients are required"
for recipient in "${recipients[@]}"; do
  [[ "$recipient" =~ ^age1[a-z0-9]+$ ]] || die "invalid expected age recipient"
done
python3 - "${recipients[@]}" <<'PY'
import sys

if len(set(sys.argv[1:])) != len(sys.argv[1:]):
    raise SystemExit("expected age recipients must be unique")
PY

git_ssh_command=""
if [[ "$repo_url" == git@* || "$repo_url" == ssh://* || "$repo_url" == github.com:* ]]; then
  require_command ssh
  deploy_key="${AUTONOMO_SECRET_DEPLOY_KEY_PATH:?AUTONOMO_SECRET_DEPLOY_KEY_PATH must be configured}"
  known_hosts="${AUTONOMO_SECRET_KNOWN_HOSTS_PATH:?AUTONOMO_SECRET_KNOWN_HOSTS_PATH must be configured}"
  [[ "$deploy_key" = /* && "$known_hosts" = /* ]] \
    || die "secret deploy key and known_hosts paths must be absolute"
  [[ "$deploy_key" =~ ^[A-Za-z0-9_./-]+$ && "$known_hosts" =~ ^[A-Za-z0-9_./-]+$ ]] \
    || die "secret SSH paths contain unsupported characters"
  require_private_file "$deploy_key" "read-only secrets repository deploy key"
  require_owned_file "$known_hosts" "SSH known_hosts"
  ssh_user_option=""
  if [[ "$repo_url" == github.com:* ]]; then ssh_user_option="-l git"; fi
  git_ssh_command="ssh $ssh_user_option -i $deploy_key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$known_hosts"
elif [[ "${AUTONOMO_SECRET_ALLOW_LOCAL_REPO:-0}" != "1" ]]; then
  die "production secret repository transport must use SSH with a read-only deploy key"
fi

mount="$(secret_config_mount)"
install -d -m 700 "$mount" "$mount/generations"
exec 8>"$mount/.sync.lock"
flock -n 8 || die "another secret config synchronization is running"

repo_dir="$mount/encrypted-repository"
if [[ ! -e "$repo_dir" ]]; then
  install -d -m 700 "$repo_dir"
  git -C "$repo_dir" init --quiet
  git -C "$repo_dir" remote add origin "$repo_url"
fi
[[ -d "$repo_dir/.git" && ! -L "$repo_dir" ]] \
  || die "encrypted secret repository cache is missing or unsafe"
chmod 700 "$repo_dir"
[[ "$(git -C "$repo_dir" remote get-url origin)" == "$repo_url" ]] \
  || die "encrypted secret repository origin does not match AUTONOMO_SECRET_CONFIG_REPO"

if [[ -n "$git_ssh_command" ]]; then
  GIT_SSH_COMMAND="$git_ssh_command" git -C "$repo_dir" fetch --quiet --prune --no-tags origin "$branch"
else
  git -C "$repo_dir" fetch --quiet --prune --no-tags origin "$branch"
fi
git -C "$repo_dir" cat-file -e "$secret_sha^{commit}" 2>/dev/null \
  || die "secret config SHA was not fetched from the configured repository"
git -C "$repo_dir" merge-base --is-ancestor "$secret_sha" FETCH_HEAD \
  || die "secret config SHA is not reachable from the fetched branch"

generation="$(secret_generation_path "$secret_sha")"
if [[ -e "$generation" ]]; then
  validate_secret_generation "$secret_sha"
  if [[ "$mode" == "--activate" ]]; then activate_secret_config "$secret_sha"; fi
  printf 'config_sha=%s\nstatus=already-staged\n' "$secret_sha"
  exit 0
fi

stage="$(mktemp -d "$mount/.config-sync.XXXXXX")"
cleanup() {
  if [[ -n "${stage:-}" && -d "$stage" ]]; then
    chmod -R u+rwX "$stage" 2>/dev/null || true
    rm -rf -- "$stage"
  fi
}
trap cleanup EXIT
install -d -m 700 "$stage/encrypted" "$stage/credentials"

entries=(
  "required|prod/runtime.sops.env|runtime.env|dotenv"
  "required|prod/config.sops.yaml|config.yaml|yaml"
  "required|prod/google-drive-reader-service-account.sops.json|credentials/google-drive-reader-service-account.json|json"
  "optional|prod/google-drive-oauth-client.sops.json|credentials/google-drive-oauth-client.json|json"
  "required|prod/google-drive-oauth-token.sops.json|credentials/google-drive-oauth-token.json|json"
  "required|prod/rclone.sops.ini|credentials/rclone.conf|ini"
  "required|prod/storage-backends.sops.json|storage-backends.json|json"
)

decrypted_count=0
for entry in "${entries[@]}"; do
  IFS='|' read -r requirement source_path destination_path file_type <<< "$entry"
  if ! git -C "$repo_dir" cat-file -e "$secret_sha:$source_path" 2>/dev/null; then
    [[ "$requirement" == "optional" ]] && continue
    die "required encrypted config is missing: $source_path"
  fi
  encrypted_size="$(git -C "$repo_dir" cat-file -s "$secret_sha:$source_path")"
  [[ "$encrypted_size" =~ ^[0-9]+$ && "$encrypted_size" -le 5242880 ]] \
    || die "encrypted config exceeds the 5 MiB safety limit: $source_path"
  encrypted="$stage/encrypted/${source_path##*/}"
  git -C "$repo_dir" show "$secret_sha:$source_path" > "$encrypted"
  chmod 600 "$encrypted"
  python3 - "$encrypted" "$file_type" "${recipients[@]}" <<'PY'
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
file_type = sys.argv[2]
expected = set(sys.argv[3:])
content = path.read_text(encoding="utf-8")
found: set[str] = set()
if file_type == "json":
    document = json.loads(content)
    metadata = document.get("sops", {}) if isinstance(document, dict) else {}
    age_entries = metadata.get("age", []) if isinstance(metadata, dict) else []
    if isinstance(age_entries, list):
        found = {
            entry.get("recipient")
            for entry in age_entries
            if isinstance(entry, dict) and isinstance(entry.get("recipient"), str)
        }
elif file_type == "dotenv":
    pattern = re.compile(r"^sops_age__list_\d+__map_recipient=(age1[a-z0-9]+)$")
    found = {match.group(1) for line in content.splitlines() if (match := pattern.fullmatch(line))}
elif file_type == "ini":
    in_sops = False
    pattern = re.compile(r"age__list_\d+__map_recipient\s*=\s*(age1[a-z0-9]+)")
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_sops = stripped == "[sops]"
            continue
        if in_sops and (match := pattern.fullmatch(stripped)):
            found.add(match.group(1))
elif file_type == "yaml":
    in_sops = False
    pattern = re.compile(r"\s+recipient:\s*(age1[a-z0-9]+)\s*")
    for line in content.splitlines():
        if not line.startswith((" ", "\t")):
            in_sops = line.strip() == "sops:"
            continue
        if in_sops and (match := pattern.fullmatch(line)):
            found.add(match.group(1))
else:
    raise SystemExit(f"unsupported encrypted config type: {file_type}")
missing = expected - found
if missing:
    raise SystemExit(f"encrypted config is missing {len(missing)} expected age recipient(s): {path.name}")
unexpected = found - expected
if unexpected:
    raise SystemExit(f"encrypted config contains {len(unexpected)} unexpected age recipient(s): {path.name}")
PY
  destination="$stage/$destination_path"
  SOPS_AGE_KEY_FILE="$age_key" sops decrypt \
    --input-type "$file_type" --output-type "$file_type" \
    "$encrypted" > "$destination"
  [[ -s "$destination" ]] || die "decrypted config is empty: $source_path"
  [[ "$(wc -c < "$destination")" -le 5242880 ]] \
    || die "decrypted config exceeds the 5 MiB safety limit: $source_path"
  chmod 600 "$destination"
  decrypted_count=$((decrypted_count + 1))
done

rm -rf -- "$stage/encrypted"
printf '%s\n' "$secret_sha" > "$stage/.secret-config-sha"
chmod 600 "$stage/.secret-config-sha"
python3 - "$stage" "$secret_sha" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
secret_sha = sys.argv[2]
files = {}
for path in sorted(root.rglob("*")):
    if path.is_file() and path.name not in {".manifest.json", ".secret-config-sha"}:
        content = path.read_bytes()
        files[path.relative_to(root).as_posix()] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "byte_size": len(content),
        }
(root / ".manifest.json").write_text(
    json.dumps(
        {"format": "autonomo-secret-generation/v1", "secret_config_sha": secret_sha, "files": files},
        sort_keys=True,
        separators=(",", ":"),
    )
    + "\n",
    encoding="utf-8",
)
PY
chmod 600 "$stage/.manifest.json"
find "$stage" -type d -exec chmod 700 {} +
find "$stage" -type f -exec chmod 600 {} +
validate_target="$generation"
mv "$stage" "$validate_target"
stage=""
validate_secret_generation "$secret_sha"
if [[ "$mode" == "--activate" ]]; then activate_secret_config "$secret_sha"; fi
printf 'config_sha=%s\nstatus=staged\nfiles=%s\n' "$secret_sha" "$decrypted_count"
