# Accounting status explanations

## Historical implementation plan

This document preserves the accepted plan for the original implementation.
It is not a current operating guide, a record of completed checks or proof of
deployment. Read [Understanding accounting statuses](../user/ACCOUNTING_STATUSES.md)
for user-facing guidance. Current behavior is defined by the implementation
and its tests together with the [design contract](../../DESIGN.md); update those
sources when behavior changes rather than extending this historical plan.

## Accepted implementation decisions

- Keep existing pages and GET array envelopes; enrich each row with a read-only
  `ui_context`. No schema migration, fiscal decision, posting or deployment.
- Use the server posting preview as the sole posting-readiness authority,
  including cleanup blockers. A future date means `deferred` only when the
  preview says so. Review editability remains separate from posting readiness.
- Preserve structured blockers (`code`, `details`, subject references), alongside
  compatibility text. Localize known codes explicitly, with honest unknown-code
  fallbacks. Do not infer meaning from free-text messages.
- Count distinct open issues and distinct blocking issues separately. Aggregate
  tax treatments before joining transactions; conflicting values remain unknown.
  Complementary NULL fields follow the canonical tax-row loader: a unique known
  value is retained; two distinct non-null amounts are a conflict.
- `/api/assets?period=YYYY-QN` keeps its array response. Without a period use the
  current or latest available quarter. Unknown valid quarters return a controlled
  not-found error. Keep all assets visible and explicitly scope schedule amounts.
- Count and sum quarterly schedule/adjustment rows separately from annual
  evidence. Separate `include_in_books` values; excluded historical entries are
  not automatically called forecasts, and book entries do not prove tax filing.
- Use a modal native dialog outside `#app`, styled as a right drawer, full screen
  at the existing 760px breakpoint. Opening details does not navigate, rerender
  the list or write data. Browser Back closes the panel before leaving the page.
  Escape/close restore focus. Changing page/period closes stale details.
- Show a short reason and next action inline. The panel contains facts, reasons,
  source documents and existing review links. Copying a question is local only.
  Tooltips contain definitions, never required actions or the sole blocker reason.
- Resolve status text/tone by domain; do not relabel every `unknown` globally.
  Distinguish zero, missing and inapplicable values. Translate both RU and EN.
- Use a cached structural capability query for review navigation; opening the
  actual review remains the authoritative evidence/decision check. Do not hash
  source files per contact or per document merely to offer a navigation link.
- Remove duplicate helper declarations before modifying them. Test the effective
  scripts as well as endpoint payloads and browser behavior.
- Keep the existing light theme; check browser forced colors without introducing
  a separate theme. Test keyboard, 200% zoom and mobile layout.

## Verification

Cover mixed blockers plus future dates, nonblocking issues, multiple treatments,
missing context, unsupported codes, excluded/annual/adjustment schedule rows,
missing periods, source links, zero amounts and filed-without-values states.
Use synthetic data only. Verify GET requests preserve database contents. Run
browser checks on a local synthetic service; never change production data.

Deliver a reviewed commit and draft PR from the isolated task worktree.
