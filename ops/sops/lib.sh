#!/usr/bin/env bash
# Shared, deliberately small safety helpers for production operations.
set -euo pipefail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*" >&2; }

require_command() { command -v "$1" >/dev/null 2>&1 || die "required command is unavailable: $1"; }

bootstrap_env_path() {
  printf '%s\n' "${AUTONOMO_BOOTSTRAP_ENV_PATH:-${XDG_CONFIG_HOME:-$HOME/.config}/autonomo-tax/ops-bootstrap.env}"
}

load_bootstrap_env() {
  [[ "${AUTONOMO_BOOTSTRAP_LOADED:-0}" == "1" ]] && return 0
  local path line key value
  path="$(bootstrap_env_path)"
  [[ -f "$path" && ! -L "$path" ]] || die "bootstrap environment is missing or unsafe: $path"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" =~ ^(AUTONOMO_[A-Z0-9_]+)=(.*)$ ]] \
      || die "invalid bootstrap environment assignment in $path"
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] \
      || die "invalid bootstrap environment value for $key"
    export "$key=$value"
  done < "$path"
  export AUTONOMO_BOOTSTRAP_LOADED=1
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

validate_secret_sha() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]] || die "secret config must be a full lowercase 40-character Git SHA"
}

secret_config_mount() {
  local data mount
  data="$(private_root)"
  mount="${AUTONOMO_SECRET_CONFIG_MOUNT:-$data/runtime-config}"
  require_absolute_directory "$data"
  require_absolute_directory "$mount"
  python3 - "$data" "$mount" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
mount = Path(sys.argv[2]).resolve()
try:
    relative = mount.relative_to(root)
except ValueError as exc:
    raise SystemExit("secret config mount must be inside AUTONOMO_PRIVATE_ROOT") from exc
if not relative.parts:
    raise SystemExit("secret config mount must not equal AUTONOMO_PRIVATE_ROOT")
print(mount)
PY
}

secret_generation_path() {
  local sha="$1"
  validate_secret_sha "$sha"
  printf '%s/generations/%s\n' "$(secret_config_mount)" "$sha"
}

require_private_file() {
  local path="$1" label="${2:-private file}"
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
if not stat.S_ISREG(info.st_mode) or path.is_symlink():
    raise SystemExit(f"{label} must be a regular non-symlink file: {path}")
if info.st_uid != os.geteuid():
    raise SystemExit(f"{label} must be owned by the service user: {path}")
if stat.S_IMODE(info.st_mode) != 0o600:
    raise SystemExit(f"{label} must have mode 0600: {path}")
if info.st_nlink != 1:
    raise SystemExit(f"{label} must not have hard links: {path}")
PY
}

require_owned_file() {
  local path="$1" label="${2:-file}"
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
if not stat.S_ISREG(info.st_mode) or path.is_symlink():
    raise SystemExit(f"{label} must be a regular non-symlink file: {path}")
if info.st_uid != os.geteuid():
    raise SystemExit(f"{label} must be owned by the service user: {path}")
if stat.S_IMODE(info.st_mode) & 0o022:
    raise SystemExit(f"{label} must not be group/world writable: {path}")
PY
}

