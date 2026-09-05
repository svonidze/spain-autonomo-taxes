# Installing a release with compiled frontend assets

This is a preparation contract, not permission to deploy. Use the existing
reviewed-SHA, backup, active-mode and rollback procedures in the operations runbook.

## Build contract 1

The target commit declares `[tool.autonomo.web-ui] build-contract = 1` in
`pyproject.toml`. The shared `prepare_ui_release.py` reads that declaration from
the exact Git object. Missing declaration means legacy; unknown contracts fail.

For a new contract-1 target, provision Node 24.20.0 and bundled npm 11.19.0 on the
operator's explicit build PATH. Both deployment modes validate and invoke the
same resolved tools. A working npm registry/cache is required during preparation.
Dependency or network failure stops preparation before stopping the current
service. Node is used only to build; the running service is still Python.

The gate checks a clean target SHA and reviewed lockfile, runs
`npm ci --include=dev --no-audit --no-fund`, then `npm run build`. The build
environment excludes service secrets, ambient VITE variables and Node options.
The caller installs the Python package, records its schema marker and creates a
completion receipt only after isolated imports and installed resource hashes pass.
The receipt is written atomically and includes build SHA, tool versions, lockfile
digest, installed resource inventory and schema version. It is not a deployment
acceptance or backup receipt.

An existing complete target is verified without Node/npm. An incomplete target
is preserved, and preparation refuses to repair it or mint a missing receipt.
Follow separately scoped recovery; do not delete it or blindly rerun deployment.
Old releases do not need the new receipt or assets, and rollback never rebuilds.

## Updating an existing ops installation

Application deployment does not update installed ops helpers. Before the first
contract-1 rollout, compare the reviewed files with the installed active mode:

1. Back up the currently installed `prepare_ui_release.py` if present,
   `healthcheck.py`, and the active mode's `deploy.sh` to the operator's private
   recovery directory. Record which helper was previously absent.
2. Install the reviewed shared Python helper first, then the health checker, each
   through a temporary sibling and atomic replacement with existing ownership and
   intended permissions. Validate Python syntax.
3. Atomically replace only the active mode's deploy caller and validate shell
   syntax. For SOPS the caller uses the shared helper in its parent ops directory.
   Preserve the chosen mode, existing OCR/backup preflights and runtime overrides.
4. On update failure restore the caller before restoring/removing the new helper.
   Do not run the general provisioning installer or change timers.
5. Perform the actual reviewed release only through the existing deployment skill.

Fresh provisioning installers include the helper. The runtime serves only files
listed in `web_ui/dist/build-manifest.json`; the manifest, maps and source files
are not public. `/assets` remains an application route. Both shell and resources
retain `no-store`; there are no lazy UI chunks or service worker.

Health checking discovers JS/CSS references in the served shell, supporting both
old flat resources and new hashed resources. An HTML 200 alone is insufficient.
The developer/CI installed-wheel browser check runs outside the checkout; see
`docs/UI_DEVELOPMENT.md`.
