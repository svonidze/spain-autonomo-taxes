import {test, expect} from '@playwright/test';
const ID = '11111111-1111-4111-8111-111111111111';
const OTHER = '22222222-2222-4222-8222-222222222222';
function row(id = ID, kind = 'expense', label = 'Synthetic entry') {
  return {transaction_id: id, entry_type: kind, expense_kind: 'purchase', lifecycle_status: 'posted', document_status: 'recorded', transaction_date: '2026-07-02', period_key: '2026-Q3', description: label, document_number: 'SYN-123', counterparty_name: label, amount_eur: '90.00', amount_original: '100.00', currency: 'USD', deductible_irpf_eur: '90.00', deductible_vat_eur: '0.00', ui_context: {domain: 'transaction', state: 'posted', subject_id: id, reasons: []}};
}
function expensePage(revision: string, rows: ReturnType<typeof row>[], more = false, offset = rows.length) {
  return {as_of: '2026-09-05', view_revision: revision, rows, has_more: more, next_offset: offset, matching_counts: {purchase: 2, amortization: 0}, period_counts: {purchase: 2, amortization: 0}, summary: {purchase: {reviewed_total: {amount_eur: '180.00', missing_amount_count: 0}}, amortization: {reviewed_total: {amount_eur: '0.00', missing_amount_count: 0}}}};
}
test('income search ignores old replies and keeps current records on a failed retry', async ({page}) => {
  let release!: () => void, oldStarted = false;
  const gate = new Promise<void>(resolve => {release = resolve;});
  await page.route(/\/api\/transactions\?/, async route => {
    const q = new URL(route.request().url()).searchParams.get('q');
    if (q === 'old') {oldStarted = true; await gate; return route.fulfill({json: [row(ID, 'income', 'Old reply')]});}
    if (q === 'fail') return route.fulfill({status: 503, json: {error: 'Synthetic search unavailable'}});
    return route.fulfill({json: [row(ID, 'income', q === 'new' ? 'New reply' : 'Initial reply')]});
  });
  await page.goto('/income?period=2026-Q3');
  await expect(page.locator('#transactions-table')).toContainText('Initial reply');
  await page.locator('#transaction-search').fill('old'); await expect.poll(() => oldStarted).toBe(true);
  await page.locator('#transaction-search').fill('new'); await expect(page.locator('#transactions-table')).toContainText('New reply');
  release();
  await page.locator('#transaction-search').fill('fail');
  await expect(page.locator('[data-vue-owned] [role="alert"]')).toContainText('Synthetic search unavailable');
  await expect(page.locator('#transactions-table')).toContainText('New reply');
  await expect(page.locator('#transactions-table')).not.toContainText('Old reply');
});
test('income copy preserves original values and cancellation leaves an untouched saved draft', async ({page}) => {
  const source = row(ID, 'income', 'Synthetic copy'); source.amount_original = '0.00';
  await page.route(/\/api\/transactions\?/, route => route.fulfill({json: [source]}));
  await page.goto('/income?period=2026-Q3');
  await page.evaluate(()=>localStorage.setItem('autonomo.intake-draft',JSON.stringify({schema:1,values:{document_number:'SYN-OLDER'}})));
  await page.locator('[data-copy-transaction-id]').click();
  await expect(page.locator('#vue-intake-dialog')).toBeVisible();
  await expect(page.locator('#vue-intake-form [name="currency"]')).toHaveValue('USD');
  await expect(page.locator('#vue-intake-form [name="gross"]')).toHaveValue('0.00');
  await expect(page.locator('#vue-intake-form [name="document_number"]')).toBeFocused();
  await page.locator('#vue-close-dialog').click();
  await page.locator('#new-entry-button').click();await expect(page.locator('#vue-intake-form [name="document_number"]')).toHaveValue('SYN-OLDER');await page.locator('#vue-close-dialog').click();
  await page.locator('[data-copy-transaction-id]').click();
  await page.locator('#vue-intake-form [name="document_number"]').fill('Synthetic new invoice');
  await page.locator('[data-locale="en"]').evaluate((button: HTMLElement) => button.click());
  await expect(page.locator('#vue-intake-form [name="document_number"]')).toHaveValue('Synthetic new invoice');
  await expect(page.locator('#vue-intake-form [name="gross"]')).toHaveValue('0.00');
  await page.locator('#vue-close-dialog').click();await page.locator('#new-entry-button').click();await expect(page.locator('#vue-intake-form [name="document_number"]')).toHaveValue('Synthetic new invoice');
});
test('expense paging restarts a changed revision and search preserves the detail return query', async ({page}) => {
  let changed = false; const offsets: number[] = [];
  await page.route(/\/api\/expenses\?/, route => {
    const params = new URL(route.request().url()).searchParams, offset = Number(params.get('offset'));
    offsets.push(offset); if (offset === 1) changed = true;
    const name = `${changed ? 'New' : 'Old'} ${offset}`;
    return route.fulfill({json: expensePage(changed ? '2' : '1', [row(offset ? OTHER : ID, 'expense', name)], offset === 0, offset + 1)});
  });
  await page.goto('/expenses?period=2026-Q3');
  await page.locator('#expense-more').click();
  await expect(page.locator('#expense-results tbody tr')).toHaveCount(2);
  await expect(page.locator('#expense-results')).toContainText('New 0');
  expect(offsets).toEqual([0,1,0,1]);
  await page.locator('#expense-search').fill('Synthetic query');
  await expect(page).toHaveURL(/q=Synthetic\+query/);
  await expect(page.locator('#expense-results tbody tr')).toHaveCount(1);
  await expect(page.locator('.expense-document-link').first()).toHaveAttribute('href', /returnTo=.*Synthetic/);
  await page.locator('[data-locale="en"]').click();
  await expect(page.locator('#expense-search')).toHaveValue('Synthetic query');
});
test('expense refresh retains expanded rows, defers during help, and disposes polling', async ({page}) => {
  await page.clock.install();
  let requests = 0;
  await page.route(/\/api\/expenses\?/, route => {
    requests++; const offset = Number(new URL(route.request().url()).searchParams.get('offset'));
    return route.fulfill({json: expensePage('1', [row(offset ? OTHER : ID)], offset === 0, offset + 1)});
  });
  await page.goto('/expenses?period=2026-Q3');
  await page.locator('#expense-more').click(); await expect(page.locator('#expense-results tbody tr')).toHaveCount(2);
  const initial = requests;
  await page.locator('#expense-results [data-status-help]').first().click();
  await page.clock.fastForward(60000); expect(requests).toBe(initial);
  await page.goBack(); await expect(page.locator('#status-help-dialog')).not.toBeVisible();
  await page.clock.fastForward(60000); await expect.poll(() => requests).toBe(initial + 2);
  await expect(page.locator('#expense-results tbody tr')).toHaveCount(2);
  await page.locator('a[data-view="income"]').click(); const before = requests;
  await page.clock.fastForward(60000); expect(requests).toBe(before);
});
