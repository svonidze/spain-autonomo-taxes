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
again and requires an exact match. It also checks the current review snapshot,
the archived source and all decision fields. Any failure rolls back both the FX
write and the accounting decision.

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
  calculations.

