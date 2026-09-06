# Spain autónomo accounting toolkit

A local-first Python toolkit for bookkeeping review, evidence tracking, accounting books, and Spanish tax-preparation workflows. The code is designed to be shareable; taxpayer records, source documents, generated reports, browser sessions, and credentials are not part of the repository.

This project does not provide legal or tax advice. Review generated filing data against official sources and a qualified professional before submission.

## Setup

Python 3.11 or newer is required.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Runtime data defaults to an OS-local private directory. On Windows the default is:

```text
%LOCALAPPDATA%\spain-autonomo-taxes
```

To use a different location, set an absolute path before running any command:

```powershell
$env:AUTONOMO_PRIVATE_ROOT = "D:\private\spain-autonomo-taxes"
```

Copy `config/example.yaml` to `config.yaml` inside the private root and replace every placeholder with a local value. Relative paths in the private config are resolved from that config file's directory.

```powershell
autonomo-tax --help
autonomo-web --help
```

Configuration precedence is explicit `--config`, then `AUTONOMO_PRIVATE_ROOT`, then the OS-local default. A repository-local `.local/config.yaml` is supported only as a warned migration fallback.

## Private data boundary

These paths and file types are prohibited from Git:

- `data/`, `runs/`, `evidence/`, `.local/`, `.omx/`, `tmp/`, and browser-session directories;
- SQLite databases, source documents, exports, archives, logs, private keys, and environment files;
- real names, identity or tax numbers, addresses, contact details, financial records, Drive links, and credentials.

Keep private configuration and runtime data in the private root. The default deployment uses private server files. Production runtime secrets may optionally use the [SOPS + age control plane](ops/sops/README.md): only SOPS ciphertext is committed to the dedicated private `spain-autonomo-taxes-secrets` repository, while age identities and SSH deploy keys remain outside every Git repository. Plaintext secrets, environment files, credentials, and operational data remain prohibited in this code repository.

For a one-time migration from an older worktree-local layout:

```powershell
python scripts/migrate_private_root.py `
  --source-root C:\path\to\project `
  --target-root "$env:LOCALAPPDATA\spain-autonomo-taxes"
```

The migration copies files, verifies SHA-256 hashes, and transactionally rewrites supported SQLite evidence paths. It does not delete the source data.

## Outbound network use

Routine bookkeeping and tax calculation are local. Guided review of a foreign-currency transaction requests the ECB daily reference-rate CSV for the currency and a seven-day date window. No amount, counterparty, document, identifier or local record is sent. If the service is unavailable, the review offers documented settlement evidence instead; there is no setting that disables the lookup.

## Using the accounting workflow

Posting makes an approved income or expense transaction eligible for the
working accounting calculations. Approval and posting are separate actions.
Neither action transfers money, proves payment, or submits a return to AEAT.
Routine review and posting use the application and do not require administrator
scripts or a terminal.

Start with the [accounting workflow](docs/ACCOUNTING_WORKFLOW.md) for the normal
steps, status meanings, source-document rules and common problems. When a needed
correction is unavailable in the installed interface, an operator follows
[scoped accounting maintenance](ops/README.md#scoped-accounting-maintenance).
Exceptional maintenance is separate from the routine workflow.

Use [Understanding accounting statuses](docs/ACCOUNTING_STATUSES.md) for review
versus posting readiness, blocking reasons, next actions, assets and filing
evidence.

Use [Foreign-currency exchange rates](docs/FX_RATES.md) for the ECB rate
convention, date selection, EUR rounding, provenance and settlement fallback.

See [Account settings](docs/ACCOUNT_SETTINGS.md) for taxpayer details, local backup
retention, observed backup status and release requirements.

## Operating and recovering the service

- [Correcting counterparty names](docs/COUNTERPARTY_NAMES.md): manual corrections,
  history, import protection, conflicts and schema compatibility.

- [Operations](ops/README.md): safe diagnostics, exact-SHA deployment, migration, rollback, and backup scheduling.
- [Provisioning](ops/PROVISIONING.md): Google originals versus new uploads, optional Picker, OAuth renewal, and Yandex configuration.
- [Rebuilding history from source books safely](docs/HISTORY_REPLAY.md): guard rails for a full ledger replay, what stops it, and how to prove the result against filed returns.
- [Disaster recovery](docs/DISASTER_RECOVERY.md): backup coverage, isolated restore drill, five failure scenarios, and separately marked production cutover.
- [Optional SOPS](ops/sops/README.md): encrypted configuration bootstrap, paired application/config deployment, and identity recovery.

Private-root backups do not automatically include credentials stored elsewhere,
such as `~/.config`. Verify their independent recovery copy before relying on a
server-loss recovery plan. Documents under `docs/plans/` record proposals and
historical implementation decisions; they do not establish current behavior
or that a feature or recovery procedure is deployed.

## Privacy checks

Run the guard before committing and against the complete reachable history before sharing:

```powershell
python scripts/privacy_guard.py
python scripts/privacy_guard.py --history
python scripts/install_privacy_hook.py
```

CI performs the history scan with a full clone. The scanner fails closed on prohibited paths, binary artifacts, oversized files, likely personal identifiers, Drive links, and common credential formats. Findings print categories and fingerprints, never the matched value.

## Tests

```powershell
python -m pytest -q
```

Test fixtures are synthetic and use reserved domains or explicit placeholder identities.

See [docs/PRIVACY.md](docs/PRIVACY.md) before granting repository access or adding a new import/export workflow.
