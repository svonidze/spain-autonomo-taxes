import { test, expect, type Page } from '@playwright/test';
async function open(page: Page, path = '/income?period=2026-Q3') {
  await page.goto(path);
  await page.locator('#new-entry-button').click();
  await expect(page.locator('#vue-intake-dialog')).toBeVisible();
}
const file = {
  name: 'synthetic-proof.txt',
  mimeType: 'text/plain',
  buffer: Buffer.from('SYNTHETIC FILE CONTENT'),
};
test('upload retry preserves the file and fields; accepted submission cannot be repeated or resurrect its draft', async ({
  page,
}) => {
  await page.clock.install();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const bodies: string[] = [];
  await page.route('**/api/intake', async (route) => {
    bodies.push(route.request().postData() || '');
    if (bodies.length === 1) {
      await gate;
      return route.fulfill({ status: 503, json: { error: 'Synthetic intake unavailable' } });
    }
    return route.fulfill({
      json: { system_marker: 'synthetic-marker', period: '2026-Q3', kind: 'income_invoice' },
    });
  });
  await open(page);
  await page.locator('#vue-intake-file').setInputFiles(file);
  await page.locator('#vue-intake-form [name="document_number"]').fill('SYN-RETRY');
  await page.locator('#vue-submit-intake').click();
  await expect.poll(() => bodies.length).toBe(1);
  await page.locator('[data-locale="en"]').evaluate((button: HTMLElement) => button.click());
  await expect(page.locator('#vue-submit-intake')).toBeDisabled();
  await expect(page.locator('#vue-intake-form [name="document_number"]')).toHaveValue('SYN-RETRY');
  release();
  await expect(page.locator('#vue-intake-status')).toHaveText('Synthetic intake unavailable');
  expect(
    await page
      .locator('#vue-intake-file')
      .evaluate((input: HTMLInputElement) => input.files?.[0]?.name),
  ).toBe(file.name);
  await page.locator('#vue-submit-intake').click();
  await expect(page.locator('#vue-intake-status')).toContainText('Accepted');
  await expect(page.locator('#vue-submit-intake')).toBeDisabled();
  expect(bodies).toHaveLength(2);
  expect(bodies[1]).toContain('SYN-RETRY');
  expect(bodies[1]).toContain(file.name);
  await page.locator('#vue-close-dialog').click();
  expect(await page.evaluate(() => localStorage.getItem('autonomo.intake-draft'))).toBeNull();
  await page.locator('#new-entry-button').click();
  await expect(page.locator('#vue-intake-form [name="document_number"]')).toHaveValue('');
  await page.locator('#vue-intake-form [name="document_number"]').fill('SYN-NEXT');
  await page.clock.fastForward(1000);
  await expect(page.locator('#vue-intake-dialog')).toBeVisible();
  await expect(page.locator('#vue-intake-form [name="document_number"]')).toHaveValue('SYN-NEXT');
});
test('Drive input validates its URL and sends JSON without upload-only fields', async ({
  page,
}) => {
  const bodies: Record<string, unknown>[] = [];
  await page.route('**/api/intake/google-drive', (route) => {
    bodies.push(route.request().postDataJSON());
    return route.fulfill({ status: 503, json: { error: 'Synthetic Drive unavailable' } });
  });
  await open(page, '/expenses?period=2026-Q3');
  await page.locator('#vue-intake-dialog [data-intake-source="google_drive"]').click();
  await page
    .locator('#vue-google-drive-url')
    .fill('https://evil.example/file/d/synthetic-file-123');
  await page.locator('#vue-submit-intake').click();
  await expect(page.locator('#vue-intake-status')).not.toHaveText('');
  expect(bodies).toHaveLength(0);
  await page
    .locator('#vue-google-drive-url')
    .fill('https://drive.google.com/file/d/synthetic-file-123/view');
  await page.locator('#vue-submit-intake').click();
  await expect(page.locator('#vue-intake-status')).toHaveText('Synthetic Drive unavailable');
  expect(bodies[0]).toMatchObject({
    drive_url: 'https://drive.google.com/file/d/synthetic-file-123/view',
    fields: { kind: 'expense_invoice', period: '2026-Q3', defer_counterparty: '1' },
  });
  expect((bodies[0]!.fields as Record<string, unknown>).file).toBeUndefined();
  expect((bodies[0]!.fields as Record<string, unknown>).drive_url).toBeUndefined();
});
async function mockPicker(page: Page) {
  await page.route('**/api/google-picker/config', (route) =>
    route.fulfill({
      json: {
        enabled: true,
        developer_key: 'synthetic-key',
        app_id: 'synthetic-app',
        access_token: 'synthetic-access-token',
      },
    }),
  );
  await page.addInitScript(() => {
    const target = window as unknown as {
      google: unknown;
      pickerVisible: boolean;
      intakeHidden: boolean;
      choose: (action: string) => void;
    };
    let callback: (data: Record<string, unknown>) => void;
    class View {
      setSelectFolderEnabled() {
        return this;
      }
      setMode() {
        return this;
      }
    }
    class Builder {
      addView() {
        return this;
      }
      setOAuthToken() {
        return this;
      }
      setDeveloperKey() {
        return this;
      }
      setAppId() {
        return this;
      }
      setOrigin() {
        return this;
      }
      setLocale() {
        return this;
      }
      setCallback(value: typeof callback) {
        callback = value;
        return this;
      }
      build() {
        return {
          setVisible(value: boolean) {
            target.pickerVisible = value;
            if (value)
              target.intakeHidden = !(
                document.querySelector('#vue-intake-dialog') as HTMLDialogElement
              ).open;
          },
          dispose() {},
        };
      }
    }
    target.google = {
      picker: {
        DocsView: View,
        PickerBuilder: Builder,
        ViewId: { FOLDERS: 'folders' },
        DocsViewMode: { LIST: 'list' },
        Action: { PICKED: 'picked', CANCEL: 'cancel' },
        Response: { DOCUMENTS: 'docs' },
        Document: { ID: 'id', NAME: 'name' },
      },
    };
    target.choose = (action) =>
      callback({ action, docs: [{ id: 'synthetic-folder-123', name: '<Synthetic folder>' }] });
  });
}
test('Picker cancel and selection retain fields/file, leave the native top layer and never persist credentials', async ({
  page,
}) => {
  await mockPicker(page);
  let body = '';
  await page.route('**/api/intake', (route) => {
    body = route.request().postData() || '';
    return route.fulfill({ status: 503, json: { error: 'Synthetic upload unavailable' } });
  });
  await open(page);
  await page.locator('#vue-intake-file').setInputFiles(file);
  await page.locator('#vue-intake-form [name="document_number"]').fill('SYN-PICKER');
  await page.locator('#vue-choose-google-folder').click();
  await expect
    .poll(() =>
      page.evaluate(() => (window as unknown as { pickerVisible: boolean }).pickerVisible),
    )
    .toBe(true);
  expect(
    await page.evaluate(() => (window as unknown as { intakeHidden: boolean }).intakeHidden),
  ).toBe(true);
  await page.evaluate(() =>
    (window as unknown as { choose: (action: string) => void }).choose('cancel'),
  );
  await expect(page.locator('#vue-intake-dialog')).toBeVisible();
  await expect(page.locator('#vue-choose-google-folder')).toBeFocused();
  await expect(page.locator('#vue-intake-form [name="document_number"]')).toHaveValue('SYN-PICKER');
  await page.locator('#vue-choose-google-folder').click();
  await expect
    .poll(() =>
      page.evaluate(() => (window as unknown as { pickerVisible: boolean }).pickerVisible),
    )
    .toBe(true);
  await page.evaluate(() =>
    (window as unknown as { choose: (action: string) => void }).choose('picked'),
  );
  await expect(page.locator('#vue-intake-dialog')).toBeVisible();
  await expect(page.locator('#vue-google-folder-controls')).toContainText('<Synthetic folder>');
  expect(
    await page
      .locator('#vue-intake-file')
      .evaluate((input: HTMLInputElement) => input.files?.[0]?.name),
  ).toBe(file.name);
  await page.locator('#vue-submit-intake').click();
  await expect(page.locator('#vue-intake-status')).toHaveText('Synthetic upload unavailable');
  expect(body).toContain('google_folder_id');
  expect(body).toContain('synthetic-folder-123');
  expect(body).not.toContain('synthetic-access-token');
  expect(
    await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage })),
  ).not.toContain('synthetic-access-token');
});
test('late intake completion does not navigate or close a new form', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/intake', async (route) => {
    await gate;
    await route.fulfill({
      json: {
        system_marker: 'synthetic-id',
        kind: 'expense_invoice',
        transaction_id: '11111111-1111-4111-8111-111111111111',
        period: '2026-Q3',
      },
    });
  });
  await open(page, '/expenses?period=2026-Q3');
  await page.locator('#vue-intake-file').setInputFiles(file);
  await page.locator('#vue-submit-intake').click();
  await expect(page.locator('#vue-submit-intake')).toBeDisabled();
  await page.locator('#vue-close-dialog').click();
  await page.locator('a[data-view="income"]').click();
  release();
  await expect(page.locator('#toast')).toBeVisible();
  await page.locator('#new-entry-button').click();
  await page.locator('#vue-intake-form [name="document_number"]').fill('SYN-NEW-FORM');
  await page.clock.install();
  await page.clock.fastForward(1000);
  await expect(page).toHaveURL(/\/income\?/);
  await expect(page.locator('#vue-intake-dialog')).toBeVisible();
  await expect(page.locator('#vue-intake-form [name="document_number"]')).toHaveValue(
    'SYN-NEW-FORM',
  );
});

