# Foreign-currency exchange rates

Foreign-currency income and expenses need a sourced EUR conversion before
they can be approved and posted. The application proposes a reference rate in
the guided review, but the user or authorized operator still confirms the
invoice facts, business purpose and tax treatment.

## Rate convention and calculation

The ECB EXR daily series quotes the number of currency units for one euro. The
ledger stores the inverse convention: euros for one currency unit.

```text
eur_per_unit = 1 / units_per_eur
amount_eur = original_amount * eur_per_unit
```

Both the inversion and multiplication use `Decimal`, not binary floating-point
arithmetic. The final EUR amount is rounded to the nearest cent with
`ROUND_HALF_UP`. The stored provenance retains the source URL, the normalized
raw observation and its SHA-256 hash.

The lookup date is the transaction date. It is not automatically the document
issue date, service period or payment date; those facts remain separate and
must be reviewed for the entry.

## Manual rates

`autonomo-tax review apply-fx` takes the rate from its JSON payload, and
`autonomo-tax transactions apply-fx` applies a rate stored earlier with
`autonomo-tax fx add`. Both use the ledger convention above: the rate is euros
for one unit of the foreign currency, the figure the ledger multiplies by the
original amount. The ECB, Banco de España and public quote pages publish the
opposite direction, units for one euro, so a published quote must be inverted
before it is entered.

```text
published quote:  1.2500 units per EUR
rate to enter:    1 / 1.2500 = 0.8000 EUR per unit
100.00 units   -> 80.00 EUR
```

Entering the published figure unchanged (`1.2500`) would book 125.00 EUR for
the same 100.00 units, and nothing downstream distinguishes that from a
correct entry.

### Reference guard

When `rate_source` is `ecb` or `banco_de_espana`, both commands fetch the ECB
observation for the rate date (or the latest prior one within the seven-day
window) before writing anything and compare the entered rate with it.

| Comparison | Outcome |
|---|---|
| Within 5% of the ECB EUR-per-unit rate | Recorded. |
| Within 0.5% of the ECB units-per-EUR figure while more than 5% off the EUR-per-unit rate | Refused as an inverted quote. The error shows both conventions and the expected value; enter the inverse. `--allow-unverified-rate` does not override this. |
| More than 5% off for any other reason | Refused unless `--allow-unverified-rate` is passed. The recorded `source_reference` then carries a note that the rate was not verified against the ECB reference. |
| Reference unavailable: offline, HTTP error, unsupported currency or no observation in the window | Recorded, with a warning on stderr and an audit note in `source_reference`. The JSON result carries the unavailable status so API clients can show the warning. |

Both commands include `fx_rate_check` in their JSON result, with `status` and
`detail`. Status is `verified`, `unverified` (an explicit deviation override),
`unavailable` or `exempt`. `/api/review/apply-fx` preserves this object beside the
refreshed review item. Clients must show `unavailable` and `unverified` details;
a successful write does not establish that the reference was checked. The audit
note remains in the stored source reference after the response is gone.

`actual_settlement` and `xolo_recorded` rates are not compared: a settlement
or recorded rate legitimately differs from the fixing. A rate that genuinely
differs from the official reference should be recorded under the source that
describes it rather than forced through as `ecb` or `banco_de_espana`.

## Suggestion states

| State | Meaning | Required action |
|---|---|---|
| `exact` | The ECB published a rate for the transaction date. | Check the currency, date, rate and converted amount, then confirm it with the review. |
| `prior` | No same-day observation exists; the latest earlier observation is no more than seven calendar days old. | Check the displayed observation date as well as the transaction date before confirming. |
| `existing` | A sourced rate is already linked to the transaction. | Check the saved rate and provenance; do not create another rate merely to repeat review. |
| `unavailable` | The ECB lookup failed, the currency is unsupported or no eligible observation exists. | Do not guess or use an unsourced web conversion. Supply documented settlement evidence or stop the review. |

A later observation is never substituted for the transaction date. A prior
observation keeps its real publication date and is not relabeled as a same-day
rate.

## ECB reference versus actual settlement

An ECB rate is an official reference observation, not proof of the amount
credited or debited by a bank or payment provider. When the reviewed accounting
basis is an actual settlement, record that rate with its own documentary
reference. A settlement rate must not be labeled as `ecb`, and replacing a
linked rate must preserve the provenance relationship.

The normal guided-review confirmation sends the selected rate and the invoice
decision together. For ECB selections, the server fetches the official rate
again, records its own observation rather than browser-supplied evidence, and
requires an exact match. The chosen ECB observation must be dated on the
transaction date or within the same seven-day prior window used for the
suggestion. It also checks the current review snapshot, the archived source and
all decision fields. Any failure rolls back both the FX write and the accounting
decision.

Each confirmed ECB selection records an immutable verification for that
transaction and rate. Reusing a rate records the server observation and the
actual retrieval URL even if the original rate was saved with a different
date window. A conflicting numeric value is rejected.

The rate's original provenance and retrieval URL remain intact. Older records
may contain browser-supplied evidence, including `source_reference` in their
raw JSON; upgrading does not relabel or rewrite them as server-verified. The
review response exposes this original `provenance` separately from the latest
`verification` for the transaction's currently linked rate. The displayed
source reference and raw observation use that verification when it exists.

This requires schema **24**, applied through the normal explicit database
migration before starting the updated service. Migration adds an empty
`fx_verifications` table; it performs no network calls or retrospective
verification. Preview writes, including verification records, are rolled back;
confirmation commits the verification with the accounting decision.

## Workflow boundaries

Confirming the rate and review leaves the transaction approved. Posting is a
separate action and must be restricted to the intended transaction. Neither
approval nor posting records payment, transfers money or submits a return to
AEAT.

If a response is lost, reload the transaction before retrying. If the period is
closed, the transaction is future-dated, the source is missing, the review has
changed concurrently or the FX provenance cannot be verified, retain the
existing record and follow the reported blocker instead of bypassing it.

Implementation source of truth:

- `src/autonomo_taxes/fx_reference.py` defines ECB retrieval, validation,
  inversion and the seven-day fallback window.
- `src/autonomo_taxes/review_packet.py` defines suggestions, EUR rounding and
  atomic confirmation.
- `src/autonomo_taxes/fx_policy.py` defines sources allowed in production
  calculations and the manual-rate reference guard.
