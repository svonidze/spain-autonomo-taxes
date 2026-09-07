# Repository layout

The repository has two application roots:

| Directory | Responsibility |
| --- | --- |
| `frontend/` | Node manifest and lockfile, Vite configuration, TypeScript source, and frontend unit and browser tests. |
| `backend/` | Python package metadata, `backend/src/autonomo_taxes`, and application tests. |
| `tests/` | Cross-stack support, fixtures, contracts, operations tests, and packaging tests. |
| `ops/` | Installed operational helpers, systemd templates, and operator runbooks in `ops/docs/`. |
| `examples/` | Safe configuration and import examples. |
| `reference/tax-calendars/` | Versioned calendar inputs that callers pass explicitly. |

These are ownership and tooling boundaries within one release. The frontend
build writes verified assets into `backend/src/autonomo_taxes/web_ui/dist`; the
Python wheel embeds those assets, so frontend build precedes Python packaging.

From the repository root, install and verify the application with the current
project roots:

```sh
npm --prefix frontend ci --include=dev --no-audit --no-fund
npm --prefix frontend run build
python -m pip install -e ./backend
npm --prefix frontend run check:runtime
npm --prefix frontend run test:coverage-map
python -m pytest -q
```

Release preparation still accepts a checked-out layout-1 release with root
`pyproject.toml` and Node files at its root. A layout-2 release has
`backend/pyproject.toml` with `layout-version = 2` and its Node project in
`frontend/`; the release helper selects the layout from the requested release
SHA, rather than the caller's working directory.

The repository calendar moved from `config/tax-calendar-2026.json` to
`reference/tax-calendars/2026.json`. Operator commands must pass the new path
explicitly; private configuration is not rewritten by this repository move.

This is a directory-ownership change. Splitting the Python package into domain
subpackages remains a separate follow-up, so existing `autonomo_taxes` module
imports continue to be supported.
