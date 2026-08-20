# Production operations

This directory manages a single-host, user-systemd deployment. Runtime data is
outside Git. Application releases and secret configurations are independently
pinned by full Git commit SHA; `current` links are atomic convenience pointers.
Never deploy a branch name or the moving head of the secrets repository.
The `ops/` directory is a small control plane installed outside those releases,
so it can bootstrap a code version (including PR #1) that predates `ops/`.

## Bootstrap the host

Install `git`, `sops`, `age`, Python 3, and `flock`. Then run as the non-root
service user after cloning the code repository and creating the private root:

```bash
install -d -m 700 ~/.config/autonomo-tax ~/.ssh ~/.local/share/spain-autonomo-taxes
install -m 600 \
  /srv/spain-autonomo-taxes/repository/ops/ops-bootstrap.env.example \
  ~/.config/autonomo-tax/ops-bootstrap.env
# Replace placeholders, install the server age identity and read-only deploy key,
# and pin GitHub's reviewed SSH host keys in the configured known_hosts file.
ops/config-sync.sh --activate <full-secret-config-sha>
/srv/spain-autonomo-taxes/repository/ops/install-systemd-user-units.sh
loginctl enable-linger "$USER"
```

`ops-bootstrap.env` is a strict, non-shell dotenv file. It contains operational
paths and revision selectors, never secret values. Do not quote values or add
spaces around `=`. The age identity and SSH deploy key are separate `0600`
regular files; the recovery identity is never installed on the server.

The installer copies the control-plane scripts and private-root backup tool into
`AUTONOMO_OPS_ROOT`, so the first deployed code release does not need to contain
those scripts.

See [PROVISIONING.md](PROVISIONING.md) for the one-time GitHub, Yandex, Google
Drive, and server-runtime setup steps.

Keep this `ops/` directory at the `AUTONOMO_OPS_ROOT` control-plane location,
such as `~/.local/lib/autonomo-ops`; execute `deploy.sh` from there. It renders the
unit and health check independently of the target release, while the target
release supplies only the application venv and code.

Configure Tailscale Serve separately to forward the private tailnet hostname to
`http://127.0.0.1:8765`; the application refuses non-loopback listeners.

## Private secrets repository

The approved repository is
[`svonidze/spain-autonomo-taxes-secrets`](https://github.com/svonidze/spain-autonomo-taxes-secrets),
configured for deployment as `github.com:svonidze/spain-autonomo-taxes-secrets.git`
with the forced SSH user `git`; its branch is `main`. Its
runtime tree is fixed:

```text
.sops.yaml
prod/runtime.sops.env
prod/config.sops.yaml
prod/google-drive-reader-service-account.sops.json
prod/google-drive-oauth-client.sops.json       # optional after authorization
prod/google-drive-oauth-token.sops.json
prod/rclone.sops.ini
prod/storage-backends.sops.json
```

The `.sops.yaml` creation rule must address every file to both public recipients:

```yaml
creation_rules:
  - path_regex: ^prod/.*\.sops\.(env|yaml|json|ini)$
    age:
      - age1replace-server-recipient
      - age1replace-offline-recovery-recipient
```

Generate the server identity on the server and the recovery identity on a
separate trusted device:

```bash
umask 077
age-keygen -o ~/.config/autonomo-tax/sops-age.key
age-keygen -y ~/.config/autonomo-tax/sops-age.key
```

Store the recovery private identity in a password manager/offline recovery
bundle. Put only its public recipient in `.sops.yaml` and
`AUTONOMO_EXPECTED_AGE_RECIPIENTS`. Add a repository-specific GitHub deploy key
with read-only access and keep its private half only at
`AUTONOMO_SECRET_DEPLOY_KEY_PATH`.

Keep `main` protected against force-push and deletion. Require review for secret
changes where the GitHub plan permits it, and record the reviewed full commit
SHA in the deploy command; the server rejects revisions no longer reachable
from the configured branch.

Create plaintext source files only on a trusted workstation with `umask 077`,
then convert them in place before staging anything in Git:

```bash
sops encrypt --in-place prod/runtime.sops.env
sops encrypt --in-place prod/config.sops.yaml
sops encrypt --in-place prod/google-drive-reader-service-account.sops.json
sops encrypt --in-place prod/google-drive-oauth-token.sops.json
sops encrypt --in-place prod/rclone.sops.ini
sops encrypt --in-place prod/storage-backends.sops.json
git diff --check
```

`storage-backends.sops.json` has the shape
`{"backends": [...]}`. Each entry uses stable credential references below
`runtime-config/current/credentials/`. The deploy unit applies the complete list
in one SQLite transaction after any schema migration and before the web process
starts. Missing backend keys are not deleted automatically.

`config-sync.sh` fetches an exact commit reachable from the configured branch,
verifies that every encrypted file contains both expected recipients, decrypts
the allowlisted files into a `0700` staging directory under the private root,
validates them, and then creates an immutable generation. Only the `current`
symlink is changed during activation. Successful deploys retain only the active
and immediately previous plaintext generations; any older revision is
re-materialized from its pinned ciphertext SHA when needed. The sync never
prints decrypted values.

## Deploy and rollback

Deploy the immutable merge commit from GitHub, not the PR ref or a moving branch:

```bash
ops/deploy.sh \
  cd18bdf8d55aa4ddcc7fe2f6da029ec928b27d4f \
  0123456789abcdef0123456789abcdef01234567
ops/rollback.sh <previous-40-character-sha>
```

When rolling a schema-18 storage release back to a schema-17 release, set
`AUTONOMO_ROLLBACK_SNAPSHOT` to the pre-migration SQLite snapshot. The rollback
script copies that snapshot before it starts the older unit.

`deploy.sh` acquires a common lock, stages and validates the exact secret commit,
fetches and verifies the exact application commit, creates an isolated worktree
and venv, renders a unit named by both SHAs, snapshots SQLite before migrations
or authoritative backend changes, and starts the new deployment. A failed
migration, config apply, service start, or health check restores the previous
database, secret generation, release link, and service.

Successful deployments record the application-to-secret binding outside the
immutable code release. `rollback.sh` re-fetches and validates the target
release's recorded secret SHA before it stops the current service. This also
allows a configuration-only rotation by deploying the current application SHA
with a new secret SHA. To undo only that rotation, deploy the same application
SHA again with the previous secret SHA; `rollback.sh` selects application
history, not secret-only history.

For the storage-schema release set `AUTONOMO_ENABLE_STORAGE_MIGRATION=1`.
The release-specific unit then runs `autonomo-tax storage migrate --startup
--require-complete` before binding the listener. Keep it at `0` for PR #1 and
other schema-17 releases. Startup backfill inventories existing local or mounted
files only; Google/Yandex uploads reconcile after the service is healthy.

For the first managed cutover set `AUTONOMO_LEGACY_UNIT` to the existing user
unit name. The deploy script stops it only after all preflight checks pass,
starts it again on a failed release, and disables it only after the new release
passes health checks. The legacy unit file and checkout remain untouched.

## Backup and restore

`autonomo-backup.timer` creates a daily verified SQLite snapshot plus a full
private-root archive; `autonomo-backup-monthly.timer` retains a monthly archive.
Their rclone remotes must be `crypt` remotes rooted exactly at the raw bucket
prefixes `backups/daily/` and `backups/monthly/`. The daily job also invokes
`storage reconcile` for `AUTONOMO_RECONCILE_BACKENDS`, so registered external
or mounted sources receive a verified Yandex replica instead of relying on an
ad-hoc directory copy. `AUTONOMO_ALERT_WEBHOOK` is required in production.

Run a restore drill monthly:

```bash
ops/restore.sh --yes-restore /absolute/path/to/autonomo-YYYYMMDDTHHMMSS.sqlite
ops/restore.sh --yes-restore-archive \
  /absolute/path/to/private-root-YYYYMMDDTHHMMSS.tar.gz \
  /absolute/path/to/private-root-YYYYMMDDTHHMMSS.manifest.json \
  /absolute/path/to/empty-restore-drill
```

The restore command validates the snapshot, stops only the versioned service,
retains the old DB as `autonomo.sqlite.before-restore-...`, then starts and
checks the service. Secret synchronization and preflight finish before the
service is stopped; a failed start restores the previous database and secret
generation. It intentionally does not delete the prior file. The archive
restore command only accepts an empty target root; validate its SQLite database
and start the app on an alternate port before any production cutover.

## Key rotation and disaster recovery

For a planned server replacement, add the new server recipient to `.sops.yaml`,
run `sops updatekeys -y` for every encrypted file, verify decryption with the new
server identity, commit, and deploy the new secret SHA. Remove the old recipient
in a second reviewed commit only after the new host is healthy.

If an age identity may be compromised, `updatekeys` is insufficient: run
`sops rotate --in-place` for every file after changing recipients, rotate every
provider credential exposed by historical ciphertext, and revoke the old GitHub
deploy key. Historical Git objects remain decryptable with a compromised old
identity until the underlying values themselves are revoked.

For total server loss, use the offline recovery identity on a trusted device to
decrypt the repository, generate a new server identity, update/rotate keys,
create a fresh read-only deploy key, and only then restore Yandex backups. The
server age identity and deploy key are bootstrap credentials and are never
recovered from the secrets repository itself.
