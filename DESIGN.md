# Design contract: analytics and charts

This file is the source of truth for measure definitions, status policy, chart
placement, color/pattern semantics, accessibility rules, and empty states used
by the analytics endpoint (`/api/analytics`, built by
`src/autonomo_taxes/analytics_series.py`) and the web charts
(`src/autonomo_taxes/web_ui/charts.js`). When the SPA, generated artifacts, or
a future server-side SVG renderer disagree with each other, this document
decides. Change the definitions here first, then the code.

## Measures

- **Income (IRPF basis)**: `tax_treatments.taxable_base_minor` when present and
  non-zero, otherwise the stored EUR amount. This mirrors
  `calculate_modelo130_rows` (`src/autonomo_taxes/tax_engine.py`), which falls
  back to the gross amount when the taxable base is zero.
- **Deductible expense**: `tax_treatments.deductible_irpf_minor`; missing
  treatment means 0 deductible, and the gross amount is reported separately in
  the expense-structure dataset.
- **Business net ("net before difficult-to-justify expenses")**: income (IRPF
  basis) minus deductible expenses. No difficult-expenses provision, no rates.
- **Tax cash due**: positive payables per form — Modelo 130 casilla `19`,
  Modelo 303 casilla `71` — never merged into a single "tax burden" number,
  and no effective-rate line (a VAT settlement is not an income-tax rate).
- **Money encoding**: every API money field ends in `_minor` and is an integer
  of EUR cents or `null`. `0` means a known zero; `null` means
  unavailable/not calculated. Rates use basis points. The API returns machine
  keys only; the client owns translation and formatting.

## Lifecycle status policy

The analytics `policy` block is authoritative; `analytics_series.py` implements
it. `date` below is `transactions.transaction_date` compared with `as_of`.

| Series | Statuses | Date rule |
|---|---|---|
| `actual` | `posted`, `included_in_snapshot` | date ≤ as_of |
| (quality only) `future_posted` | `posted`, `included_in_snapshot` | date > as_of — data-quality anomaly, excluded from datasets |
| `approved_unposted` (backlog) | `approved` | date ≤ as_of |
| `approved_future` (forecast) | `approved` | date > as_of |
| review queue (counts only) | `received`, `extracted`, `needs_review` | any — never in money/tax projections |
| excluded | `duplicate`, `rejected`, `void` | any |

Additional rules:

- EUR amounts come from `amount_eur_minor`, falling back to `amount_minor`
  only when `currency = 'EUR'`. A foreign-currency row without FX is excluded
  from money datasets and counted in `quality.missing_fx_transaction_count`.
- `tax_treatments` may hold several rows per transaction; they are collapsed
  into one per-transaction projection first. Conflicting values exclude the
  transaction and increment `quality.conflicting_treatment_count` (unlike
  `tax_row_loader`, analytics must not raise on user data).
- Signed corrections sum through; the API never inverts expense signs.
- Periods: open quarters report actuals and previews separately; closed
  quarters prefer filed snapshot values; amended quarters keep the original
  filed result and annotate `amendment_period_key` — history is not restated.
  A quarter absent from the `periods` table reports `status: "missing"`.
- `GET /api/analytics` is strictly read-only: it opens SQLite in `mode=ro` and
  never launches CLI subprocesses or refreshes caches.

## Chart placement

| View | Charts |
|---|---|
| Dashboard | monthly income vs deductible expenses; quarterly tax cash due; IVA position (27/45/71 + disposition); tax reserve bullet; cumulative business net |
| Taxes | year-over-year YTD comparison (decision support, never final Renta) |
| Expenses | expense structure by AEAT concept (deductible / non-deductible / unclassified) |
| Review | queue aging by age bucket and status |
| Contacts | top customers with an "Other" fold |
| Assets | amortization per quarter (`include_in_books = 1` only) |

## Color and pattern semantics

Chart series colors bind to the existing CSS custom properties via
`chart-tone-*` classes; meaning is never carried by color alone.

- Income → `--info`; expense/deductible → `--warning`; primary result lines
  and current-year values → `--accent`; `--danger` is reserved for alert
  states (overdue approved rows), never an ordinary series; `--muted` is for
  annotations and bands only.
- Actual = solid fill. Approved-not-posted = hatched fill (SVG `<pattern>`
  keyed by chart id). Forecast = outlined fill. Projected lines = dashed.
- Validated with the color-vision checks: the pair `--info`/`--warning` and
  the chain `info → warning → accent → danger` pass CVD separation on the
  light surface. **Do not place a `--muted` mark adjacent to an `--accent`
  mark** — that pair measured ΔE 1.8 under deuteranopia and is illegible.
- Legends appear for two or more series; single-series charts rely on the
  caption. Value text uses text tokens, not series colors.

## Accessibility

Every chart is a `<figure>` with a visible `<figcaption>`, an SVG with
`role="img"` and an `aria-label`, and a `<details>` data table with the same
numbers, so the content survives screen readers, printing, and forced colors.
Marks carry native `<title>` tooltips. The renderer builds DOM exclusively via
`createElementNS`/`textContent` with an attribute allowlist: no inline styles,
no inline handlers, no `innerHTML`, no `Date.now`/`Math.random` (scene output
is deterministic for a given spec).

## Empty states

Distinguish, in this order: analytics fetch failed → "could not load"; some
transactions lack FX → missing-FX message; only unreviewed rows exist →
unreviewed message; forms filed without extractable values → filed-without-
values message; calculation blocked (reserve) → blocked message; otherwise →
plain no-data message. All-zero data is a real chart with a zero baseline,
not an empty state.

## Documented deltas

- The assets table's "schedule" column sums all amortization entries, while
  the amortization chart counts only `include_in_books = 1` rows (annual
  evidence rows are excluded); the chart carries a note saying so.
- `src/autonomo_taxes/amortization_chain.py` is a CSV audit tool for Xolo
  artifacts and does not back the web analytics; the chart reads
  `amortization_entries` directly.
- The tax reserve bullet reports `not_checked` when no explicit available-cash
  figure exists (`cash_check.py` receives `available_eur=None`); available
  cash is never inferred from payment rows.
- `app.js` contains a historical duplicated declaration block (`t`,
  `intlLocale`, …). All chart logic lives in `charts.js` and uniquely named
  `build*Spec` helpers; the duplicate block is out of scope here.
