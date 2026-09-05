import {test, expect, type Page} from '@playwright/test';
import chartMessages from '../../frontend/src/locales/en/charts.json' with {type: 'json'};

const ID = '11111111-1111-4111-8111-111111111111';
const DOCUMENT = '22222222-2222-4222-8222-222222222222';

async function guided(page: Page) {
  let reads = 0;
  const transaction = {transaction_id: ID, entry_type: 'income', lifecycle_status: 'needs_review', original_currency: 'EUR', transaction_date: '2026-07-02'};
  await page.route(`**/api/transactions/${ID}`, route => {reads++; return route.fulfill({json: {transaction, period: {period_key: '2026-Q3'}}});});
  await page.route(/\/api\/review\/work-item\?/, route => {
    reads++;
    return route.fulfill({json: {
      packet: {review_id: `transaction:${ID}`, snapshot_hash: 'synthetic-review-snapshot', state: {
        period: {period_key: '2026-Q3'}, transaction, counterparty: {country_code: 'ES'}, issues: [],
      }, decision: {business_purpose: '', counterparty_changes: {}, tax_treatment: {tax_code: 'domestic_output'}, issue_resolutions: []}},
      requirements: [], fx_suggestion: null, supported: true, review_allowed: true,
      posting_context: {preview_bucket: 'needs_review'},
      ui_context: {domain: 'transaction', state: 'needs_review', reasons: []},
    }});
  });
  await page.goto(`/review/${ID}?period=2026-Q3`);
  await expect(page.locator('[data-decision-path="business_purpose"]')).toBeEditable();
  return () => reads;
}

async function language(page: Page, locale: string) {
  await page.locator(`[data-locale="${locale}"]`).first().evaluate((element: HTMLElement) => element.click());
  await expect(page.locator('html')).toHaveAttribute('lang', locale);
}

test('guided fields, rejection draft and focus survive a presentation-only locale change', async ({page}) => {
  const reads = await guided(page);
  await page.locator('[data-decision-path="business_purpose"]').fill('Synthetic business purpose');
  await page.locator('.review-technical-details > summary').click();
  await page.locator('.review-reject-panel > summary').click();
  await page.locator('#review-reject-document-valid').selectOption('false');
  const reason = page.locator('#review-reject-reason');
  await reason.fill("Synthetic rejection 'reason'");
  await reason.evaluate((field: HTMLTextAreaElement) => {field.focus(); field.setSelectionRange(3, 12);});
  const before = reads();
  await language(page, 'en');
  await expect(page.locator('[data-decision-path="business_purpose"]')).toHaveValue('Synthetic business purpose');
  await expect(reason).toHaveValue("Synthetic rejection 'reason'");
  await expect(page.locator('#review-reject-document-valid')).toHaveValue('false');
  await expect(page.locator('.review-technical-details')).toHaveAttribute('open', '');
  await expect(page.locator('.review-reject-panel')).toHaveAttribute('open', '');
  await expect(reason).toBeFocused();
  expect(await reason.evaluate((field: HTMLTextAreaElement) => [field.selectionStart, field.selectionEnd])).toEqual([3, 12]);
  expect(reads()).toBe(before);
});

test('locale change during a POST does not orphan its result or submit twice', async ({page}) => {
  const reads = await guided(page);
  const writes: unknown[] = [];
  let release!: () => void;
  const gate = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/api/review/confirm', async route => {
    writes.push(route.request().postDataJSON());
    await gate;
    await route.fulfill({status: 400, json: {error: 'Synthetic rejection failed', code: 'review_packet_invalid'}});
  });
  await page.locator('.review-reject-panel > summary').click();
  await page.locator('#review-reject-document-valid').selectOption('false');
  await page.locator('#review-reject-reason').fill('Synthetic rejection');
  await page.locator('#review-reject-button').click();
  await expect.poll(() => writes.length).toBe(1);
  const before = reads();
  await language(page, 'en');
  await expect(page.locator('#review-reject-button')).toBeDisabled();
  expect(reads()).toBe(before);
  release();
  await expect(page.locator('#app')).toContainText('Synthetic rejection failed');
  await expect(page.locator('#review-reject-button')).toBeEnabled();
  await expect(page.locator('#review-reject-reason')).toHaveValue('Synthetic rejection');
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({packet: {decision: {outcome: 'reject', document_valid: false, reason: 'Synthetic rejection'}}});
});

