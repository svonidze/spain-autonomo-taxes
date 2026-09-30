# Checking a customer's EU VAT number in VIES

An intra-Community B2B invoice without Spanish VAT (reverse charge, reported in
Modelo 349) depends on the customer's valid EU VAT number. Modelo 349 already
blocks when the VAT ID is missing. The VIES check adds dated evidence that the
number was valid when you relied on it.

## When to run it

- Before the first invoice to a new EU business customer.
- When the customer's VAT ID or country changes.
- Periodically for recurring customers, close to the invoice date. A result
  proves validity only at the moment of the check.

The application never contacts VIES automatically.

## What is sent

The command sends a request to the European Commission VIES service only when you
pass `--confirm-network-to-vies`. The request contains the counterparty's country code
and VAT number, taken from its recorded `vat_id` and `country_code`. Spaces,
punctuation and the country prefix are removed first. VIES uses `EL` for Greece.
Only EU member states are accepted.

Record VAT numbers with their country prefix, for example `FR` followed by the
11 characters. A French key can consist of letters, so a French number without a
prefix could start like another member state's code; such a number is rejected
rather than checked in the wrong country.

`XI` numbers are not accepted. They cover goods under the Windsor Framework;
services to a Northern Ireland customer are UK supplies, so record the customer's
GB VAT number instead.

With `--requester-vat ES...`, your own VAT number is also sent. Only then does VIES
return a consultation reference, `requestIdentifier`, which identifies the check,
so using it is recommended. As with any web request, VIES also sees this machine's
IP address and the client User-Agent (`autonomo-tax/0.1`). Nothing else is sent.

```bash
autonomo-tax counterparties vies-check \
  --db /private/autonomo.sqlite \
  --counterparty-id <synthetic-counterparty-id> \
  --confirm-network-to-vies \
  [--requester-vat ES<your-nif>] \
  [--apply]

autonomo-tax counterparties vies-list --db /private/autonomo.sqlite \
  [--counterparty-id <synthetic-counterparty-id>]
```

## Results and `roi_status`

| Outcome | Meaning | With `--apply` |
| --- | --- | --- |
| `valid` | VIES confirms that the number is valid | `roi_status` becomes `registered` |
| `invalid` | VIES reports that the number is not valid | `roi_status` becomes `not_registered` |
| `unavailable` | VIES or the member-state service is down, busy or blocked, or the response was unexpected | unchanged; try again later |
| `invalid_input` | VIES rejected the number or the requester VAT as malformed | unchanged; correct the recorded number |

Without `--apply`, only the evidence is recorded. `unavailable` and `invalid_input`
never mean that the customer is unregistered. When the status is applied, the
counterparty's `row_version` increases and its `source_hash` becomes the SHA-256 of
the VIES response. The check records the applied status and the resulting version.
If the counterparty changed during the request, the evidence is kept and the status
remains unchanged.

The command exits with 0 when VIES answered `valid` or `invalid` and any requested
application succeeded; otherwise it exits with 2. The output field `sent` is `false`
when the request never left this machine, for example after a DNS, TLS or connection
failure; such an attempt is still recorded as `unavailable`. If the counterparty
changed during the request, the output also contains `reason: counterparty_changed`.

## Evidence

Each check is one append-only row in the `vies_checks` table (schema 26). The row
cannot be updated or deleted. It contains the time, country, normalised number,
outcome, VIES error code, VIES `requestDate` and `requestIdentifier`, whether a
requester VAT was used, the HTTP status and the raw response with its SHA-256.
The response is kept as text when it is valid UTF-8 and otherwise as base64, as
shown by `response_encoding`, so the SHA-256 can be recomputed from the stored
bytes.
The name and address are stored as VIES returned them; `---` means that the member
state does not disclose them. Compare them with the invoice yourself.

The name and address of a sole trader are personal data. Keep the database private.

A later manual review, for example from the spreadsheet, can change `roi_status`
again. The earlier checks remain in the history.
