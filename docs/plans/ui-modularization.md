# UI modularization: execution contract

Approved scope: Vue 3 + strict TypeScript, Vite, framework-independent FormatJS/ICU
catalogs, Python/SQLite retained. Twelve stages, each with atomic commits and a
separate dependent PR. No automatic merging, production rollout or tax/schema
changes. The planning review was performed with Claude Opus/max; this does not
approve the future implementation.

## Source and upstream changes

Implementation starts from freshly fetched master
`27522dec76169435d4793324cce11c8be3179922`, not the older planning snapshot.
This base includes account/backup settings, expense workflow and follow-up,
depreciation actions, tax-result/payment views, verified FX fixes and deployment
environment fixes. Preserve these features and their existing safeguards.
The source app now has 6,371 lines and there are 13 standalone JavaScript suites.

Settings migrate with the shell in stage 11. Expense follow-up migrates with the
detail in stage 4; the newer expense workflow migrates with review in stage 8.
Depreciation and tax-result actions remain with their views in stage 7. These are
preservation obligations, not permission to perform real accounting operations.

## Stages and PR dependencies

| Stage | Branch suffix | Result | Gate |
|---|---|---|---|
| 1 | baseline | Runtime, test inventory and synthetic browser baseline | Existing Python/JS tests, route/help history and file-input browser checks |
| 2 | build-install | Vite legacy entry, packaged assets, both deployment modes | Installed-wheel CSP/MIME/404, fail-before-stop, legacy rollback |
| 3 | core-locales | Typed HTTP, formatters, drafts, shared ICU catalogs | Module-aware tests, catalog/parameter/plural validation, live locale state |
| 4 | expense-detail | Vue expense detail and follow-up, lifecycle bridge | Authoritative period, returnTo, errors, late replies, help/focus |
| 5 | contacts | List/detail/history/menu/rename | Revisions/conflicts, dirty/busy, local period, pagination/scroll |
| 6 | transaction-lists | Expenses/income and copy intent | Revision-aware paging/search/refresh, original values, legacy intake bridge |
| 7 | overview-analytics | Dashboard/assets/taxes, existing chart geometry | Chart oracle first; preserve depreciation/tax actions and FX evidence |
| 8 | review-detail | Guided review and expense workflow | Draft/snapshot/FX/decision parity, additive errors, return to legacy overview |
| 9 | review-posting | Whole review overview and batch posting | Frozen period/items, partial responses, refresh never reposts |
| 10 | intake | Upload/Drive/Picker/copy dialog | File and form preservation, busy/error/retry, mocked remote services |
| 11 | shell-router | Vue shell/settings, Vue Router and help history | Complete navigation/dirty/busy/settings parity; old test oracles retained |
| 12 | finalize | Remove monolith, temporary adapters/globals/source tests | Coverage disposition complete, installed app acceptance, documentation |

Branches use `codex/ui-NN-<suffix>`. The first PR targets master, each next PR
targets the previous branch at its recorded tested SHA. Publish coherent tested
commits as draft PRs. Merge only when separately authorized, bottom-up using merge
commits; retarget and propagate ancestor updates upward, then rerun affected gates.
No hard stack-depth limit or freeze of master. Preserve unrelated work.

## Interfaces and compatibility

- One typed core supplies HTTP, locale, formatting and navigation. View bridges
  implement mount/updateContext/dispose and receive narrow services, not the
  mutable legacy state. Delegated legacy events ignore Vue-owned roots.
- Keep the existing route dispatcher and help overlay history until stage 11.
  Vue status cells gain scoped registry ownership with the stage 4 bridge.
  Until then, legacy DOM replacement sweeps detached help contexts while retaining
  live sibling buttons and the active overlay. Locale changes never dispose Vue
  hosts, navigate, or invalidate a running write. A POST cannot be cancelled by
  aborting its local observer. Preserve existing settings/rename leave policies.
