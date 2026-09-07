import { test, expect, type Page } from '@playwright/test';
const ID = '11111111-1111-4111-8111-111111111111',
  OTHER = '22222222-2222-4222-8222-222222222222';
const profile = {
  taxpayer_profile_id: ID,
  row_version: 2,
  full_name: 'Synthetic profile',
  tax_id: 'SYNTHETIC-ID',
  residency_country: 'ES',
  identity_locked: false,
  activities: [],
};
const backups = {
  available: true,
  revision: 'synthetic-revision-4',
  daily_keep: 14,
  monthly_keep: null,
  last_success: {
    daily: {
      recorded_at: '2026-07-02T12:00:00Z',
      keep: 14,
      offsite_status: 'pending',
      settings_format: 1,
    },
  },
  recovery_verification: {
    monthly: {
      last_attempt: { recorded_at: '2026-07-03T12:00:00Z', status: 'failed' },
      last_success: { recorded_at: '2026-07-02T12:00:00Z', status: 'success' },
    },
  },
};
async function fixture(page: Page) {
  await page.route('**/api/settings', (route) =>
    route.fulfill({
      json: {
        profiles: [
          profile,
          {
            ...profile,
            taxpayer_profile_id: OTHER,
            full_name: 'Synthetic other',
            identity_locked: true,
          },
        ],
        backups,
      },
    }),
  );
}
test('settings busy and conflict states retain private drafts and API revisions', async ({
  page,
}) => {
  await fixture(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const writes: Record<string, unknown>[] = [];
  await page.route('**/api/settings/profile', async (route) => {
    writes.push(route.request().postDataJSON());
    if (writes.length === 1) {
      await gate;
      return route.fulfill({
        status: 409,
        json: { error: 'Synthetic conflict', code: 'stale_profile' },
      });
    }
    return route.fulfill({ json: { ...profile, full_name: 'Synthetic saved', row_version: 3 } });
  });
  await page.goto('/income?period=2026-Q3');
  await page.locator('a[data-view="settings"]').click();
  const name = page.locator('[name="full_name"]');
  await name.fill('Synthetic saved');
  await page.locator('#settings-profile-form button[type="submit"]').click();
  await expect.poll(() => writes.length).toBe(1);
  await page.goBack();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(name).toHaveValue('Synthetic saved');
  await expect(name).toBeDisabled();
  await page.locator('[data-locale="en"]').click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'ru');
  release();
  await expect(page.locator('#settings-profile-form [role="alert"]')).not.toHaveText('');
  await expect(name).toBeEnabled();
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.locator('#settings-profile-select').selectOption(OTHER);
  await expect(name).toHaveValue('Synthetic saved');
  page.once('dialog', (dialog) => dialog.dismiss());
  await page.locator('#settings-locale').selectOption('en');
  await expect(page.locator('#settings-locale')).toHaveValue('ru');
  await page.locator('#settings-profile-form button[type="submit"]').click();
  await expect(page.locator('#profile-name')).toHaveText('Synthetic saved');
  expect(writes.map((row) => row.expected_row_version)).toEqual([2, 2]);
  await page.locator('#settings-profile-select').selectOption(OTHER);
  await expect(page.locator('[name="tax_id"]')).toHaveAttribute('readonly', '');
  expect(
    await page.evaluate(() => Object.keys(localStorage).filter((key) => key !== 'autonomo.locale')),
  ).toEqual([]);
});
test('backup confirmation, null limits and recovery evidence remain explicit', async ({ page }) => {
  await fixture(page);
  const writes: Record<string, unknown>[] = [];
  await page.route('**/api/settings/backups', (route) => {
    writes.push(route.request().postDataJSON());
    return route.fulfill({
      json: { ...backups, revision: 'synthetic-revision-5', daily_keep: null, monthly_keep: 3 },
    });
  });
  await page.goto('/settings');
  await page.locator('[data-locale="en"]').click();
  await expect(page.locator('#app')).toContainText('The latest recovery verification failed');
  await page.locator('[name="daily_keep"]').fill('');
  await page.locator('[name="monthly_keep"]').fill('3');
  let prompt = '';
  page.once('dialog', (dialog) => {
    prompt = dialog.message();
    void dialog.dismiss();
  });
  await page.locator('#settings-backup-form button').click();
  expect(writes).toHaveLength(0);
  expect(prompt).toContain('PERMANENTLY DELETED');
  page.once('dialog', (dialog) => dialog.accept());
  await page.locator('#settings-backup-form button').click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toEqual({
    expected_revision: 'synthetic-revision-4',
    daily_keep: null,
    monthly_keep: 3,
    confirm_local_pruning: true,
  });
  await expect(page.locator('#settings-backup-form [role="status"]')).toContainText('Limits saved');
});
test('settings is usable without periods', async ({ page }) => {
  await fixture(page);
  await page.route('**/api/bootstrap', (route) =>
    route.fulfill({
      json: { periods: [], default_period: null, profile_name: 'Synthetic', intake_enabled: false },
    }),
  );
  await page.goto('/settings');
  await expect(page.locator('[name="full_name"]')).toHaveValue('Synthetic profile');
  await expect(page.locator('#period-select')).not.toBeVisible();
});
test('help Back/Forward retains search and Router history; reload discards transient overlay', async ({
  page,
}) => {
  await page.goto('/expenses?period=2026-Q3');
  await page.locator('#expense-search').fill('Example');
  await expect(page).toHaveURL(/q=Example/);
  const help = page.locator('#app [data-status-help]').first();
  await help.click();
  await expect.poll(() => page.evaluate(() => history.state.accountingHelp)).toBeTruthy();
  await page.goBack();
  await expect(page.locator('#status-help-dialog')).not.toBeVisible();
  await expect(page.locator('#expense-search')).toHaveValue('Example');
  await page.goForward();
  await expect(page.locator('#status-help-dialog')).toBeVisible();
  await page.reload();
  await expect(page.locator('#status-help-dialog')).not.toBeVisible();
  await expect(page.locator('#expense-search')).toHaveValue('Example');
});

test('late settings data cannot replace a new route', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/settings', async (route) => {
    await gate;
    await route.fulfill({ json: { profiles: [profile], backups } });
  });
  await page.goto('/settings');
  await page.locator('a[data-view="income"]').click();
  release();
  await expect(page.locator('#transactions-table')).toBeVisible();
  await expect(page.locator('#settings-profile-form')).toHaveCount(0);
  await expect(page).toHaveURL(/\/income\?/);
});

test('review period selection and navigation keep the active tab', async ({ page }) => {
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
  for (const tab of ['posting', 'documents']) {
    await page.goto(`/review?period=2026-Q3&tab=${tab}`);
    await page.locator('#period-select').selectOption('2026-Q2');
    await expect(page).toHaveURL(new RegExp(`period=2026-Q2&tab=${tab}$`));
    await page.locator('a[data-view="income"]').click();
    await page.locator('a[data-view="review"]').click();
    await expect(page).toHaveURL(new RegExp(`period=2026-Q2&tab=${tab}$`));
  }
});
