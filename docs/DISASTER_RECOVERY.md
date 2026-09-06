# Disaster recovery

Use this runbook to recover data without guessing which files a backup contains.
The default deployment uses private server files; SOPS is a separate
[optional control plane](../ops/sops/README.md). A prepared secrets repository
does not prove that it contains the credentials needed for recovery.

These procedures describe supported tools, not a claim that a real-data disaster
recovery exercise has passed or that an off-server credential bundle exists.
Record that evidence separately, privately, with the test date, backup pair,
application SHA, schema, missing material, and observed result.

## What is backed up

| Material | Authoritative location in default mode | Recovery coverage |
|---|---|---|
| SQLite, including file/replica identities and accounting records | `AUTONOMO_PRIVATE_ROOT/autonomo.sqlite` | Consistent snapshot included as `autonomo.sqlite` |
| Evidence bytes | Original Google files; registered Yandex crypt replicas; local files under private-root | Local regular files included; remote-only bytes require their provider credentials and locator records |
| Application config, including Picker settings | Private-root `config.yaml` | Included when a regular file inside the root |
| Runtime environment | `~/.config/autonomo-tax/runtime.env` unless overridden | Usually outside the archive; needs a separate recoverable copy |
| Google reader service-account JSON, OAuth token/client | Paths in enabled backend `credential_ref` values | Included only if actual regular files are inside private-root |
| Session principal secret | `AUTONOMO_SESSION_PRINCIPAL_SECRET_FILE`, or private runtime configuration | Coverage depends on its real location; replacement invalidates existing sessions |
| S3 access and crypt configuration | `AUTONOMO_RCLONE_CONFIG`, often outside private-root | Separate recoverable copy required if outside the root |
| Application release, installed ops, unit definitions | Code Git plus release/ops roots and user-systemd directory | Not normally in the archive; record SHA and rebuild from reviewed code |
| Tesseract executable and English model | Ubuntu `tesseract-ocr` / `tesseract-ocr-eng` packages | Not in private-root backups; reinstall and verify in the service environment |

The [backup helper](../scripts/backup_private_root.py) produces a pair:
`private-root-<timestamp>.tar.gz` and `private-root-<timestamp>.manifest.json`.
The manifest records format version, creation timestamp, archive basename and
SHA-256, and every member's relative path, size, and SHA-256. It does **not**
record application SHA or database schema. Record those separately.

