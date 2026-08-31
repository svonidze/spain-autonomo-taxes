# Reviewing and posting an accounting entry

Posting makes an approved income or expense transaction eligible for the
working accounting calculations. The application checks whether the transaction
can be posted, then records its posted status. You, or an operator acting on
your instructions, confirm the facts and accounting decisions first.

Approval and posting do not transfer money, prove that an invoice was paid,
or send a tax return to AEAT. A posted entry also does not establish that a
whole period is complete or ready to file.

Routine review and posting use the application. You do not need administrator
access, a terminal, or a maintenance script for these actions. Missing editing
features and service failures may require an operator, as described below.

## The normal workflow

These are the English labels in this source version. An installed server can
run an older release: merging a change does not deploy it. If the available
actions differ, ask the operator to check the active version before proceeding.

1. Open **Income** or **Expenses**, select the intended period and use **New
   entry** to add the document. Check its number, dates, currency and amount
   before submitting it. If the original is already registered, work with that
   existing entry instead of uploading it again to fix a field.
2. The service attempts extraction. Recognized text and amounts are candidates
   for review; successful OCR does not establish that the document or its tax
   treatment is correct. Some intermediate statuses may be skipped.
3. Use **Open review** for the entry and **Open file** to compare its saved
   facts with the original. Confirm the business purpose and accounting
   treatment, explain the decision and address each outstanding requirement.
   Ask an operator about unsupported or uncertain fields; do not guess values
   merely to make validation pass.
4. Use **Confirm review** when the facts and decision are correct. The service
   validates and applies the decision, including a confirmed exchange rate
   when needed. Successful confirmation leaves the transaction approved, not
   posted. A validation preview alone does not save approval.
5. In **Review**, inspect **Post approved transactions** and choose **Post
   ready**. Check every row in the **Post ready transactions** confirmation
   before confirming **Post ready**. This action covers the ready rows shown
   in that preview, not necessarily just the entry you last opened. If the
   list exceeds your intended scope, do not confirm it; ask an operator to
   post only the authorized transaction through the supported single-entry
   operation.
6. Check the posting result and reload the entry. Its transaction status should
   be `posted`. Calculations can refresh separately: if the app reports that
   transactions were posted but calculations are stale, use **Retry calculation
   refresh**. Do not repeat posting or create another entry to refresh totals.

The server decides posting readiness from the current record. A closed period,
unresolved blocking issue, unreviewed tax treatment, missing required currency
conversion or document that is not ready can prevent posting. Future-dated
transactions cannot be posted before their transaction date. A disabled button
is a reason to inspect the explanation, not to bypass the checks.

## What the statuses mean

This table describes transaction statuses for ordinary current accounting.
Documents have their own statuses: a linked document remaining `approved`
while its transaction is `posted` is normal.

| Status | Meaning | Included in actual calculations? | Next action and owner |
|---|---|---|---|
| `received` | The entry has been recorded for processing. | No. | The service attempts extraction; an operator investigates if processing cannot continue. |
| `extracted` | Candidate data has been extracted. | No. | The user or authorized operator checks the facts and decision. |
| `needs_review` | Confirmation or issue resolution is still needed. | No. | The user or authorized operator completes the supported review or identifies the exact missing fact. |
| `approved` | The review decision is saved; posting has not happened. | No, in the ordinary actuals path. It may appear in a clearly labeled preview or forecast. | The user or operator checks posting readiness and explicitly posts the intended rows. If deferred or blocked, follow the reason. |
| `posted` | The transaction has passed the posting checks and is recorded as posted. | Eligible, subject to the reviewed treatment and report rules. | Check the result and calculation freshness. Use the correction workflow if a saved fact is wrong. |
| `included_in_snapshot` | The transaction is linked to a saved accounting calculation snapshot. | Eligible; saved snapshots retain their historical values. | Inspect the saved result. The operator handles any required correction or amendment without rewriting the snapshot. |
| `duplicate`, `rejected`, `void` | The entry is excluded from the active accounting set. | No. | The operator explains the exclusion and identifies the valid entry or correction, if needed. Do not recreate it blindly. |

