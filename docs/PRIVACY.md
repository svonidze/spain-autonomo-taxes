# Privacy and repository boundary

The repository contains implementation, synthetic tests, public reference configuration, and technical documentation only. Operational accounting data belongs in the external private root.

## Storage model

| Material | Storage | Git |
|---|---|---|
| Source code and synthetic fixtures | repository | allowed |
| Local configuration and database | private root | plaintext prohibited; approved SOPS ciphertext only in the dedicated private secrets repository |
| Invoices, receipts, tax forms, exports, and generated reports | private root or encrypted archive | prohibited |
| Browser profiles, cookies, storage state, and login logs | private root under `browser/` | prohibited |
| API tokens | private root, optionally materialized by SOPS | plaintext prohibited; approved SOPS ciphertext only in the dedicated private secrets repository |
| age identities and Git deploy keys | server credential directory or password manager | prohibited in every Git repository |

The private-root resolver uses this precedence:

1. explicit `--config` or command-specific path;
2. absolute `AUTONOMO_PRIVATE_ROOT`;
3. the OS-local default;
4. warned legacy `.local/config.yaml` fallback, only when no explicit environment root exists.

Canonical config wins as a whole; configurations are not merged across roots. This avoids split-brain reads and writes.

## Outbound network use

| Destination | Purpose | Payload |
|---|---|---|
| `data-api.ecb.europa.eu` | Official daily rate for a foreign-currency review | Currency code and a seven-day date window only |

The request contains no amount, counterparty, document, identifier or other local accounting data. The currency and approximate transaction date remain observable by the public service.

## Backup model

Use client-side authenticated encryption such as `age` for private-data and forensic Git backups. Keep at least two independently stored ciphertext copies and verify each by restoring it to an isolated directory. Store the private decryption identity outside the repository, preferably in a password manager.

The only approved Git exception is `svonidze/spain-autonomo-taxes-secrets`: it may contain SOPS-encrypted runtime configuration whose data key is wrapped for both the server age recipient and an offline recovery recipient. It must never contain plaintext secrets, age identities, SSH private keys, accounting data, source documents, or decrypted generations. The code repository remains entirely outside this exception.

The secrets repository is a ciphertext distribution and recovery layer, not an authorization boundary or a replacement for credential rotation. Git history is durable, filenames and commit metadata remain visible, and anyone holding a historical age identity may decrypt every historical revision addressed to it. If an identity is compromised, rotate the underlying provider credentials as well as the SOPS data keys.

## Adding code or fixtures

- Generate fixtures from scratch; do not edit or truncate a real export.
- Use `example.invalid` for email and URL examples.
- Use explicit non-production identifiers such as `TEST-TAX-ID-001`.
- Keep amounts, dates, document numbers, names, and combinations independent from operational records.
- Do not add binary fixtures unless the privacy guard is deliberately extended with an exact reviewed hash and the review explains why the binary is necessary.
- Write commit messages without email addresses; name tools in `Co-Authored-By` trailers without an address. The only reviewed exception is the digest-allowlisted Anthropic no-reply service address that already appears in merged history.
- Any new default output path must resolve under the private root, independent of the current working directory.

## What each scan covers

All modes inspect the Git index and working-tree versions of indexed paths.
New untracked files are not included until added to the index. Stage only the
intended files before relying on a scan of a new document or fixture.

| Command | Additional history checked |
|---|---|
| `python scripts/privacy_guard.py` | None; index and corresponding working-tree files only. |
| `python scripts/privacy_guard.py --commit-range origin/master..HEAD` | The specified revision range. Fetch the remote base first when this is intended to represent a PR's new commits. |
| `python scripts/privacy_guard.py --commit-range HEAD` | History reachable from the current commit, not merely the last commit's diff. |
| `python scripts/privacy_guard.py --history` | All history reachable through local Git references (`--all`), including other branches, remote-tracking references and tags; annotated tag content is also checked. |

History checks inspect reachable blobs, historical paths and commit messages,
not just changed lines. The command's reported scope matters: passing the PR
range does not certify every branch or tag. A local backup reference also
retains history for an all-reference scan.

CI runs `--history` after a full-history checkout. The references available to
that checkout can differ from those in a developer's clone. For a CI finding,
record its category, location/commit ID and fingerprint without reproducing the
matched private value. Compare the reported commit with the run's revision and
the current PR head/base SHAs, then inspect which references reach it. Do not
assume a finding belongs to the PR's changes, or that a local range scan clears
it. Repeat verification when the branch or base changes.

Do not weaken the scanner or rewrite unrelated history to turn a check green.
Investigate the identified material under the repository privacy boundary and
report the affected scope separately from merge/rebase availability.

## Before sharing

1. Run all tests and `python scripts/privacy_guard.py --history`.
2. Clone the proposed repository into a new directory and repeat the checks there.
3. Confirm the remote exposes only the intended branch and tags.
4. Grant the least repository permission required. Remember that private-repository access cannot prevent a recipient from retaining a local clone.

Changing `.gitignore` does not remove existing Git objects. If private content is committed, revoke access, rotate affected credentials or sessions, rebuild a clean history from a trusted snapshot, and replace the remote only after validating a fresh clone.
