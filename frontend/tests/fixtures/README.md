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