validate_secret_generation() {
  local sha="$1" generation
  validate_secret_sha "$sha"
  generation="$(secret_generation_path "$sha")"
  python3 - "$generation" "$sha" <<'PY'
import json
import hashlib
import os
import re
import stat
import sys
from pathlib import Path

generation = Path(sys.argv[1])
expected_sha = sys.argv[2]
required = {
    ".secret-config-sha": "text",
    ".manifest.json": "json",
    "runtime.env": "dotenv",
    "config.yaml": "text",
    "credentials/google-drive-reader-service-account.json": "json",
    "credentials/google-drive-oauth-token.json": "json",
    "credentials/rclone.conf": "text",
    "storage-backends.json": "json",
}
optional = {
    "credentials/google-drive-oauth-client.json": "json",
}
if not generation.is_dir() or generation.is_symlink():
    raise SystemExit(f"secret config generation is missing or unsafe: {generation}")
directory_info = generation.stat()
if directory_info.st_uid != os.geteuid() or stat.S_IMODE(directory_info.st_mode) != 0o700:
    raise SystemExit(f"secret config generation must be owned by the service user with mode 0700: {generation}")
for directory in (path for path in generation.rglob("*") if path.is_dir()):
    info = directory.lstat()
    if directory.is_symlink() or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise SystemExit(f"secret config directory must be owned by the service user with mode 0700: {directory}")
for file_path in (path for path in generation.rglob("*") if not path.is_dir()):
    info = file_path.lstat()
    if (
        file_path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise SystemExit(f"secret config file must be a private regular file without links: {file_path}")
for relative, kind in {**required, **optional}.items():
    path = generation / relative
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        if relative in optional:
            continue
        raise SystemExit(f"required decrypted secret config is missing: {relative}") from exc
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_nlink != 1:
        raise SystemExit(f"decrypted secret config must be a regular file without links: {relative}")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise SystemExit(f"decrypted secret config must be owned by the service user with mode 0600: {relative}")
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise SystemExit(f"decrypted secret config must not be empty: {relative}")
    if kind == "json":
        value = json.loads(content)
        if not isinstance(value, dict):
            raise SystemExit(f"decrypted JSON config must contain an object: {relative}")
    elif kind == "dotenv":
        for number, line in enumerate(content.splitlines(), 1):
            if not line or line.startswith("#"):
                continue
            match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
            if match is None:
                raise SystemExit(f"invalid runtime.env assignment at line {number}")
            if match.group(1).startswith(("AUTONOMO_SECRET_", "AUTONOMO_AGE_")):
                raise SystemExit("bootstrap secret-sync variables must not be stored in runtime.env")
if (generation / ".secret-config-sha").read_text(encoding="utf-8").strip() != expected_sha:
    raise SystemExit("secret config generation marker mismatch")
storage_document = json.loads((generation / "storage-backends.json").read_text(encoding="utf-8"))
backends = storage_document.get("backends") if isinstance(storage_document, dict) else None
if not isinstance(backends, list) or not backends or not all(isinstance(item, dict) for item in backends):
    raise SystemExit("storage-backends.json must contain a non-empty backends object array")
backend_keys = [item.get("backend_key") for item in backends]
if any(not isinstance(key, str) or not key for key in backend_keys) or len(set(backend_keys)) != len(backend_keys):
    raise SystemExit("storage-backends.json backend keys must be non-empty and unique")
credential_root = generation.parent.parent / "current" / "credentials"
for backend in backends:
    credential_ref = backend.get("credential_ref")
    if credential_ref is None:
        continue
    if not isinstance(credential_ref, str) or not credential_ref.startswith("file:"):
        raise SystemExit("storage backend credential_ref must use file:<absolute-path>")
    credential_path = Path(credential_ref.removeprefix("file:"))
    if not credential_path.is_absolute() or ".." in credential_path.parts:
        raise SystemExit("storage backend credential_ref path must be absolute and normalized")
    try:
        relative_credential = credential_path.relative_to(credential_root)
    except ValueError as exc:
        raise SystemExit("storage backend credential_ref must use runtime-config/current/credentials") from exc
    if not relative_credential.parts:
        raise SystemExit("storage backend credential_ref must identify a credential file")
manifest = json.loads((generation / ".manifest.json").read_text(encoding="utf-8"))
if manifest.get("format") != "autonomo-secret-generation/v1" or manifest.get("secret_config_sha") != expected_sha:
    raise SystemExit("secret config generation manifest metadata mismatch")
manifest_files = manifest.get("files")
if not isinstance(manifest_files, dict):
    raise SystemExit("secret config generation manifest files are invalid")
actual_files = {
    path.relative_to(generation).as_posix(): path
    for path in generation.rglob("*")
    if path.is_file() and path.name not in {".manifest.json", ".secret-config-sha"}
}
if set(actual_files) != set(manifest_files):
    raise SystemExit("secret config generation manifest file set mismatch")
for relative, path in actual_files.items():
    content = path.read_bytes()
    expected = manifest_files.get(relative)
    if not isinstance(expected, dict) or expected.get("byte_size") != len(content):
        raise SystemExit(f"secret config generation size mismatch: {relative}")
    if expected.get("sha256") != hashlib.sha256(content).hexdigest():
        raise SystemExit(f"secret config generation digest mismatch: {relative}")
PY
}

current_secret_config_sha() {
  local mount
  mount="$(secret_config_mount)"
  python3 - "$mount" <<'PY'
import re
import sys
from pathlib import Path

mount = Path(sys.argv[1]).resolve()
link = mount / "current"
if not link.is_symlink():
    raise SystemExit(1)
target = link.resolve()
try:
    relative = target.relative_to(mount / "generations")
except ValueError:
    raise SystemExit("current secret config link escapes the managed generations directory")
if len(relative.parts) != 1 or re.fullmatch(r"[0-9a-f]{40}", relative.parts[0]) is None:
    raise SystemExit("current secret config link has an invalid target")
print(relative.parts[0])
PY
}

activate_secret_config() {
  local sha="$1" mount generation
  validate_secret_generation "$sha"
  mount="$(secret_config_mount)"
  generation="$(secret_generation_path "$sha")"
  python3 - "$mount" "$generation" <<'PY'
import os
import sys
from pathlib import Path

mount = Path(sys.argv[1])
generation = Path(sys.argv[2])
temporary = mount / f".current-{os.getpid()}"
if temporary.exists() or temporary.is_symlink():
    temporary.unlink()
temporary.symlink_to(Path("generations") / generation.name)
os.replace(temporary, mount / "current")
PY
}

restore_secret_config() {
  local sha="${1:-}" mount
  if [[ -n "$sha" ]]; then
    activate_secret_config "$sha"
    return
  fi
  mount="$(secret_config_mount)"
  python3 - "$mount" <<'PY'
import sys
from pathlib import Path

link = Path(sys.argv[1]) / "current"
if link.is_symlink():
    link.unlink()
elif link.exists():
    raise SystemExit("refusing to remove non-symlink current secret config path")
PY
}

prune_secret_generations() {
  local active_sha="$1" previous_sha="${2:-}" mount
  validate_secret_sha "$active_sha"
  if [[ -n "$previous_sha" ]]; then validate_secret_sha "$previous_sha"; fi
  mount="$(secret_config_mount)"
  python3 - "$mount" "$active_sha" "$previous_sha" <<'PY'
import re
import shutil
import sys
from pathlib import Path

mount = Path(sys.argv[1]).resolve()
generations = mount / "generations"
keep = {value for value in sys.argv[2:] if value}
removed = 0
for path in generations.iterdir():
    if path.name in keep:
        continue
    if re.fullmatch(r"[0-9a-f]{40}", path.name) is None:
        raise SystemExit(f"refusing to prune unexpected secret generation entry: {path.name}")
    if path.is_symlink() or not path.is_dir():
        raise SystemExit(f"refusing to prune unsafe secret generation entry: {path.name}")
    shutil.rmtree(path)
    removed += 1
print(f"pruned_secret_generations={removed}")
PY
}

release_path() {
  local sha="$1" root
  validate_sha "$sha"
  root="$(release_root)"
  require_absolute_directory "$root"
  printf '%s/releases/%s\n' "$root" "$sha"
}

current_release_sha() {
  local root
  root="$(release_root)"
  python3 - "$root" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
link = root / "current"
if not link.is_symlink():
    raise SystemExit("current is not a managed release")
target = link.resolve()
try:
    relative = target.relative_to(root / "releases")
except ValueError as exc:
    raise SystemExit("current is not a managed release") from exc
if len(relative.parts) != 1 or re.fullmatch(r"[0-9a-f]{40}", relative.parts[0]) is None:
    raise SystemExit("current is not a managed release")
print(relative.parts[0])
PY
}

deployment_unit_name() {
  local release_sha="$1" secret_sha="$2"
  validate_sha "$release_sha"
  validate_secret_sha "$secret_sha"
  printf 'autonomo-web-%s-%s.service\n' "$release_sha" "$secret_sha"
}

current_deployment_unit() {
  local root state release_sha secret_sha linked_release
  root="$(release_root)"
  state="$root/current-deployment"
  if [[ ! -f "$state" ]]; then
    release_sha="$(current_release_sha)"
    printf 'autonomo-web-%s.service\n' "$release_sha"
    return
  fi
  require_private_file "$state" "current deployment metadata"
  read -r release_sha secret_sha < "$state"
  validate_sha "$release_sha"
  validate_secret_sha "$secret_sha"
  linked_release="$(current_release_sha)"
  [[ "$release_sha" == "$linked_release" ]] \
    || die "current deployment metadata does not match the current release link"
  deployment_unit_name "$release_sha" "$secret_sha"
}

record_release_secret_binding() {
  local release_sha="$1" secret_sha="$2" root
  validate_sha "$release_sha"
  validate_secret_sha "$secret_sha"
  root="$(release_root)"
  install -d -m 700 "$root/release-config"
  python3 - "$root/release-config/$release_sha" "$secret_sha" <<'PY'
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
secret_sha = sys.argv[2]
temporary = path.with_name(f".{path.name}.{os.getpid()}")
temporary.write_text(f"{secret_sha}\n", encoding="utf-8")
temporary.chmod(0o600)
os.replace(temporary, path)
PY
}

record_current_deployment() {
  local release_sha="$1" secret_sha="$2" root
  validate_sha "$release_sha"
  validate_secret_sha "$secret_sha"
  root="$(release_root)"
  record_release_secret_binding "$release_sha" "$secret_sha"
  python3 - "$root" "$release_sha" "$secret_sha" <<'PY'
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
release_sha, secret_sha = sys.argv[2:]

def replace_text(path: Path, content: str, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(mode)
    os.replace(temporary, path)

replace_text(root / "current-deployment", f"{release_sha} {secret_sha}\n", 0o600)
PY
}

restore_current_deployment_record() {
  local release_sha="${1:-}" secret_sha="${2:-}" root state
  if [[ -n "$release_sha" && -n "$secret_sha" ]]; then
    record_current_deployment "$release_sha" "$secret_sha"
    return
  fi
  [[ -z "$release_sha" && -z "$secret_sha" ]] \
    || die "deployment metadata restoration requires both SHAs or neither"
  root="$(release_root)"
  state="$root/current-deployment"
  if [[ -f "$state" && ! -L "$state" ]]; then
    rm -- "$state"
  elif [[ -e "$state" || -L "$state" ]]; then
    die "refusing to remove unsafe current deployment metadata"
  fi
}

release_secret_config_sha() {
  local release_sha="$1" binding secret_sha
  validate_sha "$release_sha"
  binding="$(release_root)/release-config/$release_sha"
  [[ -f "$binding" ]] || die "release has no successful secret config binding: $release_sha"
  require_private_file "$binding" "release secret config binding"
  read -r secret_sha < "$binding"
  validate_secret_sha "$secret_sha"
  printf '%s\n' "$secret_sha"
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
