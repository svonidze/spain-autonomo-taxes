# Production operations

The default is a single-host deployment with user-systemd, server-owned private
files, and one full application Git SHA. It needs no SOPS, age identity, or
secrets repository. Immutable releases live under `AUTONOMO_RELEASE_ROOT/releases`;
`current` points to the active release. The installed `AUTONOMO_OPS_ROOT` control
plane lives outside releases and supplies deployment and backup helpers.

Use [provisioning](PROVISIONING.md) for initial setup,
[disaster recovery](DISASTER_RECOVERY.md) for data recovery, and
[optional SOPS](../sops/README.md) only when that separate control plane is enabled.
Roadmaps in `../../docs/plans/` are not operating instructions.

## Command safety

| Class | Examples | Effect |
|---|---|---|
| Diagnostics | Unit status, SHA, read-only SQLite checks, remote listing | No intended application or remote-object changes |
| Isolated drill | Download archive and restore into a fresh directory | Writes private scratch files; no production replacement |
| Production change | Accounting correction, approval or posting; deploy, rollback, live restore, installer, backup job | Can change accounting records, restart services, replace data, upload objects, or reconcile replicas |

Run server examples in a fresh **Bash** shell as the non-root service user.
Do not use shell tracing, print environment files, or paste logs with private
paths into public issues. Read each section before running its commands.

## Scoped accounting maintenance

Use the [accounting workflow](../../docs/user/ACCOUNTING_WORKFLOW.md) for routine
review and posting. This section covers an authorized correction or recovery
that the installed interface cannot complete. It does not authorize a
deployment, a tax filing, changes to other records, or broader access.

### Establish the target and supported action

Before any write, record the intended service URL, host/instance, active release
SHA, service account, effective configuration and database in a private work
record. Derive the database from the running service's configuration and
overrides; do not select a convenient local copy or guess from a default path.
Check the period, exact document and transaction IDs, counterparty, current row
versions, source hash, amounts and dates. Confirm the linked transaction is the
one the user authorized. Do not print secrets or the service's full environment.

Inspect the actual release's capabilities. A merged PR, a newer local checkout
or a copied operations helper does not prove that the running application has
that feature. Never use a newer schema or application library against the live
database merely to obtain an editor; migration/deployment is a separate change.

Prefer the supported web API or CLI. The following are interface names, not
complete commands to paste into a production shell:

| Purpose | Supported interface | Boundary |
|---|---|---|
| Read the current invoice review | `GET /api/review/work-item`; CLI `review prepare` | The CLI writes a private packet file. For a single invoice, a document-scoped review requires exactly one linked transaction. |
| Validate a decision | `POST /api/review/validate`; CLI `review apply --dry-run` | The native validation transaction is rolled back. Validation does not save approval or post the entry. |
| Apply a reviewed decision | `POST /api/review/confirm`; CLI `review apply` | These save approval, not posting. The web confirmation can also apply a confirmed FX rate atomically; CLI packet application alone does not replace that FX workflow. |
| Post the authorized approved entry | CLI `review post` with its current expected row version | Uses the native posting checks. The web `POST /api/review/post-ready` and CLI `review post-batch` accept explicit lists; restrict them to authorized rows. |

Read the release's CLI help and API validation contract before constructing a
request. Preserve the session/Origin protections for web writes. Edit only the
packet's decision fields, never its state or snapshot hash. If a version is
stale, reload and review the new facts before making another decision.

Document-scoped review checks the single-transaction relationship during
application as well as preparation. A custom repair must retain that check
at the write boundary; an earlier inspection cannot protect against a later
second link. Resolve only the specifically justified issues, with their current
versions and evidence-backed reasons. Do not close every open issue to obtain
a ready status.

