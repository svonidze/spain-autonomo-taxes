import { test, expect, type Page } from '@playwright/test';
const ID = '33333333-3333-4333-8333-333333333333';
function deferred() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}
async function intakeOnContacts(page: Page) {
  const upload = deferred();
  const calls = { lists: 0, uploads: 0 };
  await page.route('**/api/counterparties', (route) => {
    calls.lists++;
    return route.fulfill({
      json: [
        {
          counterparty_id: ID,
          display_name: 'Synthetic Supplier',
          row_version: 1,
          country_code: 'ES',
          ui_context: { domain: 'counterparty', state: 'unknown', subject_id: ID, reasons: [] },
        },
      ],
    });
  });
  await page.route(`**/api/counterparties/${ID}/name-history`, (route) =>
    route.fulfill({ json: { changes: [] } }),
  );
  await page.route('**/api/dashboard/refresh', (route) => route.fulfill({ json: {} }));
  await page.route('**/api/intake', async (route) => {
    calls.uploads++;
    await upload.promise;
    await route.fulfill({
      json: { system_marker: 'synthetic-intake', kind: 'income_invoice', period: '2026-Q3' },
    });
  });
  await page.goto('/contacts');
  await expect(page.locator('.counterparty-link')).toBeVisible();
  await page.locator('#new-entry-button').click();
  await page.locator('#vue-intake-dialog [data-kind="income_invoice"]').click();
  await page.locator('#vue-intake-file').setInputFiles({
    name: 'synthetic.txt',
    mimeType: 'text/plain',
    buffer: Buffer.from('Synthetic intake guard test'),
  });
  await page.locator('#vue-submit-intake').click();
  await expect.poll(() => calls.uploads).toBe(1);
  await page.locator('#vue-close-dialog').click();
  return { upload, calls };
}
async function rename(page: Page) {
  await page.locator('#app [data-counterparty-menu]').first().click();
  await page.locator('#vue-counterparty-actions-menu button').click();
  await page.locator('#vue-counterparty-name-input').fill('Synthetic unsaved rename');
}
test('closed intake leaves a dirty rename untouched without prompting and permits later manual refresh', async ({
  page,
}) => {
  const { upload, calls } = await intakeOnContacts(page);
  await rename(page);
  let confirmations = 0;
  page.on('dialog', async (dialog) => {
    confirmations++;
    await dialog.dismiss();
  });
  upload.release();
  await expect(page.locator('#toast')).toContainText('2026-Q3');
  expect(confirmations).toBe(0);
  await expect(page.locator('#vue-counterparty-name-input')).toHaveValue(
    'Synthetic unsaved rename',
  );
  await expect(page.locator('#vue-counterparty-name-dialog')).toBeVisible();
  expect(calls.lists).toBe(1);
  page.removeAllListeners('dialog');
  page.once('dialog', (dialog) => dialog.accept());
  await page.locator('#vue-counterparty-name-dialog button.icon-button').click();
  await page.locator('#refresh-button').click();
  await expect.poll(() => calls.lists).toBe(2);
  expect(calls.uploads).toBe(1);
});
test('closed intake does not interrupt a pending rename or submit it twice', async ({ page }) => {
  const { upload, calls } = await intakeOnContacts(page);
  const saved = deferred();
  let writes = 0,
    confirmations = 0;
  await page.route(`**/api/counterparties/${ID}/rename`, async (route) => {
    writes++;
    await saved.promise;
    await route.fulfill({
      json: { display_name: 'Synthetic unsaved rename', row_version: 2, changed: true },
    });
  });
  await rename(page);
  await page.locator('#vue-counterparty-name-dialog button[type="submit"]').click();
  await expect.poll(() => writes).toBe(1);
  page.on('dialog', async (dialog) => {
    confirmations++;
    await dialog.dismiss();
  });
  upload.release();
  await expect(page.locator('#toast')).toContainText('2026-Q3');
  await expect(page.locator('#vue-counterparty-name-dialog')).toBeVisible();
  await expect(page.locator('#vue-counterparty-name-input')).toHaveValue(
    'Synthetic unsaved rename',
  );
  await expect(page.locator('#vue-counterparty-name-dialog button[type="submit"]')).toBeDisabled();
  expect(calls.lists).toBe(1);
  expect(confirmations).toBe(0);
  saved.release();
  await expect(page.locator('.counterparty-link')).toContainText('Synthetic unsaved rename');
  await expect(page.locator('#vue-counterparty-name-dialog')).not.toBeVisible();
  expect(writes).toBe(1);
  expect(calls.uploads).toBe(1);
});
test('closed intake refreshes an idle screen once', async ({ page }) => {
  const { upload, calls } = await intakeOnContacts(page);
  upload.release();
  await expect.poll(() => calls.lists).toBe(2);
  await expect(page.locator('#vue-intake-dialog')).not.toBeVisible();
  expect(calls.uploads).toBe(1);
});
