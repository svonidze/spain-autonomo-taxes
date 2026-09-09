import { test, expect } from '@playwright/test';

test('real backend serves routes, scripts and guarded history under CSP', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => {
    (window as any).__cspViolations = [];
    document.addEventListener('securitypolicyviolation', (event) => {
      (window as any).__cspViolations.push(event.violatedDirective);
    });
  });
  const response = await page.goto('/expenses?period=2026-Q3');
  expect(response?.headers()['content-security-policy']).toContain("script-src 'self'");
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#app')).toContainText('Example expense');
  const globals = await page.evaluate(() =>
    [
      'AccountingHelp',
      'AutonomoCharts',
      'ExpenseWorkflow',
      'AutonomoSettings',
      'AutonomoCore',
      'AutonomoViews',
      '__AUTONOMO_WEB_UI_TEST_HOOKS__',
    ].some((name) => name in window),
  );
  expect(globals).toBe(false);
  const help = page.locator('#app [data-status-help]').first();
  await help.click();
  await expect(page.locator('#status-help-dialog')).toBeVisible();
  await page.goBack();
  await expect(page.locator('#status-help-dialog')).not.toBeVisible();
  await expect(page).toHaveURL(/\/expenses\?period=2026-Q3$/);
  await page.goForward();
  await expect(page.locator('#status-help-dialog')).toBeVisible();
  await page.goBack();
  await page.locator('nav a[data-view="income"]').click();
  await expect(page.locator('#app')).toContainText('Example income');
  await page.goBack();
  await expect(page.locator('#app')).toContainText('Example expense');
  await page.reload();
  await expect(page.locator('#app')).toContainText('Example expense');
  expect(await page.evaluate(() => (window as any).__cspViolations)).toEqual([]);
  expect(errors).toEqual([]);
});

test('intake keeps selected file and typed fields across a locale change', async ({ page }) => {
  await page.goto('/income?period=2026-Q3');
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  await page.locator('#new-entry-button').click();
  const dialog = page.locator('#vue-intake-dialog');
  await expect(dialog).toBeVisible();
  await dialog.locator('[name="document_number"]').fill('SYNTHETIC-UI-1');
  await dialog.locator('input[type="file"]').setInputFiles({
    name: 'synthetic.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('Synthetic browser test document'),
  });
  // The blocking dialog makes the background switch unreachable to a normal click.
  // Invoke its real registered handler to cover an asynchronous locale change.
  await page
    .locator('[data-locale="en"]')
    .first()
    .evaluate((element: HTMLElement) => element.click());
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('[name="document_number"]')).toHaveValue('SYNTHETIC-UI-1');
  expect(
    await dialog
      .locator('input[type="file"]')
      .evaluate((element: HTMLInputElement) => element.files?.[0]?.name),
  ).toBe('synthetic.txt');
});

test('unknown routes and missing resources never masquerade as a working page', async ({
  request,
}) => {
  await request.get('/'); // Acquire the same session cookie as a browser page.
  expect((await request.get('/does-not-exist')).status()).toBe(404);
  expect((await request.get('/missing-script.js')).status()).toBe(404);
});

test('AEAT document registry is global, searchable and responsive', async ({ page }) => {
  await page.goto('/aeat-documents');
  await expect(page.locator('#app')).toHaveAttribute('aria-busy', 'false');
  await expect(page.locator('#page-title')).toHaveText('Документы AEAT');
  await expect(page.locator('.period-control')).toBeHidden();
  await expect(page.locator('.aeat-documents-table')).toContainText('Synthetic ROI registration');
  await page.locator('input[type="search"]').fill('036');
  await expect(page.locator('.aeat-documents-table tbody tr')).toHaveCount(1);
  await page.locator('.aeat-upload-panel summary').click();
  await expect(page.locator('.aeat-upload-panel input[type="file"]')).toBeVisible();
  await page.setViewportSize({ width: 375, height: 800 });
  await expect(page.locator('.aeat-documents-table thead')).toBeHidden();
  await expect(page.locator('.aeat-documents-table tbody tr').first()).toBeVisible();
});

test('settings preserves a private draft when leaving is declined', async ({ page }) => {
  await page.goto('/settings');
  const name = page.locator('#app [name="full_name"]');
  await expect(name).toBeVisible();
  await name.fill('Synthetic unsaved profile');
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.locator('nav a[data-view="income"]').click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(name).toHaveValue('Synthetic unsaved profile');
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.locator('[data-locale="en"]').first().click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
  await expect(name).toHaveValue('Synthetic unsaved profile');
  expect(
    await page.evaluate(() => Object.keys(localStorage).filter((key) => key !== 'autonomo.locale')),
  ).toEqual([]);
});
