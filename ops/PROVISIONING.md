# External provisioning checklist

Do not place credentials or personal document URLs in the repository. The
commands below are run only after the code branch is published and the operator
has confirmed the target accounts.

## GitHub publication

The GitHub CLI token needs the `workflow` scope because this branch creates
`.github/workflows/ci.yml`:

```bash
gh auth refresh -s workflow
git push -u origin codex/storage-replica-deploy
```

## Yandex Object Storage

In the `spain-autonomo-taxes` bucket:

1. Keep anonymous object/list/config reads disabled.
2. Enable bucket versioning before the first backup upload.
3. Create service account `autonomo-backup` and assign `storage.uploader` on
   this bucket only.
4. Create one static access key for that service account and save its ID and
   secret only in the server rclone config with mode `0600`. Configure the
   raw S3 remote with `no_check_bucket = true`: `storage.uploader` intentionally
   cannot call the bucket-creation check that rclone performs by default.
5. Configure three rclone `crypt` remotes, each rooted at exactly one raw S3
   prefix: `backups/daily/`, `backups/monthly/`, and `data/evidence/`.
6. Configure Object Storage lifecycle: daily 35 days, monthly 400 days,
   non-current backup versions 30 days, incomplete multipart uploads 7 days;
   do not expire `data/evidence/`.
7. Run put/get/head/list/delete-denied smoke through the rclone config before
   enabling scheduled backup.

The deployment writes no S3 key to SQLite. Register the Yandex backend with
`config/storage-yandex-s3.example.json`; keep `credential_ref` as the absolute
private rclone config path.

## Google Drive

1. In the user's My Drive create a private folder for Autónomo evidence.
2. Create a production OAuth desktop client with the `drive.file` scope; do not
   use a service-account writer for My Drive quota.
3. On a trusted interactive machine, run:

```bash
python scripts/authorize_google_drive.py \
  --client-secret /absolute/private/google-client.json \
  --token-out /absolute/private/google-oauth-token.json
```

4. Copy the token to the server private config with mode `0600`.
5. Register `google_primary_rw` using
`config/storage-google-drive.example.json`, substituting the folder ID and a
`credential_ref` of `file:/absolute/private/google-oauth-token.json`.
6. In the same Google Cloud project, enable Google Picker API and create a
   browser API key restricted to:
   - `https://ubuntu-16gb-nbg1-2.tail6c29f3.ts.net`
   - `https://ubuntu-16gb-nbg1-2.tail6c29f3.ts.net/*`
   Restrict the key to Google Picker API only.
7. Put the restricted key and the numeric Google Cloud project number in the
   private `config.yaml` as `google_picker_developer_key` and
   `google_picker_app_id`.

   ```bash
   <current-release>/.venv/bin/python ops/configure-google-picker.py \
     --config <private-root>/config.yaml \
     --developer-key-file <private-0600-key-file> \
     --app-id <google-cloud-project-number>
   ```

   The helper validates both private files, retains the prior config, installs
   the update atomically with mode `0600`, and never prints the developer key.
8. Run a one-byte `storage reconcile --backend google_primary_rw` smoke before
   the full legacy inventory is promoted.

## Server runtime configuration

Set private absolute paths in `~/.config/autonomo-tax/runtime.env`:

```text
AUTONOMO_PRIVATE_ROOT=/home/agent/.local/share/spain-autonomo-taxes
AUTONOMO_RELEASE_ROOT=/home/agent/apps/spain-autonomo-taxes
AUTONOMO_OPS_ROOT=/home/agent/.local/lib/autonomo-ops
AUTONOMO_DEPLOY_REPOSITORY=/home/agent/apps/spain-autonomo-taxes/repository
# Default transport ref. A deployment always still requires an exact 40-character SHA.
AUTONOMO_DEPLOY_REF=master
AUTONOMO_HEALTHCHECK_URL=https://ubuntu-16gb-nbg1-2.tail6c29f3.ts.net
AUTONOMO_RCLONE_CONFIG=/absolute/private/rclone.conf
AUTONOMO_RCLONE_REMOTE=yandex-daily-crypt:
AUTONOMO_RCLONE_MONTHLY_REMOTE=yandex-monthly-crypt:
AUTONOMO_RCLONE_EVIDENCE_REMOTE=yandex-evidence-crypt:
AUTONOMO_RECONCILE_BACKENDS=yandex_evidence
AUTONOMO_ALERT_WEBHOOK=https://private-alert-endpoint.invalid/autonomo
```

Set `AUTONOMO_ENABLE_STORAGE_MIGRATION=1` only for a release that must migrate the live database. Use a one-shot environment override for a single rollout instead of leaving the flag enabled in `runtime.env`.

## Optional encrypted Git configuration

The ordinary single-host deployment keeps credentials as `0600` server files.
If encrypted Git configuration is explicitly enabled later, follow
`ops/sops/ops-bootstrap.env.example`, `ops/sops/runtime.env.example`, and the
SOPS section in `ops/README.md`. The private secrets repository is not required
for the default deploy path.
