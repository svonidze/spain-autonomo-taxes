# Production operations

The default is a single-host deployment with user-systemd, server-owned private
files, and one full application Git SHA. It needs no SOPS, age identity, or
secrets repository. Immutable releases live under `AUTONOMO_RELEASE_ROOT/releases`;
`current` points to the active release. The installed `AUTONOMO_OPS_ROOT` control
plane lives outside releases and supplies deployment and backup helpers.

Use [provisioning](PROVISIONING.md) for initial setup,
[disaster recovery](../docs/DISASTER_RECOVERY.md) for data recovery, and
[optional SOPS](sops/README.md) only when that separate control plane is enabled.
Roadmaps in `docs/plans/` are not operating instructions.

## Command safety

| Class | Examples | Effect |
|---|---|---|
| Diagnostics | Unit status, SHA, read-only SQLite checks, remote listing | No intended application or remote-object changes |
| Isolated drill | Download archive and restore into a fresh directory | Writes private scratch files; no production replacement |
| Production change | Deploy, rollback, live restore, installer, backup job | Can restart services, replace data, upload objects, or reconcile replicas |

Run server examples in a fresh **Bash** shell as the non-root service user.
Do not use shell tracing, print environment files, or paste logs with private
paths into public issues. Read each section before running its commands.

## Bootstrap the default control plane

Prerequisites: Linux user-systemd, Git, Python 3.11+, venv support, `flock`, a
reviewed code checkout, and an initialized private database/config. Install
rclone for cloud backup. Configure Tailscale Serve separately for the loopback
listener at port 8765. These steps create files and enable backup timers.

In a fresh shell, select the reviewed checkout and create the runtime file only
if it does not exist:

```bash
set -euo pipefail
umask 077
read -r -p 'Absolute reviewed code checkout: ' code_root
test -f "$code_root/ops/runtime.env.example"
runtime_dir="${XDG_CONFIG_HOME:-$HOME/.config}/autonomo-tax"
install -d -m 700 "$runtime_dir"
test ! -e "$runtime_dir/runtime.env"
install -m 600 "$code_root/ops/runtime.env.example" "$runtime_dir/runtime.env"
```