Do not reimport the original to correct metadata or clear an old extraction
error. Keep document/transaction identity and source bytes intact. Check
existing source material and user confirmations before asking for another
document or repeating a question. Explain the precise unresolved requirement.
See [source and date rules](../../docs/user/ACCOUNTING_WORKFLOW.md#dates-source-documents-and-missing-details)
and [OCR provisioning](PROVISIONING.md#local-ocr-dependency).

### When a bounded repair is necessary

If no supported interface can make the correction, agree the exact fields and
records first. Do not publish a case-specific repair as a general database
editing script. Prepare and review the repair privately with these safeguards:

1. Use the compatible active release and its domain operations. Keep schema,
   lifecycle, evidence and posting checks enabled. Posted records and closed
   periods require the supported correction/amendment procedure, not a forced
   transition back into review or direct SQL that bypasses the checks.
2. Create a consistent private database backup and verify its integrity and
   foreign keys. Rehearse the complete operation on an isolated copy, checking
   that only the authorized records and fields change. Keep the copy isolated
   from external writes such as uploads, cleanup or notifications. A successful
   rehearsal is not proof of a successful live write.
3. Before each live write, recheck current state and expected versions under
   the appropriate transaction/locking discipline. Stop on unrelated changes,
   conflicting evidence or new blockers. Preserve other users' work.
4. Run application/database operations as the service user, not root. Any
   necessary administrator step must be explicitly scoped. The user enters
   the administrator password only in their own interactive terminal, never
   in chat, a command argument, a log or a file. Keep a one-shot launcher bound
   to its reviewed payload and verify its integrity before execution. Do not
   add persistent privileges, alter sudo/SSH policy or establish a tunnel as
   a routine prerequisite for posting. If access is unavailable, report the
   exact remaining action and stop the privileged step.
5. Preserve the original's verified hash and record the reason and source for
   each correction. Distinguish source facts, explicit user confirmation and
   an operator's accounting decision. Maintain an audit record without
   silently rewriting earlier evidence.

Store the backup, reviewed repair, before/after evidence and results under the
appropriate private storage boundary. Follow [Privacy](../../docs/development/PRIVACY.md);
public examples must be independently synthetic, without case-specific
identifiers, amounts, server addresses or private paths.

### Interruptions, retries and proof of completion

Date correction, approval and posting may be separate commits. A batch may
also succeed for some rows and fail for others. After a lost response, a
terminal disconnect or any other interruption, read the actual records before
retrying. A progress message is only a lower bound on completed work; writing
that message and committing the database are not necessarily atomic.

For a resumable repair, check the entire expected checkpoint, including exact
versions, treatment identity and jurisdiction, source hashes and audit notes.
Verify the resolved issue still exists with the intended resolution and
version. An empty list of open issues alone does not prove how an issue was
resolved. Stop if another writer changed the checkpoint. Retry only verified
unfinished work; do not duplicate the entry or repeat an already saved stage.

Do not automatically restore the whole database to undo a partial repair.
That could remove later valid changes. Escalate any required live restore
through the separately authorized [recovery procedure](DISASTER_RECOVERY.md).

After execution, independently reread the entry through the running service.
Verify its actual transaction status, document number, amounts/currency,
issue/transaction/payment facts and booking date, reviewed treatment, linked
document and remaining issues. Check for an unintended duplicate and verify
the accessible original's hash against the pre-change evidence. If a record
is posted but calculation refresh or expense-inbox cleanup failed, report
those remaining tasks separately. Neither failure implies the accounting
write was rolled back.

### Operator and assistant updates

Every update should identify what is saved, what is still pending and who will
perform the next action. Use the installed application's terminology with an
explicit explanation of internal review. Avoid an unexplained "tax review"
or "tax check", which can sound like an inspection by AEAT.

Examples without operational data:

- "The document details are saved. The entry still needs internal confirmation
  of its accounting treatment; I will review it next."
- "The decision is approved but not posted. I will post the authorized entry
  after checking the current posting preview."
- "The correction is prepared but has not run. You need to enter the
  administrator password in your terminal for this one authorized repair."
- "The transaction is posted in the application. Calculation refresh failed;
  I will handle the refresh separately. Nothing was submitted to AEAT."

Say "posted" only after verifying the saved transaction. Report deployments,
original-file changes and tax submissions separately, and only when supported
by evidence. Do not turn unfinished operator work into an unexplained task
for the user or ask again for facts already confirmed.

## Bootstrap the default control plane

Prerequisites: Linux user-systemd, Git, Python 3.11+, venv support, `flock`, a
reviewed code checkout, and an initialized private database/config. Install
rclone for cloud backup and the [local OCR dependency](PROVISIONING.md#local-ocr-dependency).
Configure Tailscale Serve separately for the loopback
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

### Run manual operations in the service environment

The web and backup units receive `PATH` from their `EnvironmentFile`. The
runtime loader deliberately preserves a non-empty caller value, so loading the
file in an SSH shell does **not** prove that a tool such as `rclone` is available
to the service (or vice versa). In default mode, use the installed wrapper for
every manual operation that depends on runtime tools:

```bash
read -r -p 'Absolute installed ops directory: ' ops_root
run="$ops_root/run-with-service-env.sh"
test -x "$run"
"$run" -- /bin/sh -c 'command -v rclone'
```

It runs one absolute command in a transient user-systemd unit with the same
runtime file and keeps standard input attached. To pass a temporary deployment
control, use `--setenv AUTONOMO_NAME=value` before `--`; the wrapper rejects
other names and always supplies the selected `AUTONOMO_RUNTIME_ENV_PATH` itself.
For a nonstandard runtime file, export `AUTONOMO_RUNTIME_ENV_PATH` before
calling the wrapper. This is a **default-mode** helper; do not use it to mix
the optional SOPS control plane with default units.

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

### Updating the OCR deployment checks in an existing ops installation

This is a scoped production control-plane change, not a reason to rerun the
general installer: that installer also renders units and activates backup timers.
Use an approved full Git SHA with passing tests, and retain the previous installed
copies privately. Publishing or merging code does not update installed ops.

1. Confirm the installed ops directory and active mode from the private host
   inventory. Serialize this update with deployments/backups using the existing
   operations lock. Save the old `deploy.sh`, `sops/deploy.sh` when present,
   and `ocr-readiness.py` when present, with their permissions, in a new private
   backup directory. Record the source SHA and precisely which files existed.
2. Test the candidate helper **from the reviewed checkout** using the actual
   service runtime file (the validated candidate generation in SOPS mode).
   Missing OCR must be fixed before updating the callers. Never disable the
   readiness gate merely to make this check pass.
3. Copy `ops/ocr-readiness.py` to a temporary sibling of the installed
   `ocr-readiness.py` with mode `0644`, then atomically rename it into place.
   Both modes use this one helper in the parent ops directory.
4. Only after the helper exists, install the changed default deploy script and
   installed SOPS deploy script with mode `0755`, using a temporary sibling and
   atomic rename for each file. Preserve their one-SHA and two-SHA interfaces.
   Do not replace lib/preflight files, units, runtime configuration, or
   timers as a side effect of this update.
5. Run the **installed** base preflight and OCR helper before any deployment.
   Default: `"$ops_root/preflight.sh"`, then the helper with the service runtime
   file. SOPS: `"$ops_root/sops/preflight.sh" "$secret_sha"`, passing the full
   **staged** secret-config SHA, then the helper with that validated generation's
   `runtime.env`. Use `python3 "$ops_root/ocr-readiness.py" --runtime-env
   "$runtime_env_file"` with that exact file; do not substitute the current
   generation. Both deploy scripts repeat OCR readiness after base preflight
   and before Git fetch or service stop.
6. If validation fails, stop the rollout. Restore the saved calling deploy scripts
   first, then the previous helper if it existed, using atomic replacements.
   A newly added unreferenced helper may remain. Recheck the previous control
   plane and live service. Do not restore the accounting database or run the
   general installer as compensation.

The helper uses Python subprocess timeouts, not the platform-specific shell
`timeout` program. Each query is bounded to ten seconds; image-recognition
accuracy is verified separately. OCR is a **new-deployment** gate, not part of
the base preflight shared with recovery. Default and SOPS rollback/restore
remain available when OCR is missing. These checks do not disable a live web
service, add startup hooks, or authorize posting accounting entries.
An optional-mode config-only reversion performed through its two-SHA `deploy.sh`
is still a deployment and requires OCR readiness; it is not the `rollback.sh`
recovery path described above.

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
run="$ops_root/run-with-service-env.sh"
test -x "$run"
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
systemctl --user is-enabled autonomo-backup.timer autonomo-backup-monthly.timer \
  autonomo-backup-verification-monthly.timer
systemctl --user list-timers --all autonomo-backup.timer autonomo-backup-monthly.timer \
  autonomo-backup-verification-monthly.timer
systemctl --user show autonomo-backup.service autonomo-backup-monthly.service \
  -p Result -p ExecMainStatus -p ExecMainStartTimestamp -p ExecMainExitTimestamp
"$run" -- /bin/sh -c 'command -v rclone'
```

Expected: active web unit, current SHA matching the selected release, successful
HTTPS root/bootstrap check, `integrity=ok`, expected schema, enabled timers with
future runs, and a recent completed backup (`Result=success`, exit status 0).
A never-run unit can also show a default success status: check timestamps.
Inspect recent backup logs privately with `journalctl --user -u autonomo-backup.service -n 50 --no-pager`
if necessary. This is read-only but can display private paths. The settings page
distinguishes a locally verified pair, an acknowledged upload and the separate
monthly recovery verification. A green timer or upload marker alone is not proof
of recoverability.

## Deploy a reviewed release (production change)

Before either route: require green CI for the **exact SHA**, create a fresh
format-2 local backup pair, record the current SHA/schema, and reserve a
write-free maintenance window if migration is needed. The deploy-only readiness
helper validates that local pair without contacting remote storage. Upload and
monthly recovery-verification gaps are reported but do not block deployment.
Use the checklist setup above in the same shell.
`deploy.sh` takes exactly one full lowercase 40-character SHA, never a branch.

### Merged code

Copy the merged commit SHA from the reviewed PR. This command installs code,
restarts the service, and switches `current` after checks pass. It verifies the
SHA is reachable from fetched `master`, not merely present in the local object
database. Expected final output: `deployed_sha` and `previous_sha`.

```bash
read -r -p 'Reviewed merge commit, full SHA: ' app_sha
validate_sha "$app_sha"
"$run" --setenv AUTONOMO_DEPLOY_REF=master -- "$ops_root/deploy.sh" "$app_sha"
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
"$run" --setenv "AUTONOMO_DEPLOY_REF=$deploy_ref" -- "$ops_root/deploy.sh" "$app_sha"
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
"$run" --setenv "AUTONOMO_DEPLOY_REF=$deploy_ref" \
  --setenv AUTONOMO_ENABLE_STORAGE_MIGRATION=1 -- "$ops_root/deploy.sh" "$app_sha"
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
"$run" -- "$ops_root/rollback.sh" "$target_sha"
```

If the target schema is older, use this alternative with the previously recorded
snapshot. Verify its integrity, foreign keys, and schema by the
[recovery checks](DISASTER_RECOVERY.md#download-and-verify-a-backup) first;
the rollback script requires a private snapshot but does not establish that it
is the correct schema for you. **All writes since that snapshot are lost from
the active database.** Preserve them separately before proceeding.

```bash
read -r -p 'Installed older release, full SHA: ' target_sha
read -r -p 'Absolute verified pre-migration SQLite snapshot: ' rollback_snapshot
validate_sha "$target_sha"
"$run" --setenv "AUTONOMO_ROLLBACK_SNAPSHOT=$rollback_snapshot" \
  -- "$ops_root/rollback.sh" "$target_sha"
```

After any deployment or rollback, rerun the checklist in a fresh shell so it
resolves the new `current`. Verify selected evidence reads from Google and
Yandex privately; a database status alone does not check the provider.

## Backups and monthly drill

Daily and monthly jobs archive the private root with a consistent SQLite
snapshot. Local retention is **35 daily / 13 monthly archive pairs by count**
unless overridden, not a guarantee of days covered. Bucket lifecycle is
configured separately. See [exact coverage and exclusions](DISASTER_RECOVERY.md#what-is-backed-up).

Running a backup is a production change: it creates/prunes local archives,
uploads to configured remotes, may copy local evidence, and reconciles
`AUTONOMO_RECONCILE_BACKENDS`. Both classes use this common script. The remote
must be a crypt remote rooted at the matching daily/monthly bucket prefix;
without a configured remote a successful job can be local-only.

For a deliberate fresh backup, after the checklist setup:

```bash
"$run" -- "$ops_root/backup.sh"
```

Expect `archive=` and `manifest=`. The format-2 marker records local validation
before remote work and distinguishes `pending`, `acknowledged`, `failed`, and
`disabled`; its legacy `offsite` Boolean remains compatible with older readers.
The ordinary backup never reads an uploaded object back. Upload/configuration
failure returns nonzero and preserves the locally verified marker.

The monthly backup still runs on day 1. On day 2 the separate
`autonomo-backup-verification-monthly.timer` downloads the exact pair named by
the current-month marker, restores it into private scratch, validates hashes,
SQLite integrity, foreign keys and schema, then removes plaintext scratch.
Its failure is recorded and alerted independently and never blocks deployment.
Run `verify-backup.sh monthly` for a deliberate manual retry. `AUTONOMO_ALERT_WEBHOOK`
is optional; no external alert delivery exists unless configured and tested.

For the monthly exercise, follow the [isolated recovery drill](DISASTER_RECOVERY.md#download-and-verify-a-backup).
It downloads through crypt, validates the pair, extracts to an empty directory,
and checks SQLite. **Do not run `restore.sh --yes-restore` for a drill.** That
mode replaces the live database. Production replacement is described only in
[production cutover](DISASTER_RECOVERY.md#production-cutover-dangerous).

### Adding the manual-operation wrapper to an existing default installation

This procedure also updates an existing wrapper. Publishing application code does
not update installed ops. Compare the installed `run-with-service-env.sh` with the
reviewed source at the pinned SHA before relying on one-shot controls.

Take the shared operations lock in a short separate process. Retain the prior
wrapper privately with its mode and digest, or record that it was absent. Install
the reviewed wrapper through a temporary sibling with mode `0755` and the existing
service ownership; validate with `bash -n`, then atomically replace it. Run the
diagnostic below and the service-environment `rclone` check. On validation failure,
atomically restore the saved wrapper and recheck the baseline service; if it was
previously absent, preserve the failed candidate privately and remove only that
new installation. Stop using the failed wrapper. Exit the lock-owning process
before invoking backup, deploy or rollback.

Do not run the general installer, rewrite units/runtime or change timers for this
update. Leave SOPS callers untouched in a default-only update.

### Verify the installed service-environment wrapper

Older installed wrappers passed overrides as `systemd-run --setenv` properties;
values from `EnvironmentFile` can override those values. The reviewed wrapper
instead applies one-shot assignments with `env` to the command after systemd has
loaded the runtime file. A correct source checkout does not prove the installed
wrapper has this behavior.

Run this read-only diagnostic before maintenance, in the default-mode checklist
shell. Use the same transport ref and migration setting intended for deployment.
It asserts values inside the child process without printing the environment.

```bash
read -r -p 'Reviewed transport ref: ' deploy_ref
read -r -p 'This deployment needs migration (0 or 1): ' migration_flag
[[ "$migration_flag" == 0 || "$migration_flag" == 1 ]]
expected_runtime="$(runtime_env_path)"
"$run" --setenv "AUTONOMO_DEPLOY_REF=$deploy_ref" \
  --setenv "AUTONOMO_ENABLE_STORAGE_MIGRATION=$migration_flag" -- /bin/bash -c '
    set -euo pipefail
    test "$AUTONOMO_DEPLOY_REF" = "$1"
    test "$AUTONOMO_ENABLE_STORAGE_MIGRATION" = "$2"
    test "$AUTONOMO_RUNTIME_ENV_PATH" = "$3"
    command -v rclone >/dev/null
    printf "service environment overrides verified\n"
  ' -- "$deploy_ref" "$migration_flag" "$expected_runtime"
```

For a compiled frontend, additionally check tools through the exact build launcher
from [frontend preparation](FRONTEND_BUILD.md#install-and-select-the-pinned-build-tools).
Do not persist deployment flags or weaken the wrapper's argument validation to
make the diagnostic pass. A failed assertion requires diagnosis/update before
retrying deployment; a successful probe with values identical to runtime defaults
does not by itself prove override precedence. Compare the installed reviewed code
as well; exercise conflicting values only in an isolated synthetic runtime.

### Retry after a preparation failure

Keep each attempt's log and result in the private maintenance record. First inspect
`current`, the actual process/unit, live schema/integrity, and target directory;
do not infer the failure stage from the last displayed progress message.

| Observed state | Next action |
|---|---|
| Target absent; deployment has not changed the baseline release/unit or database | Resolve the authorized preparation issue, repeat the installed wrapper/tool preflights and retry the same approved SHA. |
| Compiled-UI target (contract 1) exists without a valid completion receipt | Preserve it and stop this rollout for separately scoped recovery; do not repair, delete or retry the partial release in place. |
| Compiled-UI target (contract 1) has a valid receipt and installed resources | Verify it with the installed preparation helper; reuse without Node/npm or rebuilding. |
| Service switched, schema changed, or state is uncertain | Follow deployment acceptance/rollback rules; do not treat this as a preparation retry. |

Before a retry, recheck exact-SHA CI, remote-ref reachability, local backup age and
schema binding, and absence of concurrent jobs. Preserve the accounting baseline
after the completed backup; configured backup reconciliation can itself change
storage records. Do not silently replace the target with a newer branch head.
If maintenance is paused or abandoned, finish it safely as below. A later retry
needs a new write-free window and fresh baseline checks.

### Finish or pause the maintenance window

Before stopping any timer, record its `ActiveState`, `UnitFileState`, and configured
schedule privately. Stop only the daily, monthly and monthly-verification timers
involved in this operation; stopping a timer does not stop an already-running job.
Wait for those jobs to finish without killing them, then perform the authorized
backup. Exclude other CLI/user writes through final acceptance. Timer suspension
and an announcement alone do not technically prevent application writes.

Restore scheduling only after either final acceptance or verification that an
early failure left the baseline service healthy and its database compatible with
its release. If the service was manually stopped, first follow the early-failure
restart checks. Restore only timers that were active before maintenance; leave
previously inactive timers inactive. Do not use `enable`, `disable` or the general
installer, and do not rewrite calendar settings. Compare enabled states and
schedule expressions to the saved record, not randomized next-run timestamps.

For the three recorded timers, this is the restoration decision, after the safety
checks above. Repeat it with each timer's saved state; it is not an unconditional
shell-exit trap:

```bash
# timer and prior_active come from the private pre-maintenance record.
case "$timer" in
  autonomo-backup.timer|autonomo-backup-monthly.timer|autonomo-backup-verification-monthly.timer) ;;
  *) exit 1 ;;
esac
case "$prior_active" in
  active) systemctl --user start "$timer" ;;
  inactive) test "$(systemctl --user show "$timer" -p ActiveState --value)" = inactive ;;
  *) printf 'Resolve unexpected saved timer state before resuming\n' >&2; exit 1 ;;
esac
```

Confirm the monthly verification timer is active when it was active originally;
if it was already inactive, report that pre-existing gap rather than activating it
as a side effect. Explicitly announce completion or a safe pause and whether users
may resume writes. When code/schema compatibility or possible later writes are
uncertain, retain the write-free window and keep operational writers suspended
pending recovery direction. Preserve the timer record so a later operator can
restore the intended state.

## Optional SOPS control plane

[The SOPS runbook](../sops/README.md) contains bootstrap, artifact mapping, exact
recipient validation, dual-SHA deployment, and recovery. Copying that directory
or preparing the private Git repository does not enable it. Do not mix its
units, environment files, or rollback scripts with the default control plane.

## Compiled frontend releases

Releases that declare frontend build contract 1 require the pinned Node/npm
build tools during preparation, and include their compiled resources in the
installed Python package. Before the first such release on an existing host,
follow [Frontend build and installed-helper update](FRONTEND_BUILD.md). Update
only the active deployment caller and its shared helpers; do not run the general
installer or change timers for this update. Legacy rollback remains build-free.