- Preserve URLs, allowed returnTo, authoritative detail period, contact-local
  period, review tabs, snapshots, versions, frozen posting inputs and native links.
- Keep existing error/code/current responses. Add optional message_code/params/
  field for actionable validation without changing accounting rules. Retain tested
  text-error compatibility for older responses in a separate module.
- UI messages are key+parameters; stored user/source text and audit reasons are
  data. RU remains initial/fallback with autonomo.locale. Dates/numbers follow UI
  locale; source values, currency and units never change with language.
- JSON catalogs are per language/domain. Compile ICU AST and generated TS message
  types at build time. Validate duplicate keys before JSON parsing, logical key
  coverage, named arguments, syntax and locale-specific Intl plural categories.
  New language = catalogs + registry entry. Ship RU/EN; pseudolocale/ES fixtures
  are tests only. Keep one shared FormatJS engine and a thin Vue adapter.
- Before Vue cutover, locale refresh preserves review reject/FX/details state and
  semantic focus. Translate open dialogs in place (especially file inputs), and
  defer background legacy repaint when necessary. Do not reset toast timers.

## Build and release

Pin Node 24.20.0/npm 11.19.0 and exact npm lockfile dependencies. Keep root package
CommonJS-compatible for existing tests. Legacy imports retain this order:
status-help, charts, expense-workflow, settings, app. New modules are strict TS.
Build into ignored web_ui/dist; resources use /ui-assets, never /assets. Publish
only known assets, preserve CSP and no-store, and eagerly load routes/catalogs.
No source fallback, public maps, dev servers or runtime secrets in the bundle.

Declare a build-contract version in project configuration. For new releases,
preflight the pinned tools, install dev build dependencies, build from the reviewed
SHA, install the Python package, validate the installed resources, and atomically
record completion before stopping the old service. Both deployment modes share
the gate. Existing completed releases can be reused without build tools; partial
ones remain untouched pending separately scoped recovery. Legacy releases retain
their old install/rollback path without requiring new receipts or Node.
Build in a sanitized environment; runtime and Picker configuration still use API.
An installed-helper update is a separate documented rollout step, never a general
provisioning/timer change. No deployment is performed by this migration task.

## Verification and evidence

`ui-test-map.json` maps existing JS suites to stages and replacing tests. A suite
cannot disappear without existing replacement paths. Review behavior coverage,
not only this structural check. Keep Python API tests. Browser tests use the real
server with a temporary synthetic database; no operational config/data is loaded.

Baseline at the starting SHA: **1,319 passed, 4 skipped**, Python 3.11.15, pytest
9.1.1. Three warnings are expected legacy-config migration tests. Initial Python
run used the host Node 26.7.0; the separate JS/browser gates use pinned Node 24.
All 13 JS suites pass with Node 24.20.0. The expense-workflow suite consumes JSON
from its existing Python fixture and is invoked through that test driver.
Browser checks cover real route/help history under CSP, intake file/field state,
authenticated unknown-resource responses and settings dirty navigation.

## Progress

- Stage 1: published as draft PR #41, commit `668b9ae`; local gates and independent reviews passed.
- Stage 2: build/install verified; 1,339 Python checks passed with four skips,
  plus 13 legacy suites and four browser scenarios against both source-built and
  isolated installed-wheel resources. Added missing/corrupt/stale-wheel checks.
  Independent code review APPROVE and architecture review CLEAR. Exact-SHA local
  preparation/install/receipt/reuse was exercised at `39f9ed3`, including reuse
  with Node absent from PATH. No production services or data were accessed.
- Stage 3: shared TypeScript core and 1,049 ICU message IDs implemented. Twenty-one
  direct module checks, 13 legacy suites, ten installed-wheel browser scenarios
  and the test-only third-locale browser scenario pass. Python: 1,340 passed,
  four skipped. Registry lifetime regression verifies detached record release,
  live siblings and Back/Forward.
