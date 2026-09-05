import {expect, test} from 'vitest';
import {createExpensePager} from '../src/features/transactions/expense-pager.ts';
import {buildIncomeCopy} from '../src/features/transactions/income-copy.ts';
import {copyable, type TransactionRow, type ExpensePage} from '../src/features/transactions/model.ts';
const row = (id: string): TransactionRow => ({transaction_id: id, entry_type: 'expense', lifecycle_status: 'posted', period_key: '2026-Q3', transaction_date: '2026-07-01', ui_context: {domain: 'transaction', state: 'posted'}});
const page = (revision: string, rows: TransactionRow[], next: number, more = false): ExpensePage => ({as_of: '2026-07-01', view_revision: revision, rows, next_offset: next, has_more: more, matching_counts: {purchase: 2, amortization: 0}, period_counts: {purchase: 2, amortization: 0}, summary: {purchase: {reviewed_total: {amount_eur: 2, missing_amount_count: 0}}, amortization: {reviewed_total: {amount_eur: 0, missing_amount_count: 0}}}});
test('expense paging restarts a changed revision and preserves the expanded count on refresh', async () => {
  const offsets: number[] = []; let revision = 'old';
  const pager = createExpensePager(async ({offset}) => {
    offsets.push(offset);
    if (offset === 1) revision = 'new';
    return page(revision, [row(`${revision}-${offset}`)], offset + 1, offset === 0);
  });
  await pager.load(''); const expanded = await pager.load('', true);
  expect(offsets).toEqual([0,1,0,1]);
  expect(expanded?.rows.map(row => row.transaction_id)).toEqual(['new-0','new-1']);
  const refreshed = await pager.refresh('');
  expect(refreshed?.rows).toHaveLength(2); expect(offsets.slice(-2)).toEqual([0,1]);
});
test('obsolete search and failed pagination cannot corrupt current rows', async () => {
  let release!: (page: ExpensePage) => void;
  const delayed = new Promise<ExpensePage>(resolve => {release = resolve;});
  const pager = createExpensePager(async ({query, offset}) => query === 'old' ? delayed : offset ? page('new', [], offset, true) : page('new', [row('new')], 1, true));
  const old = pager.load('old');
  const latest = await pager.load('new'); release(page('old', [row('old')], 1));
  expect(await old).toBeNull(); expect(latest?.rows[0]?.transaction_id).toBe('new');
  await expect(pager.load('new', true)).rejects.toThrow('expense.invalidPage');
});
test('copy intent preserves original zero/currency and explicit target-date rules', () => {
  const source = {...row('source'), entry_type: 'income', amount_original: '0.00', amount_eur: 400, currency: 'USD', counterparty_name: ' Synthetic ', document_number: ' SYN-1 '};
  const context = {sourcePeriodKey: '2026-Q2', targetPeriodKey: '2026-Q3', targetIsCurrentQuarter: true, currencyOptions: ['EUR','USD'], today: new Date(2026, 6, 5)};
  expect(buildIncomeCopy(source, context).prefill).toEqual({issued_on: '2026-07-05', counterparty_name: 'Synthetic', document_number: 'SYN-1', gross: '0.00', currency: 'USD'});
  expect(buildIncomeCopy(source, {...context, targetIsCurrentQuarter: false}).prefill.issued_on).toBe('');
  const unsupported = buildIncomeCopy({...source, currency: 'ZZZ', amount_original: '-1'}, context);
  expect(unsupported.prefill.currency).toBe(''); expect(unsupported.prefill.gross).toBe('');
  expect(unsupported.noticeLines.map(line => line.key)).toEqual(['intake.copyNotice','intake.copyUnsupportedCurrency','intake.copyInvalidAmount']);
  expect(copyable(source, '2026-Q3')).toBe(true);
  for (const status of ['duplicate','rejected','void']) expect(copyable({...source, document_status: status}, '2026-Q3')).toBe(false);
});
