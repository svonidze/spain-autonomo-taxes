# Understanding accounting statuses

These rules also apply without a browser. See [the agent workflow](AGENT_WORKFLOW.md)
and [CLI capabilities](CLI_CAPABILITIES.md) for supported commands and recovery.


Use **Understand status / Разобраться** beside a record to see its facts,
reasons, related documents and next actions. Read the explanation as well as
the badge: the same short status can have different causes. The **?** help
buttons define terms; required actions do not depend on hovering over them.

This guide describes the source version, not proof of what is deployed on a
particular server. The [design contract](../DESIGN.md#accounting-status-explanations)
defines the interface rules. For the step-by-step review and posting process,
see the [accounting workflow](ACCOUNTING_WORKFLOW.md).

## Review progress and posting readiness

A transaction has two independent states: how far its review has progressed
and whether the server currently permits posting. Saving an `approved` review
does not post the transaction. An approved transaction can still be `ready`,
`deferred` or `blocked`; its badge can show that readiness while the saved
lifecycle remains `approved`.

| State | English / Russian label | Meaning and next action |
|---|---|---|
| `needs_review` | Review required / Требуется проверка | Check the original and resolve the missing facts or decision. Use **Open review / Открыть проверку** when offered. Otherwise pass the specific unresolved requirement to your accountant or operator. |
| `approved` | Reviewed, not posted / Проверено, ещё не проведено | The review decision is saved. Check the current posting readiness before arranging an explicit posting action. Approval alone is not permission to post. |
| `ready` | Ready to post / Готово к проведению | The current posting preview permits posting. Check the intended entries before confirming a separate posting action; viewing this status does not perform it. |
| `deferred` | Waiting for posting date / Ожидает даты проведения | Read the displayed earliest posting date, then refresh readiness on or after that date. Do not change the recorded date to bypass the restriction. |
| `blocked` | Posting blocked / Проведение заблокировано | Read every blocking reason and address the stated evidence, review or period requirement. A future date does not hide other blockers; waiting alone may not resolve them. |
| `posted` | Posted in ledger / Проведено в учёте | The transaction is recorded as posted. Inspect its result and use the supported correction process if needed. This is not an AEAT submission receipt. |

Receiving a document or extracting its data is not review approval. Excluded
states such as `duplicate`, `rejected` and `void` require checking the recorded
reason, not recreating the entry to bypass it. Likewise, a transaction marked
`included_in_snapshot` belongs to a saved snapshot; that marker alone does not
prove submission to AEAT.

An available review link means the record supports that review route. It does
not guarantee that changes can be applied: the review screen checks current
permissions, period restrictions and evidence. No link does not mean approval
or that nothing remains to do. Follow **Next actions / Следующие действия**;
if no confirmation form exists, check the sources and contact your accountant
or operator. An unknown status or missing amount needs clarification, not an
assumed zero or a guessed tax decision.

## Filing is a separate state

| Obligation state | English / Russian label | Next action |
|---|---|---|
| `due` | Not yet filed / Ещё не подано | Check the obligation, deadline and remaining filing work with the responsible person. Posting entries does not submit it. |
| `not_due` | Filing not required / Подача не требуется | Read the saved determination. This describes whether filing is required, not simply whether its deadline is in the future. |
| `filed` | Filing confirmed / Подача подтверждена | Inspect the saved filing information and evidence. This is separate from an individual entry being posted. |
| `waived` | Filing waived by saved decision / Подача не требуется по сохранённому решению | Consult the saved decision and its applicability; do not infer a waiver from missing data. |

The explanation panel does not file returns, approve tax treatment or amend
previously filed periods.

## Assets and amortization

The assets table shows all assets but scopes schedule amounts to the selected
quarter. **In the book schedule / В графике книг** and **Not included in books /
Не включено в книги** are separate totals. A displayed adjustment is a subset
of those entries, not an extra amount to add again. Annual evidence is shown
separately and is never added to the quarterly totals.

The chart covers the year's quarters using only book-included schedule and
adjustment rows. Its scope therefore differs from the table. Neither a future
schedule nor the book-inclusion flag alone proves a recognized tax expense or
a filed return. An excluded historical entry is not automatically a forecast.

**Source records exist / Есть исходные записи** can describe an older asset
without a linked acquisition transaction. It does not automatically mean that
review is missing. Similarly, an empty separate decision field does not prove
that previous deductions were absent. Check the sources and any explicit
issues before deciding what remains unresolved.

When an explanation says **Awaiting advance and VAT review / Ожидает проверки
авансов и IVA**, establish which advance documents remain valid and whether a
deduction was already taken. Do not infer cancellation, zero purchase cost or
completed tax treatment from that status. Resolve the documented question with
your accountant or operator before changing the accounting decision.

## Originals and safe actions

Source availability has three cases:

- **Local:** the service has a local source file.
- **Catalog:** the service identifies an available catalog/archive copy.
- **Unavailable:** the service cannot currently offer the original. Check
  storage and the document link with the operator; this does not prove that
  the document never existed or should be uploaded again.

Available sources offer **Open file / Открыть файл**. Local and catalog copies
use the same action; the interface need not display the storage classification.
If opening fails, retain the existing record and investigate access to its
original rather than treating the badge as proof of availability forever.

Opening explanations and source links does not change accounting records.
**Copy question / Скопировать вопрос**, when offered, copies text locally to
the clipboard; it does not send a message. If copying fails, select the shown
text manually. A supported review link opens a separate workflow where any
confirmation or posting remains an explicit action.
