# Frontend development and verification

Use Node from `.nvmrc` (24.20.0, bundled npm 11.19.0) and Python 3.11. Create and
activate a virtual environment, then install the editable Python package and tests:

```sh
python -m pip install pytest build
npm ci --include=dev --no-audit --no-fund
npm run build
python -m pip install -e .
npm run check:runtime
npm run test:coverage-map
npm run test:legacy
npm run typecheck
npm run test:unit
npx playwright install chromium
npm run test:browser
npm run test:installed
python -m pytest -q
```

Playwright starts a real Python server on 127.0.0.1:8765 using only an ephemeral
synthetic database. It refuses to reuse an already running server. Activate the
Python environment before running it. The test server does not discover personal
configuration or contact real storage providers. Tests do not submit real records.

The migration keeps existing Node suites until their behavior is covered through
real module imports, component tests or browser tests. Maintain
`docs/plans/ui-test-map.json` whenever a suite is added or removed. Generated test
traces, browser reports, dependencies and build output remain untracked.

See [the staged migration contract](plans/ui-modularization.md) for accepted
boundaries, commit/PR policy, baseline evidence and progress.

`npm run build` must precede Python installation and startup. The runtime serves
only verified built assets, with no source fallback. `test:installed` builds a
wheel, creates a fresh environment outside the repository and runs the browser
suite with isolated Python imports. Node is a build/test dependency, not a
separate application service.

Localization uses one ICU catalog set for legacy views and typed components.
See [Interface languages](UI_LOCALIZATION.md) for adding languages, generated
types, pseudolocale checks and state-preserving language updates.

Vue components use `<script setup lang="ts">`; `npm run typecheck` runs vue-tsc
for both scripts and templates. TypeScript 6.0.3 is pinned because vue-tsc 3.3.11
currently requires the JavaScript compiler API absent from TypeScript 7's package
exports. Upgrade them together after verifying component type checking.

During migration the shell mounts each Vue view through `vue/host.ts`, disposes it
before route replacement, and supplies narrow context/services. Views subscribe
to the core locale rather than remounting. StatusCell owns its help record with
update/dispose; the dialog/history adapter stays shell-owned until stage 11.
Old expense markup is an execution oracle for existing VM tests until stage 12;
production expense-detail routing mounts the Vue component.

The expense detail API supplies a plain lifecycle status, not an accounting
`ui_context`, so this screen retains its existing badge. StatusCell's scoped
registry lifecycle is verified at the real helper and component boundaries in
stage 4; its first production consumer is the contacts view in stage 5, which
already receives an authoritative accounting context. Do not invent contexts
for screens whose API supplies only lifecycle status.
