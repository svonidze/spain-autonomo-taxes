# Optional UI design

This document governs the optional browser interface. The primary toolkit and
agent workflow are defined in README.md, AGENTS.md and docs/AGENT_WORKFLOW.md.
Accounting behavior belongs to shared Python services.

## Source of truth
- Status: Active; refreshed 2026-09-04.
- Surfaces: Expenses, dashboard expense metrics, recent operations, and the analytics charts documented below.
- Evidence: `src/autonomo_taxes/local_web.py`, `frontend/src/main.ts`, `src/autonomo_taxes/web_ui/styles.css`, the accepted
  expense-clarity plan in `docs/plans/expense-clarity.md`, the user-supplied
  expense-chart screenshot dated 2026-09-04, and AEAT's 2026 `LSI.xlsx`
  registry-book design.

## Brand
- Calm, readable bookkeeping; preserve the existing green palette and system fonts.
- Trust comes from explicit labels and unchanged source records, not decorative badges.
- Avoid implying payment, tax approval, or automatic posting from a date or lifecycle badge.

## Product goals
- Distinguish purchases from depreciation of an earlier purchase without opening a help tooltip.
- Make source-document date, recognition date, original amount, and quarterly deduction unambiguous.
- Do not change tax calculations, source amounts, posting rules, or generate scheduled entries.

## Personas and jobs
- A self-employed owner reviews recorded expenses and prepares a quarter without accounting jargon.
- Desktop inspection and mobile checking must expose the same essential information.

## Information architecture
- Preserve navigation and routes. Expenses contains two expanded sections: purchases/services/other expenses, then depreciation.
- One search and one paginated result set cover both sections. Whole-quarter reviewed totals are independent of search.

## Design principles
- Recognition is not payment; reviewed is not posted; a future date is not a new purchase.
- For depreciation, the quarterly deduction is primary and original purchase information is secondary.
- Show uncertainty explicitly. Never choose an arbitrary asset from multiple candidates.
- Keep existing ledger/API values intact; introduce separate display aggregates.

## Visual language
- Reuse existing CSS variables, panels, badges, typography, spacing, and focus rings.
- Use text as well as color for status. Do not add external fonts, images, icons, or animation.

## Components
- Reuse tables, document actions, SPA links, metrics, loading/error messages.
- Add expense section headers, recognition notes, whole-quarter totals, and a load-more control.
- Scope responsive expense cards to both expense sections; preserve the existing scrollable recent-operations table and unrelated tables.

## Accessibility
- Persistent explanations, visible search label, keyboard-operable controls, readable focus states.
- Preserve table/row/cell semantics and column labels in the mobile layout.
- Escaped user content; no hover-only information or color-only status.

## Responsive behavior
- Desktop tables; at 700px and below use labelled stacked rows in both expense sections.
- Test at 375px and desktop width, with wrapping names and nonzero VAT.

## Interaction states
- Loading preserves context; failed search/load-more offers retry without presenting stale rows as current results.
- Empty sections distinguish no records, no matches, and records on subsequent pages.
- Reviewed totals say they cover the whole quarter, not just visible matches.
- Disabled load-more prevents duplicate requests. Route/search changes invalidate stale responses.
- Refresh the read-only expense view on foreground return and periodically while visible; server dates determine future markers.

## Content voice
- Plain RU/EN labels; no new accounting abbreviations beyond the existing IRPF/IVA terms.
- Use “Дата учёта”, “Документ от”, “Амортизация за квартал”, “Проверено, не проведено”.
- A source-book attachment is a source document, not necessarily the original supplier invoice.
- Asset matching inferred from party and document date is labelled as inferred.

## Implementation constraints
- Optional Vue/TypeScript UI over shared Python/SQLite services; preserve schema and accounting rules.
- Only existing `historical_g03` expense transactions are depreciation in this iteration. This code is produced by the importer; asset ownership alone is not a classification rule.
- Preserve existing API fields and tax results. New display sums use integer minor units and retain missing/zero/negative values.
- Test data must be invented, never copied from operational accounting records.