- Stage 4: Vue expense detail and follow-up implemented with typed host/services,
  reactive locale and explicit disposal. Same-record context updates retain pending
  writes; stale reads and writes cannot update another route. Scoped StatusCell is
  tested against the actual registry and Vue lifecycle; production adoption begins
  with authoritative contact contexts in stage 5 (expense detail retains its plain
  lifecycle badge). Typecheck, 26 module tests, 13 legacy suites, 15 compiled and
  installed-wheel browser scenarios, and 1,340 Python tests pass (four skips).
- Stage 5: Vue contacts list/detail, operation paging, name history, action menu
  and revision-aware rename implemented. Contact context feeds scoped StatusCell.
  Host updates respect dirty/busy guards; rename refreshes the concentration chart.
  Browser gates cover help history, contact/global period, expense return, rename
  conflicts, pending writes, paging, menu focus/placement and CSP. Typecheck,
  27 module tests, 13 legacy suites and 1,340 Python tests pass (four skips).
- Stage 6: Vue expense/income lists implemented with revision-aware paging,
  guarded search/refresh, scoped help and typed income-copy intent. Source amounts
  and currencies are preserved when bridging to the legacy intake. Typecheck,
  30 module tests, 13 legacy suites, 25 source-built and installed-wheel browser
  scenarios, and 1,340 Python tests pass (four skips). Master was fetched again
  and remains `27522dec`; the complete stack includes the latest base.
- Stage 7: Vue dashboard/assets/taxes and depreciation schedule implemented.
  ChartHost now owns all migrated charts, cached locale redraws and cleanup.
  Numerical geometry is retained as an unchanged JS leaf with strict API types;
  domain builders and Vue lifecycle are strict TS. This explicit exception was
  reviewed by the architect and remains valid after stage 12. Legacy global
  adapters still retire at finalization. Chart/form mappings pass old-oracle
  comparisons; 35 unit checks, 13 legacy suites, full chart ES-module checks,
  29 source/installed-wheel browser scenarios and 1,340 Python tests pass (four
  skips). Independent code review APPROVE; architecture CLEAR.
- Stage 8: Vue review-detail preflight, native expense workflow and guided review
  implemented. Matching-snapshot drafts, explicit conflict reset, ID-matched issue
  edits, FX evidence, reject flow and request idempotency retain their contracts.
  Parent and child lifecycles preserve same-record pending work and use current
  callbacks after context updates. Required-field errors gain optional localized
  metadata without changing original codes/text/status. Forty unit tests, 13
  legacy suites, 33 source/installed-wheel browser scenarios and 1,342 Python
  tests pass (four skips). Code review APPROVE; architecture CLEAR.
- Stage 9: Vue review overview tabs and batch posting implemented together.
  Consent snapshots freeze period/items/versions; durable feature state retains
  late results and per-period recovery markers. Refresh identity protects newer
  markers; retry never reposts. Mismatched previews fail closed. Forty-four unit
  tests, 13 legacy suites, 36 source/installed-wheel browser scenarios and
  1,342 Python tests pass (four skips). Code review APPROVE; architecture CLEAR.
- Stages 10–12: pending.

Stage 2 compatibility note: Vite's CommonJS handling hid the settings global used
by the old shell. The module now retains that namespace as well as its CommonJS
export; the existing settings tests and compiled browser checks cover both forms.
Obsolete Python script-tag assertions now verify built resources; initialization
order remains covered by the browser baseline. See `ops/FRONTEND_BUILD.md` for the
scoped installed-helper update, partial-target handling and legacy rollback.

Stage 3 extraction evidence: 1,510 original RU/EN main/settings dictionary values
were compared with the ICU-backed compatibility adapters against parent `1db5d58`;
all matched. Literal copy moved out of the four UI scripts and the HTML source.
The remaining legacy renderer is 4,997 lines; feature ownership moves to Vue in
stages 4–11. Old VM contexts now receive the real typed core explicitly, while new
module tests import it directly. Browser tests cover live locale changes during a
POST, rejection drafts/focus, native expense drafts and help history.
