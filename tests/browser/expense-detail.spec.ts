import {test, expect, type Page} from '@playwright/test';
const ID = '11111111-1111-4111-8111-111111111111';
const DETAIL = `/expenses/${ID}?period=2026-Q3&returnTo=%2Fexpenses%3Fperiod%3D2026-Q3%26q%3DSynthetic`;
function fixture(pending = false) {
  return {transaction: {transaction_id: ID, entry_type: 'expense', lifecycle_status: 'posted', currency: 'USD', original_currency: 'USD', amount_original_minor: 12345, amount_eur_minor: 11000, transaction_date: '2026-04-01', description: 'Synthetic <source>'},
    period: {period_key: '2026-Q2'}, document: {document_id: '22222222-2222-4222-8222-222222222222', document_number: 'SYN-EXPENSE'},
    workflow_follow_up: {follow_up_pending: pending}, tax_treatments: [
      {treatment_type: 'expense', jurisdiction: 'ES', notes: '<script>synthetic escaped note</script>\nsecond line', deductible_irpf_minor: 0, deductible_vat_minor: null, include_modelo130: true},
      {treatment_type: 'adjustment', jurisdiction: 'ES', notes: 'Second treatment note', deductible_irpf_minor: -1000},
    ]};
}
async function locale(page: Page) {await page.locator('[data-locale="en"]').click();}
test('Vue expense uses authoritative period, preserves notes and values, and returns to list query', async ({page}) => {
  let reads = 0;
  await page.route(`**/api/transactions/${ID}`, route => {reads++; return route.fulfill({json: fixture()});});
  await page.goto(DETAIL);
  await expect(page.locator('[data-vue-owned] h2').first()).toHaveText('SYN-EXPENSE');
  await expect(page).toHaveURL(/period=2026-Q2/);
  await expect(page.locator('.expense-note-text')).toHaveText(['<script>synthetic escaped note</script>\nsecond line', 'Second treatment note']);
  await expect(page.locator('.expense-detail script')).toHaveCount(0);
  await locale(page);
  await expect(page.locator('.expense-detail')).toContainText('123.45 USD');
  await expect(page.locator('.expense-detail')).toContainText('€110.00');
  expect(reads).toBe(1);
  await expect(page.locator('.expense-detail a[target="_blank"]')).toHaveAttribute('rel', 'noreferrer');
  await page.locator('.review-workspace-header > a').click();
  await expect(page).toHaveURL(/\/expenses\?period=2026-Q3&q=Synthetic$/);
  await expect(page.locator('[data-vue-owned]')).toHaveCount(0);
  await page.goBack();
  await expect(page.locator('[data-vue-owned] h2').first()).toHaveText('SYN-EXPENSE');
});
test('follow-up POST remains single and busy across locale changes, then reloads detail', async ({page}) => {
  let posted = false, writes = 0, reads = 0;
  let release!: () => void;
  const gate = new Promise<void>(resolve => {release = resolve;});
  await page.route(`**/api/transactions/${ID}`, route => {reads++; return route.fulfill({json: fixture(!posted)});});
  await page.route(`**/api/expense-workflows/${ID}/follow-up`, async route => {writes++; await gate; posted = true; await route.fulfill({json: {status: 'ok'}});});
  await page.goto(DETAIL);
  await page.locator('#expense-follow-up').click();
  await locale(page);
  await expect(page.locator('#expense-follow-up')).toBeDisabled();
  await expect(page.locator('#expense-follow-up')).toHaveText('Retry follow-up');
  expect(writes).toBe(1); expect(reads).toBe(1);
  release();
  await expect(page.locator('#expense-follow-up')).toHaveCount(0);
  expect(reads).toBe(2);
});
test('a late detail response cannot replace a new route', async ({page}) => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => {release = resolve;});
  await page.route(`**/api/transactions/${ID}`, async route => {await gate; await route.fulfill({json: fixture()});});
  await page.goto(DETAIL);
  await expect(page.locator('[data-vue-owned]')).toHaveCount(1);
  await page.locator('a[data-view="income"]').click();
  release();
  await expect(page).toHaveURL(/\/income/);
  await expect(page.locator('[data-vue-owned]')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('SYN-EXPENSE');
});
test('follow-up failure is retryable and a late success cannot change another route', async ({page}) => {
  let writes = 0, release!: () => void;
  const gate = new Promise<void>(resolve => {release = resolve;});
  await page.route(`**/api/transactions/${ID}`, route => route.fulfill({json: fixture(true)}));
  await page.route(`**/api/expense-workflows/${ID}/follow-up`, async route => {
    writes++;
    if (writes === 1) return route.fulfill({status: 409, json: {error: 'Synthetic follow-up conflict', code: 'follow_up_conflict'}});
    await gate; await route.fulfill({json: {status: 'ok'}});
  });
  await page.goto(DETAIL);
  await page.locator('#expense-follow-up').click();
  await expect(page.locator('#expense-follow-up-error')).toHaveText('Synthetic follow-up conflict');
  await expect(page.locator('#expense-follow-up')).toBeEnabled();
  await page.locator('#expense-follow-up').click();
  await page.locator('a[data-view="income"]').click();
  release();
  await expect(page).toHaveURL(/\/income/);
  await expect(page.locator('[data-vue-owned]')).toHaveCount(0);
  expect(writes).toBe(2);
});
test('missing, forbidden and wrong-type detail states retain usable navigation', async ({page}) => {
  await page.route(`**/api/transactions/${ID}`, route => route.fulfill({status: 404, json: {error: 'Synthetic missing', code: 'transaction_not_found'}}));
  await page.goto(DETAIL);
  await expect(page.locator('.expense-detail [role="alert"]')).toBeVisible();
  await expect(page.locator('.expense-detail a').first()).toHaveAttribute('href', '/expenses?period=2026-Q3&q=Synthetic');
  await page.unroute(`**/api/transactions/${ID}`);
  await page.route(`**/api/transactions/${ID}`, route => route.fulfill({status: 403, json: {error: 'Synthetic denied', code: 'session_forbidden'}}));
  await page.reload();
  await expect(page.locator('.expense-detail [role="alert"]')).toContainText('Synthetic denied');
  await expect(page.locator('.expense-detail [role="alert"] button')).toBeEnabled();
  await page.unroute(`**/api/transactions/${ID}`);
  const data = fixture(); data.transaction.entry_type = 'income';
  await page.route(`**/api/transactions/${ID}`, route => route.fulfill({json: data}));
  await page.reload();
  await expect(page.locator('.expense-detail a[href="/income?period=2026-Q2"]')).toBeVisible();
});
