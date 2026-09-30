# Spain autónomo accounting toolkit

A Python toolkit for bookkeeping review, evidence tracking, accounting books,
and Spanish tax-preparation workflows, with a bundled browser interface.

Requires Python 3.11 or newer. A prebuilt wheel includes the compiled interface
and needs no Node runtime. When installing from the repository, build the
frontend with the pinned Node/npm toolchain first, then install this directory:

```sh
npm --prefix frontend ci --include=dev --no-audit --no-fund
npm --prefix frontend run build
python -m pip install -e ./backend
```

These commands run from the repository root. The installed entrypoints are
`autonomo-tax` and `autonomo-web`. Private records, configuration, credentials,
and generated accounting data stay outside the repository.

See the repository README and documentation for setup, accounting workflows,
and operations. Review generated filing data against official sources and a
qualified professional before submission.
