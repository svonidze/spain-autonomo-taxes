import { createApp, nextTick } from 'vue';
import { afterEach, expect, test } from 'vitest';
import ExpenseTable from '../../src/features/transactions/ExpenseTable.vue';
import type { ExpenseKind, TransactionRow } from '../../src/features/transactions/model.ts';
import { setLocale, t } from '../../src/core/i18n.ts';
import { disposeHelp } from '../../src/vue/help.ts';

const cleanups: (() => void)[] = [];
afterEach(() => {
  cleanups.splice(0).forEach((cleanup) => cleanup());
  disposeHelp();
  setLocale('ru');
});
function mount(overrides: Partial<TransactionRow>, kind: ExpenseKind = 'purchase') {
  const row: TransactionRow = {
    transaction_id: 'synthetic-expense',
    entry_type: 'expense',
    lifecycle_status: 'needs_review',
    transaction_date: '2025-05-12',
    period_key: '2025-Q2',
    currency: 'USD',
    amount_original: '43.25',
    amount_eur: null,
    ui_context: {
      domain: 'transaction',
      state: 'needs_review',
      reasons: [{ code: 'document_classification_review' }],
    },
    ...overrides,
  };
  const root = document.createElement('div');
  document.body.append(root);
  const app = createApp(ExpenseTable, {
    rows: [row],
    kind,
    period: row.period_key,
    returnUrl: '/expenses',
    navigate() {},
  });
  app.mount(root);
  cleanups.push(() => {
    app.unmount();
    root.remove();
  });
  return root.querySelectorAll('tbody td')[3]!;
}

test.each(['en', 'ru'] as const)(
  'original amounts and FX uncertainty stay distinct in %s',
  async (locale) => {
    setLocale(locale);
    const foreign = mount({});
    expect(foreign.querySelector('strong')!.textContent).toBe(
      locale === 'en' ? '43.25 USD' : '43,25 USD',
    );
    expect(foreign.textContent).toContain(t('expense.fxUnconfirmed', {}, locale));
    expect(foreign.textContent).not.toContain(t('expense.missing', {}, locale));
    expect(document.querySelector('.status-context small')!.textContent).toContain(
      t('help.reasons.document_classification_review.title', {}, locale),
    );
    const missing = mount({ amount_original: null });
    expect(missing.querySelector('strong')!.textContent).toBe('—');
    expect(missing.textContent).toContain(t('expense.missing', {}, locale));
    expect(missing.textContent).not.toContain(t('expense.fxUnconfirmed', {}, locale));
    const zero = mount({ amount_original: 0 });
    expect(zero.querySelector('strong')!.textContent).toBe(
      locale === 'en' ? '0.00 USD' : '0,00 USD',
    );
    expect(zero.textContent).not.toContain(t('expense.missing', {}, locale));
    const confirmed = mount({ amount_eur: 0 });
    expect(confirmed.querySelector('strong')!.textContent).toMatch(/0[.,]00/);
    expect(confirmed.querySelector('strong')!.textContent).toContain('€');
    expect(confirmed.textContent).toContain(locale === 'en' ? '43.25 USD' : '43,25 USD');
    expect(confirmed.textContent).not.toContain(t('expense.fxUnconfirmed', {}, locale));
    const domestic = mount({ currency: 'EUR', amount_original: '0.00' });
    expect(domestic.querySelector('strong')!.textContent).toContain('EUR');
    expect(domestic.querySelector('small')).toBeNull();
    const amortization = mount({ deductible_irpf_eur: null }, 'amortization');
    expect(amortization.querySelector('strong')!.textContent).toBe('—');
    expect(amortization.textContent).toContain(t('expense.missing', {}, locale));
    expect(amortization.textContent).not.toContain('USD');

    setLocale(locale === 'en' ? 'ru' : 'en');
    await nextTick();
    expect(foreign.querySelector('strong')!.textContent).toBe(
      locale === 'en' ? '43,25 USD' : '43.25 USD',
    );
  },
);
