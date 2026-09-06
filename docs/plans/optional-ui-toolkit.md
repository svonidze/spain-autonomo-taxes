# Primary toolkit and optional UI

Approved baseline: PR #52, `4b6693170ba840933d3a676158ee4ccfd18ea71d`.
No production deployment, schema change or fiscal-rule change belongs to this work.
Each stage gets a separate commit-bearing dependent PR; no automatic merge.

1. Independent core/UI packages and contract-2 web release preparation — implemented; independent reviews approve/clear.
2. Shared services, reads, original intake, income review and atomic FX — pending.
3. Native expense workflow, depreciation and retryable follow-up — pending.
4. Counterparties, settings and financial read parity — pending.
5. Complete agent instructions, skills and synthetic acceptance — pending.

The core is `spain-autonomo-taxes` with `autonomo-tax`. Optional
`spain-autonomo-taxes-ui` owns `autonomo-web` and its assets. Core never imports
or starts the web adapter. Agent-oriented command additions preserve old CLI
semantics and reuse domain services rather than duplicating accounting logic.

Final acceptance: full income/FX, expense and depreciation flows in an isolated
core-only environment, equivalent HTTP/CLI results, conflicts/retries/partial
follow-up, paired UI installation and legacy release compatibility. Examples use
synthetic private roots and cannot contact real storage or production services.
