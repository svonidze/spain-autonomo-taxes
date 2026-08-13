# Privacy and repository boundary

The repository contains implementation, synthetic tests, public reference configuration, and technical documentation only. Operational accounting data belongs in the external private root.

## Storage model

| Material | Storage | Git |
|---|---|---|
| Source code and synthetic fixtures | repository | allowed |
| Local configuration and database | private root | prohibited |
| Invoices, receipts, tax forms, exports, and generated reports | private root or encrypted archive | prohibited |
| Browser profiles, cookies, storage state, and login logs | private root under `browser/` | prohibited |
| API tokens and encryption identities | OS credential store or password manager | prohibited |

The private-root resolver uses this precedence:

1. explicit `--config` or command-specific path;
2. absolute `AUTONOMO_PRIVATE_ROOT`;
3. the OS-local default;
4. warned legacy `.local/config.yaml` fallback, only when no explicit environment root exists.

Canonical config wins as a whole; configurations are not merged across roots. This avoids split-brain reads and writes.

## Backup model

Use client-side authenticated encryption such as `age` for private-data and forensic Git backups. Keep at least two independently stored ciphertext copies and verify each by restoring it to an isolated directory. Store the private decryption identity outside the repository, preferably in a password manager.

Do not use a private Git repository as a secret store. Git history is durable, clones are hard to revoke, and repository access often grants more capability than secret consumers need.

## Adding code or fixtures

- Generate fixtures from scratch; do not edit or truncate a real export.
- Use `example.invalid` for email and URL examples.
- Use explicit non-production identifiers such as `TEST-TAX-ID-001`.
- Keep amounts, dates, document numbers, names, and combinations independent from operational records.
- Do not add binary fixtures unless the privacy guard is deliberately extended with an exact reviewed hash and the review explains why the binary is necessary.
- Any new default output path must resolve under the private root, independent of the current working directory.

## Before sharing

1. Run all tests and `python scripts/privacy_guard.py --history`.
2. Clone the proposed repository into a new directory and repeat the checks there.
3. Confirm the remote exposes only the intended branch and tags.
4. Grant the least repository permission required. Remember that private-repository access cannot prevent a recipient from retaining a local clone.

Changing `.gitignore` does not remove existing Git objects. If private content is committed, revoke access, rotate affected credentials or sessions, rebuild a clean history from a trusted snapshot, and replace the remote only after validating a fresh clone.
