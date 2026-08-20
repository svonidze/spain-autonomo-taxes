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

The deployment writes no S3 key to SQLite. Add the Yandex backend to encrypted
`prod/storage-backends.sops.json`; set `credential_ref` to
`file:<private-root>/runtime-config/current/credentials/rclone.conf`.

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

4. Encrypt the token as `prod/google-drive-oauth-token.sops.json` in the
   dedicated secrets repository. Do not copy it through the code repository.
5. Add the Google backend to encrypted `prod/storage-backends.sops.json`,
   substituting the folder ID and using a `credential_ref` below
   `<private-root>/runtime-config/current/credentials/`.
6. Run a one-byte `storage reconcile --backend google_primary_rw` smoke before
the full legacy inventory is promoted.

## Server bootstrap and runtime configuration

Copy `ops/ops-bootstrap.env.example` to
`~/.config/autonomo-tax/ops-bootstrap.env`, set mode `0600`, and configure the
code checkout, private root, exact secrets repository, age identity, read-only
deploy key, pinned `known_hosts`, and both expected public age recipients.
Operational release controls such as `AUTONOMO_ENABLE_STORAGE_MIGRATION` belong
in this bootstrap file.

The encrypted `prod/runtime.sops.env` contains service runtime values:

```text
AUTONOMO_PRIVATE_ROOT=/home/agent/.local/share/spain-autonomo-taxes
AUTONOMO_RCLONE_CONFIG=/home/agent/.local/share/spain-autonomo-taxes/runtime-config/current/credentials/rclone.conf
AUTONOMO_RCLONE_REMOTE=yandex-daily-crypt:
AUTONOMO_RCLONE_MONTHLY_REMOTE=yandex-monthly-crypt:
AUTONOMO_RCLONE_EVIDENCE_REMOTE=yandex-evidence-crypt:
AUTONOMO_RECONCILE_BACKENDS=yandex_evidence
AUTONOMO_ALERT_WEBHOOK=https://private-alert-endpoint.invalid/autonomo
```

Place `google_picker_developer_key` inside encrypted `prod/config.sops.yaml`;
the separate single-value key file is not consumed by the application.

Set `AUTONOMO_ENABLE_STORAGE_MIGRATION=1` in `ops-bootstrap.env` only for a
release that needs storage migration. Every deployment still requires exact,
independent 40-character SHAs for the application and secret configuration.
