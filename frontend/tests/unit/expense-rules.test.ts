import { nextTick } from 'vue';
import { afterEach, expect, test, vi } from 'vitest';
import { mountView } from '../support/mount.ts';
import ReviewDetailView from '../../src/features/review/ReviewDetailView.vue';
import type { ReviewDetailContext } from '../../src/features/review/guided-model.ts';
import type { JsonOptions } from '../../src/core/http.ts';
import { setLocale } from '../../src/core/i18n.ts';
import {
  chooseRule,
  defaults,
  type WorkflowDraft,
} from '../../src/features/review/workflow-model.ts';
import { REVIEW_ID, nativeFixture, ruleFixture } from '../fixtures/review.ts';
async function flush() {
  for (let count = 0; count < 8; count++) {
    await Promise.resolve();
    await nextTick();
  }
}
const preview = {
  preview_token: 'synthetic-token',
  supplier: 'Synthetic',
  period: '2026-Q3',
  currency: 'EUR',
  gross_minor: 12100,
  deductible_vat_minor: 525,
  deductible_irpf_minor: 700,
  future_depreciation_minor: 0,
  schedule: [],
};
async function mount(draft: WorkflowDraft, saves: unknown[] = []) {
  const request = vi.fn(async (url: string, options?: JsonOptions) => {
    if (url.startsWith('/api/transactions/'))
      return {
        transaction: {
          transaction_id: REVIEW_ID,
          entry_type: 'expense',
          lifecycle_status: 'needs_review',
        },
        period: { period_key: '2026-Q3' },
      };
    if (url === '/api/counterparties') return [];
    if (url.endsWith('/save')) {
      const body = JSON.parse(String(options?.body));
      saves.push(body.payload);
      return { ...draft, payload: body.payload, draft_version: draft.draft_version + 1 };
    }
    if (url.endsWith('/preview')) return { ...preview, deduction: draft.deduction_proposal };
    return draft;
  });
  const context: ReviewDetailContext = {
    transactionId: REVIEW_ID,
    period: '2026-Q3',
    returnUrl: '/review?period=2026-Q3',
    knownPeriods: ['2026-Q3'],
    services: { request, navigate: vi.fn() },
    resolved: () => '/review?period=2026-Q3',
    complete: vi.fn(),
    posted: vi.fn(),
    settled: vi.fn(),
    notify: vi.fn(),
  };
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, ReviewDetailView, context);
  await flush();
  return { root, host };
}
const card = (root: HTMLElement) => root.querySelector('#wf-rule-card')!.textContent || '';
const input = (root: HTMLElement, path: string) =>
  root.querySelector<HTMLInputElement>(`#wf-form [data-p="${path}"]`)!;
function type(element: HTMLInputElement, value: string) {
  element.value = value;
  element.dispatchEvent(new Event('input', { bubbles: true }));
}
afterEach(() => {
  setLocale('ru');
  document.body.replaceChildren();
});

test('the proposal card is translated for RU and EN, with sources, risk and parser origin', async () => {
  setLocale('ru');
  const { root, host } = await mount(ruleFixture());
  const selected = root.querySelector<HTMLSelectElement>('#wf-rule-select')!;
  expect(selected.value).toBe('home_utility_partial_dwelling');
  expect(selected.selectedOptions[0].textContent).toContain('по распознаванию документа');
  expect(root.querySelector('#wf-rule-card')!.hasAttribute('aria-live')).toBe(false);
  expect(root.querySelector('#wf-rule-card .wf-rule-live')!.getAttribute('aria-live')).toBe(
    'polite',
  );
  expect(root.querySelector('.wf-rule-live')!.textContent).not.toContain('Только для основного');
  expect(card(root)).toContain('Предел IRPF');
  expect(card(root)).toMatch(/7,50\s€/);
  expect(card(root)).toContain('IVA: решите сами');
  expect(card(root)).toContain('Только для основного жилья');
  expect(card(root)).toContain('Средний риск');
  expect(card(root)).not.toContain('вторичный источник');
  expect(card(root)).not.toContain('Only for your habitual home');
  setLocale('en');
  await flush();
  expect(selected.selectedOptions[0].textContent).toContain('(from document parser)');
  expect(card(root)).toContain('IRPF ceiling');
  expect(card(root)).toContain('€7.50');
  expect(card(root)).toContain('IRPF: 30% of the floor-area share of 25%.');
  expect(card(root)).toContain('Only for your habitual home');
  expect(card(root)).toContain('checked 01 Oct 2026');
  expect(card(root)).toContain(
    'Copy writes IRPF €7.50 into its field; IVA stays as you entered it.',
  );
  expect(card(root)).not.toContain('secondary source');
  host.dispose();
});

