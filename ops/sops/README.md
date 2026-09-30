# Optional SOPS control plane

The [default deployment](../docs/README.md) remains the normal single-host workflow.
This directory preserves encrypted-Git configuration as an opt-in control
plane. Preparing the repository or copying this directory does not activate it.
Do not mix SOPS deploy/rollback/restore scripts or units with the default ones.
`AUTONOMO_SECRET_SYNC_REQUIRED=0` is not a supported switch back to default mode.

This runbook documents the existing scripts, not a claim that production secrets
have been migrated, a recovery identity exists, or a live SOPS cutover has passed.
Resolve the [operational limits](#operational-limits-before-opting-in) before
choosing this mode.

## Bootstrap boundaries

The approved ciphertext repository is
[`svonidze/spain-autonomo-taxes-secrets`](https://github.com/svonidze/spain-autonomo-taxes-secrets),
branch `main`. Production sync uses SSH with a repository-specific **read-only**
deploy key. Keep the private deploy key and server age identity outside every
Git repository; keep the recovery identity on a separate trusted device or in
an independently recoverable password manager, never on the service host.

Prerequisites: Linux user-systemd, Git/SSH, Python 3.11+, venv, `flock`, SOPS
with age support, age tools for identity operations, rclone for backup, a
reviewed code checkout, and a verified private database/recovery point. Approve
the account, key, and unit changes separately before initial activation.

Copy [ops-bootstrap.env.example](ops-bootstrap.env.example) privately to
`~/.config/autonomo-tax/ops-bootstrap.env`, mode `0600`, replacing placeholders.
The loader accepts only plain `AUTONOMO_*=value` assignments, not shell syntax.
It does not use the default runtime.env for bootstrap. Set:

- Private/release/ops roots, code checkout, transport ref, and health URL.
- `AUTONOMO_SECRET_CONFIG_REPO=github.com:svonidze/spain-autonomo-taxes-secrets.git`
  and `AUTONOMO_SECRET_CONFIG_BRANCH=main` (SSH forces user `git`).
- `AUTONOMO_SECRET_CONFIG_MOUNT` to a dedicated directory **inside**, but not
  equal to, private-root, normally `runtime-config`.
- `AUTONOMO_AGE_PRIVATE_KEY_PATH`, `AUTONOMO_SECRET_DEPLOY_KEY_PATH`, and
  `AUTONOMO_SECRET_KNOWN_HOSTS_PATH` to verified private/owned files.
- `AUTONOMO_EXPECTED_AGE_RECIPIENTS` to the comma-separated **exact** public
  recipient set, at least distinct server and recovery recipients.
- `AUTONOMO_SECRET_SYNC_REQUIRED=1`; keep release/migration/rollback controls
  in bootstrap, not in encrypted runtime.env.

Verify GitHub SSH host keys through a trusted independent source before pinning
known_hosts. Do not disable host-key checking or enable the local-repository
test bypass in production. Read-only deploy access must be configured in GitHub;
the scripts cannot prove that a key lacks write permission. Protect `main`
against force-push/deletion and require review where the GitHub plan permits it.
The server checks origin equality and SHA reachability, not review signatures.

### Server and recovery identities

This example **creates a key** only when opt-in provisioning has been approved.
Run once on the server for its identity, and separately on the trusted recovery
device for the recovery identity. Enter an unused absolute path on that device;
never copy the recovery private identity onto the server. Prerequisite: age tools
installed. Expected output is only the public recipient; the private key is a
new `0600` file in a `0700` directory.

```bash
set -euo pipefail
umask 077
read -r -p 'Unused absolute age identity path on this device: ' identity_path
[[ "$identity_path" = /* ]]
test ! -e "$identity_path"
install -d -m 700 "$(dirname "$identity_path")"
age-keygen -o "$identity_path"
chmod 600 "$identity_path"
age-keygen -y "$identity_path"
```

Store only the public recipients in `.sops.yaml` and bootstrap. Use the standard
comma-separated age creation rule in the **private secrets repository**:

```yaml
creation_rules:
  - path_regex: ^prod/.*\.sops\.(env|yaml|json|ini)$
    age: >-
      age1replace-server-recipient,
      age1replace-offline-recovery-recipient
```

Replace both placeholders with actual public recipients. Metadata such as
`.sops.yaml` may be plaintext; runtime values must be SOPS ciphertext. Never
stage plaintext sources temporarily, even in private Git. Prepare them outside
all repositories with private permissions, encrypt to the allowlisted filenames,
and verify both identities can decrypt before publication. Keep encryption
output formats explicit: dotenv, YAML, JSON, and INI as mapped below.

For example, on the trusted maintenance device, this creates only the encrypted
YAML artifact from an existing private plaintext source. Prerequisites: a
reviewed `.sops.yaml`, both public recipients, SOPS, and an unused destination
in the intended private secrets checkout. It neither stages Git files nor
changes the server. The source must be outside every Git checkout. Use the
matching explicit formats from the table for other artifacts.

```bash
set -euo pipefail
umask 077
read -r -p 'Absolute private secrets checkout: ' secrets_checkout
read -r -p 'Absolute private plaintext application YAML, outside Git: ' plaintext_config
read -r -p 'Exact comma-separated server and recovery public recipients: ' public_recipients
cd "$secrets_checkout"
test -f .sops.yaml
test -f "$plaintext_config"
install -d -m 700 prod
test ! -e prod/config.sops.yaml
sops encrypt --input-type yaml --output-type yaml --age "$public_recipients" \
  "$plaintext_config" > prod/config.sops.yaml
test -s prod/config.sops.yaml
```

Expected: a nonempty SOPS ciphertext file with both recipients. Review encrypted
metadata and decrypt privately to verify the result with each identity before
committing; do not print the decrypted result. The explicit recipient argument
must match the `.sops.yaml` rule and the server's expected set. Keep the private
source until successful recovery validation, then remove that exact temporary
source under the private cleanup policy.

## Artifact mapping

Paths on the right are relative to
`AUTONOMO_SECRET_CONFIG_MOUNT/generations/<secret-config-sha>/`.
This is a fixed allowlist, not a generic `*.enc` loader.

| Encrypted repository artifact | Materialized path | Format | Required |
|---|---|---|---|
| `prod/runtime.sops.env` | `runtime.env` | dotenv | Yes |
| `prod/config.sops.yaml` | `config.yaml` | YAML | Yes |
| `prod/google-drive-reader-service-account.sops.json` | `credentials/google-drive-reader-service-account.json` | JSON object | Yes |
| `prod/google-drive-oauth-client.sops.json` | `credentials/google-drive-oauth-client.json` | JSON object | No |
| `prod/google-drive-oauth-token.sops.json` | `credentials/google-drive-oauth-token.json` | JSON object | Yes |
| `prod/rclone.sops.ini` | `credentials/rclone.conf` | INI | Yes |
| `prod/storage-backends.sops.json` | `storage-backends.json` | JSON object | Yes |

Use [runtime.env.example](runtime.env.example) for runtime assignments. Its
private-root must match bootstrap; the rclone path must be
`<secret-mount>/current/credentials/rclone.conf`. Both Picker fields belong in
encrypted `config.yaml`, not a separately named Picker key artifact. Session
secret values can be in encrypted runtime.env via the existing
`AUTONOMO_SESSION_PRINCIPAL_SECRET`; an external `_FILE` reference still needs
separate recovery because the allowlist has no session-secret file artifact.

Config paths must be explicit: the config is nested inside a generation, so
relative paths no longer resolve from private-root. `ledger_db` must equal
`AUTONOMO_PRIVATE_ROOT/autonomo.sqlite`; inbox/archive/cache must stay inside
that root. `storage-backends.json` has shape `{"backends": [...]}` with unique
backend keys. Non-null credential references must be `file:` absolute paths
under `<secret-mount>/current/credentials/`, not generation-specific paths.
Never put credentials in that backend document.

Deployment runs `storage backends-apply` before web startup, after any migration.
The application applies the list transactionally/idempotently. Absent backend
keys are **not** automatically deleted. This startup apply exists only in SOPS
units and does not affect ordinary deployment.

## Staging and activation

Staging reads the encrypted repository over SSH and writes a private local cache
plus plaintext generation; it does not change active config, SQLite, or services.
Prerequisites: completed bootstrap/identities, published reviewed ciphertext
reachable from `main`, and an existing private database. In a fresh Bash shell,
choose the source SOPS directory or its installed `AUTONOMO_OPS_ROOT/sops` copy:

```bash
set -euo pipefail
read -r -p 'Absolute SOPS control-plane directory: ' sops_ops
read -r -p 'Reviewed secret-config commit, full SHA: ' secret_sha
test -f "$sops_ops/lib.sh"
source "$sops_ops/lib.sh"
load_bootstrap_env
validate_secret_sha "$secret_sha"
"$sops_ops/config-sync.sh" --stage-only "$secret_sha"
"$sops_ops/preflight.sh" "$secret_sha"
```

Expected: `status=staged` or `already-staged`, then `preflight=passed`. Sync
fetches the exact reachable commit, rejects missing/unexpected recipients and
missing required artifacts, decrypts into a temporary `0700` directory, and
validates before publishing the generation. Files are `0600`, owned by the
service user, without symlinks/hardlinks. Artifacts have a 5 MiB size limit.
The generation marker and manifest bind SHA, file set, sizes, and digests.
Existing generation drift is rejected, not silently overwritten. Missing SOPS,
identity, SSH access, or failed validation blocks deployment before service stop.

Standalone `config-sync.sh --activate` changes the `current` secret symlink
only. It does **not** restart a web unit, apply backend configuration, or record
a deployment binding. It also uses a sync lock, not the common deployment lock.
Use it only for a deliberate bootstrap/recovery step with all consumers stopped
and no concurrent operations. With the variables defined above, this is the
explicit config-changing command; expect the active symlink to resolve to the
validated generation:

```bash
"$sops_ops/config-sync.sh" --activate "$secret_sha"
```

Do not use standalone activation for normal secret updates. Web units pin their
generation while backups and credential references use `current`; an arbitrary
symlink change would split their configuration. Use paired deployment instead.

## Dual-SHA deployment and rollback

Use the staging setup above. Prerequisites: reviewed application and config
SHAs, green tests, verified pre-change data/credential backups, and a maintenance
window with writers quiesced. This command installs a release, applies backend
configuration, switches secrets, and restarts the service. It accepts exactly
two full SHAs. The ordinary one-SHA script is not interchangeable.

```bash
read -r -p 'Reviewed application commit, full SHA: ' app_sha
validate_sha "$app_sha"
"$sops_ops/deploy.sh" "$app_sha" "$secret_sha"
```

The bootstrap transport ref normally selects `master`; use a one-shot
`AUTONOMO_DEPLOY_REF` only for an explicitly approved PR SHA. Use the one-shot
storage migration flag only for a release needing it, as in the
[default migration explanation](../docs/README.md#schema-migration).

SOPS deployment stages/preflights first, renders
`autonomo-web-<app-sha>-<secret-sha>.service`, and snapshots SQLite under
`backups/pre-deploy` before migration/backend apply. It then stops the old unit,
activates secrets, and starts/checks the new unit. On handled startup/health
failures it attempts to restore the previous database, generation, release,
and unit. Check the result; it is not an assurance against every interruption.

Successful deployments record `current-deployment` (both SHAs) and
`release-config/<app-sha>` (last successful secret SHA for that code SHA), outside
immutable releases. Only active and immediately previous plaintext generations
are retained after successful deploy/rollback. Older ones are re-materialized
from Git if still reachable and decryptable. Retention of ciphertext is not
sufficient if its old recipient policy/identity is no longer accepted.

For first installation or an intentional update, run the SOPS installer **from
a reviewed code checkout/release**, after a healthy paired deployment and only
when backup targets are ready. Source-tree SOPS deploy works before installation.
The installer requires an active validated generation, copies helpers, replaces
backup/alert unit templates, and immediately enables both backup timers.
Persistent timers may run missed jobs at once. It does not perform the web
deployment itself. In the same shell:

```bash
read -r -p 'Absolute reviewed code checkout or release: ' code_root
"$code_root/ops/sops/install-systemd-user-units.sh"
```

Expect the SOPS installer completion message and enabled timers. On an existing
default host this is a deliberate control-plane transition: preserve old units
and server files, prevent mixed backup jobs during cutover, and rehearse recovery
first. Return to default mode also requires a coordinated unit/config/backend
transition, not a flag flip.

For a code rollback, use the target application's **recorded** secret binding.
The target release/unit must be installed. With `sops_ops` from staging:

```bash
read -r -p 'Installed target application SHA: ' target_sha
validate_sha "$target_sha"
"$sops_ops/rollback.sh" "$target_sha"
```

This stages and validates the bound config before service stop. Cross-schema
rollback also requires a verified `AUTONOMO_ROLLBACK_SNAPSHOT`; loss of newer
writes and the safety procedure are the same as
[default rollback](../docs/README.md#rollback). Keep the exact snapshot outside the
excluded backups tree in an independently verified recovery copy as appropriate.

For a config-only change, deploy the current app SHA with the new secret SHA.
To undo it, deploy that **same app SHA with the previous secret SHA explicitly**.
`rollback.sh <app-sha>` cannot select older bindings for the same app because the
per-app binding records only its last successful config. Record both SHAs in
the private maintenance record before each change.

For live SQLite recovery use this directory's `restore.sh`, with the same
dangerous `--yes-restore` contract and prerequisites as
[disaster recovery](../docs/DISASTER_RECOVERY.md#production-cutover-dangerous).
It stages the recorded configuration before stopping the paired unit. Archive
extraction remains isolated and does not activate any configuration.

## Identity rotation and recovery

### Planned server identity replacement

Generate the new server identity on its host. Retain the offline recovery
identity. Temporarily include old server, new server, and recovery public
recipients in `.sops.yaml`. Update the **exact expected set** in the destination
bootstrap to the same three recipients before staging. Two-recipient bootstrap
will reject the transitional three-recipient ciphertext.

On the trusted secrets-maintenance device, after reviewing `.sops.yaml`, use
these commands for each required and present optional mapped artifact. They
change ciphertext files locally, not production. Prerequisites: a secrets
checkout, SOPS, and an authorized decryption identity in its private key file.
The example names one selected artifact and suppresses decrypted output.

```bash
set -euo pipefail
read -r -p 'Absolute private secrets checkout: ' secrets_checkout
read -r -p 'Absolute authorized age identity: ' maintenance_identity
read -r -p 'Mapped artifact relative path, e.g. prod/config.sops.yaml: ' encrypted_path
cd "$secrets_checkout"
test -f .sops.yaml
test -f "$encrypted_path"
SOPS_AGE_KEY_FILE="$maintenance_identity" sops updatekeys -y "$encrypted_path"
SOPS_AGE_KEY_FILE="$maintenance_identity" sops decrypt "$encrypted_path" >/dev/null
```

Expected: successful recipient update and decryption, without plaintext output.
Repeat the decryption check separately with new server and recovery identities
on their own trusted devices. Review/commit ciphertext only, then stage/deploy
the exact new secret SHA. Once healthy, remove the old recipient in a second
reviewed revision, rotate data keys, update expected recipients to new server
plus recovery, and verify again. Test rollback compatibility before retiring
the old identity; strict expected-recipient validation may reject older commits.

### Suspected compromise

Fence the compromised host/key, remove the compromised recipient, then run
`updatekeys` followed by data-key rotation for **every** affected artifact. Using
the variables from the preceding example, this changes the selected ciphertext
in place and validates it with an uncompromised maintenance identity:

```bash
SOPS_AGE_KEY_FILE="$maintenance_identity" sops updatekeys -y "$encrypted_path"
SOPS_AGE_KEY_FILE="$maintenance_identity" sops rotate --in-place "$encrypted_path"
SOPS_AGE_KEY_FILE="$maintenance_identity" sops decrypt "$encrypted_path" >/dev/null
```

Expected: successful re-encryption under a new data key. `updatekeys` alone only
changes wrappers; it does not replace the data key. See the upstream
[SOPS key-management procedure](https://getsops.io/docs/usage/key-management/).
Then rotate/revoke exposed provider credentials, OAuth grants, session secrets,
and Git deploy access as appropriate, and publish their replacements only under
the uncompromised recipient set. Historical Git revisions remain decryptable
with identities that could access them. Rotation does not erase historical
exposure. Do not blindly replace rclone crypt passwords: old backups still need
their original crypt material, even if new backups move to a new encryption set.

For total server loss, recover ciphertext using the independently stored recovery
identity on a trusted device, provision a new server identity and read-only
deploy key, then materialize an approved compatible configuration and follow
the [data recovery procedure](../docs/DISASTER_RECOVERY.md). SSH keys, age
identities, trusted host keys, bootstrap paths, and deployment bindings are not
reconstructed automatically by cloning the secrets repository.

## Operational limits before opting in

- Sync validates generations but is not a full credentials/permissions test.
  Test Google, Picker, crypt download, and health independently.
- Picker token refresh currently rewrites its OAuth token file. If that path is
  in a hash-verified generation, the next validation can report digest drift.
  Rehearse this lifecycle and resolve it before production opt-in; do not edit
  manifests or bypass validation. Normal default mode does not have this
  immutable-generation constraint.
- Private-root archives can include plaintext generation files. They exclude
  the `current` symlink and external bootstrap identities. Preserve independent
  bootstrap/recovery material; re-stage/activate through SOPS when rebuilding.
- Test coverage for staging/rotation helpers is not evidence of a real production
  SOPS migration. Keep actual activation and off-server recovery evidence private.

The repository and credential boundaries remain in [PRIVACY.md](../../docs/development/PRIVACY.md).