## Open questions
- None for this iteration. Creating new depreciation operations is a separate accounting workflow.

## Counterparty list and detail

- The list's primary action is opening `/contacts/{id}`. Names are real links
  (including new-tab behavior); unmodified clicks on non-interactive row space
  also navigate. Text selection, links and controls keep their normal behavior.
- Keep existing columns, status explanations and the concentration chart.
  Remove persistent rename buttons and manual-correction labels from rows.
  A borderless 44px actions trigger at the right edge opens the single
  additional action, Correct name. The same menu appears in the detail header.
- One menu at a time, outside table overflow; clamp it to the viewport. Escape
  and clicks on noninteractive outside space restore focus to the trigger.
  Clicking another interactive control closes the menu without stealing focus.
- The detail page is read-only: existing identity/contact facts, linked
  transactions and collapsed name history. Do not add monetary totals or new
  accounting actions. Preserve unknown, zero and negative amounts.
- Operations default to all periods, newest first, in batches of 50. The local
  period filter belongs to the card and does not change other screens' period.
  Hide the global period selector on the card; return links preserve the card's
  filter and the list's scroll position. Preserve the originating global
  period across an expense excursion; the expense itself keeps its true period.
- Use the existing read-only expense page and document availability/actions.
  No income detail editor, schema change or import change is part of this work.
- Reuse the existing rename dialog from both surfaces, including direct links
  that have never loaded the list. Saving refreshes the active card or list,
  not another record reached while the request was in flight.
- Navigation uses the rename dialog's dirty/busy policy. A refused Back restores
  the current route and draft; while saving, navigation waits for the result.
- Preserve the palette, fonts, status-help and chart components. Check RU/EN,
  keyboard access, 375px width, long names, bottom-row menus, empty/error states,
  pagination and stale responses during navigation or filter changes.

## Reviewed IVA investment classification

The expense review now separates the IRPF asset decision from the IVA investment
goods decision. This authorized accounting change supersedes the earlier
display-only constraints for this field and its schema/calculation integration.
Keep existing components, routes, source amounts and unrelated design rules.

- Add a labelled, keyboard-accessible RU/EN select beside the expense decision:
  unknown, current purchase for IVA, or investment good for IVA. Unknown remains
  visibly blank and must not become false through truthiness or a default.
- Explain persistently that IRPF depreciation does not determine IVA treatment.
  Approval requires an explicit IVA choice; rejection does not.
- Preserve the selected boolean, including false, across edits, reloads and
  validation. Stale review packets must be reloaded, not silently upgraded.
- Show the saved classification in the read-only expense card. Legacy null
  means unreviewed classification, not an affirmative tax decision.
- Existing rows and snapshots are not automatically reclassified. Legacy
  calculation fallback must be accompanied by a visible report warning.

## Accounting status explanations

This contract also covers the read-only context
from `src/autonomo_taxes/status_context.py` and its presentation in
`frontend/src/help/presentation.ts`. For operator-facing meanings and
actions, see [Understanding accounting statuses](docs/ACCOUNTING_STATUSES.md).

- Present the short reason beside the status and make the next action readable
  in the explanation panel without hover. Tooltips define terms only; neither
  a required action nor the only explanation of a blocker belongs in a tooltip.
- Keep review lifecycle, posting readiness and permission to apply a review
  separate. The server posting preview is authoritative: `approved` is not
  `ready`. An approved transaction can be blocked or deferred. A future date
  must not hide other blockers or make the client infer readiness.
- Preserve structured blocker codes and subject references. Translate known
  reasons explicitly in RU and EN; unknown reasons retain an honest fallback.
  Do not infer a tax decision from free text, a missing decision or a generic
  status belonging to another domain.