test('a late Picker result cannot reopen intake on another route', async ({ page }) => {
  await mockPicker(page);
  await open(page);
  await page.locator('#vue-intake-file').setInputFiles(file);
  await page.locator('#vue-intake-form [name="document_number"]').fill('SYN-BEFORE-PICKER');
  await page.locator('#vue-choose-google-folder').click();
  await expect
    .poll(() =>
      page.evaluate(() => (window as unknown as { pickerVisible: boolean }).pickerVisible),
    )
    .toBe(true);
  await page.locator('a[data-view="expenses"]').click();
  await page.evaluate(() =>
    (window as unknown as { choose: (action: string) => void }).choose('picked'),
  );
  await expect
    .poll(() =>
      page.evaluate(() => (window as unknown as { pickerVisible: boolean }).pickerVisible),
    )
    .toBe(false);
  await expect(page.locator('#vue-intake-dialog')).not.toBeVisible();
  await page.locator('#new-entry-button').click();
  await expect(page.locator('#vue-intake-form [name="document_number"]')).toHaveValue(
    'SYN-BEFORE-PICKER',
  );
  await expect(page.locator('#vue-google-folder-controls')).not.toContainText('<Synthetic folder>');
});
test('drag-drop uses the real file input and retains fields through source and locale switches', async ({
  page,
}) => {
  await open(page);
  await page.locator('#vue-intake-form [name="document_number"]').fill('SYN-DROP');
  const data = await page.evaluateHandle(() => {
    const transfer = new DataTransfer();
    transfer.items.add(
      new File(['Synthetic dropped bytes'], 'dropped.txt', { type: 'text/plain' }),
    );
    return transfer;
  });
  await page.locator('#vue-file-drop').dispatchEvent('drop', { dataTransfer: data });
  expect(
    await page
      .locator('#vue-intake-file')
      .evaluate((input: HTMLInputElement) => input.files?.[0]?.name),
  ).toBe('dropped.txt');
  await page.locator('#vue-intake-dialog [data-intake-source="google_drive"]').click();
  await page.locator('#vue-intake-dialog [data-intake-source="upload"]').click();
  await page.locator('[data-locale="en"]').evaluate((button: HTMLElement) => button.click());
  await expect(page.locator('#vue-file-label')).toHaveText('dropped.txt');
  await expect(page.locator('#vue-intake-form [name="document_number"]')).toHaveValue('SYN-DROP');
  await data.dispose();
});

