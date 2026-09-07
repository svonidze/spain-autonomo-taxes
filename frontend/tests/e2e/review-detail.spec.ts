import { test, expect, type Page } from '@playwright/test';
import {
  guidedFixture,
  nativeFixture,
  REVIEW_ID as ID,
  DOCUMENT_ID as DOC,
} from '../fixtures/review.ts';
import type { ExpensePayload } from '../../src/features/review/workflow-model.ts';
const path = `/review/${ID}?period=2026-Q3`;
async function detail(page: Page, kind = 'income', posted = () => false) {
  await page.route(`**/api/transactions/${ID}`, (route) =>
    route.fulfill({
      json: {
        transaction: {
          transaction_id: ID,
          entry_type: kind,
          lifecycle_status: posted() ? 'posted' : 'needs_review',
          currency: 'EUR',
        },
        period: { period_key: '2026-Q3' },
      },
    }),
  );
}
async function native(page: Page) {
  await detail(page, 'expense');
  await page.route(`**/api/document/${DOC}/preview`, (route) =>
    route.fulfill({ contentType: 'text/plain', body: 'Synthetic document' }),
  );
}
test('native save/preview/confirm preserves units and independent IVA choice, then retries one request', async ({
  page,
}) => {
  await native(page);
  const draft = nativeFixture();
  let saved: ExpensePayload | undefined;
  const writes: unknown[] = [];
  let confirmations = 0;
  await page.route(`**/api/expense-workflows/${ID}`, (route) => route.fulfill({ json: draft }));
  await page.route(`**/api/expense-workflows/${ID}/save`, (route) => {
    const body = route.request().postDataJSON();
    saved = body.payload;
    expect(body.expected_version).toBe(1);
    return route.fulfill({ json: { ...draft, payload: saved, draft_version: 2 } });
  });
  await page.route(`**/api/expense-workflows/${ID}/preview`, (route) => {
    expect(route.request().postDataJSON()).toEqual({ expected_version: 2 });
    return route.fulfill({
      json: {
        preview_token: 'synthetic-token',
        supplier: 'Synthetic supplier',
        period: '2026-Q3',
        currency: 'EUR',
        gross_minor: 12100,
        deductible_vat_minor: 2100,
        deductible_irpf_minor: 0,
        future_depreciation_minor: 10000,
        schedule: [],
      },
    });
  });
  await page.route(`**/api/expense-workflows/${ID}/confirm`, (route) => {
    writes.push(route.request().postDataJSON());
    return ++confirmations === 1
      ? route.fulfill({ status: 503, json: { error: 'Synthetic transient confirmation failure' } })
      : route.fulfill({ json: { transaction_id: ID, follow_up_pending: true } });
  });
  await page.route(`**/api/expense-workflows/${ID}/follow-up`, (route) =>
    route.fulfill({ json: { follow_up_pending: false } }),
  );
  await page.goto(path);
  await page.locator('#wf-kind').selectOption('asset');
  await page.locator('[data-p="asset.method"]').selectOption('linear');
  await page.locator('[data-p="asset.annual_rate_basis_points"]').fill('25.50');
  await page.locator('[data-p="decision.tax_treatment.vat_investment_good"]').selectOption('false');
  await page
    .locator('[data-p="decision.tax_treatment.tax_code"]')
    .selectOption('eu_service_expense');
  await page.locator('#wf-form button[type="submit"]').click();
  await expect(page.locator('#wf-preview')).toBeVisible();
  expect(saved?.asset?.annual_rate_basis_points).toBe(2550);
  expect(saved?.decision.tax_treatment.vat_investment_good).toBe(false);
  expect(saved?.decision.tax_treatment.include_modelo130).toBe(false);
  expect(saved?.decision.tax_treatment.aeat_reverse_charge).toBe(true);
  await page.locator('#wf-confirm').click();
  await expect(page.locator('.expense-workflow [role="alert"]')).toContainText(
    'Synthetic transient',
  );
  await page.locator('#wf-confirm').click();
  await expect(page.locator('#wf-open-posted')).toBeVisible();
  expect(writes).toHaveLength(2);
  expect(writes[0]).toEqual(writes[1]);
  await page.locator('#wf-follow-up').click();
  await expect(page.locator('#wf-follow-up')).toHaveCount(0);
  const before = await page.evaluate(() => history.length);
  await page.locator('#wf-open-posted').click();
  await expect(page).toHaveURL(new RegExp(`/expenses/${ID}\\?`));
  expect(await page.evaluate(() => history.length)).toBe(before + 1);
});
test('native conflict reset is explicit and edits invalidate a posting preview', async ({
  page,
}) => {
  await native(page);
  const draft = nativeFixture();
  draft.conflict = true;
  draft.current_snapshot_hash = 'new-source';
  draft.source_values.facts.document_number = 'SYN-CURRENT';
  draft.payload.facts.document_number = 'SYN-SAVED';
  await page.route(`**/api/expense-workflows/${ID}`, (route) => route.fulfill({ json: draft }));
  await page.route(`**/api/expense-workflows/${ID}/save`, (route) => {
    const body = route.request().postDataJSON();
    expect(body.source_snapshot_hash).toBe('new-source');
    return route.fulfill({
      json: {
        ...draft,
        conflict: false,
        source_snapshot_hash: 'new-source',
        payload: body.payload,
        draft_version: 2,
      },
    });
  });
  await page.route(`**/api/expense-workflows/${ID}/preview`, (route) =>
    route.fulfill({
      json: {
        preview_token: 'synthetic-new-token',
        supplier: 'Synthetic',
        period: '2026-Q3',
        currency: 'EUR',
        gross_minor: 12100,
        deductible_vat_minor: 2100,
        deductible_irpf_minor: 10000,
        future_depreciation_minor: 0,
        schedule: [],
      },
    }),
  );
  await page.goto(path);
  await expect(page.locator('[data-p="facts.document_number"]')).toHaveValue('SYN-SAVED');
  await expect(page.locator('#wf-form button[type="submit"]')).toBeDisabled();
  page.once('dialog', (dialog) => dialog.accept());
  await page.locator('#wf-reset').click();
  await expect(page.locator('[data-p="facts.document_number"]')).toHaveValue('SYN-CURRENT');
  await page.locator('#wf-form button[type="submit"]').click();
  await expect(page.locator('#wf-preview')).toBeVisible();
  await page.locator('[data-p="facts.document_number"]').fill('SYN-EDITED');
  await expect(page.locator('#wf-preview')).toHaveCount(0);
});
test('late guided confirmation preserves a newer draft and uses additive localized validation', async ({
  page,
}) => {
  await detail(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let writes = 0;
  await page.route(/\/api\/review\/work-item\?/, (route) =>
    route.fulfill({ json: guidedFixture() }),
  );
  await page.route('**/api/review/confirm', async (route) => {
    if (++writes === 1) {
      await gate;
      return route.fulfill({ json: { status: 'ok' } });
    }
    return route.fulfill({
      status: 400,
      json: {
        error: 'decision.business_purpose is required',
        code: 'invalid_review',
        message_code: 'review.validationBusinessPurpose',
        field: 'business_purpose',
      },
    });
  });
  await page.goto(path);
  await page.locator('[data-decision-path="business_purpose"]').fill('First draft');
  await page.locator('#review-primary-button').click();
  await expect.poll(() => writes).toBe(1);
  await page.locator('a[data-view="income"]').click();
  await page.goBack();
  await expect(page.locator('[data-decision-path="business_purpose"]')).toBeEditable();
  await page.locator('[data-decision-path="business_purpose"]').fill('Newer draft');
  release();
  await expect(page).toHaveURL(new RegExp(`/review/${ID}\\?`));
  await expect
    .poll(() =>
      page.evaluate(
        (id) =>
          JSON.parse(localStorage.getItem('autonomo.review-draft:' + id) || '{}').decision
            ?.business_purpose,
        ID,
      ),
    )
    .toBe('Newer draft');
  await page.locator('#review-primary-button').click();
  await page.locator('[data-locale="en"]').click();
  await expect(page.locator('.review-guided-field [role="alert"]')).toHaveText(
    'Describe the business purpose of this transaction.',
  );
  await expect(page.locator('[data-decision-path="business_purpose"]')).toHaveValue('Newer draft');
});
test('snapshot changes retain only counterparty facts; blocked approval still permits explicit rejection', async ({
  page,
}) => {
  await detail(page);
  const fixture = guidedFixture();
  fixture.packet.snapshot_hash = 'current-snapshot';
  fixture.packet.state.counterparty = { country_code: 'ZZ' };
  fixture.packet.decision.business_purpose = 'Current source purpose';
  fixture.ui_context.state = 'deferred';
  fixture.review_allowed = false;
  fixture.posting_context = { preview_bucket: 'deferred', posting_deferred_until: '2026-10-01' };
  await page.addInitScript(
    ({ id }) =>
      localStorage.setItem(
        'autonomo.review-draft:' + id,
        JSON.stringify({
          review_id: 'transaction:' + id,
          transaction_id: id,
          snapshot_hash: 'old-snapshot',
          decision: {
            business_purpose: 'Old draft purpose',
            tax_treatment: { tax_code: 'reverse_charge' },
            counterparty_changes: { country_code: 'US' },
          },
        }),
      ),
    { id: ID },
  );
  await page.route(/\/api\/review\/work-item\?/, (route) => route.fulfill({ json: fixture }));
  const posted: unknown[] = [];
  await page.route('**/api/review/confirm', (route) => {
    posted.push(route.request().postDataJSON());
    return route.fulfill({ json: { status: 'ok' } });
  });
  await page.goto(path);
  await expect(page.locator('[data-decision-path="business_purpose"]')).toHaveValue(
    'Current source purpose',
  );
  await expect(
    page.locator('[data-decision-path="counterparty_changes.country_code"]'),
  ).toHaveValue('US');
  await expect(page.locator('[data-decision-path="tax_treatment.tax_code"]')).toHaveValue(
    'domestic_output',
  );
  await expect(page.locator('#review-primary-button')).toBeDisabled();
  await page.locator('.review-reject-panel > summary').click();
  await page.locator('#review-reject-button').click();
  expect(posted).toHaveLength(0);
  await page.locator('#review-reject-document-valid').selectOption('false');
  await page.locator('#review-reject-reason').fill('Synthetic duplicate document');
  await page.locator('#review-reject-button').click();
  await expect(page.locator('#review-form')).toHaveCount(0);
  expect(posted).toHaveLength(1);
  expect(posted[0]).toMatchObject({
    packet: {
      decision: {
        outcome: 'reject',
        document_valid: false,
        reason: 'Synthetic duplicate document',
        counterparty_changes: {},
        tax_treatment: { tax_code: null },
      },
    },
    fx: null,
  });
});
