# Installing a release with compiled frontend assets

This is a preparation contract, not permission to deploy. Use the existing
reviewed-SHA, backup, active-mode and rollback procedures in the operations runbook.

## Web build contracts 1 and 2

The target commit declares `[tool.autonomo.web-ui] build-contract = 2` in
`pyproject.toml`. The shared `prepare_ui_release.py` reads that declaration from
the exact Git object. Missing declaration means legacy; unknown contracts fail.

For a new compiled-UI target, use the exact Node/npm engines in `package.json`
and matching `.nvmrc` from the pinned Git object. The commands below derive those
versions rather than maintaining another version list. A working registry/cache
is required. Tool or network failure must leave the current service running;
when installation is already authorized, resolve it within that scope and repeat
preflight. Node is used only to build; the running service is still Python.

The gate checks a clean target SHA and reviewed lockfile, runs
`npm ci --include=dev --no-audit --no-fund`, then `npm run build`. The build
environment excludes service secrets, ambient VITE variables and Node options.
The caller installs the Python package, records its schema marker and creates a
completion receipt only after isolated imports and installed resource hashes pass.
The receipt is written atomically and includes build SHA, tool versions, lockfile
digest, installed resource inventory and schema version. It is not a deployment
acceptance or backup receipt.

An existing complete target is verified without Node/npm. An incomplete target
is preserved, and preparation refuses to repair it or mint a missing receipt.
Follow separately scoped recovery; do not delete it or blindly rerun deployment.
Old releases do not need the new receipt or assets, and rollback never rebuilds.

## Install and select the pinned build tools