test('copy into fields preserves manual IVA and drops an existing preview', async () => {
  setLocale('en');
  const saves: unknown[] = [];
  const draft = ruleFixture();
  draft.payload.decision.tax_treatment.deductible_vat_minor = 600;
  const { root, host } = await mount(draft, saves);
  root
    .querySelector('#wf-form')!
    .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  expect(root.querySelector('#wf-preview-rule')!.textContent).toContain(
    'Supplies of the habitual home partly used for work: IRPF €0.50 below the ceiling; IVA is your decision',
  );
  root.querySelector<HTMLButtonElement>('#wf-rule-copy')!.click();
  await flush();
  expect(root.querySelector('#wf-preview')).toBeNull();
  expect(input(root, 'decision.tax_treatment.deductible_irpf_minor').value).toBe('7.5');
  expect(input(root, 'decision.tax_treatment.deductible_vat_minor').value).toBe('6');
  expect(root.querySelector('.wf-message')!.textContent).toContain('Run the preview again');
  root.querySelector<HTMLButtonElement>('#wf-save')!.click();
  await flush();
  expect(saves.at(-1)).toMatchObject({
    decision: { tax_treatment: { deductible_irpf_minor: 750, deductible_vat_minor: 600 } },
  });
  host.dispose();
});

test('an unsaved input the proposal depends on shows the stale hint and blocks copying', async () => {
  setLocale('en');
  const { root, host } = await mount(ruleFixture());
  const copy = root.querySelector<HTMLButtonElement>('#wf-rule-copy')!;
  type(input(root, 'decision.business_purpose'), 'Edited purpose');
  await flush();
  expect(root.querySelector('.wf-rule-stale')).toBeNull();
  type(
    root.querySelector<HTMLInputElement>(
      '#wf-rule [data-p="decision.tax_treatment.deductible_ratio"]',
    )!,
    '40',
  );
  await flush();
  expect(root.querySelector('.wf-rule-stale')!.textContent).toContain(
    'save the draft to recalculate',
  );
  expect(copy.disabled).toBe(true);
  root.querySelector<HTMLButtonElement>('#wf-save')!.click();
  await flush();
  expect(root.querySelector('.wf-rule-stale')).toBeNull();
  expect(copy.disabled).toBe(false);
  host.dispose();
});

test('a fresh seed without browser defaults is stale until saved, so Copy cannot write it', async () => {
  setLocale('en');
  const draft = ruleFixture();
  draft.payload.decision.tax_treatment.aeat_invoice_type = null;
  const { root, host } = await mount(draft);
  expect(input(root, 'decision.tax_treatment.aeat_invoice_type').value).toBe('F1');
  expect(root.querySelector('.wf-rule-stale')!.textContent).toContain('save the draft');
  expect(root.querySelector<HTMLButtonElement>('#wf-rule-copy')!.disabled).toBe(true);
  expect(root.querySelector('.wf-rule-pair')).toBeNull();
  root.querySelector<HTMLButtonElement>('#wf-save')!.click();
  await flush();
  expect(root.querySelector('.wf-rule-stale')).toBeNull();
  expect(root.querySelector<HTMLButtonElement>('#wf-rule-copy')!.disabled).toBe(false);
  host.dispose();
});

test('an area rule owns the share field and counts stay within one year of days', async () => {
  setLocale('en');
  const saves: unknown[] = [];
  const { root, host } = await mount(ruleFixture(), saves);
  const shares = () =>
    root.querySelectorAll('#wf-form [data-p="decision.tax_treatment.deductible_ratio"]');
  expect(shares()).toHaveLength(1);
  expect(shares()[0].closest('#wf-rule')).not.toBeNull();
  const select = root.querySelector<HTMLSelectElement>('#wf-rule-select')!;
  select.value = 'own_meals';
  select.dispatchEvent(new Event('change', { bubbles: true }));
  await flush();
  expect(shares()).toHaveLength(1);
  expect(shares()[0].closest('#wf-rule')).toBeNull();
  type(input(root, 'deduction.facts.days'), '999');
  root.querySelector<HTMLButtonElement>('#wf-save')!.click();
  await flush();
  expect(saves.at(-1)).toMatchObject({ deduction: { rule_id: 'own_meals', facts: { days: 366 } } });
  host.dispose();
});

test('an area rule never inherits the default 100% share', () => {
  const draft = ruleFixture();
  const payload = nativeFixture().payload;
  payload.decision.tax_treatment.deductible_ratio = 1;
  chooseRule(payload, draft.deduction_rules!, 'home_utility_partial_dwelling');
  expect(payload.decision.tax_treatment.deductible_ratio).toBeNull();
  expect(payload.deduction?.facts.evidence_confirmed).toBeNull();
  defaults(payload, draft.deduction_rules);
  expect(payload.decision.tax_treatment.deductible_ratio).toBeNull();
  chooseRule(payload, draft.deduction_rules!, '');
  expect(payload.deduction).toBeNull();
  expect(payload.decision.tax_treatment.deductible_ratio).toBe(1);
});
