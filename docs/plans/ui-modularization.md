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
  Status cells use scoped registry ownership. Locale changes never dispose Vue
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

- Stage 1: implemented and locally verified; source application unchanged.
- Stages 2–12: pending.
