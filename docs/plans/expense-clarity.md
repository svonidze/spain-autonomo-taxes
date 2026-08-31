# Expense clarity — accepted implementation plan

## Outcome and scope
Split Expenses into two visible sections: purchases/services/other expenses and depreciation. Use the same distinctions in dashboard expense cards and recent operations. Preserve documents, transactions, tax calculations, posting, and all existing API values. No production deployment is included.

## Accepted review refinements
- Add new display aggregates rather than modifying existing scalar totals. Compare old API values before/after, not a sum of purchase gross amounts and depreciation deductions (different quantities).
- Resolve asset labels only with exactly one distinct candidate at the selected precedence level. Preaggregate candidates to avoid multiplying transactions or issue counts. Label inferred party/date matches; missing/ambiguous matches retain source context and warnings.
- Use the live server date shared with posting. Do not use browser UTC or cached forecast dates. Refresh visible data on foreground return and every minute without invoking the write-oriented forecast refresh. Reload the entire already-visible page window atomically; a snapshot revision change between pages restarts that window to prevent omissions/duplicates.
- Explicitly retain the existing ready-to-post banner; replace both misleading expense metric cards with per-kind posted/reviewed breakdowns.
- Search asset descriptions on the server, only for unambiguous matches. Display counts and a load-more action when results are truncated. Whole-quarter reviewed totals never depend on search or page size.
- Apply the same narrow-screen stacked-row pattern to both expense sections. Unrelated tables remain untouched.
- Create `DESIGN.md`; no existing design contract was present.

## Rejected review claims
- The importer does produce `historical_g03` dynamically from source reason G03. Keep that existing classifier for existing transactions; do not infer depreciation merely from an asset link or schedule.
- New aggregates do not inherently break old fields. Keep them separate and verify backwards compatibility.
- A mobile adaptation does not require redesigning all tables across the application.

## Data and interfaces
- Enrich `/api/transactions` and dashboard recent rows additively with expense kind, document date, period, asset context, a live `view_as_of` date, and a future-date flag.
- Add read-only `/api/expenses?period=...&q=...&offset=...&limit=...` with rows, matching/quarter counts by kind, complete-quarter reviewed summaries, and explicit pagination. Keep the old transaction endpoint array shape.
- The private quarterly view loads one row per transaction, enriches asset context with one bulk query, computes summaries in minor units, then searches and slices. This is a deliberately small local-ledger projection, not a new accounting engine.
- Posted means posted/included-in-snapshot. Reviewed means approved. Reviewed totals combine those states; duplicate/rejected/void and unreviewed entries are excluded from these totals but remain visible in the list with their actual status.
- Depreciation amount is the recorded IRPF deduction; purchase amount is the recorded transaction EUR amount. Source-document totals are additional labelled context when different. Missing amounts make the relevant summary unavailable, not zero.

## Presentation
- Purchases show recognition date, source-document date if different, party/document, actual status, recorded amount, IRPF, and IVA.
- Depreciation shows asset/source context, quarter and recognition date, actual status, and quarterly deduction as primary. Original purchase amount stays secondary; zero IVA is hidden but unexpected nonzero IVA remains visible.
- Future dates get a persistent note; reaching the date never auto-posts a record. Existing issue badges remain.
- Both sections remain visible, have RU/EN empty states and whole-quarter reviewed totals, and share search/load-more controls.
- Dashboard cards show posted amounts plus reviewed-unposted and future-reviewed amounts separately, without a combined “spent” total. Income and tax cards stay unchanged.

## Acceptance and validation
- Synthetic mixed-quarter fixtures prove per-kind classification and unchanged old API/tax output.
- Verify direct, document-based, inferred, missing, and ambiguous asset links; no duplicate transactions or issue counts.
- Verify asset-name search, more than one page, totals independent of filtering, all terminal states, and missing/zero/negative amounts.
- Freeze time before/on/after recognition, test stale forecast cache and unchanged lifecycle state.
- Node behavior tests cover both locales, depreciation amounts, future ordinary expenses, counts, pagination/search races, and errors.
- Visually check synthetic pages in connected Chrome at desktop/mobile size, keyboard navigation, and long text. Run the relevant Python/Node tests, full suite, privacy guard, and two independent review lanes before committing.