This is an authorized preparation step as the existing service user, before the
maintenance window. It requires Python 3.11+, Git, curl, tar, xz and GNU coreutils
(including `sha256sum` and `mv`). Supply missing dependencies only within the
authorized preparation scope. It does not replace the deployment command. Use the
source repository containing the reviewed object, the full pinned `app_sha`, and the
installed default-mode `run` wrapper from the operations checklist. First verify
that [wrapper and its overrides](README.md#verify-the-installed-service-environment-wrapper).
These launcher commands apply to default mode; SOPS retains its own control plane.

The following example supports Linux x64/arm64. Stop on other platforms and
resolve the matching official distribution; do not substitute an unreviewed tool
version. The official Node archive must supply the pinned npm too. If it does
not, stop preparation rather than silently upgrading npm independently.

```bash
set -euo pipefail
umask 077
for dependency in python3 git curl tar xz sha256sum mv; do
  command -v "$dependency" >/dev/null
done
read -r -p 'Absolute source repository containing the pinned SHA: ' source_repo
[[ "$source_repo" == /* ]]
validate_sha "$app_sha"
read -r node_version npm_version < <(python3 - "$source_repo" "$app_sha" <<'PYTOOLS'
import json, re, subprocess, sys
repo, sha = sys.argv[1:]
def show(path):
    return subprocess.check_output(["git", "-C", repo, "show", f"{sha}:{path}"], text=True).strip()
engines = json.loads(show("package.json"))["engines"]
assert all(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", engines[key]) for key in ("node", "npm"))
assert show(".nvmrc") == engines["node"]
print(engines["node"], engines["npm"])
PYTOOLS
)
case "$(uname -s)/$(uname -m)" in
  Linux/x86_64) node_arch=x64 ;;
  Linux/aarch64|Linux/arm64) node_arch=arm64 ;;
  *) printf 'Unsupported platform for this installation example\n' >&2; exit 1 ;;
esac
tools_root="$HOME/.local/share/autonomo-tax/toolchains"
node_name="node-v$node_version-linux-$node_arch"
node_root="$tools_root/$node_name"
mkdir -p "$tools_root"
if [[ ! -e "$node_root" && ! -L "$node_root" ]]; then
  tool_stage="$(mktemp -d "$tools_root/.node-stage.XXXXXX")"
  archive="$node_name.tar.xz"
  dist="https://nodejs.org/dist/v$node_version"
  curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    "$dist/SHASUMS256.txt" -o "$tool_stage/SHASUMS256.txt"
  curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    "$dist/$archive" -o "$tool_stage/$archive"
  (
    cd "$tool_stage"
    awk -v file="$archive" '$2 == file { print }' SHASUMS256.txt > selected.sha256
    test "$(wc -l < selected.sha256)" -eq 1
    sha256sum -c selected.sha256
    tar -xJf "$archive"
  )
  # GNU mv preserves an existing destination if another preparation supplied it.
  mv -T --no-clobber "$tool_stage/$node_name" "$node_root"
fi
# Never overwrite an existing installation to correct a version mismatch.
test -d "$node_root" && test ! -L "$node_root"
```

Verify the result through a private launcher. Its first argument is the selected
absolute `bin` directory; subsequent arguments are passed unchanged. The wrapper
loads the service environment first, and the launcher prepends only that directory
to its PATH. No global symlinks, profile edits or runtime.env changes are needed.
Keep the launcher path in the private work record for this deployment/retry.

```bash
build_work="$(mktemp -d "${TMPDIR:-/tmp}/autonomo-build.XXXXXX")"
chmod 700 "$build_work"
build_launcher="$build_work/with-build-tools.sh"
cat > "$build_launcher" <<'LAUNCHER'
#!/bin/bash
set -euo pipefail
[[ $# -ge 2 && "$1" == /* && "$2" == /* ]]
build_bin="$1"
shift
export PATH="$build_bin:$PATH"
exec "$@"
LAUNCHER
chmod 700 "$build_launcher"
"$run" -- "$build_launcher" "$node_root/bin" /bin/bash -c '
  set -euo pipefail
  test "$(node --version)" = "v$1"
  test "$(npm --version)" = "$2"
  command -v rclone >/dev/null
  npm ping --registry=https://registry.npmjs.org
  printf "build tools and service PATH verified\n"
' -- "$node_version" "$npm_version"
```

Registry reachability is only preparation evidence, not proof that `npm ci` or
installation will succeed. The installed frontend helper still constructs its
sanitized build environment, excluding service secrets, ambient VITE variables
and Node options. Existing complete releases skip tool provisioning and verify
their receipt/resources; partial releases remain subject to scoped recovery.

After the backup and write-free-window prerequisites in the deployment runbook,
use the same launcher with the installed deploy command:

```bash
"$run" --setenv "AUTONOMO_DEPLOY_REF=$deploy_ref" \
  --setenv "AUTONOMO_ENABLE_STORAGE_MIGRATION=$migration_flag" -- \
  "$build_launcher" "$node_root/bin" "$ops_root/deploy.sh" "$app_sha"
```

Here `deploy_ref` and `migration_flag` are the already verified values from the
wrapper diagnostic; the flag is `1` only for an explicitly required migration.
Dependency installation, frontend build, package installation and receipt/resource
verification remain inside the active deploy script, before service downtime.
Do not manually populate a release or create its receipt to work around a failed
gate. Preserve failed staging/logs privately for diagnosis.

## Updating an existing ops installation

Application deployment does not update installed ops helpers. Before the first
compiled-UI rollout, compare the reviewed files with the installed active mode.
In default mode include `run-with-service-env.sh`; update and validate it through
the [wrapper procedure](README.md#adding-the-manual-operation-wrapper-to-an-existing-default-installation)
before using one-shot deployment controls. Do not install the default wrapper
into a SOPS workflow. Then update the shared helpers and active caller:

1. Back up the currently installed `prepare_ui_release.py` if present,
   `healthcheck.py`, and the active mode's `deploy.sh` to the operator's private
   recovery directory. Record which helper was previously absent.
2. Install the reviewed shared Python helper first, then the health checker, each
   through a temporary sibling and atomic replacement with existing ownership and
   intended permissions. Validate Python syntax.
3. Atomically replace only the active mode's deploy caller and validate shell
   syntax. For SOPS the caller uses the shared helper in its parent ops directory.
   Preserve the chosen mode, existing OCR/backup preflights and runtime overrides.
4. On update failure restore the caller before restoring/removing the new helper.
   Do not run the general provisioning installer or change timers.
5. Perform the actual reviewed release only through the existing deployment skill.

Fresh provisioning installers include the helper. The runtime serves only files
listed in the installed UI manifest (`autonomo_taxes_ui/dist/build-manifest.json`
for contract 2, the legacy embedded `web_ui/dist` for contract 1); the manifest, maps and source files
are not public. `/assets` remains an application route. Both shell and resources
retain `no-store`; there are no lazy UI chunks or service worker.

Health checking discovers JS/CSS references in the served shell, supporting both
old flat resources and new hashed resources. An HTML 200 alone is insufficient.
The developer/CI installed-wheel browser check runs outside the checkout; see
`docs/UI_DEVELOPMENT.md`.

Contract 1 retains its original embedded-package layout and receipt. Contract 2
builds and retains two wheels in the immutable release's `.ui-wheels`: core first,
then the optional UI package. The UI requires the exact core version. Preparation
installs those local artifacts (UI with `--no-deps`), verifies both installed file
inventories against the wheels, and binds their hashes/versions, source SHA and
UI manifest in a contract-2 receipt. A partial pair cannot be reused or repaired
in place. The same checks apply to default and SOPS callers. Core-only users do
not run this web-release workflow and need no Node or frontend build.
