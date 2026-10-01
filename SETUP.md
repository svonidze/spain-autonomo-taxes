# Local setup

Install the application on your own computer and create a fresh local ledger.
For an existing server installation, follow [Operations](ops/docs/README.md).
Return to the [project overview](README.md) for an introduction to the application.

## Requirements and installation

Python 3.11 or newer is required. Installing from source also requires Node
24.20.0 and npm 11.19.0 (see `frontend/.nvmrc`) to build the interface. A prebuilt Python
wheel already contains those assets and does not require Node at runtime.

Run the installation commands from the repository root. First create and
activate a virtual environment.

On macOS/Linux:

```sh
python3 -m venv .venv
. .venv/bin/activate
```

On Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Then build the interface before installing the Python package (both platforms):

```sh
npm --prefix frontend ci --include=dev --no-audit --no-fund
npm --prefix frontend run build
python -m pip install -e ./backend
```

Image intake also requires the local Tesseract executable and its English model.
See [Local OCR dependency](ops/docs/PROVISIONING.md#local-ocr-dependency) for
provisioning and verification. Documents are not sent to an external OCR service.

### Private paths and configuration

Runtime data defaults to an OS-local private directory outside the repository:

| Platform | Default private root |
| --- | --- |
| Windows | `%LOCALAPPDATA%\spain-autonomo-taxes` (falls back to the user's `AppData\Local`) |
| macOS/Linux | `$XDG_DATA_HOME/spain-autonomo-taxes`, or `~/.local/share/spain-autonomo-taxes` when `XDG_DATA_HOME` is unset or not absolute |

To use a different location, set an absolute path before running any command:

```sh
export AUTONOMO_PRIVATE_ROOT="$HOME/.local/share/autonomo-local-ledger"
```

On Windows PowerShell:

```powershell
$env:AUTONOMO_PRIVATE_ROOT = "D:\private\spain-autonomo-taxes"
```

A configuration file is optional for local use. Without one, the application
uses `autonomo.sqlite`, `inbox/`, `evidence/` and `cache/` inside the private root.
For custom paths or integrations, copy [the configuration example](examples/config/example.yaml)
to `config.yaml` inside that root and replace every placeholder with a local
value. Relative paths in the private config are resolved from that config file's
directory.

The example includes `trusted_proxy_mode: tailscale_serve` and
`allowed_tailscale_logins` for a separately provisioned Tailscale service. Omit
these settings for direct local access; follow [provisioning](ops/docs/PROVISIONING.md)
and [operations](ops/docs/README.md) for remote service setup.

Configuration precedence is explicit `--config`, then `AUTONOMO_PRIVATE_ROOT`, then the OS-local default. A repository-local `.local/config.yaml` is supported only as a warned migration fallback.

### Start a fresh local ledger

Check the installed entrypoints:

```sh
autonomo-tax --help
autonomo-web --help
```

After selecting an empty private root, initialize a new database and start the
interface:

```sh
autonomo-tax db init
autonomo-web
```

Open [the local interface](http://127.0.0.1:8765). The server accepts only loopback
hosts; use `--port <port>` if the default port is occupied. Set up taxpayer details
in [Account settings](docs/user/ACCOUNT_SETTINGS.md), then follow the
[accounting workflow](docs/user/ACCOUNTING_WORKFLOW.md).

The web service requires an existing database and does not automatically upgrade
it. For an existing operational database, use the reviewed migration procedure in
[Operations](ops/docs/README.md), with verified backups, rather than treating
`db init` as an upgrade command.

## Private data boundary

These paths and file types are prohibited from Git:

- `data/`, `runs/`, `evidence/`, `.local/`, `.omx/`, `tmp/`, and browser-session directories;
- SQLite databases, source documents, exports, archives, logs, private keys, and environment files;
- real names, identity or tax numbers, addresses, contact details, financial records, Drive links, and credentials.

Keep private configuration and runtime data in the private root. The default deployment uses private server files. Production runtime secrets may optionally use the [SOPS + age control plane](ops/sops/README.md): only SOPS ciphertext is committed to the dedicated private `spain-autonomo-taxes-secrets` repository, while age identities and SSH deploy keys remain outside every Git repository. Plaintext secrets, environment files, credentials, and operational data remain prohibited in this code repository.

### Moving older private data

For a one-time migration from an older worktree-local layout:

```powershell
python ops/maintenance/migrate_private_root.py `
  --source-root C:\path\to\project `
  --target-root "$env:LOCALAPPDATA\spain-autonomo-taxes"
```

The migration copies files, verifies SHA-256 hashes, and transactionally rewrites supported SQLite evidence paths. It does not delete the source data.

## Outbound network use

Tax calculations use the local ledger. Review and configured integrations can
contact external services:

- **ECB:** guided review of a foreign-currency transaction requests the daily
  reference-rate CSV for the currency and a seven-day date window. No amount,
  counterparty, document, identifier or local record is sent. If the service is
  unavailable, the review offers documented settlement evidence instead; there
  is no setting that disables the lookup. Manual rates labeled as ECB or Banco
  de España also trigger a reference check; see [Foreign-currency exchange rates](docs/user/FX_RATES.md).
- **VIES:** the counterparty check runs only with `--confirm-network-to-vies`.
  It sends the customer's country and VAT number, plus your own VAT number when
  `--requester-vat` is supplied. The application never checks VIES automatically;
  see [VIES checks](docs/user/VIES_CHECK.md) for the disclosure and saved evidence.
- **Configured integrations:** Google Drive intake and storage exchange document
  bytes and metadata with Google. Remote evidence replicas and backup jobs
  transfer documents or private-root archives to their configured storage
  destinations. Follow
  [provisioning](ops/docs/PROVISIONING.md) and [disaster recovery](ops/docs/DISASTER_RECOVERY.md)
  for provider setup, backup coverage and verification.

## Backups and further checks

Private-root backups do not automatically include credentials stored elsewhere,
such as `~/.config`. Verify their independent recovery copy before relying on a
server-loss recovery plan. Follow [Disaster recovery](ops/docs/DISASTER_RECOVERY.md)
for backup coverage and restore verification.

For development and testing, use [Frontend development and verification](docs/development/UI_DEVELOPMENT.md).
For repository privacy rules and the checks to run before committing or sharing,
use [Privacy and repository boundary](docs/development/PRIVACY.md).
