import { expect, test } from '@playwright/test';

test('original amounts and manual review stay readable in both locales and at mobile width', async ({
  page,
}) => {
  const rows = [
    { transaction_id: 'synthetic-foreign', amount_original: '43.25', amount_eur: null },
    { transaction_id: 'synthetic-missing', amount_original: null, amount_eur: null },
    { transaction_id: 'synthetic-zero', amount_original: '0.00', amount_eur: null },
  ].map((row) => ({
    entry_type: 'expense',
    lifecycle_status: 'needs_review',
    expense_kind: 'purchase',
    transaction_date: '2026-04-11',
    period_key: '2026-Q2',
    currency: 'USD',
    description: 'Synthetic service',
    document_number: row.transaction_id,
    ui_context: {
      domain: 'transaction',
      state: 'needs_review',
      reasons: [{ code: 'document_classification_review' }],
    },
    ...row,
  }));
  const totals = { count: 0, amount_eur: '0.00', missing_amount_count: 0 };
  const summary = {
    posted: totals,
    approved: totals,
    future_approved: totals,
    reviewed_total: totals,
  };
  await page.route('**/api/expenses?*', (route) =>
    route.fulfill({
      json: {
        rows,
        as_of: '2026-04-20',
        view_revision: 'synthetic-amounts',
        has_more: false,
        next_offset: rows.length,
        matching_count: rows.length,
        matching_counts: { purchase: rows.length, amortization: 0 },
        period_counts: { purchase: rows.length, amortization: 0 },
        summary: { purchase: summary, amortization: summary },
      },
    }),
  );
  await page.goto('/expenses?period=2026-Q2');
  for (const locale of ['ru', 'en'] as const) {
    await page.locator(`[data-locale="${locale}"]`).click();
    for (const width of [1280, 375]) {
      await page.setViewportSize({ width, height: 1100 });
      const foreign = page.locator('[data-transaction-id="synthetic-foreign"]');
      const missing = page.locator('[data-transaction-id="synthetic-missing"]');
      const zero = page.locator('[data-transaction-id="synthetic-zero"]');
      await expect(foreign).toContainText(locale === 'ru' ? '43,25 USD' : '43.25 USD');
      await expect(foreign).toContainText(
        locale === 'ru' ? 'Курс в EUR не подтвержден' : 'EUR exchange rate is not confirmed',
      );
      await expect(foreign).toContainText(
        locale === 'ru' ? 'Нужна ручная проверка документа' : 'Document needs manual review',
      );
      await expect(missing).toContainText(
        locale === 'ru' ? 'Не хватает данных о сумме' : 'Amount information is missing',
      );
      await expect(zero).toContainText(locale === 'ru' ? '0,00 USD' : '0.00 USD');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      );
      await page.screenshot({
        path: test.info().outputPath(`expense-table-${locale}-${width}.png`),
        fullPage: true,
      });
    }
  }
});
