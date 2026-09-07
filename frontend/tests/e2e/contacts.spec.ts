import { test, expect, type Page } from '@playwright/test';
const ID = '33333333-3333-4333-8333-333333333333';
const TX = '11111111-1111-4111-8111-111111111111';
const OTHER = '44444444-4444-4444-8444-444444444444';
const party = (name = 'Synthetic Supplier', revision = 1) => ({
  counterparty_id: ID,
  display_name: name,
  row_version: revision,
  country_code: 'ES',
  vat_id: 'SYNTHETIC',
  transaction_count: 2,
  last_transaction_on: '2026-07-01',
  ui_context: {
    domain: 'counterparty',
    state: 'unknown',
    subject_id: ID,
    title: name,
    reasons: [],
    facts: { country_code: 'ES' },
  },
});
const operation = (id = TX) => ({
  transaction_id: id,
  entry_type: 'expense',
  transaction_date: '2026-04-01',
  period_key: '2026-Q2',
  description: `Synthetic operation ${id}`,
  amount_eur: 12.5,
  lifecycle_status: 'posted',
  ui_context: { domain: 'transaction', state: 'posted', subject_id: id, reasons: [] },
});
async function fixture(page: Page) {
  await page.route('**/api/counterparties', (route) => route.fulfill({ json: [party()] }));
  await page.route(`**/api/counterparties/${ID}`, (route) =>
    route.fulfill({ json: { counterparty: party(), periods: ['2026-Q3', '2026-Q2'] } }),
  );
  await page.route(`**/api/counterparties/${ID}/transactions?**`, (route) =>
    route.fulfill({
      json: { rows: [operation()], next_offset: 1, matching_count: 1, has_more: false },
    }),
  );
  await page.route(`**/api/counterparties/${ID}/name-history`, (route) =>
    route.fulfill({
      json: {
        changes: [
          {
            old_name: 'Synthetic old',
            new_name: 'Synthetic Supplier',
            changed_at: '2026-07-01T12:00:00Z',
            change_source: 'local',
          },
        ],
      },
    }),
  );
}
async function edit(page: Page) {
  await page.locator('[data-vue-owned] [data-counterparty-menu]').first().click();
  await page.locator('#vue-counterparty-actions-menu button').click();
  await expect(page.locator('#vue-counterparty-name-input')).toBeEditable();
}
test('Vue contacts own help records and preserve help Back/Forward until route disposal', async ({
  page,
}) => {
  await fixture(page);
  await page.goto('/contacts');
  await page.locator('[data-vue-owned] [data-status-help]').first().click();
  await expect(page.locator('#status-help-dialog')).toBeVisible();
  await page.goBack();
  await expect(page.locator('#status-help-dialog')).not.toBeVisible();
  await page.goForward();
  await expect(page.locator('#status-help-dialog')).toBeVisible();
  await page.locator('[data-locale="en"]').evaluate((button: HTMLElement) => button.click());
  await expect(page.locator('#status-help-dialog')).toContainText('ROI registration unverified');
  await page.goBack();
  await page.locator('.counterparty-link').click();
  await expect(page).toHaveURL(new RegExp(`/contacts/${ID}$`));
  await expect(page.locator('#contact-identity h2')).toHaveText('Synthetic Supplier');
  await page.locator('#contact-identity [data-status-help]').click();
  await expect(page.locator('#status-help-dialog')).toBeVisible();
});
test('contact-local period, history and expense return preserve global period', async ({
  page,
}) => {
  await fixture(page);
  await page.route(`**/api/transactions/${TX}`, (route) =>
    route.fulfill({
      json: {
        transaction: {
          transaction_id: TX,
          entry_type: 'expense',
          lifecycle_status: 'posted',
          currency: 'EUR',
        },
        period: { period_key: '2026-Q2' },
      },
    }),
  );
  await page.goto(`/contacts/${ID}`);
  await expect(page.locator('a[data-view="dashboard"]')).toHaveAttribute(
    'href',
    '/dashboard?period=2026-Q3',
  );
  await page.locator('#contact-period').selectOption('2026-Q2');
  await expect(page.locator('a[data-view="dashboard"]')).toHaveAttribute(
    'href',
    '/dashboard?period=2026-Q3',
  );
  await page.locator('#contact-history > summary').click();
  await expect(page.locator('#contact-history-body')).toContainText('Synthetic old');
  await page.locator('#contact-operations-results a').first().click();
  await expect(page).toHaveURL(new RegExp(`/expenses/${TX}\\?`));
  await page.locator('.review-workspace-header > a').click();
  await expect(page).toHaveURL(new RegExp(`/contacts/${ID}\\?period=2026-Q2$`));
  await expect(page.locator('a[data-view="dashboard"]')).toHaveAttribute(
    'href',
    '/dashboard?period=2026-Q3',
  );
});
test('rename conflict preserves draft and explicitly adopts current revision before retry', async ({
  page,
}) => {
  await fixture(page);
  const submissions: { display_name: string; expected_row_version: number }[] = [];
  await page.route(`**/api/counterparties/${ID}/rename`, (route) => {
    submissions.push(route.request().postDataJSON());
    return submissions.length === 1
      ? route.fulfill({
          status: 409,
          json: {
            error: 'Synthetic conflict',
            code: 'stale_counterparty',
            current: { display_name: 'Synthetic concurrent', row_version: 2 },
          },
        })
      : route.fulfill({
          json: { display_name: 'Synthetic corrected', row_version: 3, changed: true },
        });
  });
  await page.goto(`/contacts/${ID}`);
  await edit(page);
  await page.locator('#vue-counterparty-name-input').fill('Synthetic corrected');
  await page.locator('#vue-counterparty-name-dialog button[type="submit"]').click();
  await expect(page.locator('.counterparty-name-conflict:visible')).toContainText(
    'Synthetic concurrent',
  );
  await expect(page.locator('#vue-counterparty-name-input')).toHaveValue('Synthetic corrected');
  await page.locator('[data-locale="en"]').evaluate((button: HTMLElement) => button.click());
  await expect(page.locator('#vue-counterparty-name-input')).toHaveValue('Synthetic corrected');
  await page.locator('#vue-counterparty-name-dialog .counterparty-name-conflict button').click();
  await page.locator('#vue-counterparty-name-dialog button[type="submit"]').click();
  await expect(page.locator('#vue-counterparty-name-dialog')).not.toBeVisible();
  await expect(page.locator('#contact-identity h2')).toHaveText('Synthetic corrected');
  expect(submissions).toEqual([
    { display_name: 'Synthetic corrected', expected_row_version: 1 },
    { display_name: 'Synthetic corrected', expected_row_version: 2 },
  ]);
});
test('dirty rename declines Back and pending rename stays singular through locale change', async ({
  page,
}) => {
  await fixture(page);
  let release!: () => void,
    writes = 0;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/counterparties/${ID}/rename`, async (route) => {
    writes++;
    await gate;
    await route.fulfill({
      json: { display_name: 'Synthetic corrected', row_version: 2, changed: true },
    });
  });
  await page.goto('/contacts');
  await page.locator('.counterparty-link').click();
  await edit(page);
  await page.locator('#vue-counterparty-name-input').fill('Synthetic corrected');
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`/contacts/${ID}$`));
  await expect(page.locator('#vue-counterparty-name-input')).toHaveValue('Synthetic corrected');
  await page.locator('#vue-counterparty-name-dialog button[type="submit"]').click();
  await page.locator('[data-locale="en"]').evaluate((button: HTMLElement) => button.click());
  await expect(page.locator('#vue-counterparty-name-dialog button[type="submit"]')).toBeDisabled();
  await expect(page.locator('#vue-counterparty-name-input')).toHaveValue('Synthetic corrected');
  expect(writes).toBe(1);
  release();
  await expect(page.locator('#contact-identity h2')).toHaveText('Synthetic corrected');
});
test('contact pagination retries without duplicate rows and ignores late pages from another period', async ({
  page,
}) => {
  await fixture(page);
  let attempts = 0,
    release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/counterparties/${ID}/transactions?**`, async (route) => {
    const query = new URL(route.request().url()).searchParams;
    if (query.get('period') === '2026-Q2')
      return route.fulfill({
        json: { rows: [operation(OTHER)], next_offset: 1, matching_count: 1, has_more: false },
      });
    if (query.get('offset') === '0')
      return route.fulfill({
        json: { rows: [operation()], next_offset: 1, matching_count: 2, has_more: true },
      });
    if (++attempts === 1)
      return route.fulfill({ status: 503, json: { error: 'Synthetic unavailable' } });
    await gate;
    await route.fulfill({
      json: {
        rows: [operation(), operation(OTHER)],
        next_offset: 2,
        matching_count: 2,
        has_more: false,
      },
    });
  });
  await page.goto(`/contacts/${ID}`);
  await page.locator('#contact-load-more').click();
  await expect(page.locator('#contact-operations-results [role="alert"]')).toBeVisible();
  await page.locator('#contact-load-more').click();
  await page.locator('#contact-period').selectOption('2026-Q2');
  release();
  await expect(page.locator('#contact-operations-results tbody tr')).toHaveCount(1);
  await expect(page.locator('#contact-operations-results')).toContainText(OTHER);
});

