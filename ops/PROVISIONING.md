# External provisioning checklist

This guide describes intentional account/configuration changes. Confirm the
target accounts and maintenance scope first. Do not put credentials, document
URLs, or private inventory in the code repository. Normal operation uses
server-owned `0600` files; [SOPS](sops/README.md) is optional.

## GitHub and code publication

Use reviewed commits with green CI. Publishing a branch or opening a PR is not
a deployment. Only changes to GitHub workflow files may require the GitHub CLI
token's additional `workflow` scope; do not expand permissions for a docs-only
change. Follow the [deployment runbook](README.md#deploy-a-reviewed-release-production-change)
for merge SHAs or explicitly approved unmerged PR SHAs.

## Local OCR dependency

Image intake uses the local Tesseract executable and its default English model.
On Ubuntu, an authorized administrator installs the distribution packages:

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends tesseract-ocr tesseract-ocr-eng
```

Do not install development libraries, add third-party repositories, change sudo
policy, or send documents to an external OCR service for this setup. If the
operator cannot run the approved administrative command, stop installation and
arrange administrator access; local code/test work can continue independently.
See the [upstream installation guide](https://tesseract-ocr.github.io/tessdoc/Installation.html).

As the **service user**, run `python3 /absolute/installed-ops/ocr-readiness.py
--runtime-env /absolute/private/runtime.env` using the file selected by the web
unit's `EnvironmentFile`. Expected output is `ocr=ready language=eng`. This
checks executable lookup, successful version/language queries, and `eng`, with
a ten-second limit per query. It does not establish recognition accuracy.

Set an explicit plain `PATH=/usr/local/bin:/usr/bin:/bin` in runtime.env (extend
only for intentional service dependencies) so an interactive shell's PATH
cannot mask a broken service PATH. With `--runtime-env`, the check builds a clean
environment from that file and requires a non-empty PATH; operator-only settings
such as `TESSDATA_PREFIX` are not inherited. In both ordinary and SOPS modes,
use plain `NAME=value` assignments without quotes, backslashes, or surrounding
whitespace; ambiguous values are rejected, not interpreted as shell/systemd
syntax. Values are never evaluated or printed. Without `--runtime-env`, the
standalone diagnostic uses the caller's environment, not a service-readiness
proof. Verify the actual running unit separately, including
any environment changes or drop-ins. Do not dump its environment or secrets.
If runtime.env changed, restart only the active web unit and recheck health;
package installation alone does not require a web restart.

After the default ops control plane is installed, verify every runtime-only
dependency through its service environment. For example, if rclone was installed
under the service user's local bin directory, add that **absolute** directory to
runtime `PATH`, then run:

```bash
read -r -p 'Absolute installed ops directory: ' ops_root
"$ops_root/run-with-service-env.sh" -- /bin/sh -c 'command -v rclone'
```

Do not treat `command -v rclone` in an SSH shell or after a bare
`load_runtime_env` call as an equivalent check: inherited non-empty values take
precedence in that loader.

For SOPS, check the **validated candidate generation's** runtime.env before
switching releases, not the currently active generation. Do not edit a decrypted
generation in place: publish the intended encrypted configuration revision.

Finally, run the current release's `inspect_document` on an explicitly selected
private image as the service user, without running `ingest`. Check its extraction
status and compare date, currency and total to the original privately. Image OCR
has a sixty-second execution limit; failure/timeout leaves the document in
review and never posts it. This change takes effect only after deploying the
application release containing that limit, not by copying ops helpers alone.

Installing OCR does not resolve old validation issues. A human review or a
successful fresh extraction must support any targeted issue resolution, with
its reason and current row version. A manual review must not be described as
successful automatic recognition. OCR readiness is not fiscal-document validity;
do not infer an invoice issue date from a payment date or invent required tax
fields to pass review. Do not reimport evidence merely to clear an old error.

## Yandex Object Storage

For the private `spain-autonomo-taxes` bucket:

1. Keep anonymous object/list/config access disabled and enable versioning.
2. Use the dedicated backup service account with bucket-scoped permissions.
   The existing design uses `storage.uploader`; validate put/get/head/list and
   delete-denied behavior with disposable synthetic objects before scheduling.
3. Save static access credentials only in the private rclone config. Use
   `no_check_bucket = true` on the raw S3 remote so rclone does not attempt a
   bucket-creation check. Do not print `rclone config show` output to logs.
4. Configure separate crypt remotes rooted at the exact raw prefixes
   `backups/daily/`, `backups/monthly/`, and `data/evidence/`.
5. Set and verify the intended lifecycle: daily backups 35 days, monthly
   backups 400 days, non-current backup versions 30 days, incomplete multipart
   uploads 7 days. Do not expire evidence. This cloud policy is separate from
   the scripts' local retention by archive count.
6. Independently preserve and test recovery of the complete rclone config,
   including crypt material. A new access key cannot decrypt old backups.

The backend shape is in [the Yandex example](../config/storage-yandex-s3.example.json).
Keep `credential_ref` as `file:` plus the private absolute rclone config path.
Credentials do not belong in SQLite. See [backup coverage](../docs/DISASTER_RECOVERY.md#what-is-backed-up)
before relying on a private-root archive to recover this config.

## Google Drive storage choices

### Existing originals and URL adoption

Leave existing files in their understandable archive folders. The Google
replica's identity is its `fileId`; the database also keeps the web URL, original
name, archive-path metadata, and content hash. Yandex is an independent physical
backup, not another Google folder. A filesystem mount is only a compatibility
fallback, not the file identity.

Configure a `google_drive` backend with `access_mode: read_only`,
`credential_mode: service_account`, the archive `root_folder_id`, and a private
reader JSON credential reference. Grant that reader access only to the intended
archive and use `drive.readonly`. The user OAuth writer is a separate backend.
Use the existing Google URL intake for accessible originals; it downloads to
verify/hash/extract and registers the original without making another Google
copy. Verify the Yandex mirror separately. For existing database records, use
`storage adopt-google-archive` with a reviewed records file after matching their
stored SHA-256, not intake again merely to change their storage identity. The
[record builder](../scripts/build_drive_adoption_records.py) resolves archive
paths through an existing rclone Drive remote. Its records contain private file
IDs/URLs and must stay outside Git. Run adoption with `--dry-run` first; applying
without that flag updates replica/primary records. Retiring managed replicas
additionally requires explicitly selected `--retire-backend` values, a fresh
backup, and verified independent mirrors. None of these operations is required
just to enable Picker.

An inaccessible URL needs a sharing/credential check. The current web Picker
selects **folders**; automatic file-Picker rescue of an inaccessible URL is not
implemented. Do not promise that fallback or create a duplicate as a workaround.

### Local uploads and an optional destination folder

Local intake first accepts a verified local copy. To archive new uploads to
Google, configure a user OAuth `read_write` backend with
`credential_mode: oauth` explicitly and an intentional destination folder.
Use [the Google backend example](../config/storage-google-drive.example.json)
as a shape, not a command to recreate the old managed archive. Keep the token
in a `file:` credential reference and use `drive.file`, not broad `drive`.
Do not use a service-account writer for My Drive.

Picker allows a folder override for an upload. New objects use original names
and period/document-type structure. Without a configured upload destination,
keep local primary plus a verified Yandex mirror; do not silently fall back to
the retired managed folder. A backend's name does not promote it to primary:
the particular replica must be verified. Do not run a broad Google reconcile
as an authentication smoke test; it can upload files.

## Google Picker configuration

Picker is needed for the in-browser destination-folder chooser, not for reading
an already accessible Google URL or for Yandex backup. Prerequisites: the
trusted Tailscale web session, a working OAuth writer, and Google Drive API
access in its Cloud project. Enable Google Picker API in that **same** project.

Create a browser key with website restrictions for the actual HTTPS origin and
its pages. For the deployed tailnet origin, the entries are:

- `https://ubuntu-16gb-nbg1-2.tail6c29f3.ts.net`
- `https://ubuntu-16gb-nbg1-2.tail6c29f3.ts.net/*`

Restrict the key to Google Picker API only. Set
`google_picker_developer_key` together with `google_picker_app_id`, which is the
numeric **Cloud project number**, not the project name or OAuth client ID.
The API returns `app_id` and the frontend calls `PickerBuilder.setAppId`.
Google documents the [Picker project/App ID and scope requirements](https://developers.google.com/workspace/drive/picker/guides/web-picker)
and [key restrictions](https://docs.cloud.google.com/api-keys/docs/add-restrictions-api-keys).

To install the pair in **default mode**, first use the
[operating checklist setup](README.md#read-only-operating-checklist) in the same
Bash shell. Save the restricted key in a private `0600` temporary file, not a
command-line value. This helper changes production config, keeps
`config.yaml.before-picker-<timestamp>`, and writes atomically without printing
the key. It requires the current release to contain the helper and PyYAML.

```bash
read -r -p 'Absolute private temporary Picker key file: ' picker_key_file
read -r -p 'Numeric Cloud project number: ' picker_app_id
"$release/.venv/bin/python" "$release/ops/configure-google-picker.py" \
  --config "$data/config.yaml" --developer-key-file "$picker_key_file" \
  --app-id "$picker_app_id"
systemctl --user restart "$unit"
"$release/.venv/bin/python" "$ops_root/healthcheck.py" "$AUTONOMO_HEALTHCHECK_URL"
```

Expected: backup/config paths, project number, active service, and successful
health check. Open the folder chooser in the authorized browser, select a
folder, and confirm the form receives it; submitting an upload is a separate
write test. If startup fails, stop the unit, restore the retained config
atomically, restart, and recheck health. After successful verification, delete
only the exact temporary key file through a private file manager. Retain the
previous config under the credential-backup retention policy.

The browser receives the restricted key and a short-lived OAuth access token
on demand; it must not receive a refresh token. Do not capture/publish the
Picker config response or network HAR. Those contain usable credentials.

## Google troubleshooting

| Symptom | Check and action |
|---|---|
| `invalid_grant` while refreshing OAuth | Confirm the account/client and consent status, then reauthorize below. Changing the Picker key does not repair a revoked/expired refresh grant. |
| Picker is disabled or has an invalid App ID | Both config fields must be present; use the numeric project number belonging to the OAuth client/key, and confirm an enabled OAuth writer exists. |
| Picker opens but access is denied | Confirm Picker API is enabled in that project and the key allows only the actual requesting HTTPS website and Picker API. Check the signed-in account and folder access. |
| Restricted key rejected despite correct website entries | Inspect the document's `Referrer-Policy` and request referrer privately. With Picker enabled, this app sends `strict-origin-when-cross-origin`; a proxy forcing `no-referrer` prevents origin verification. Do not remove key restrictions to compensate. |
| Settings were just changed | Allow propagation time, then reload the page and open a new chooser. Do not repeatedly issue replacement keys. If the problem persists after several minutes, inspect the error and project/restriction settings again; there is no fixed propagation SLA in this runbook. |

OAuth consent apps left in Testing can have short-lived refresh grants. Review
Google's [refresh-token expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration)
when diagnosing repeated `invalid_grant`. Keep the reader service account and
user writer distinct: reauthorizing the writer does not fix reader sharing.

## OAuth reauthorization without exposing credentials

This is an intentional credential/account operation, not safe diagnostics. Use
the same approved desktop OAuth client and intended Google account, with
`drive.file`. Do not revoke the previous grant preemptively. Old tokens retained
on disk may already be invalid or become invalid; retention is not a guarantee
of authentication rollback.

On a trusted interactive workstation, use a reviewed checkout and its installed
Python dependencies. This opens the browser consent flow and writes a new
private token without touching the server token. Do not run with shell tracing.

```bash
set -euo pipefail
umask 077
read -r -p 'Absolute reviewed code checkout: ' code_root
read -r -p 'Absolute Python interpreter with project dependencies: ' oauth_python
read -r -p 'Absolute private desktop OAuth client JSON: ' oauth_client
oauth_stage="$(mktemp -d "${TMPDIR:-/tmp}/autonomo-oauth.XXXXXX")"
"$oauth_python" "$code_root/scripts/authorize_google_drive.py" \
  --client-secret "$oauth_client" --token-out "$oauth_stage/token.json"
"$oauth_python" - "$oauth_stage/token.json" "$oauth_client" <<'PY'
import json
from pathlib import Path
import sys
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
try:
    token = Path(sys.argv[1])
    client = json.loads(Path(sys.argv[2]).read_text())['installed']
    credentials = Credentials.from_authorized_user_file(str(token))
    assert credentials.client_id == client['client_id']
    assert credentials.refresh_token
    assert set(credentials.scopes or []) == {'https://www.googleapis.com/auth/drive.file'}
    credentials.refresh(Request())
    assert credentials.valid
    token.write_text(credentials.to_json())
    token.chmod(0o600)
except Exception:
    raise SystemExit('OAuth validation failed; existing server token is unchanged') from None
print('OAuth refresh validated; no credential values printed')
PY
```

Transfer the validated file over the approved private SSH channel to a new
`0600` staging file on the server. Do not use chat, clipboard logs, Git, or a
public attachment. In **default mode**, identify the exact token destination
from the OAuth backend's credential reference and schedule a short write-free
window. Stop web/background writers so automatic token refresh cannot race the
replacement. Preserve the old token and install the candidate on the same
filesystem atomically:

```bash
set -euo pipefail
umask 077
read -r -p 'Absolute validated token staging file on this server: ' candidate_token
read -r -p 'Absolute existing OAuth token path from credential_ref: ' live_token
python3 - "$candidate_token" "$live_token" <<'PY'
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
candidate, live = map(Path, sys.argv[1:])
assert candidate.is_absolute() and live.is_absolute() and candidate != live
for path in (candidate, live):
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    assert info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600
payload = json.loads(candidate.read_text())
assert payload.get('refresh_token') and payload.get('client_id')
assert payload['client_id'] == json.loads(live.read_text())['client_id']
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
preserved = live.with_name(live.name + '.before-reauthorize-' + stamp)
with preserved.open('xb') as handle:
    handle.write(live.read_bytes())
preserved.chmod(0o600)
fd, staged_name = tempfile.mkstemp(prefix='.oauth-install-', dir=live.parent)
try:
    with os.fdopen(fd, 'wb') as handle:
        handle.write(candidate.read_bytes())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staged_name, live)
finally:
    Path(staged_name).unlink(missing_ok=True)
print('Token installed; previous token retained privately')
PY
```

Restart only the previously active services/timers, run the operating checklist,
and verify the actual intended folder through Picker. This final check confirms
account/folder access beyond the token refresh test. If it fails, diagnose before
uploading anything; restore the retained token if still valid. After success,
remove the exact workstation/server staging files and empty staging directory
using a private file manager. Never delete the live token or its retained
previous version during this cleanup. In SOPS mode, do not edit a generation:
publish a new encrypted token revision and use paired deployment instead.

## Server runtime configuration

Start with [runtime.env.example](runtime.env.example) and privately fill in
absolute paths. `AUTONOMO_PRIVATE_ROOT`, `AUTONOMO_RELEASE_ROOT`,
`AUTONOMO_OPS_ROOT`, and `AUTONOMO_DEPLOY_REPOSITORY` identify different roots.
The default systemd templates read `~/.config/autonomo-tax/runtime.env`; a
script's `AUTONOMO_RUNTIME_ENV_PATH` override alone does not rewrite those units.

Set the HTTPS health URL, private rclone path, daily/monthly/evidence crypt
remotes, and intended reconcile backend list (normally Yandex only). Configure
the private `config.yaml` with the trusted Tailscale proxy mode and explicit
login allowlist. Keep session principal secret material private and recoverable.
Use `0600` regular files owned by the service user and `0700` credential
directories; verify the service can refresh its OAuth token at that path under
its systemd filesystem restrictions. Do not broadly relax the sandbox.

Use `AUTONOMO_ENABLE_STORAGE_MIGRATION=1` as a deliberate one-shot deployment
override for the release needing migration. Keep the default `0` in runtime.env.
Alert webhooks are optional and remain unconfigured unless explicitly set up.
Check the independent credential-recovery inventory before enabling backups.

## Optional encrypted Git configuration

Only if explicitly enabled, use the complete [SOPS bootstrap and lifecycle](sops/README.md).
It has different units, paths, and deployment arguments. Preparing that private
repository does not migrate current authoritative server files or prove that
the recovery identity exists.