test('the native expense workflow translates without reloading or losing its unsaved payload', async ({page}) => {
  let reads = 0;
  await page.route(`**/api/transactions/${ID}`, route => route.fulfill({json: {
    transaction: {transaction_id: ID, entry_type: 'expense', lifecycle_status: 'received'}, period: {period_key: '2026-Q3'},
  }}));
  await page.route(`**/api/document/${DOCUMENT}/preview`, route => route.fulfill({contentType: 'text/plain', body: 'Synthetic original'}));
  await page.route(`**/api/expense-workflows/${ID}`, route => {
    reads++;
    return route.fulfill({json: {
      editable: true, draft_version: 1, current_snapshot_hash: 'synthetic-expense-snapshot', source_snapshot_hash: 'synthetic-expense-snapshot',
      source: {assets: [], issues: [], document: {document_id: DOCUMENT}, period: {period_key: '2026-Q3'}},
      activities: [], allowed_values: {tax_code: ['domestic_input']},
      payload: {facts: {document_number: 'SYNTHETIC-1', issued_on: '2026-07-02', transaction_date: '2026-07-02', booking_date: '2026-07-02', currency: 'EUR', gross_minor: 12100},
        decision: {business_purpose: '', reason: '', document_valid: false, tax_treatment: {tax_code: 'domestic_input', taxable_base_minor: 10000, vat_minor: 2100, deductible_irpf_minor: 10000, deductible_vat_minor: 2100}},
        asset: null, supplier: null, fx: null},
    }});
  });
  await page.goto(`/review/${ID}`);
  const number = page.locator('[data-p="facts.document_number"]');
  await expect(number).toBeEditable();
  await number.fill('SYNTHETIC-CHANGED');
  await page.locator('[data-p="decision.business_purpose"]').fill('Changed private draft');
  await page.locator('#wf-new-supplier').click();
  await page.locator('[data-p="supplier.display_name"]').fill('Synthetic draft supplier');
  const before = reads;
  await language(page, 'en');
  await expect(page.locator('#app')).toContainText('Review and post expense');
  await expect(number).toHaveValue('SYNTHETIC-CHANGED');
  await expect(page.locator('[data-p="decision.business_purpose"]')).toHaveValue('Changed private draft');
  await expect(page.locator('[data-p="supplier.display_name"]')).toHaveValue('Synthetic draft supplier');
  expect(reads).toBe(before);
});

test('an open help dialog changes language without consuming its Back entry', async ({page}) => {
  await page.goto('/expenses?period=2026-Q3');
  await page.locator('#app [data-status-help]').first().click();
  const before = await page.evaluate(() => history.state.accountingHelp);
  await language(page, 'en');
  await expect(page.locator('#status-help-dialog')).toBeVisible();
  expect(await page.evaluate(() => history.state.accountingHelp)).toBe(before);
  await page.goBack();
  await expect(page.locator('#status-help-dialog')).not.toBeVisible();
  await expect(page).toHaveURL(/\/expenses\?period=2026-Q3$/);
  await page.goForward();
  await expect(page.locator('#status-help-dialog')).toBeVisible();
});

test('income search remains selected after changing the language', async ({page}) => {
  await page.goto('/income?period=2026-Q3');
  const search = page.locator('#transaction-search');
  await expect(search).toBeVisible();
  await search.fill('Example');
  await language(page, 'en');
  await expect(search).toHaveValue('Example');
  await expect(page.locator('#transactions-table')).toContainText('Example income');
});

test('an expanded chart is translated from its cached data without closing', async ({page}) => {
  await page.goto('/dashboard?period=2026-Q3');
  await page.locator('#chart-business-result .chart-expand-button').click();
  const title = page.locator('#chart-dialog-title');
  const previous = await title.textContent();
  await language(page, 'en');
  await expect(page.locator('#chart-dialog')).toBeVisible();
  await expect(title).not.toHaveText(previous!);
  await expect(title).toHaveText(chartMessages['charts.business.title']);
});

test('pseudolocale comes from the catalog registry and leaves record data intact', async ({page}) => {
  test.skip(process.env.AUTONOMO_PSEUDO !== '1', 'Run npm run test:pseudo for a test-only third locale');
  await page.setViewportSize({width: 375, height: 812});
  await page.goto('/income?period=2026-Q3');
  await expect(page.locator('#app')).toContainText('Example income');
  await language(page, 'qps');
  await expect(page.locator('nav [data-i18n="nav.income"]')).toContainText('⟦');
  await expect(page.locator('#app')).toContainText('Example income');
  await page.locator('#new-entry-button').click();
  await expect(page.locator('#intake-dialog')).toBeVisible();
  await expect(page.locator('#intake-dialog [data-i18n="intake.title"]')).toContainText('⟦');
});
