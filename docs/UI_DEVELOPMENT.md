# Frontend development and verification

Use Node from `.nvmrc` (24.20.0, bundled npm 11.19.0) and Python 3.11. Create and
activate a virtual environment, then install the editable Python package and tests:

```sh
python -m pip install -e . pytest build
npm ci --include=dev --no-audit --no-fund
npm run check:runtime
npm run test:coverage-map
npm run test:legacy
npm run typecheck
npm run test:unit
npx playwright install chromium
npm run test:browser
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
