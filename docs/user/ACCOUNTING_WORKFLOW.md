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

## Expenses: document to posting in one screen

These actions require the expense workflow release (schema 22 or later).
Merging code does not update an installed server. Deployment is a separate
administrative operation; it never appears in the expense form.

1. Open **Expenses → New entry**, choose the period and upload the original.
   Extraction supplies candidates, not confirmed accounting facts. An existing
   document should be reopened rather than uploaded again to correct its data.
2. The expense review opens beside the original. Check or edit the number,
   issue date, transaction date, booking date, currency and gross amount. Select
   the business activity and enter the business purpose and decision basis.
3. Search the supplier by name or tax identifier. Select the existing card when
   its identifier matches. **New supplier** creates a card only at confirmation;
   a collision with any existing identifier blocks creation. A shop's name does
   not rename a shared supplier. Use the separate contact correction action if
   a verified shared name correction is needed.
4. Choose **Ordinary expense** or **Equipment**. Confirm the tax classification,
   base, IVA quota and deduction, and the separate IVA current/investment choice.
   Ordinary expenses also show the current IRPF deduction. Foreign-currency
   amounts need a confirmed exchange rate and reconciled EUR tax amounts.
5. **Save draft** saves on the server so you can close the browser and resume.
   Unsaved fields are not durable. A saved change to an approved but unposted
   entry invalidates approval and requires a reason, retained in audit history.
   If another session changed the source, reload and review it explicitly.
6. **Preview result** validates the current data without posting. Check the exact
   purchase, IVA deduction, IRPF deduction now and future depreciation. The
   tax amounts must reconcile to the original's converted gross. Mixed-rate or
   composite invoices need a separate workflow; do not force them into one row.
7. **Confirm and post** posts only this expense and the depreciation explicitly
   shown as due. It neither pays an invoice nor submits a return. Repeated
   requests use the saved action result and cannot create another acquisition.
8. Check **Posted**. If cleanup or calculation refresh failed, **Retry follow-up**
   runs only those steps. The warning survives reopening the posted expense.
   Never re-create or re-post the expense to repair a refresh failure.

A readable original can be checked manually after extraction fails: confirm
that its facts were inspected and explain the manual review. This closes only
supported structural/classification issues belonging to this document. Missing
or altered originals, closed/future periods, unresolved unrelated blockers and
unreviewed tax/FX decisions remain blocking. The application cannot make an
incomplete period ready by silently resolving its other issues.

### Equipment and depreciation

One new asset is supported per expense. Confirm its description/category,
service date, amortizable basis before business share, and business-use share.
The acquisition itself has zero current IRPF deduction; depreciation creates
separate internal accounting entries linked directly to schedule rows.

