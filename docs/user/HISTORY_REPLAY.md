# Rebuilding history from source books safely

`migrate-history` reconstructs the entries supported by exported source books.
`filings record-receipt` compares calculated amounts with values extracted from
filed returns. These checks help verify a reconstruction, but neither proves that
the books contain every entry or that the rest of the ledger has been preserved.

Manual entries can fall inside a period covered by the books. Reviewed identities,
payments, evidence links, filing records and other operator decisions may also be
absent from an export. A matching last date or matching tax total does not establish
that these records can be regenerated. Keep the existing database and build the
candidate in a separate private directory.

## Guard rails for a replay script

1. **Establish source coverage before planning a replacement.** Record the exact
   database selected by the running service's configuration, the source books and
   their periods. Match existing entries to source rows using their recorded
   provenance and identifiers, not just dates or names. List every unmatched
   entry, including manual entries within imported periods, and every category of
   state the import cannot recreate. Define and verify how each will be preserved;
   otherwise the candidate is only a partial reconstruction and must not replace
   the live ledger.

2. **Take a consistent snapshot and create a separate candidate.** For a readable
   database compatible with the installed release, the following Bash setup pins
   both paths explicitly. The staging parent must already be a private directory:

   ```bash
   set -euo pipefail
   read -r -p 'Absolute live database path from service config: ' replay_live_db
   read -r -p 'Absolute private staging parent: ' replay_parent
   [[ "$replay_live_db" = /* && "$replay_parent" = /* ]]
   test -f "$replay_live_db"
   test -d "$replay_parent"
   replay_dir="$(mktemp -d "$replay_parent/history-replay.XXXXXX")"
   replay_db="$replay_dir/autonomo.sqlite"
   autonomo-tax db status --db "$replay_live_db"
   autonomo-tax backup create --db "$replay_live_db" --out "$replay_dir/source.sqlite"
   autonomo-tax db init --db "$replay_db"
   ```

   In a script, stop on any failed check or command. If the live database is
   damaged or incompatible, follow [the recovery procedure](../../ops/docs/DISASTER_RECOVERY.md#1-sqlite-is-damaged)
   before attempting these commands; preserve its raw database and sidecars.

3. **Rebuild only the candidate.** Put extraction outputs and calculations in the
   staging directory and pass `--db "$replay_db"` to every ledger command. Run the
   usual chain in chronological order: the `audit-source-book-*` extraction into
   a canonical rows CSV, `migrate-history`,
   `obligations mark` for the historical quarters, `review post-batch` per period,
   `period close`, `calculate --mode verify_history`, and `filings record-receipt`
   for every filed PDF. Treat any `recorded=False` or non-empty mismatch list as a
   failure of the replay, not as a rounding difference to shrug off.

   On failure, stop and preserve the candidate and diagnostics. Do not run an
   automatic restore against the live path: that could discard writes accepted
   after the snapshot. The live ledger has not been replaced at this stage.

4. **Record decisions for the open quarter.** Check every applicable obligation
   and use `obligations mark` against the candidate to save the reviewed decisions.
   An absent decision is not evidence that filing is unnecessary; do not infer it
   from a dashboard label. `period open`, on releases providing that command,
   seeds undecided obligations but does not decide them.

5. **Verify preservation before a separate cutover.** Reconcile the candidate
   against the snapshot using the coverage inventory from step 1, then check the
   filed amounts and compensation chain. If live writes continued during the
   rehearsal, the snapshot is now stale: stop the service, scheduled jobs and
   other writers, take a fresh snapshot, reconcile all intervening changes and
   repeat validation before switching. Follow the existing
   [production cutover prerequisites](../../ops/docs/DISASTER_RECOVERY.md#production-cutover-dangerous).
   This recipe does not authorize or automate replacing the live database.

6. **Keep the snapshot and verification records.** Retain the source inputs,
   coverage inventory, unmatched-record decisions and calculation/receipt results
   beside the candidate in private storage. Confirm a compatible rollback path
   before any cutover; the presence of a snapshot alone is not a restore test.

## Conditions that stop a replay

- **Schema migrations are explicit.** Use a compatible release to snapshot an
  older database, then test `autonomo-tax db init --db <candidate>` on a copy.
  Do not upgrade the live database merely to prepare a replay.
- **Placeholder identities in the source books.** Exports commonly record the same
  supplier with placeholder identifiers (`9999999`, `0000000T`) and inconsistent
  country codes across months. A shared name does not prove that two rows describe
  the same legal entity. Resolve the source relationship from evidence and, when
  appropriate, record a reviewed primary identity with
  `autonomo-tax counterparties identity-upsert --db <candidate>`. Import warnings
  or a successful retry do not substitute for that review.
- **Zero-amount rows** (for example a provider's free month recorded at 0.00) must
  be voided before posting; otherwise the posting gate rejects the period.

## Verifying the result

Tax verification requires every filed quarter to reconcile to the cent:
each `filings record-receipt` returns `recorded=True` with no mismatches, and the
VAT compensation chain (`compensation_carryforward` from one quarter feeding
`--previous-vat-compensation` of the next) ends on the figure carried into the
first quarter you file yourself. Keep the calculation JSON files next to the
receipts; they are what the receipts were verified against. This verifies the
compared amounts, not preservation of all ledger records. Keep the separate
coverage and state-preservation checks as a prerequisite to replacement.

If no usable old ledger or snapshot survives, document the missing coverage and
unrecoverable state explicitly. A reconstruction from books alone must not be
described as a proven complete restore.