On a replacement server, restore the [OCR dependency](../ops/PROVISIONING.md#local-ocr-dependency)
before the new-release OCR readiness check. Deliver the shared `ocr-readiness.py` with the
installed ops files; restoring the database alone neither installs OCR nor
clears earlier document-review issues. Readiness must pass under the intended
service runtime, followed by a private extraction-only image check. Do not
reimport documents or waive accounting checks to compensate for missing packages.
OCR readiness does not gate rollback or restore of an existing release; repair
the dependency before a subsequent new deployment or image intake.
A SOPS config-only reversion using paired `deploy.sh` also remains OCR-gated;
this differs from the dedicated rollback/restore commands.

The helper snapshots SQLite through its backup API and verifies integrity and
foreign keys. It archives regular files from private-root, with these exclusions:

- Top-level `backups/`, `cache/`, and `browser/` trees.
- The live database and its WAL/SHM/journal files; the consistent snapshot is
  substituted for them.
- Symlinks and non-regular entries. Empty directories are not preserved.
- Files outside private-root. Credential references and external Drive paths
  are not followed into the archive.

Archive and manifest are local plaintext files with mode `0600`; rclone crypt
encrypts their upload. Local retention defaults to 35 daily or 13 monthly
pairs by count. Cloud lifecycle is a separate provisioning policy. SQLite is
consistent, but this is not a filesystem-wide point-in-time snapshot: quiesce
uploads/config edits when a coordinated recovery point matters.

**A private-root backup does not guarantee recovery of credentials from
`~/.config`.** In a private inventory, check runtime paths and enabled backend
credential references without printing values. Confirm a separately encrypted,
off-server copy can actually be decrypted. Include runtime.env, config,
Google credentials, session secret, and the complete rclone configuration.
Keep the recovery decryption key independently accessible. Do not store the
only copy of crypt configuration inside a backup encrypted by that same config.

S3 access keys authorize bucket access. The rclone crypt password, optional
`password2` salt, filename settings, and remote/prefix mapping allow decryption.
Reissuing an S3 access key cannot recreate lost crypt material. Rclone's stored
password obscuring is reversible, so protect `rclone.conf` as a secret even
when its values look encoded. See [rclone crypt](https://rclone.org/crypt/).

## Download and verify a backup

This is the monthly drill, not production restore. Prerequisites: a trusted
machine, Bash, Python 3.11+, rclone, sufficient private scratch space, and a
reviewed code checkout containing the restore helper. Use a recovered private
rclone config with read access and the original crypt material. These commands
list/read remote objects and write only a new scratch directory. Never use the
raw S3 remote for the download: it returns ciphertext and encrypted names.

Run in one fresh shell. Select the daily or monthly **crypt** remote from the
private inventory. Listing displays backup filenames; keep it private.

This procedure is for a recovery machine with its intended recovered rclone
environment. If an isolated drill is deliberately performed on the production
host, do not use an ad-hoc SSH shell: invoke the installed default-mode
`run-with-service-env.sh` wrapper so its `PATH` and runtime file match the
backup service.

The installed monthly verifier automates this drill on day 2 for the exact pair
recorded by the current-month format-2 monthly marker. It keeps a latest-attempt
marker and a separate last-success marker, validates the restored SQLite database,
and always removes downloaded plaintext. This scheduled evidence reports recovery
health; it is deliberately not a deployment gate. Manual recovery still follows
the commands below and must not rely on a marker alone.

```bash
set -euo pipefail
umask 077
read -r -p 'Absolute reviewed code checkout: ' code_root
read -r -p 'Absolute private recovery rclone.conf: ' recovery_rclone
read -r -p 'Backup crypt remote, including colon: ' backup_remote
test -f "$code_root/scripts/restore_private_root.py"
test -f "$recovery_rclone"
drill_root="$(mktemp -d "${TMPDIR:-/tmp}/autonomo-drill.XXXXXX")"
chmod 700 "$drill_root"
rclone --config "$recovery_rclone" lsf --files-only "$backup_remote"
read -r -p 'Exact archive basename from that listing: ' archive_name
[[ "$archive_name" == private-root-*.tar.gz && "$archive_name" != */* ]]
manifest_name="${archive_name%.tar.gz}.manifest.json"
archive="$drill_root/$archive_name"
manifest="$drill_root/$manifest_name"
rclone --config "$recovery_rclone" copyto \
  "${backup_remote%/}/$archive_name" "$archive"
rclone --config "$recovery_rclone" copyto \
  "${backup_remote%/}/$manifest_name" "$manifest"
chmod 600 "$archive" "$manifest"
restore_root="$drill_root/restored"
python3 "$code_root/scripts/restore_private_root.py" \
  --archive "$archive" --manifest "$manifest" --target-root "$restore_root"
python3 - "$restore_root/autonomo.sqlite" <<'PY'
import sqlite3
import sys
from pathlib import Path
with sqlite3.connect(Path(sys.argv[1]).resolve().as_uri() + '?mode=ro', uri=True) as db:
    assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    assert db.execute('PRAGMA foreign_key_check').fetchone() is None
    print('integrity=ok')
    print(f'schema={db.execute("PRAGMA user_version").fetchone()[0]}')
PY
```

Expected: `restored_root=...`, `integrity=ok`, and the schema you recorded for
that backup. The restore helper checks the archive basename and whole-file
hash before extraction, then the member set, safe paths, per-file sizes and
hashes. It rejects a nonempty destination. It does not check SQLite itself;
that is why the second command is required. Manifest hashes detect mismatch,
not an independently signed provenance guarantee.

On missing manifest or any mismatch, stop and select another complete pair.
Do not edit a manifest to make a damaged backup pass. A failed extraction may
leave partial files; retain them for investigation and use another empty target
for a retry. Do not overwrite or merge into an existing private-root.

The installed default control plane offers equivalent archive-only extraction
as `restore.sh --yes-restore-archive ARCHIVE MANIFEST EMPTY_TARGET`. It does not
load production runtime or restart services. Use the installed copy for this
wrapper: the source-tree default wrapper expects a helper beside it, which the
installer supplies. The source helper command above works directly in a checkout.
There are no `backup list`, `backup verify`, or `backup drill` CLI commands.

## Optional isolated web smoke

First complete the verified extraction above. Use a code version compatible with
the restored schema, with its application venv installed. Prefer a recovery
machine/container without live provider credentials or production mounts. A
copied SQLite database still contains external replica locators and credential
paths; changing the private-root alone does not neutralize them.

Do **not** start the restored `config.yaml`: its absolute paths may still point
to production. In the scratch directory, create a new `drill-config.yaml` with
mode `0600`, containing only this YAML. Its relative paths resolve from that
file, not from the code checkout. Creating it affects scratch files only.

```yaml
ledger_db: restored/autonomo.sqlite
inbox_root: drill-inbox
archive_root: drill-evidence
cache_root: drill-cache
```

In the same shell, select the compatible installed release and an unused
loopback port different from production. This starts a foreground test process
and may write only the test application's cache/session state. It does not
install units or enable timers. The empty environment avoids inherited
migration/session settings; it is not an OS security sandbox.

```bash
read -r -p 'Absolute compatible release with .venv: ' drill_release
read -r -p 'Unused drill port (not the production port): ' drill_port
[[ "$drill_port" =~ ^[0-9]+$ && "$drill_port" != 8765 ]]
test -f "$drill_root/drill-config.yaml"
env -i PATH="$PATH" AUTONOMO_PRIVATE_ROOT="$drill_root" \
  "$drill_release/.venv/bin/autonomo-web" \
  --project-root "$drill_release" --config "$drill_root/drill-config.yaml" \
  --db "$restore_root/autonomo.sqlite" --inbox-root "$drill_root/drill-inbox" \
  --archive-root "$drill_root/drill-evidence" --cache-root "$drill_root/drill-cache" \
  --host 127.0.0.1 --port "$drill_port"
```

Expect the loopback URL. In a second terminal, use the existing
`ops/healthcheck.py` with that URL, or request the root page only. Do not open
evidence, request Picker tokens, run intake/reconcile, or launch backup timers.
Stop with Ctrl-C. Record the result; arrange deliberate cleanup of the exact
scratch directory and downloaded plaintext when no longer needed. This smoke
does not prove cloud replication or a production cutover.

## Recovery scenarios

### 1. SQLite is damaged

If no usable snapshot exists but the source books do, see [Rebuilding history from source books safely](HISTORY_REPLAY.md) before attempting a replay.

Stop user/CLI writes and scheduled writers. Preserve the failed database with
its WAL/SHM/journal and any logs in private quarantine; do not discard sidecars
or overwrite the only copy. Select a backup predating the corruption, perform
the isolated checks, and confirm schema compatibility. If a new safety snapshot
fails integrity, keep the raw failed set for investigation and explicitly
record that a clean pre-cutover safety snapshot was unavailable. Proceed only
under the [cutover prerequisites](#production-cutover-dangerous).

### 2. The private-root is lost

Stop the service and timers so they cannot create replacement state. Recover
the archive pair plus independently stored credentials. Restore into an empty
sibling directory, verify SQLite and local evidence, and audit config paths
and backend credential references. The archive may lack remote-only evidence;
recover those reads from Google originals or Yandex replicas. Never assume an
external credential file survived just because SQLite names it.

### 3. The server is lost

On a trusted replacement host, recover the credential bundle and crypt material
first, then download/verify data. Recreate the service user, private directories,
reviewed code release, default runtime file, ops installation, and tailnet
access using [provisioning](../ops/PROVISIONING.md). Review any absolute paths
that changed, including those stored in SQLite. Do not start the installer or
timers until the restored data and destinations are ready. If SOPS was actually
enabled, follow its separate identity/bootstrap recovery, not the default units.
Keep the old host fenced off; never run two writers against the same archive.

### 4. Google Drive is unavailable

Keep the original file IDs and primary designations. Existing verified Yandex
replicas can serve as fallback when their credentials and crypt configuration
work; a recorded `available` status is not a fresh connectivity check. Test a
known file read and compare its downloaded SHA-256 to `files.content_sha256` in
the private maintenance environment. A fallback read can update replica status
and cache, so it is not a purely read-only database check. Diagnose network,
sharing, or [OAuth failure](../ops/PROVISIONING.md#google-troubleshooting).
Do not re-upload the archive or bulk-reconcile to Google to fix authentication.
Unmirrored files remain unavailable until Google or another copy is restored.

### 5. Crypt configuration is lost

Recover the exact rclone config or original crypt password/salt and settings
from an independent recovery bundle. A new S3 key only restores bucket access.
If no copy of the crypt material exists, the encrypted objects cannot be
recovered by resetting access credentials. Preserve the bucket unchanged; use
surviving Google/local evidence where possible. A new crypt setup would protect
new backups, not decrypt old ones. Treat that as a separately approved recovery
decision and document unrecoverable data.

## Production cutover (dangerous)

This section changes live data and availability. It requires explicit approval
of the backup, target host/root, compatible release/schema, downtime, and loss
of writes after the selected snapshot. It is not part of a monthly drill.

Prerequisites:

1. A completed isolated restore and SQLite check; identified missing files and
   verified credential recovery, including crypt material.
2. A recorded current SHA/schema/config, prior unit and timer states, a rollback
   destination, and a private safety snapshot/archive of recoverable current
   state. For damaged SQLite, preserve the raw failed set and record the exception.
3. No concurrent deploy, backup, intake, reconcile, or manual SQLite writer.
   Stop scheduling new jobs, wait for active jobs, then stop the web service.
4. Verify the service is stopped. If WAL/SHM/journal files remain after clean
   shutdown, preserve the entire database set and resolve that condition before
   a single-file replacement. The live restore helper does not manage stale
   sidecars. Never delete them merely to make a check pass.

### Database-only replacement in default mode

Use a fresh Bash shell on the default-mode host. The setup below resolves paths
without requiring the damaged live database to pass a health check. Capture the
previous timer states privately. The following stops timers and the web service; stopping
a timer does not stop an already-running backup. Wait for such a job to finish
before continuing. Create the safety snapshot before these commands if the
database is healthy and retain its verified path for rollback.

```bash
set -euo pipefail
read -r -p 'Absolute installed default ops directory: ' ops_root
source "$ops_root/lib.sh"
load_runtime_env
data="$(private_root)"
unit="autonomo-web-$(current_release_sha).service"
systemctl --user is-enabled autonomo-backup.timer autonomo-backup-monthly.timer || true
systemctl --user is-active autonomo-backup.timer autonomo-backup-monthly.timer || true
systemctl --user stop autonomo-backup.timer autonomo-backup-monthly.timer
systemctl --user show autonomo-backup.service autonomo-backup-monthly.service -p ActiveState
for backup_unit in autonomo-backup.service autonomo-backup-monthly.service; do
  test "$(systemctl --user show "$backup_unit" -p ActiveState --value)" = inactive
done
# If a job was running, wait for it to finish and repeat; do not kill a backup mid-write.
# Continue only when all other CLI writers and users are also quiescent.
systemctl --user stop "$unit"
test "$(systemctl --user is-active "$unit" || true)" = inactive
test ! -e "$data/autonomo.sqlite-wal"
test ! -e "$data/autonomo.sqlite-shm"
test ! -e "$data/autonomo.sqlite-journal"
read -r -p 'Absolute verified SQLite snapshot compatible with this release: ' approved_snapshot
"$ops_root/restore.sh" --yes-restore "$approved_snapshot"
```

Expected: `restored=` and `preserved=` paths, active web unit, successful health
check. The helper validates the candidate, locks operations, stops the unit,
retains the old database as `autonomo.sqlite.before-restore-<timestamp>`, installs
the replacement, then starts/checks the unit. On start/health failure it attempts
to restore the preserved DB; verify the outcome yourself. It neither restores
all credentials nor changes release schema for you.

Rerun the default checklist, check expected record counts and representative
evidence reads, then restore **only the timers that were active before**. If
checks fail, stop writers again and restore the verified safety snapshot with
the same procedure. Keep both failed and previous data for investigation. If
the previous database was corrupt, restarting it is not a viable rollback;
leave the service stopped and choose another recovery point.

### Whole-root or replacement-host cutover

There is no transactional whole-root cutover command. While all writers and
timers are stopped, retain the old root intact at a specifically reviewed
quarantine path, install the verified restored tree at the intended private
root, and reinstate external credentials with `0600` files / `0700` directories.
Do not overlay a populated root. Recheck config/SQLite paths, ownership,
release-schema compatibility, and default preflight before starting the unit.
The archive excludes symlinks; SOPS generation links and release links require
their own control-plane reconstruction.

Rollback means stopping the new unit and restoring the retained root,
external config/credentials, and prior compatible release as one set. On a new
host, keep it unavailable until a verified replacement is ready. Cross-schema
code rollback additionally needs the pre-migration snapshot described in the
[deployment runbook](../ops/README.md#rollback). Only re-enable scheduled writes
after application and recovery checks pass.

## Synthetic validation of the backup helpers

This exercise uses no production files, credentials, network, or systemd. Run
from a reviewed checkout with its test dependencies installed. The shell defines
the checkout and Python interpreter; the Python block creates only synthetic
SQLite/text data inside a temporary directory and removes that directory on
exit. Expected final line: `synthetic backup/restore checks passed`.

```bash
set -euo pipefail
code_root="$(git rev-parse --show-toplevel)"
read -r -p 'Absolute Python interpreter with project dependencies: ' test_python
"$test_python" - "$code_root" <<'PY'
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

code = Path(sys.argv[1])
def load(name):
    spec = importlib.util.spec_from_file_location(name, code / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

backup = load('backup_private_root').create_backup
restore = load('restore_private_root').restore_backup
with tempfile.TemporaryDirectory(prefix='autonomo-synthetic-') as scratch:
    root = Path(scratch) / 'private'
    root.mkdir(mode=0o700)
    database = root / 'autonomo.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE sample (id INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO sample VALUES (1)')
    (root / 'config.yaml').write_text('ledger_db: autonomo.sqlite\n')
    archive, manifest = backup(private_root=root, database=database,
                               output_dir=root / 'backups', keep=2)
    target = Path(scratch) / 'restored'
    restore(archive=archive, manifest=manifest, target_root=target)
    with sqlite3.connect((target / 'autonomo.sqlite').as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert db.execute('PRAGMA foreign_key_check').fetchone() is None
        assert db.execute('SELECT count(*) FROM sample').fetchone()[0] == 1
    def rejected(destination):
        try:
            restore(archive=archive, manifest=manifest, target_root=destination)
        except ValueError:
            return
        raise AssertionError('unsafe restore unexpectedly succeeded')
    rejected(target)  # nonempty destination
    original_manifest = manifest.read_bytes()
    payload = json.loads(original_manifest)
    payload['archive_sha256'] = '0' * 64
    manifest.write_text(json.dumps(payload))
    rejected(Path(scratch) / 'bad-hash')
    manifest.write_bytes(original_manifest)
    original_archive = archive.read_bytes()
    archive.write_bytes(original_archive + b'corrupt')
    rejected(Path(scratch) / 'bad-archive')
    archive.write_bytes(original_archive)
    payload = json.loads(original_manifest)
    payload['files'][0]['sha256'] = '0' * 64
    manifest.write_text(json.dumps(payload))
    rejected(Path(scratch) / 'bad-member')
print('synthetic backup/restore checks passed')
PY
```

This checks existing helper behavior, not a remote backup or live recovery.
Keep the [privacy boundary](PRIVACY.md) when recording any real drill results.
