# Recording AEAT documents

This runbook archives one already obtained official AEAT PDF. It does not file
a declaration, answer an AEAT request or change an accounting period.

## Preflight

Use the active release and its configured database and archive root. Keep the
source outside Git, copy it to a private staging directory with mode `0600`, and
record its SHA-256. Do not print taxpayer identifiers or PDF text in shared logs.

Run a non-writing preview first, using synthetic values here:

```bash
autonomo-tax aeat-documents record \
  --db /private/autonomo.sqlite \
  --evidence /private/staging/synthetic-modelo-036.pdf \
  --procedure-kind roi_registration \
  --procedure-code G322 \
  --form 036 \
  --document-kind submission_receipt \
  --status submitted \
  --occurred-at 2032-04-03T10:20:30+02:00 \
  --requested-effective-on 2032-04-10 \
  --reference 2032C3600000001A \
  --submission-reference 2032C3600000001A \
  --justificante 0367000000001 \
  --verification-code SYNTHETICCSV0001 \
  --actor synthetic-operator \
  --dry-run
```

The preview must match the PDF. Re-run without `--dry-run` and add the configured
`--archive-root`. The command copies the file beneath
`aeat/<year>/<document-kind>/`, verifies its hash, registers the file replica,
creates the case and appends the initial status event in one database transaction.

Repeat the same command after an uncertain response. An identical PDF and
metadata returns `idempotent: true`; different metadata for the same bytes is an
error and must be investigated.

## Status changes

Read the current case id and row version from the authenticated UI or a scoped
read-only query, then preview the transition:

```bash
autonomo-tax aeat-documents record-status \
  --db /private/autonomo.sqlite \
  --case-id 11111111-1111-4111-8111-111111111111 \
  --status approved \
  --occurred-at 2032-04-08T09:00:00Z \
  --evidence-reference synthetic-vies-check \
  --actor synthetic-operator \
  --expected-row-version 1 \
  --dry-run
```

Remove `--dry-run` only after checking the transition. Status events and AEAT
document metadata are immutable; a stale row version requires re-reading the
case. Terminal cases cannot be reopened.

## Verification and cleanup

After recording, verify the row in **AEAT documents**, open the original through
the application and compare its SHA-256 with the staging file. Confirm SQLite
integrity and foreign keys. Remove only the verified transient staging copy;
the archived original remains. Run the configured backup and check its truthful
local and upload markers according to the production operations runbook.
