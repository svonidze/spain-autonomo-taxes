# AEAT documents and ROI registration

The **AEAT documents** page keeps official submission receipts, requests,
response receipts, decisions and certificates separate from invoices and
accounting transactions. A saved receipt proves what was submitted; it does
not prove that AEAT approved the request.

## Requesting an intra-Community VAT number

Use the full electronic **Modelo 036** form while signed in to AEAT as the
taxpayer or an authorised representative.

1. Confirm that there is a genuine intended intra-Community business operation.
   A customer is not the only possible basis: receiving a taxable business
   service from a supplier in another EU member state may also require ROI.
2. On page 1 select `casilla 130`, solicitud de alta/baja en el Registro de
   operadores intracomunitarios. Do not select unrelated census changes.
3. On page 5 select `casilla 582`, Alta, and enter the honest expected date of
   the next qualifying operation in `casilla 584`.
4. Complete the place and signature as the interested taxpayer or the actual
   representative. Validate the declaration and inspect the generated preview.
5. Check that the preview contains the intended NIF and only the requested ROI
   change. The person responsible for the filing must explicitly confirm the
   final **Firmar y Enviar** action.
6. Keep the success PDF. It should contain the submission timestamp,
   `Expediente/Referencia`, `Número de justificante` and `Código Seguro de
   Verificación` (CSV).

Never invent a counterparty or backdate `casilla 584`. If the qualifying
operation already occurred, record that fact separately and obtain appropriate
tax advice instead of altering the receipt.

## Saving the receipt

Open **AEAT documents**, expand **Add AEAT document**, select the official PDF
and enter the procedure facts. Use **Check metadata** first. The server reads
the PDF and rejects a manually entered form, timestamp, reference, justificante
or CSV that conflicts with the file. Save only after the preview matches.

For an ROI request use:

- procedure `ROI` and procedure code `G322`;
- Modelo `036`;
- document type `Submission receipt`;
- status `Submitted, awaiting decision`;
- the expected operation date from `casilla 584`.

The original PDF is archived by SHA-256. Uploading the identical file again is
idempotent and does not create a second case.

## Recording the outcome

Use **Add status** on the saved case. Every status change needs a dated evidence
reference, for example an AEAT decision or a documented manual VIES check.
Status history is append-only.

`submitted` means only that AEAT accepted the declaration for processing. Use
the Spanish VAT number for intra-Community treatment only after the authoritative
VIES check reports it as valid. The application does not poll VIES automatically.
