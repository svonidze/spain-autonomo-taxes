import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {expect, test} from 'vitest';
import {legacyCore} from '../src/core/legacy.ts';
import {createFinancePresentation} from '../src/features/overview/presentation.ts';
import {operationRequest} from '../src/core/operation-request.ts';
const statuses = ['not_started','undetermined','no_payment_required','paid','partially_paid','overpaid','planned','evidence_unavailable','unfiled','payment_unconfirmed'];
for (const locale of ['ru','en'] as const) test(`tax status and evidence presentation match the legacy oracle in ${locale}`, () => {
  const context = vm.createContext({AutonomoCore: legacyCore, Intl, Date, setTimeout, clearTimeout, URLSearchParams, console});
  vm.runInContext(readFileSync('src/autonomo_taxes/web_ui/app.js','utf8'), context);
  context.locale = locale; vm.runInContext('state.locale = locale', context);
  const presentation = createFinancePresentation(locale);
  for (const status of statuses) for (const phase of ['past','current','future']) for (const amount of [null, 0, 12345]) {
    const summary = {settlement_status: status, total_payable_minor: amount, total_confirmed_paid_minor: 10000, outstanding_minor: 2345, overpaid_minor: 0, calculated_as_of: '2026-07-01', calculation_source: 'filed'};
    context.summary = summary; context.periodState = {phase};
    const actual = presentation.taxHeadlineModel({phase}, summary);
    expect(actual).toEqual(JSON.parse(JSON.stringify(vm.runInContext('taxHeadlineModel(periodState, summary)', context))));
  }
  for (const state of ['filed','filed_without_values','preview','snapshot_only','unavailable']) for (const value of [null, 0, 12.34]) {
    const form = {form_code: '130', display_state: state, values: {}, headline_value: value, headline_detail: 0, filed_on: '2026-07-01', preview_as_of: '2026-07-02'};
    const obligation = {obligation_code: '130', determination: 'due', filing_status: 'filed'};
    context.form = form; context.obligation = obligation;
    expect(presentation.formCardData(form, obligation, {warnOnMissingHeadline: true})).toEqual(JSON.parse(JSON.stringify(vm.runInContext('formCardData(form, obligation, {warnOnMissingHeadline: true})', context))));
    expect(presentation.formSubtitle(form, obligation)).toBe(vm.runInContext('formSubtitle(form, obligation)', context));
  }
});
test('depreciation retries keep their request ID without storage and rotate on a new version', () => {
  let generated = 0;
  const denied = () => {throw new Error('storage disabled');};
  const uuid = () => `synthetic-request-${++generated}`;
  const first = operationRequest('synthetic-depreciation-key', 'depreciation', 1, denied, uuid);
  expect(operationRequest('synthetic-depreciation-key', 'depreciation', 1, denied, uuid)).toBe(first);
  expect(operationRequest('synthetic-depreciation-key', 'depreciation', 2, denied, uuid).request_id).not.toBe(first.request_id);
  expect(generated).toBe(2);
});