- Distinguish a known zero, unknown/missing data and an inapplicable value.
  Do not format missing amounts as zero. Posting, schedule inclusion, annual
  evidence and confirmed filing remain distinct facts; the period scopes in
  [Documented deltas](#documented-deltas) apply to the asset table and chart.
- Viewing explanations and copying a question must not mutate accounting data
  or send messages. Only server-supported review links are offered; navigation
  does not grant permission to apply a change. Available local/catalog originals
  may be linked, while unavailable files need explicit recovery guidance.
- Keep the explanation dialog outside the rerendered app container. Escape
  and close restore focus; Back closes the panel before leaving the page.
  A page or period change must not leave stale record details open.

When resolving overlapping UI changes, preserve both status explanations and
analytics. A textual merge without conflicts is not evidence that shared
helpers or mounted components survived. Check the effective loaded scripts,
single helper declarations, combined asset status/chart rendering and stale
render handling using `frontend/tests/help-lifetime.test.ts`, `tests/test_status_context.py`
and the Playwright scenarios in `tests/browser`. Keyboard, mobile reflow and browser-native
zoom are separate checks; equivalent-width reflow does not certify 200% zoom.

## Interface states and review navigation

This contract covers the app-wide view states and the review screen's
navigation added by the clarity redesign.

- Views render through distinguishable states: a loading skeleton
  (`uiLoadingSkeleton`), then content, a contextual empty state, or an error
  state. Errors (`errorState`) carry the danger-toned `state-error` look,
  `role="alert"`, and a retry button — or a reload button when the session
  expired. An empty search result is not the same message as an empty
  period; the income register and the review queue say what is empty and
  what to do next.
- Chart slots reserve height while empty (`.chart-slot:empty`) so mounted
  charts do not shift the layout. Skeleton shimmer honors
  `prefers-reduced-motion`.
- The review overview is three tabs — queue, posting, documents — with the
  actionable counts on the labels (needs review / ready to post / open
  issues). The active tab travels in `?tab=`; `queue` is the implied default
  and never appears in the URL; unknown values normalize to queue in the
  current history entry; review and expense detail routes never carry a tab;
  a period switch preserves the active tab. The dashboard posting banner
  deep-links to the posting tab via `data-nav-tab`.
- The workspace posting-readiness chip (`reviewCategoryBadge`) maps the
  evaluation categories ready/later/blocked/needs_review onto the
  status-help tone classes with localized labels; an unexpected category
  degrades to the neutral tone with the generic status label, never a raw
  token.
- When a work item cannot be posted (later or blocked), every decision field
  is disabled and a banner explains why; the reject panel stays usable.
  Confirm and reject show a busy "Submitting" state while a request is in
  flight. Minor-unit integer inputs carry a live euro preview
  (`minorUnitEurPreview`): a known zero previews as 0.00 €, missing or
  non-numeric input previews nothing.
- The intake dialog keeps a versioned draft in
  `localStorage["autonomo.intake-draft"]` (`{schema: 1, values}`): restored
  on reopen unless a copy-flow prefill wins, cleared by a successful submit,
  discarded on schema mismatch. The document date is capped at today, and an
  expense whose base plus IVA disagrees with the total shows a live,
  non-blocking hint. Server failures report through the dialog status line
  only; toasts announce success. Error toasts persist until closed.

The behavior is checked by the Vue unit tests and browser scenarios listed in
`docs/plans/ui-test-map.json`, including state, locale, route and guided-review coverage.

## Analytics and charts

This file is the source of truth for measure definitions, status policy, chart
placement, color/pattern semantics, accessibility rules, and empty states used
by the analytics endpoint (`/api/analytics`, built by
`src/autonomo_taxes/analytics_series.py`) and the web charts
(`frontend/src/charts/renderer.js`). When the SPA, generated artifacts, or
a future server-side SVG renderer disagree with each other, this document
decides. Change the definitions here first, then the code.

### Measures

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
- **Taxes-page headline**: the large amount is the cash expected to leave for
  AEAT in the selected quarter, with IRPF and IVA kept as separate components
  immediately below it. A negative IVA result never offsets positive IRPF in
  this headline. For a current quarter the headline is explicitly a dated
  preview; for a past quarter it says `paid` only for evidence-backed EUR
  payments linked to the filed obligation. Filed, payable and confirmed-paid
  amounts remain separate values.
- **Money encoding**: every API money field ends in `_minor` and is an integer
  of EUR cents or `null`. `0` means a known zero; `null` means
  unavailable/not calculated. Rates use basis points. The API returns machine
  keys only; the client owns translation and formatting.

### Lifecycle status policy

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
- `GET /api/taxes` adds `period_state` and `tax_summary` without removing the
  legacy form fields. `period_state.phase` is calendar-derived (`future`,
  `current`, `past`) and stays separate from the ledger period status
  (`open`, `closed`, `amended`). Each supported form exposes preview, filed,
  payable and confirmed-paid minor-unit amounts, plus outstanding/overpaid and
  IVA carry-forward/refund disposition where applicable.
- A future quarter has no payable total (`null`), not a known zero. Missing or
  unsupported required calculations also make the total unavailable. Partial
  payment and overpayment are explicit states. A direct-debit cutoff is only a
  deadline; the UI must not infer that a debit is scheduled without explicit
  payment-method evidence.
- Modelo 303 distinguishes credit generated in the quarter (casilla `72`),
  total credit carried forward, and a requested refund (casilla `73`). These
  are explanatory amounts and do not increase the cash-payment headline.

### Chart placement

| View | Charts |
|---|---|
| Dashboard | monthly income vs deductible expenses; quarterly tax cash due; IVA position (27/45/71 + disposition); tax reserve bullet; cumulative business net |
| Taxes | year-over-year YTD comparison (decision support, never final Renta) |
| Expenses | expense structure by AEAT concept (deductible / non-deductible / unclassified) |
| Review | queue aging by age bucket and status |
| Contacts | top customers with an "Other" fold |
| Assets | amortization per quarter (`include_in_books = 1` only) |

### Chart sizing and expansion

A chart is drawn at the width it actually occupies, never scaled to fit.
The mount site measures its host, subtracts the figure padding, and passes
that width into the scene, so one viewBox unit equals one CSS pixel and axis
and value text stay at a plain 12px in every slot. Requested widths are
clamped to 280–1600; when no measurement is available (a hidden tab, a
non-browser test harness) the scene keeps the 640-unit default. A window
resize or a return to the tab re-measures live charts and redraws only those
whose width changed.

Narrow charts thin their bucket labels deterministically — every Nth label
when a band falls under 34px — and the horizontal-bar label column shrinks
from 150 toward 90 below roughly 500px. Thinning removes labels only; every
value stays in the data table and in the mark titles. Quarterly charts have
too few buckets to thin.

Each non-empty chart carries one expand control in its caption. It re-renders
the same spec in the shared `#chart-dialog` at dialog width — no nested
expansion, no second copy of a slot id. The dialog closes on any view
re-render, returns focus to the control that opened it, and pauses the
periodic refresh while open. Empty charts show no control, and the control is
hidden in print. No animation accompanies the expansion.

The expense-structure chart uses plain-language category names from the
versioned [2026 AEAT registry-book list](https://sede.agenciatributaria.gob.es/static_files/AEAT/LSI.xlsx),
with the original G-code as a secondary label for audit and export
cross-checking. Unknown future codes remain visible
beside an honest "unknown AEAT category" label; an absent code is "category not
assigned". The table is initially expanded, adds the gross total, and shows
each deductible/non-deductible amount with its share of a positive gross total.
Shares are unavailable for zero or negative totals, negative components, or a
component sum that does not equal the gross total. On narrow screens and at
browser zoom that triggers the same breakpoint, the labelled table replaces
the dense SVG plot as the primary view. Every table cell retains its column
label when rows reflow into cards.

### Color and pattern semantics

Chart series colors bind to the existing CSS custom properties via
`chart-tone-*` classes; meaning is never carried by color alone.

- Income → `--info`; ordinary expense series → `--warning`; primary result
  lines and current-year values → `--accent`; `--danger` is reserved for alert
  states (overdue approved rows), never an ordinary series; `--muted` is for
  annotations and bands only. The expense-structure breakdown is the explicit
  exception: the portion that reduces the IRPF base is solid `--accent`, while
  the portion that does not is hatched `--warning`.
- Actual = solid fill. Approved-not-posted = hatched fill (SVG `<pattern>`
  keyed by chart id). Forecast = outlined fill. Projected lines = dashed.
- Validated with the color-vision checks: the pair `--info`/`--warning` and
  the chain `info → warning → accent → danger` pass CVD separation on the
  light surface. **Do not place a `--muted` mark adjacent to an `--accent`
  mark** — that pair measured ΔE 1.8 under deuteranopia and is illegible.
- Legends appear for two or more series; single-series charts rely on the
  caption. Value text uses text tokens, not series colors.

### Chart accessibility

Every chart is a `<figure>` with a visible `<figcaption>`, an SVG with
`role="img"` and an `aria-label`, and a `<details>` data table with the same
numbers, so the content survives screen readers, printing, and forced colors.
Chart data cells carry their column header through `data-label` so the mobile
card layout does not turn amounts into unidentified numbers.
Marks carry native `<title>` tooltips. The renderer builds DOM exclusively via
`createElementNS`/`textContent` with an attribute allowlist: no inline styles,
no inline handlers, no `innerHTML`, no `Date.now`/`Math.random` (scene output
is deterministic for a given spec).

### Chart empty states

Distinguish, in this order: analytics fetch failed → "could not load"; some
transactions lack FX → missing-FX message; only unreviewed rows exist →
unreviewed message; forms filed without extractable values → filed-without-
values message; calculation blocked (reserve) → blocked message; otherwise →
plain no-data message. All-zero data is a real chart with a zero baseline,
not an empty state.

### Documented deltas

- Expense-list and metric depreciation amounts are quarterly deductions, while
  the expense-structure chart retains the analytics dataset's source gross
  amounts. A visible note explains that a depreciation row's original purchase
  cost is not a new charge. Dashboard refresh retains expanded chart tables,
  their keyboard focus and scroll position.
- The assets table scopes schedule amounts to the selected quarter and shows
  book-included and excluded rows separately. Annual evidence is displayed
  separately in the explanation panel and is never added to quarterly totals.
  The amortization chart shows the year's quarters using only
  `include_in_books = 1` schedule/adjustment rows, excluding annual evidence;
  its note explains why its scope differs from the table.
- `src/autonomo_taxes/amortization_chain.py` is a CSV audit tool for Xolo
  artifacts and does not back the web analytics; the chart reads
  `amortization_entries` directly.
- The tax reserve bullet reports `not_checked` when no explicit available-cash
  figure exists (`cash_check.py` receives `available_eur=None`); available
  cash is never inferred from payment rows.
- The optional Vue/TypeScript interface lives in `frontend/src`; feature modules and
  shared components adapt Python service results for display. See
  `docs/UI_DEVELOPMENT.md` for build, translation and testing boundaries.
  Adding a chart must not replace status explanations.

## Expense and equipment workflow

The approved expense workflow extends the earlier display-only scope. Routine
bookkeeping uses shared services through CLI or authenticated HTTP, never sudo/SSH, deployments,
service stops or full-root backups. Keep the current palette, components and RU/EN voice.

- Unposted expenses offer a durable server draft: original alongside editable
  facts, supplier/activity selection, business purpose and reviewed tax treatment.
- Ordinary expense and equipment branches share one final preview and explicit
  Confirm and post action. Only the selected purchase and immediately eligible
  depreciation are posted. Saving a changed approved draft invalidates approval
  visibly and preserves its audit trail. Posted/closed records remain read-only.
- Equipment offers immediate low-value or calendar-day linear depreciation;
  reviewed parameters and a versioned schedule are persisted. Plans are not actuals.
  Future periods require a later explicit action from the asset page.
- Native schedule-to-transaction links identify depreciation; do not infer new
  journal types from descriptions or pretend they are historical imports.
- The final summary distinguishes purchase amount, IVA, current IRPF deduction
  and later depreciation. Business use and IVA investment status stay separate.
- Errors preserve the draft. Repeated requests return the saved outcome;
  post-commit refresh/cleanup failures offer repair without reposting.
- Reuse label/focus/error patterns at desktop and 375px width. The original must
  remain accessible if embedding fails. One expense or asset per workflow.