Edit the new file privately, replacing every placeholder with an absolute path
or configured remote. Follow [server configuration](PROVISIONING.md#server-runtime-configuration).
Use plain `NAME=value`, without quotes, expansion, or spaces around `=`. The
strict non-shell loader accepts `AUTONOMO_*`, `TZ`, `LANG`, `LC_ALL`, `PYTHONUTF8`,
and `PATH`. Non-empty one-shot environment overrides take precedence. Never
`source runtime.env`.

After provisioning and checking the backup destinations, continue in the same
shell. This installs default units and enables daily/monthly timers; lingering
keeps user services running after logout and may require host administrator help.

```bash
"$code_root/ops/install-systemd-user-units.sh"
loginctl enable-linger "$USER"
```

Expect the installer completion message and two enabled backup timers. It also
copies `ops/sops/` without activating SOPS. Run the installer **from a source
checkout or release**, not from its installed destination, because it reads
the checkout's `scripts/` directory. Repeat installation only as an intentional
control-plane update after a healthy code rollout.

## Read-only operating checklist

Prerequisites: an installed **default** control plane and its valid `0600`
runtime file. This setup defines the variables reused below. Enter the installed
ops path from your host inventory; it is not the `ops/` directory in a checkout.
For a custom runtime location, first export `AUTONOMO_RUNTIME_ENV_PATH` to that
absolute file path. No secret contents are printed.

```bash
set -euo pipefail
read -r -p 'Absolute installed ops directory: ' ops_root
test -f "$ops_root/lib.sh"
source "$ops_root/lib.sh"
load_runtime_env
data="$(private_root)"
release_root_path="$(release_root)"
app_sha="$(current_release_sha)"
release="$(release_path "$app_sha")"
unit="autonomo-web-$app_sha.service"
printf 'current_sha=%s\nunit=%s\n' "$app_sha" "$unit"
systemctl --user is-active "$unit"
systemctl --user show "$unit" -p ActiveState -p SubState -p ExecMainStatus
test -n "${AUTONOMO_HEALTHCHECK_URL:-}"
"$release/.venv/bin/python" "$ops_root/healthcheck.py" "$AUTONOMO_HEALTHCHECK_URL"
python3 - "$data/autonomo.sqlite" "$release/.schema-version" <<'PY'
import sqlite3
import sys
from pathlib import Path
with sqlite3.connect(Path(sys.argv[1]).resolve().as_uri() + '?mode=ro', uri=True) as db:
    assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    assert db.execute('PRAGMA foreign_key_check').fetchone() is None
    schema = db.execute('PRAGMA user_version').fetchone()[0]
print(f'integrity=ok schema={schema} release_schema={Path(sys.argv[2]).read_text().strip()}')
PY
systemctl --user is-enabled autonomo-backup.timer autonomo-backup-monthly.timer
systemctl --user list-timers --all autonomo-backup.timer autonomo-backup-monthly.timer
systemctl --user show autonomo-backup.service autonomo-backup-monthly.service \
  -p Result -p ExecMainStatus -p ExecMainStartTimestamp -p ExecMainExitTimestamp
```

Expected: active web unit, current SHA matching the selected release, successful
HTTPS root/bootstrap check, `integrity=ok`, expected schema, enabled timers with
future runs, and a recent completed backup (`Result=success`, exit status 0).
A never-run unit can also show a default success status: check timestamps.
Inspect recent backup logs privately with `journalctl --user -u autonomo-backup.service -n 50 --no-pager`
if necessary. This is read-only but can display private paths. Confirm both
archive and manifest in the configured crypt remote using the
[recovery download procedure](../docs/DISASTER_RECOVERY.md#download-and-verify-a-backup).
A green timer or stored replica status is not proof of recoverability.

## Deploy a reviewed release (production change)

Before either route: require green CI for the **exact SHA**, take and verify a
fresh backup, record the current SHA/schema, and reserve a write-free maintenance
window if migration is needed. Use the checklist setup above in the same shell.
`deploy.sh` takes exactly one full lowercase 40-character SHA, never a branch.

### Merged code

Copy the merged commit SHA from the reviewed PR. This command installs code,
restarts the service, and switches `current` after checks pass. It verifies the
SHA is reachable from fetched `master`, not merely present in the local object
database. Expected final output: `deployed_sha` and `previous_sha`.

```bash
read -r -p 'Reviewed merge commit, full SHA: ' app_sha
validate_sha "$app_sha"
AUTONOMO_DEPLOY_REF=master "$ops_root/deploy.sh" "$app_sha"
```

### Explicitly authorized unmerged PR

Only use this route when deployment of that PR head is explicitly approved.
Copy its remote branch and full head SHA after CI. Do not merge it as a side
effect. The temporary ref controls fetching/reachability only; the installed
release is still pinned to `app_sha`. The override is not saved in runtime.env.
Side effects and expected output are the same as merged deployment.

```bash
read -r -p 'Approved remote PR branch: ' deploy_ref
read -r -p 'Approved PR head, full SHA: ' app_sha
validate_sha "$app_sha"
AUTONOMO_DEPLOY_REF="$deploy_ref" "$ops_root/deploy.sh" "$app_sha"
```

### Schema migration

For a release that needs storage migration, use the following command **instead
of** the deployment command above. Set `deploy_ref` to `master` or the approved
PR branch. The flag renders an `ExecStartPre` storage migration and readiness
gate into this release's unit; it is not a permanent runtime.env change. That
unit retains the startup hook on later restarts. Do not use it for old releases
without the storage migration command.

```bash
read -r -p 'Reviewed transport ref (master or approved PR branch): ' deploy_ref
read -r -p 'Reviewed migration release, full SHA: ' app_sha
validate_sha "$app_sha"
AUTONOMO_DEPLOY_REF="$deploy_ref" AUTONOMO_ENABLE_STORAGE_MIGRATION=1 \
  "$ops_root/deploy.sh" "$app_sha"
```

The default script creates a verified SQLite snapshot under
`$data/backups/pre-migration` **before stopping the old unit** and prints its
`backup=` path. Record that exact path, old SHA, and old schema in the private
maintenance record. Quiesce user/CLI writes before the snapshot; otherwise a
rollback can lose changes accepted between snapshot and shutdown. This local
snapshot is excluded from full-root archives because it is under `backups/`.
Keep a separately verified pre-change archive remotely as well.

Migration inventories local/mounted files before the listener starts; it does
not itself prove cloud replicas have been uploaded. Set a Drive mount override
only if that particular migration requires it. The default preflight's generic
`AUTONOMO_MIGRATE_COMMAND` message is not evidence that the storage hook ran:
check the rendered unit, its startup result, and live schema.

On migration/start/health/link-switch failure, the script attempts to restore
the migration snapshot and restart the previous unit. Run the checklist even
after an error; do not assume compensation succeeded. For an initial managed
cutover, `AUTONOMO_LEGACY_UNIT` names the old unit to stop/restart. It is disabled
only after the new release passes checks.

### Rollback

Prerequisites: the target release and compatible unit are already installed,
its schema is known, and a write-free window is in effect. Use the checklist
setup. For same-schema rollback, the command keeps the live database, takes a
safety snapshot, and changes the active release. Expect `rolled_back_to`.

```bash
read -r -p 'Installed rollback target, full SHA: ' target_sha
validate_sha "$target_sha"
"$ops_root/rollback.sh" "$target_sha"
```

If the target schema is older, use this alternative with the previously recorded
snapshot. Verify its integrity, foreign keys, and schema by the
[recovery checks](../docs/DISASTER_RECOVERY.md#download-and-verify-a-backup) first;
the rollback script requires a private snapshot but does not establish that it
is the correct schema for you. **All writes since that snapshot are lost from
the active database.** Preserve them separately before proceeding.

```bash
read -r -p 'Installed older release, full SHA: ' target_sha
read -r -p 'Absolute verified pre-migration SQLite snapshot: ' rollback_snapshot
validate_sha "$target_sha"
AUTONOMO_ROLLBACK_SNAPSHOT="$rollback_snapshot" \
  "$ops_root/rollback.sh" "$target_sha"
```

After any deployment or rollback, rerun the checklist in a fresh shell so it
resolves the new `current`. Verify selected evidence reads from Google and
Yandex privately; a database status alone does not check the provider.

## Backups and monthly drill

Daily and monthly jobs archive the private root with a consistent SQLite
snapshot. Local retention is **35 daily / 13 monthly archive pairs by count**
unless overridden, not a guarantee of days covered. Bucket lifecycle is
configured separately. See [exact coverage and exclusions](../docs/DISASTER_RECOVERY.md#what-is-backed-up).

Running a backup is a production change: it creates/prunes local archives,
uploads to configured remotes, may copy local evidence, and reconciles
`AUTONOMO_RECONCILE_BACKENDS`. Both classes use this common script. The remote
must be a crypt remote rooted at the matching daily/monthly bucket prefix;
without a configured remote a successful job can be local-only.

For a deliberate fresh backup, after the checklist setup:

```bash
"$ops_root/backup.sh"
```

Expect `archive=` and `manifest=` plus exit status 0. Verify the pair remotely,
not just its local existence. `AUTONOMO_ALERT_WEBHOOK` is optional; no alert
delivery exists unless separately configured and tested.

For the monthly exercise, follow the [isolated recovery drill](../docs/DISASTER_RECOVERY.md#download-and-verify-a-backup).
It downloads through crypt, validates the pair, extracts to an empty directory,
and checks SQLite. **Do not run `restore.sh --yes-restore` for a drill.** That
mode replaces the live database. Production replacement is described only in
[production cutover](../docs/DISASTER_RECOVERY.md#production-cutover-dangerous).

## Optional SOPS control plane

[The SOPS runbook](sops/README.md) contains bootstrap, artifact mapping, exact
recipient validation, dual-SHA deployment, and recovery. Copying that directory
or preparing the private Git repository does not enable it. Do not mix its
units, environment files, or rollback scripts with the default control plane.
