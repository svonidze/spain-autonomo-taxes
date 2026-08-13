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

Keep private configuration and runtime data in the private root. Back it up with client-side encryption and store the encryption identity in a password manager. A second Git repository for secrets is not recommended because it creates another clonable history and access-control surface.

For a one-time migration from an older worktree-local layout:

```powershell
python scripts/migrate_private_root.py `
  --source-root C:\path\to\project `
  --target-root "$env:LOCALAPPDATA\spain-autonomo-taxes"
```

The migration copies files, verifies SHA-256 hashes, and transactionally rewrites supported SQLite evidence paths. It does not delete the source data.

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
