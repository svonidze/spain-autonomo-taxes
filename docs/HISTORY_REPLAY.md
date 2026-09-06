# Rebuilding history from source books safely

`migrate-history` can rebuild the whole ledger from exported source books, and
`filings record-receipt` proves the rebuilt quarters against the filed returns.
That makes a full replay the strongest integrity check the toolkit offers — and,
done carelessly, the fastest way to destroy the only copy of your own entries.

The rows that came from the source books can always be regenerated. The rows you
posted yourself after the last imported period cannot. A replay script that starts
with `rm autonomo.sqlite` deletes both.

## Guard rails for a replay script

1. **Refuse when the ledger holds rows newer than the source books.** Compare the
   last date covered by the imported books with the ledger before touching it:

   ```bash
   autonomo-tax db status   # refuses on a schema mismatch; see below
   sqlite3 "$AUTONOMO_PRIVATE_ROOT/autonomo.sqlite" \
     "SELECT COUNT(*) FROM transactions WHERE transaction_date > '<last source date>'"
   ```

   A non-zero count means the replay would delete your own postings. Stop, or
   require an explicit override variable that the operator has to type.

2. **Snapshot before the first write.**

   ```bash
   autonomo-tax backup create --out "$AUTONOMO_PRIVATE_ROOT/backups/pre-rebuild-$(date +%Y%m%d-%H%M%S).sqlite"
   ```

3. **Restore automatically when any step fails.** In a shell script, combine
   `set -e` with an `ERR` trap that runs `autonomo-tax backup restore --from <snapshot>`
   and exits non-zero. A half-finished replay is worse than no replay.

4. **Rebuild into the private root only after the snapshot exists**, then run the
   usual chain in chronological order: `db init` on the fresh file, the
   `audit-source-book-*` extraction into a canonical rows CSV, `migrate-history`,
   `obligations mark` for the historical quarters, `review post-batch` per period,
   `period close`, `calculate --mode verify_history`, and `filings record-receipt`
   for every filed PDF. Treat any `recorded=False` or non-empty mismatch list as a
   failure of the replay, not as a rounding difference to shrug off.

5. **Re-create the open quarter and seed its obligations.** `ensure_period` only
   creates the period row; without `obligations mark` for the quarter's forms the
   web interface and the dashboard report the quarter as "filing not required".

6. **Keep the snapshot.** It is the evidence that the replay reproduced the same
   ledger, and the rollback point if a later step turns out to have drifted.

## Conditions that stop a replay

- **Schema migrations are explicit.** Opening a database created by an older
  release fails with a schema-version error until you run `autonomo-tax db init`
  on it; the migration is one-way, so snapshot first.
- **Placeholder identities in the source books.** Exports commonly record the same
  supplier with placeholder identifiers (`9999999`, `0000000T`) and inconsistent
  country codes across months. The importer refuses rows that contradict a known
  identity unless the counterparty carries a reviewed primary identity
  (`autonomo-tax counterparties identity-upsert`). Register the real identity once,
  then replay.
- **Zero-amount rows** (for example a provider's free month recorded at 0.00) must
  be voided before posting; otherwise the posting gate rejects the period.

## Verifying the result

The replay is trustworthy only when every filed quarter reconciles to the cent:
each `filings record-receipt` returns `recorded=True` with no mismatches, and the
VAT compensation chain (`compensation_carryforward` from one quarter feeding
`--previous-vat-compensation` of the next) ends on the figure carried into the
first quarter you file yourself. Keep the calculation JSON files next to the
receipts; they are what the receipts were verified against.