test('a mismatched acknowledgement retains the draft and permits correction', async ({ page }) => {
  await page.route('**/api/intake', (route) =>
    route.fulfill({ json: { kind: 'expense_invoice', period: '2026-Q2' } }),
  );
  await open(page);
  await page.locator('#vue-intake-file').setInputFiles(file);
  await page.locator('#vue-intake-form [name="document_number"]').fill('SYN-MISMATCH');
  await page.locator('#vue-submit-intake').click();
  await expect(page.locator('#vue-intake-status')).not.toHaveText('');
  await expect(page.locator('#vue-submit-intake')).toBeEnabled();
  await expect(page.locator('#vue-intake-form [name="document_number"]')).toHaveValue(
    'SYN-MISMATCH',
  );
  expect(await page.evaluate(() => localStorage.getItem('autonomo.intake-draft'))).toContain(
    'SYN-MISMATCH',
  );
  await expect(page.locator('#vue-intake-dialog')).toBeVisible();
});

test('closing intake during the upload still delivers the accepted result on the same route', async ({
  page,
}) => {
  const id = '11111111-1111-4111-8111-111111111111';
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/intake', async (route) => {
    await gate;
    await route.fulfill({
      json: {
        system_marker: 'synthetic-id',
        kind: 'expense_invoice',
        transaction_id: id,
        period: '2026-Q3',
      },
    });
  });
  await page.route(`**/api/transactions/${id}`, (route) =>
    route.fulfill({
      json: {
        transaction: {
          transaction_id: id,
          entry_type: 'expense',
          lifecycle_status: 'needs_review',
          currency: 'EUR',
        },
        period: { period_key: '2026-Q3' },
      },
    }),
  );
  await open(page, '/expenses?period=2026-Q3');
  await page.locator('#vue-intake-file').setInputFiles(file);
  await page.locator('#vue-submit-intake').click();
  await expect(page.locator('#vue-submit-intake')).toBeDisabled();
  await page.locator('#vue-close-dialog').click();
  await expect(page.locator('#vue-intake-dialog')).not.toBeVisible();
  release();
  await expect(page.locator('#toast')).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/review/${id}\\?period=2026-Q3$`));
});
test('a long server diagnostic stays inside the 375 px intake footer', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.route('**/api/intake', (route) =>
    route.fulfill({ status: 503, json: { error: 'SyntheticUnbrokenDiagnostic'.repeat(12) } }),
  );
  await open(page);
  await page.locator('#vue-intake-file').setInputFiles(file);
  await page.locator('#vue-submit-intake').click();
  const status = page.locator('#vue-intake-status');
  await expect(status).toContainText('SyntheticUnbrokenDiagnostic');
  const [statusBox, submitBox] = await Promise.all([
    status.boundingBox(),
    page.locator('#vue-submit-intake').boundingBox(),
  ]);
  expect(statusBox!.width).toBeLessThanOrEqual(375);
  expect(submitBox!.x + submitBox!.width).toBeLessThanOrEqual(375);
  expect(await status.evaluate((element) => getComputedStyle(element).overflowWrap)).toBe(
    'anywhere',
  );
});
test('typing a list filter while the upload runs does not drop the accepted hand-off', async ({
  page,
}) => {
  const id = '11111111-1111-4111-8111-111111111111';
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/intake', async (route) => {
    await gate;
    await route.fulfill({
      json: {
        system_marker: 'synthetic-id',
        kind: 'expense_invoice',
        transaction_id: id,
        period: '2026-Q3',
      },
    });
  });
  await page.route(`**/api/transactions/${id}`, (route) =>
    route.fulfill({
      json: {
        transaction: {
          transaction_id: id,
          entry_type: 'expense',
          lifecycle_status: 'needs_review',
          currency: 'EUR',
        },
        period: { period_key: '2026-Q3' },
      },
    }),
  );
  await open(page, '/expenses?period=2026-Q3');
  await page.locator('#vue-intake-file').setInputFiles(file);
  await page.locator('#vue-submit-intake').click();
  await expect(page.locator('#vue-submit-intake')).toBeDisabled();
  await page.locator('#vue-close-dialog').click();
  await page.locator('#expense-search').fill('a');
  await expect(page).toHaveURL(/q=a/);
  release();
  await expect(page.locator('#toast')).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/review/${id}\\?period=2026-Q3$`));
});