- **New low-value equipment:** full depreciation requires an object costing at
  most EUR 300 before business share and an available annual EUR 25,000 limit.
  The application conservatively counts recorded low-value assets in the same
  service year and requires a direct-estimation activity. This does not prove
  that unrecorded assets are absent. Confirm a complete register and the
  decision's basis; short tax years or other special regimes need separate
  review. See [AEAT's depreciation guidance](https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-ayuda-presentacion/irpf-2025/7-cumplimentacion-irpf/7_4-rendimientos-actividades-economicas/7_4_2-regimen-estimacion-directa/7_4_2_3-gastos-fiscalmente-deducibles/dotaciones-amotizacion.html).
- **Linear schedule:** enter the reviewed annual rate (the app does not choose
  the legally appropriate rate). The calculation applies business share once,
  accrues by actual calendar days using 365/366 days, rounds cumulative totals
  at quarter ends and caps the last installment at the remaining basis.

Immediate depreciation becomes due on the service date. Linear installments
become due at quarter end. Already-due rows are shown in the purchase preview;
future rows remain a plan. In **Assets → Schedule and recognized depreciation**,
choose the asset and explicitly **Post** the eligible quarter. Closed or future
rows cannot be posted, and an already recognized row cannot create a duplicate.

New schedules retain their calculation version and reviewed parameters. Annual
actuals come from linked posted journals, not the plan and not a fabricated
external `annual_evidence` document. Imported schedules and historical amounts
are not recalculated. Only unambiguous legacy service links are migrated.
Existing acquisitions already linked to an asset continue through their legacy
review rather than creating a second asset in this wizard.

### Income and legacy review

The existing **Confirm review** API and legacy review form still save approval
without posting. Their **Post ready** action is a separate batch action: inspect
all listed rows before confirming it. It may include more than the last opened
entry. Routine new expense posting should use the scoped wizard above.

For a foreign-currency income entry, check the proposed rate, observation date,
converted EUR amount and provenance before confirming the review. See
[Foreign-currency exchange rates](FX_RATES.md) for the ECB quote convention,
weekend fallback, rounding, settlement alternative and failure boundaries.

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
[analytics status policy](../../DESIGN.md#lifecycle-status-policy) for chart details.

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
| OCR is unavailable or unreadable. | Extraction failed; this is not a tax-authority check. | Use the documented manual-original check in the expense wizard when the original is readable. Missing/unreadable files remain blockers; an operator can investigate the [OCR dependency](../../ops/docs/PROVISIONING.md#local-ocr-dependency). |
| A fact or accounting decision is missing. | The entry still needs internal confirmation. | The operator first checks the original, applicable earlier sources and confirmations already supplied. Ask the user only for the unresolved fact, with an explanation of its effect. Do not invent a value. |
| The transaction is approved but absent from posted totals. | Approval is saved, but posting has not happened. | The user or operator checks the posting preview, addresses any blocker and posts only the intended rows. |
| An unposted expense has incorrect data. | Its server draft can be edited before posting. | Correct the facts beside the original and record the reason; an earlier approval is invalidated. Posted data requires the separate correction procedure. |
| A save or posting response is lost. | The server may already have saved some or all of the work. | Reload first. The operator compares the actual status, current versions and saved decision before retrying only unfinished work. Keep intervening changes. |
| Posting succeeded but refresh or cleanup failed. | The accounting write may be complete even though follow-up work is not. | Check the saved transaction. Use **Retry follow-up** on the posted expense; ask an operator about a persistent cleanup problem. Do not treat this as an unposted entry. |
| A posted entry needs correction. | Posting is not a license to reopen and overwrite finalized data. | Ask the operator for the supported correction procedure, respecting period and snapshot restrictions. The read-only expense card is not an editor. |

An operator's update should say what is already saved, what remains and who
will do it. For example: "The details are saved. The entry still needs internal
confirmation of its accounting treatment; the operator will review it next."
"Posted in the application; nothing submitted to AEAT" describes a different,
completed step. Neither statement implies approval by a tax authority.

## IRPF assets and IVA investment goods

Expense review requires a separate **IVA purchase classification** choice.
`vat_investment_good=false` means a current purchase for IVA; `true` means an
IVA investment good. Neither choice establishes business use or the deductible
percentage. Confirm those facts and amounts independently.

An asset amortized for IRPF need not be an IVA investment good. In particular,
the IVA definition excludes goods valued below EUR 3,005.06. See
[AEAT's investment goods definition](https://sede.agenciatributaria.gob.es/Sede/ayuda/manuales-videos-folletos/manuales-practicos/manual-iva-2025/capitulo-05-deducciones-devoluciones/deducciones/regularizacion-deducciones-bienes-inversion/concepto-bienes-inversion.html).
Do not remove the IRPF asset link to change IVA boxes, or deduct an acquisition
again as a current IRPF expense when its cost is recognized through amortization.

New expense approvals require an explicit boolean in the review packet. Empty
legacy values stay null: existing calculations retain their previous asset-link
classification and report an unreviewed-classification warning. This fallback
is compatibility behavior, not tax approval. The warning is carried into the
annual IVA summary and AEAT book projection. Migration does not reclassify old
records or rewrite saved snapshots; stale packets must be prepared again.

Reviewed classification controls the current/investment input categories in
Modelo 303 and the corresponding Modelo 390 totals, plus `bien_inversion` in
the expense book. The IRPF asset and its amortization remain in the unified
asset register independently. The read-only expense card shows the saved choice
or that the IVA classification has not been reviewed.

## Modelo 347 and clients established abroad

The annual third-party report lists every person or entity with whom operations
exceeded 3,005.06 EUR in the calendar year (art. 33.1 RD 1065/2007). The client's
country does not exclude the relationship: the exclusions in art. 33.2 are specific
(operations for which no invoice had to be issued, goods imports and exports, and a
few others), and none of them covers services invoiced to a customer established
outside Spain or outside the EU. The Direccion General de Tributos confirmed this in
binding ruling V0516-19 (12 March 2019): a self-employed designer working for an
organisation established in Switzerland had to include those services in Modelo 347
once they exceeded the threshold.

Advisers frequently assume the opposite for non-EU clients. The obligation engine in
this toolkit therefore keeps foreign-established counterparties reportable and only
excludes intra-Community operators (reported in Modelo 349) and payments already
reported through withholding returns.

## Issuing your own invoices and the Verifactu timeline

Outgoing invoice drafts in this toolkit are a numbering and review aid. They are
not a certified invoicing system (SIF) under the Verifactu regulation, and the
toolkit does not send invoice records to the AEAT.

Current timeline for the Verifactu obligation (Real Decreto 1007/2023 as amended by
Real Decreto-ley 15/2025 of 2 December, BOE 3 December 2025):

- taxpayers subject to Impuesto sobre Sociedades: systems adapted by 1 January 2027;
- all other taxpayers, including autonomos under IRPF: by 1 July 2027;
- software vendors: since 29 July 2025.

The postponement changed the dates only; the technical requirements (record hash
chain, unalterability, QR code, optional submission to the AEAT) are unchanged. See
the AEAT notice on the extension:
https://sede.agenciatributaria.gob.es/Sede/iva/sistemas-informaticos-facturacion-verifactu/nota-informativa-ampliacion-plazo-adaptacion-facturacion.html

Whether a spreadsheet or template used to produce invoices counts as a SIF is a
question for the AEAT FAQ and your adviser; plan the answer well before July 2027.
Suppliers already issuing Verifactu invoices print a "QR tributario" and the text
"Factura verificable en la sede electronica de la AEAT" on them — such invoices
still import like any other document.
