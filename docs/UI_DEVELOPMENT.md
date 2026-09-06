# Frontend development and verification

The application uses Vue 3, strict TypeScript, Vue Router and Vite. Python/SQLite
remain the server and accounting engine. The old app.js monolith and runtime
namespaces are removed. There is one Vue tree; adding a framework or splitting
another application service is unnecessary.

## Source ownership

| Directory | Responsibility |
|---|---|
| `frontend/src/shell` | Bootstrap, URLs, navigation guards, toolbar and notifications |
| `frontend/src/features` | Contacts, transactions, review/posting, overview, intake and settings |
| `frontend/src/core` | HTTP errors, ICU formatting, browser drafts, focus and request IDs |
| `frontend/src/help` | Read-only accounting explanations, scoped records and dialog lifecycle |
| `frontend/src/charts` | Chart domain mapping, Vue lifecycle and geometry |
| `frontend/src/locales` | Per-language/domain JSON catalogs and language registry |
| `frontend/tests` | Direct module/component tests and synthetic baseline data |
| `tests/browser` | Browser scenarios against a real temporary Python server |

Components receive narrow typed contexts/services. Route changes dispose the
active screen; intake stays mounted. Locale changes update text in place. Settings
and contact rename expose dirty/busy guards. Late requests, canonical URL changes
and posting results retain explicit ownership by route, period or operation.
Detail ids in URLs are canonicalized (lowercase, hyphenated) before requests and
comparisons, matching the server's `UUID` normalization.

`frontend/tests/support/mount.ts` is a test-only host. The production shell mounts
a fresh screen instance for every route change (the `revision` key in `Shell.vue`),
so the `*-host.test.ts` suites verify component robustness under context
replacement and pending writes; they are not a shell contract.

Vue Router owns route and same-URL help entries through public force/state APIs.
Nested return URLs retain URLSearchParams encoding. Contact-local periods stay
separate from the global quarter; help is transient and does not reopen on reload.
Help records are released by their owning StatusCell, and its registry contains no
persistent user data. Notifications retain their original timeout policy.

Chart geometry is the intentional JavaScript exception: `charts/renderer.js`
retains the numerical/SVG algorithm with a typed API in `renderer.d.ts`/`types.ts`.
The renderer has no global namespace. Its direct-module geometry suite and frozen
chart/financial presentation data preserve zero/missing/negative distinctions.
These fixtures contain expected synthetic data, not executable legacy code.

## Run locally

Use Node 24.20.0/npm 11.19.0 from `.nvmrc` and Python 3.11. Activate a Python virtual
environment, then run the checks in this order:

```sh
python -m pip install pytest build
npm ci --include=dev --no-audit --no-fund
npm run build
python -m pip install -e .
npm run check:runtime
npm run format:check
npm run test:coverage-map
npm run typecheck
npm run test:unit
npm run test:chart-module
npx playwright install chromium
npm run test:pseudo
npm run test:browser
npm run test:installed
python -m pytest -q
```

`npm run format` formats TypeScript/Vue sources and their unit tests. Generated
catalogs and the unchanged geometry kernel are excluded. TypeScript 6.0.3 is pinned
with vue-tsc 3.3.11; upgrade them together after verifying the compiler API boundary.
Dependencies and lockfile versions are exact; Node is a build/test dependency.

Playwright starts its own server on 127.0.0.1:8765 with an ephemeral synthetic
database and refuses to reuse another process. It does not discover personal
configuration. Google OAuth/Picker and external Drive endpoints are mocked; live
OAuth was not exercised. Credentials stay in memory from the runtime API. Intake
retains native File objects, while schema-1 browser drafts contain allowed text
fields only. Settings drafts remain exclusively in memory.

Run Python resource tests after builds/pseudolocale finish, as CI does: rebuilding
the same dist while tests read its manifest creates transient failures.

## Packaging and review

Build before Python installation or startup. The server serves only verified
packaged assets, with no source fallback, public maps or dev server. Installed
acceptance builds a fresh wheel and Python environment outside the checkout, then
runs the browser suite with isolated imports. Eager bundling intentionally keeps
open sessions independent of later release asset changes.

The original 13 JS suites and Python source-layout checks have explicit replacement
records in `docs/plans/ui-test-map.json` and `ui-python-test-map.json`. Accounting,
API, storage and security tests remain in Python. A path-existence check alone is
not evidence of behavioral coverage; review the named module/browser cases too.

See [the twelve-stage execution record](plans/ui-modularization.md),
[adding interface languages](UI_LOCALIZATION.md), and
[release build/install compatibility](../ops/FRONTEND_BUILD.md). The twelve PRs
are dependent drafts for bottom-up review. Merging and production rollout require
separate authorization; neither is performed by this migration.