test('appended contact pages deduplicate records and menu dismissal restores focus', async ({
  page,
}) => {
  await page.addInitScript(() => {
    (window as unknown as { cspViolations: string[] }).cspViolations = [];
    document.addEventListener('securitypolicyviolation', (event) =>
      (window as unknown as { cspViolations: string[] }).cspViolations.push(
        event.violatedDirective,
      ),
    );
  });
  await fixture(page);
  await page.route(`**/api/counterparties/${ID}/transactions?**`, (route) => {
    const more = new URL(route.request().url()).searchParams.get('offset') !== '0';
    return route.fulfill({
      json: {
        rows: more ? [operation(), operation(OTHER)] : [operation()],
        next_offset: more ? 2 : 1,
        matching_count: 2,
        has_more: !more,
      },
    });
  });
  await page.goto(`/contacts/${ID}`);
  await page.locator('#contact-load-more').click();
  await expect(page.locator('#contact-operations-results tbody tr')).toHaveCount(2);
  const trigger = page.locator('#contact-identity [data-counterparty-menu]');
  await trigger.click();
  await expect(page.locator('#vue-counterparty-actions-menu button')).toBeFocused();
  const triggerBox = (await trigger.boundingBox())!;
  const menuBox = (await page.locator('#vue-counterparty-actions-menu').boundingBox())!;
  const viewport = page.viewportSize()!;
  expect(menuBox.x).toBeGreaterThanOrEqual(8);
  expect(menuBox.x + menuBox.width).toBeLessThanOrEqual(viewport.width);
  expect(menuBox.y).toBeGreaterThanOrEqual(triggerBox.y + triggerBox.height);
  expect(menuBox.y + menuBox.height).toBeLessThanOrEqual(viewport.height);
  expect(
    await page.evaluate(() => (window as unknown as { cspViolations: string[] }).cspViolations),
  ).toEqual([]);
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await expect(page.locator('#vue-counterparty-actions-menu')).toHaveCount(0);
});