Eligibility is not a promise that an amount appears in every form or on every
chart. Period/date filters, form-inclusion flags, deductions and other reviewed
fields still apply. Historical reconciliation and explicit preview modes can
include approved rows under separate rules; that does not post them. See the
[analytics status policy](../DESIGN.md#lifecycle-status-policy) for chart details.

Snapshot inclusion is not an AEAT submission receipt. Filing and its evidence
are separate from recording an entry or saving a calculation snapshot.

## Dates, source documents and missing details

Keep these facts separate:

- **Document issue date:** when the current document was issued or composed.
- **Service period:** when the work or supply covered by the document occurred.
- **Payment date:** when money was received or paid, supported by payment
  evidence or an explicitly recorded confirmation. Confirmation is not bank
  reconciliation.

The app also has **Transaction date** and a booking date. Do not assume those
fields universally mean the payment date or change them to force a desired
period. Their recorded accounting meaning must be checked for the operation.
A sent-email date alone does not prove a document's issue date.

Names, addresses and identifiers may be supplemented from verified earlier
documents for the same party, after checking that they remain applicable.
Record which source supports each addition and any explicit confirmation.
Prefer the relevant current evidence over an outdated address. Conflicting
identities require resolution, not an automatic merge.

Do not carry forward another document's amount, number, issue date, service
period, payment date, contract reference or tax classification. An earlier
treatment can inform a review, but it does not approve the current transaction.

The original file remains evidence. Correcting the accounting record does not
edit that file, reissue an invoice or automatically establish fiscal-document
validity. Record any discrepancy and the source of the correction. Do not
request a second invoice merely because the file has an unexpected title;
identify the specific missing fact or document requirement and explain why it
is needed. If a corrected or additional source document is genuinely required,
keep its relationship to the original explicit.

For supported name corrections, see [Correcting a counterparty name](COUNTERPARTY_NAMES.md).
Other metadata is not necessarily editable through that action.

## When the workflow stops

| Situation | What it means | What happens next |
|---|---|---|
| OCR is unavailable or unreadable. | Extraction failed; this is not a tax-authority check. | The operator checks the service's [OCR dependency](../ops/PROVISIONING.md#local-ocr-dependency). A fresh extraction or documented manual review must support closing the exact old issue. Installation alone does not close it. |
| A fact or accounting decision is missing. | The entry still needs internal confirmation. | The operator first checks the original, applicable earlier sources and confirmations already supplied. Ask the user only for the unresolved fact, with an explanation of its effect. Do not invent a value. |
| The transaction is approved but absent from posted totals. | Approval is saved, but posting has not happened. | The user or operator checks the posting preview, addresses any blocker and posts only the intended rows. |
| The issue date is wrong and cannot be edited. | The current guided review shows the issue date as a fact, not an editable decision. | An operator checks the installed capabilities and, if necessary, arranges [scoped maintenance](../ops/README.md#scoped-accounting-maintenance) before approval/posting. The user should not re-upload the original or supply an administrator password for routine review. |
| A save or posting response is lost. | The server may already have saved some or all of the work. | Reload first. The operator compares the actual status, current versions and saved decision before retrying only unfinished work. Keep intervening changes. |
| Posting succeeded but refresh or cleanup failed. | The accounting write may be complete even though follow-up work is not. | Check the saved transaction. Retry calculation refresh or ask the operator to handle the reported cleanup problem; do not treat this as an unposted entry. |
| A posted entry needs correction. | Posting is not a license to reopen and overwrite finalized data. | Ask the operator for the supported correction procedure, respecting period and snapshot restrictions. The read-only expense card is not an editor. |

An operator's update should say what is already saved, what remains and who
will do it. For example: "The details are saved. The entry still needs internal
confirmation of its accounting treatment; the operator will review it next."
"Posted in the application; nothing submitted to AEAT" describes a different,
completed step. Neither statement implies approval by a tax authority.
