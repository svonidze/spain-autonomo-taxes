# Correcting a counterparty name

Use **Counterparties → ⋯ → Correct name** to fix a typo in the name of the same
person or organisation. The action is not a way to replace one counterparty
with another.

Select a counterparty name to open its own read-only page. The same actions
menu is available in its header; there are no persistent correction buttons
in the list. The card contains existing details, linked operations and
collapsible name history. It also works when opened directly in a new tab.

Operations initially cover all periods and load in batches of 50. The card's
period filter is separate from other screens. Viewing an expense and returning
to the card retains that filter and restores the originating global period,
including after an expense-page reload. Viewing a card never posts, recalculates
or changes accounting records.

The corrected name is used throughout the app and in newly generated books,
including exports for earlier periods. Existing documents and saved filing
snapshots are not rewritten. Counterparty IDs, source keys, tax identifiers,
amounts and document links are unchanged by the name correction.

## History and imports

Each actual correction records the previous and new names, time, source and
row versions. A verified Tailscale login is recorded when available; a local
session or spreadsheet change has no authenticated author. Saving an unchanged
name does not create another history entry. Returning to an earlier spelling
is a new correction, not deletion of history.

Imports cannot overwrite a manually corrected name. Old spellings are used
as matching hints when adding documents or expenses, but never override a
conflicting tax identity. Ambiguous matches require explicit resolution;
the feature does not automatically merge or split historical counterparties.

## Conflicts and retries

If another tab, import or spreadsheet changed the record while the form was
open, saving returns the current server name. The draft remains in the field.
Review the current name and explicitly accept the new version before saving
again. A lost response can produce this same conflict even if the first save
succeeded; consult the current name and history before retrying.

After a web correction, re-export any old spreadsheet before applying further
changes. Spreadsheet batches are transactional: a failed row rolls back that
batch, not other users' committed work.

## API

- `GET /api/counterparties` includes `row_version` and `name_is_manual`.
- `GET /api/counterparties/{uuid}` returns `counterparty` facts and its list of
  transaction `periods`; no internal source hashes are exposed.
- `GET /api/counterparties/{uuid}/transactions` accepts an optional `period`,
  `offset` (default 0) and `limit` (default 50, maximum 100). It returns `rows`,
  `matching_count`, `next_offset` and `has_more`, scoped strictly to that ID.
  It does not total monetary amounts across lifecycle states.
- `POST /api/counterparties/{uuid}/rename` accepts only `display_name` and
  `expected_row_version`. It returns the current ID, name, version, manual flag
  and `changed`. A current-version no-op has `changed: false`.
- `GET /api/counterparties/{uuid}/name-history` returns the history in descending
  resulting-version order; an existing party with no changes has an empty list.

Writes require the existing session, a matching Origin and JSON content type.
Names must be nonempty single-line strings without control characters.
Punctuation and Unicode are preserved; only surrounding whitespace is trimmed.
The API does not accept an author or tax-profile fields in the rename payload.

An outdated version returns HTTP 409, code `stale_counterparty`, and a
`current` object. A busy database returns HTTP 503, code `counterparty_busy`.
Malformed requests return 400 and missing counterparties return 404.

## Schema and deployment

Schema 20 adds the manual-name flag and append-only application history.
Migration does not rename existing records or infer past manual edits.
The counterparty card and compact menus add no further schema migration.

Deploy only through the reviewed operations workflow: stage the new release,
stop writers, make and verify a backup, migrate with the new code, start the
compatible server and check health before switching the active release.
Do not run old code against the new schema. A rollback must coordinate code
and a verified database snapshot and must not silently discard later writes.
See [Operations](../ops/README.md).