test('a new global period replaces the old contact-chain period', async ({ page }) => {
  await page.route('**/api/bootstrap', async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({
      json: {
        ...data,
        periods: [
          { period_key: '2026-Q3', status: 'open' },
          { period_key: '2026-Q2', status: 'open' },
        ],
      },
    });
  });
  await fixture(page);
  await page.goto(`/contacts/${ID}`);
  await expect(page.locator('#contact-identity')).toBeVisible();
  await page.locator('a[data-view="dashboard"]').click();
  await page.locator('#period-select').selectOption('2026-Q2');
  await expect(page).toHaveURL(/period=2026-Q2/);
  await page.locator('a[data-view="contacts"]').click();
  await page.locator('.counterparty-link').click();
  await expect(page.locator('#contact-identity')).toBeVisible();
  await expect(page.locator('a[data-view="dashboard"]')).toHaveAttribute(
    'href',
    '/dashboard?period=2026-Q2',
  );
  await page.locator('#contact-identity [data-status-help]').click();
  await expect.poll(() => page.evaluate(() => history.state.accountingHelp)).toBeTruthy();
  await page.reload();
  await expect(page.locator('#contact-identity')).toBeVisible();
  await expect(page.locator('#status-help-dialog')).not.toBeVisible();
  await expect(page.locator('a[data-view="dashboard"]')).toHaveAttribute(
    'href',
    '/dashboard?period=2026-Q2',
  );
});
