# RETA bracket check

The RETA bracket check compares the monthly contribution base you chose with
the TGSS (Tesorería General de la Seguridad Social) against the income tramo
that your posted income reaches so far this year. It runs the arithmetic of the
TGSS annual regularisation on 1 January to a month end, without projecting the
rest of the year.

It is decision support, not a TGSS resolution. Its amounts are estimates. They
are never added to, or netted against, the tax cash due to AEAT, the Taxes-page
headline, or any combined burden. TGSS regularises the year only after AEAT
reports your final income.

## Legal basis

- [LGSS (Real Decreto Legislativo 8/2015) art. 308](https://www.boe.es/buscar/act.php?id=BOE-A-2015-11724#a308):
  income for RETA is the IRPF business net plus the owner's own contributions,
  less a generic deduction of 7 % (3 % only for art. 305.2.b and e workers).
  Provisional contributions are compared with the tramo minimum and maximum,
  and the difference is regularised.
- [RD 2064/1995 art. 44 to 47](https://www.boe.es/buscar/act.php?id=BOE-A-1996-1579):
  the monthly average is income × 30 ÷ natural days in alta, with monthly
  differences netted (art. 44.2.a and 46.2). Base changes take effect on
  1 January, March, May, July, September or November (art. 45.1). A partial
  month pays thirtieths of its base (art. 47.1).
- [Orden PJC/297/2026 art. 18 and 37](https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-7296):
  the 2026 tramos, bases and rates (31.5 % in total). The values are in
  [reference/reta/2026.json](../../reference/reta/2026.json).
- [Ley 20/2007 art. 38 ter](https://www.boe.es/buscar/act.php?id=BOE-A-2007-13409):
  tarifa plana days are left out of the regularisation.

## Setting up

Import the table of the year once. The file is validated and stored byte for
byte; importing the same file again changes nothing.

```sh
autonomo-tax reta table import --input reference/reta/2026.json
```

Then record every base from your TGSS resolutions, oldest first:

```sh
autonomo-tax reta base add --effective-from 2026-01-01 --regime base \
  --monthly-base 950,98 --worker-kind individual \
  --source-reference "TGSS resolution, CSV code" \
  --source-file resolution.pdf
```

- `--effective-from`: the date the resolution applies from. The first row, a
  row on the `starts_on` date of a business activity (an alta or a re-alta
  after a baja), and the row that ends a tarifa plana may start on any day.
  Other changes must start on 1 January, March, May, July, September or
  November.
- `--regime`: `base` for an elected base, `tarifa_plana` for the reduced flat
  quota. A tarifa plana row takes no `--monthly-base`. Months 13 to 24 of a
  tarifa plana need their own TGSS resolution: record it as another
  `tarifa_plana` row starting on the first day of month 13.
- `--monthly-base`: the monthly base in euros, as printed on the resolution,
  with a comma or a dot and at most two decimals (`950,98` or `950.98`, no
  thousands separator). It cannot exceed the maximum base of that year, so
  import the year's table first. For a base set in a year that has no table
  here, record the base in force on 1 January as a row effective 1 January.
- `--worker-kind`: `individual`, `societario` (LGSS art. 305.2.b or e) or
  `colaborador` (familiar colaborador).
- `--source-reference`: where the value comes from, such as the resolution's
  CSV code. `--source-file` stores only the file's SHA-256; the file is not
  copied.

Rows are append-only: they cannot be edited or deleted, and each one must start
after the previous one. Repeating the same command is harmless.
`autonomo-tax reta base list` shows the live rows with their `election_id`.

To correct a mistaken row, void it and record it again:

```sh
autonomo-tax reta base void --election-id ID --source-reference "Typo in base"
```

The void is kept, with its reason, and the row no longer counts. Only one live
row may start on a date. If you find an older resolution after recording later
ones, void the later rows, record the older one, and then record the later
ones again.

The alta periods come from the business activities in the taxpayer profile
(`starts_on` and `ends_on`). The check needs exactly one taxpayer profile.

## Running the check

```sh
autonomo-tax reta check --year 2026 [--as-of 2026-07-15] [--through 2026-06-30] [--out check.json]
```

`--as-of` defaults to today. `--through` must be a month end and defaults to
the last month end before `--as-of` (31 December for a past year). The command
prints the result as JSON. It exits 0 when the status is `ok`,
`below_bracket` or `above_bracket`, and 2 when it is `unknown`.

Income is the IRPF business net of the posted rows dated 1 January to
`--through`, with the year's difficult-expense rule, plus the deductible part
of rows with AEAT expense concept `G45` (social security contributions).
Non-deductible surcharges are therefore not added back. Approved rows of a
filed Xolo source book count as posted, as in the period dashboard. Other rows
dated in that window that are still in review, or approved but not posted, are
never counted; they make the result `unknown`.

## Reading the result

| Status | Meaning |
| --- | --- |
| `ok` | The bases so far are inside the minimum and maximum of your tramo. Nothing would be regularised. |
| `below_bracket` | The bases so far total less than the tramo minimum. `estimated_additional_minor` estimates the additional payment. |
| `above_bracket` | The bases so far total more than the tramo maximum. `estimated_refund_minor` estimates the refund. |
| `unknown` | An input is missing or not verifiable. Estimates are `null`; see `reasons`. |

Amounts are in cents. Other fields:

- `income.monthly_average_minor` and `bracket`: the monthly income and the
  tramo it falls in.
- `boundary_sensitive`: the income is within 10 % of the tramo width from one
  of its limits, so a few more rows can move it to the next tramo.
- `additional_locked_in_minor`: the part of the shortfall that a base change
  requested on `as_of` can no longer avoid.
- `next_base_change`: the earliest date a new base can take effect, and the day
  by which you must request it.
- `months`: the alta, tarifa plana and regularisable days and the provisional
  base of each month.
- `ledger`: the unposted transactions, the missing quarters, and the error that
  blocked the income calculation, if any.

| Reason | What to do |
| --- | --- |
| `table_unavailable` | Import `reference/reta/<year>.json`. |
| `base_missing` | Record the base for the days without one. |
| `unposted_rows_in_window` | Review and post the rows listed in `ledger.unposted_transaction_ids`, or reject them. |
| `period_missing` | A quarter in alta has no ledger period yet; record its rows. |
| `ledger_blocked` | Fix the error in `ledger.blocked_detail`; it also blocks the tax calculations. |
| `window_empty` | No regularisable day yet (no alta, or only tarifa plana). |
| `worker_kind_changed` | The worker kind changed during the year; the check does not split the year. |
| `worker_kind_floor_unverified` | Societarios and familiares colaboradores have a minimum base the check does not model. |
| `tarifa_plana_extension_unverified` | A tarifa plana row runs past month 12. Record the extension resolution as a `tarifa_plana` row from month 13, or the base that followed. |
| `base_below_table_minimum` | A special base below the table minimum; its regularisation is not modelled. |
| `base_above_table_maximum` | A recorded base exceeds the maximum of the imported table, for example after a corrected table. Check the table and the base. |

## Limits

- No projection: the result describes 1 January to `--through` only. Income
  later in the year can still move the tramo.
- Days are natural days. Weekends, public holidays and vacations do not change
  the divisor; only alta and tarifa plana days do.
- Special bases below the table minimum, societarios (whose company income is
  not in the ledger) and familiares colaboradores stay `unknown`.
- The limit of six base changes a year, pluriactividad and other employment
  are not modelled.
- The check has not been compared with a real TGSS regularisation resolution.
