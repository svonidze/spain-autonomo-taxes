import { test, expect } from '@playwright/test';
test('error retry and expired-session reload remain distinct and escaped', async ({ page }) => {
  let status = 503;
  await page.route(/\/api\/transactions\?/, (route) =>
    status
      ? route.fulfill({
          status,
          json: {
            error: '<img src=x onerror="alert(1)">',
            code: status === 403 ? 'session_forbidden' : 'synthetic_failure',
          },
        })
      : route.fulfill({ json: [] }),
  );
  await page.goto('/income?period=2026-Q3');
  await page.locator('[data-locale="en"]').click();
  const alert = page.locator('#app [role="alert"]');
  await expect(alert).toContainText('Could not load');
  await expect(alert.locator('img')).toHaveCount(0);
  await expect(alert.locator('button')).toHaveText('Retry');
  status = 403;
  await alert.locator('button').click();
  await expect(alert.locator('button')).toHaveText('Reload page');
  status = 0;
  await alert.locator('button').click();
  await expect(page.locator('#transactions-table')).toBeVisible();
  await expect(page.locator('#app [role="alert"]')).toHaveCount(0);
});
test('invalid list periods normalize and modified navigation remains browser-owned', async ({
  page,
}) => {
  await page.goto('/income?period=invalid');
  await expect(page).toHaveURL(/\/income\?period=2026-Q3$/);
  const nativeOwned = await page.locator('a[data-view="expenses"]').evaluate((element) => {
    let owned = false;
    element.addEventListener(
      'click',
      (event) => {
        owned = !event.defaultPrevented;
        event.preventDefault();
      },
      { once: true },
    );
    element.dispatchEvent(
      new MouseEvent('click', { bubbles: true, cancelable: true, metaKey: true, button: 0 }),
    );
    return owned;
  });
  expect(nativeOwned).toBe(true);
  await expect(page).toHaveURL(/\/income\?period=2026-Q3$/);
});
test('income copy buttons and document links retain accessible native contracts', async ({
  page,
}) => {
  const id = '11111111-1111-4111-8111-111111111111';
  await page.route(/\/api\/transactions\?/, (route) =>
    route.fulfill({
      json: [
        {
          transaction_id: id,
          entry_type: 'income',
          lifecycle_status: 'posted',
          transaction_date: '2026-07-01',
          period_key: '2026-Q3',
          counterparty_name: '<Synthetic>',
          document_id: id,
          currency: 'EUR',
          amount_eur: '0.00',
          amount_original: '0.00',
          ui_context: { domain: 'transaction', state: 'posted', reasons: [] },
        },
      ],
    }),
  );
  await page.goto('/income?period=2026-Q3');
  const copy = page.locator('[data-copy-transaction-id]');
  await expect(copy).toHaveAttribute('aria-label', /.+/);
  await expect(copy.locator('svg')).toHaveAttribute('aria-hidden', 'true');
  await expect(copy.locator('svg')).toHaveAttribute('focusable', 'false');
  const link = page.locator('#transactions-table a[target="_blank"]');
  await expect(link).toHaveAttribute('rel', 'noreferrer');
  await expect(link).toHaveAttribute('href', `/api/document/${id}/content`);
  await expect(page.locator('#transactions-table')).toContainText('<Synthetic>');
  await expect(page.locator('#transactions-table synthetic')).toHaveCount(0);
});
test('dashboard approved banner opens posting for its current quarter', async ({ page }) => {
  await page.route(/\/api\/dashboard\?/, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({
      json: { ...data, posting_preview_summary: { approved_count: 1, ready_count: 1 } },
    });
  });
  await page.goto('/dashboard?period=2026-Q3');
  await page.locator('.posting-banner button').click();
  await expect(page).toHaveURL(/\/review\?period=2026-Q3&tab=posting$/);
});
test('a late failed detail read cannot replace a successful new page', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/transactions/11111111-1111-4111-8111-111111111111', async (route) => {
    await gate;
    await route.fulfill({ status: 503, json: { error: 'Synthetic obsolete failure' } });
  });
  await page.goto('/expenses/11111111-1111-4111-8111-111111111111');
  await page.locator('a[data-view="income"]').click();
  release();
  await expect(page.locator('#transactions-table')).toBeVisible();
  await expect(page.locator('#app')).not.toContainText('Synthetic obsolete failure');
});
