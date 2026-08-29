# Production operations

This directory manages a single-host, user-systemd deployment. Runtime data is
outside Git. Releases are immutable directories named by a full Git commit SHA;
`current` is merely an atomic convenience link. Never deploy a branch name.
The `ops/` directory is a small control plane installed outside those releases,
so it can bootstrap a code version (including PR #1) that predates `ops/`.

## Bootstrap the host

Run as the non-root service user after cloning the repository to
`/srv/spain-autonomo-taxes/repository` and creating the private root:

```bash
install -d -m 700 ~/.config/autonomo-tax ~/.local/share/spain-autonomo-taxes
cp /srv/spain-autonomo-taxes/repository/ops/runtime.env.example ~/.config/autonomo-tax/runtime.env
chmod 600 ~/.config/autonomo-tax/runtime.env
/srv/spain-autonomo-taxes/repository/ops/install-systemd-user-units.sh
loginctl enable-linger "$USER"
```

Default ops scripts load this `0600` file themselves with a strict non-shell
parser. Use plain `AUTONOMO_NAME=value` assignments without quotes or spaces
around `=`. Non-empty one-shot environment overrides, such as a migration flag
or temporary deploy ref, take precedence over the file.

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

## Deploy and rollback

Deploy the immutable merge commit from GitHub, not the PR ref or a moving branch:

```bash
ops/deploy.sh cd18bdf8d55aa4ddcc7fe2f6da029ec928b27d4f
ops/rollback.sh <previous-40-character-sha>
```

When rolling a schema-18 storage release back to a schema-17 release, set
`AUTONOMO_ROLLBACK_SNAPSHOT` to the pre-migration SQLite snapshot. The rollback
script copies that snapshot before it starts the older unit.

`deploy.sh` acquires a common lock, fetches and verifies the exact commit,
creates an isolated worktree and venv, renders `autonomo-web-<SHA>.service` with
the configured absolute paths, runs preflight and the explicitly configured
migration hook, then starts the new versioned unit and atomically switches
`current`. A failed migration or failed service start leaves the old link intact.

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
ad-hoc directory copy. `AUTONOMO_ALERT_WEBHOOK` is optional; when configured it receives backup failure alerts.

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
checks the service. It intentionally does not delete the prior file. The archive
restore command only accepts an empty target root; validate its SQLite database
and start the app on an alternate port before any production cutover.

## Optional SOPS control plane

The default scripts above deliberately use the server-owned `runtime.env`,
`config.yaml`, and credential files. They require neither `sops`, `age`,
a secrets repository, nor a second deployment SHA.

The previous encrypted-Git workflow is preserved under `ops/sops/` and is
activated only by invoking it explicitly:

```bash
ops/sops/config-sync.sh --activate <full-secret-config-sha>
ops/sops/install-systemd-user-units.sh
ops/sops/deploy.sh <full-application-sha> <full-secret-config-sha>
ops/sops/rollback.sh <previous-application-sha>
```

Its bootstrap and runtime templates live beside those scripts. The default
installer copies the optional directory into `AUTONOMO_OPS_ROOT/sops` without
activating it or changing the default systemd units. Do not mix the default and
SOPS installers in one rollout: choose one control plane and use its matching
deploy, rollback, restore, and unit templates.
