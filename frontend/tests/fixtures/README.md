These synthetic expected values were captured before removing the legacy oracle,
from app.js retained at stage 11 (`28681500e1c8f2541d7fa8e2785728d380f83b94`).
They are data, not executable copies of the old implementation.

- chart-expected: ten chart specs and formatter samples in RU/EN.
- finance-expected: settlement status/phase/zero/missing matrix and form states.
- review-expected: answers, FX options, issue decisions, diagnostic routing, merge.
- posting-expected: alias normalization, partial results and consent row versions.

Update expected values only for an intentional reviewed behavior change. Keep
zero/missing/negative distinctions and source amounts explicit. Synthetic analytic
and review inputs are local test data; they do not come from a taxpayer database.

Regenerating against the legacy oracle: check out stage 11
(`git worktree add ../ui-11 2868150e1c8f2541d7fa8e2785728d380f83b94`), run its
`frontend/tests/chart-parity.test.ts`, `finance-parity.test.ts`,
`review-helpers.test.ts` and `posting-state.test.ts` (they compared the typed
modules with `app.js` in a VM) with the same inputs, and copy the values they
assert into these files. Name the reviewed behaviour change in the commit.
