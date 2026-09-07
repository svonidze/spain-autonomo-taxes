import { nextTick } from 'vue';
import { test, expect, vi } from 'vitest';
import { mountView } from '../support/mount.ts';
import ReviewDetailView from '../../src/features/review/ReviewDetailView.vue';
import type { ReviewDetailContext } from '../../src/features/review/guided-model.ts';
import type { JsonOptions } from '../../src/core/http.ts';
import { guidedFixture, nativeFixture, REVIEW_ID } from '../fixtures/review.ts';
async function flush() {
  for (let count = 0; count < 8; count++) {
    await Promise.resolve();
    await nextTick();
  }
}
function context(request: ReviewDetailContext['services']['request']): ReviewDetailContext {
  return {
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
}
function help() {}
test('production wrapper preserves a guided pending write through a same-record context update', async () => {
  help();
  localStorage.clear();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let writes = 0;
  const request = vi.fn(async (url: string) => {
    if (url === '/api/review/confirm') {
      writes++;
      await gate;
      return {};
    }
    if (url.startsWith('/api/transactions/'))
      return {
        transaction: {
          transaction_id: REVIEW_ID,
          entry_type: 'income',
          lifecycle_status: 'needs_review',
        },
        period: { period_key: '2026-Q3' },
      };
    return guidedFixture();
  });
  const first = context(request),
    root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, ReviewDetailView, first);
  await flush();
  const input = root.querySelector(
    '[data-decision-path="business_purpose"]',
  ) as HTMLTextAreaElement;
  input.value = 'Synthetic current purpose';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  root
    .querySelector('#review-form')!
    .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  const latest = context(
    vi.fn(async () => {
      throw new Error('Unexpected duplicate request');
    }),
  );
  latest.returnUrl = '/review?period=2026-Q2';
  expect(host.updateContext(latest)).toBe(true);
  await flush();
  expect(root.querySelector('[data-decision-path="business_purpose"]')).toBe(input);
  expect(input.value).toBe('Synthetic current purpose');
  expect((root.querySelector('#review-primary-button') as HTMLButtonElement).disabled).toBe(true);
  expect(writes).toBe(1);
  release();
  await flush();
  expect(latest.complete).toHaveBeenCalledWith('approve');
  expect(first.complete).not.toHaveBeenCalled();
  expect(localStorage.getItem('autonomo.review-draft:' + REVIEW_ID)).toBeNull();
  host.dispose();
  root.remove();
  vi.unstubAllGlobals();
  localStorage.clear();
});
test('production wrapper keeps native save ownership and continues preview through the current service', async () => {
  help();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const draft = nativeFixture();
  const calls: string[] = [];
  const request = async (url: string, options?: JsonOptions) => {
    calls.push(url);
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
      await gate;
      return { ...draft, payload: JSON.parse(String(options?.body)).payload, draft_version: 2 };
    }
    return draft;
  };
  const first = context(request),
    root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, ReviewDetailView, first);
  await flush();
  const form = root.querySelector('#wf-form')!;
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await flush();
  const latest = context(async (url: string) => {
    calls.push(url);
    if (url.endsWith('/preview'))
      return {
        preview_token: 'synthetic-token',
        supplier: 'Synthetic',
        period: '2026-Q3',
        currency: 'EUR',
        gross_minor: 12100,
        deductible_vat_minor: 2100,
        deductible_irpf_minor: 10000,
        future_depreciation_minor: 0,
        schedule: [],
      };
    if (url.endsWith('/confirm')) return { transaction_id: REVIEW_ID };
    throw new Error('Unexpected request');
  });
  host.updateContext(latest);
  await flush();
  expect(root.querySelector('#wf-form')).toBe(form);
  expect((form.querySelector('fieldset') as HTMLFieldSetElement).disabled).toBe(true);
  release();
  await flush();
  expect(calls.filter((url) => url.endsWith('/save'))).toHaveLength(1);
  expect(calls.filter((url) => url.endsWith('/preview'))).toHaveLength(1);
  (root.querySelector('#wf-confirm') as HTMLButtonElement).click();
  await flush();
  (root.querySelector('#wf-open-posted') as HTMLButtonElement).click();
  await flush();
  expect(latest.posted).toHaveBeenCalledWith(REVIEW_ID, '2026-Q3', false);
  expect(first.posted).not.toHaveBeenCalled();
  host.dispose();
  root.remove();
  vi.unstubAllGlobals();
});

test('guided refresh uses the replacement context when a delayed detail is now posted', async () => {
  help();
  localStorage.clear();
  let release!: (value: unknown) => void;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  let reads = 0;
  const first = context(async (url: string) => {
    if (url.startsWith('/api/transactions/'))
      return ++reads === 1
        ? {
            transaction: {
              transaction_id: REVIEW_ID,
              entry_type: 'income',
              lifecycle_status: 'needs_review',
            },
            period: { period_key: '2026-Q3' },
          }
        : gate;
    return guidedFixture();
  });
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, ReviewDetailView, first);
  await flush();
  (root.querySelector('#review-refresh-button') as HTMLButtonElement).click();
  await flush();
  const editor = root.querySelector('#review-form');
  const latest = context(
    vi.fn(async () => {
      throw new Error('Unexpected reread');
    }),
  );
  host.updateContext(latest);
  await flush();
  expect(root.querySelector('#review-form')).toBe(editor);
  release({
    transaction: { transaction_id: REVIEW_ID, entry_type: 'expense', lifecycle_status: 'posted' },
    period: { period_key: '2026-Q3' },
  });
  await flush();
  expect(latest.posted).toHaveBeenCalledWith(REVIEW_ID, '2026-Q3');
  expect(first.posted).not.toHaveBeenCalled();
  expect(latest.settled).toHaveBeenCalled();
  host.dispose();
  root.remove();
  vi.unstubAllGlobals();
  localStorage.clear();
});

test('guided issue edits follow backend IDs when the decision array is reordered', async () => {
  help();
  localStorage.clear();
  const fixture = guidedFixture();
  fixture.packet.state.issues = [
    { validation_issue_id: 'synthetic-first', issue_code: 'first' },
    { validation_issue_id: 'synthetic-second', issue_code: 'second' },
  ];
  fixture.packet.decision.issue_resolutions = [
    { issue_id: 'synthetic-second', action: null, reason: '' },
    { issue_id: 'synthetic-first', action: null, reason: '' },
  ];
  const setup = context(async (url: string) =>
    url.startsWith('/api/transactions/')
      ? {
          transaction: {
            transaction_id: REVIEW_ID,
            entry_type: 'income',
            lifecycle_status: 'needs_review',
          },
          period: { period_key: '2026-Q3' },
        }
      : fixture,
  );
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, ReviewDetailView, setup);
  await flush();
  const field = root.querySelector('.review-issue-card textarea') as HTMLTextAreaElement;
  expect(field.dataset.decisionPath).toBe('issue_resolutions.1.reason');
  field.value = 'Reason for the first issue';
  field.dispatchEvent(new Event('input', { bubbles: true }));
  await flush();
  const saved = JSON.parse(localStorage.getItem('autonomo.review-draft:' + REVIEW_ID)!);
  expect(saved.decision.issue_resolutions[1]).toMatchObject({
    issue_id: 'synthetic-first',
    reason: 'Reason for the first issue',
  });
  expect(saved.decision.issue_resolutions[0].reason).toBe('');
  host.dispose();
  root.remove();
  vi.unstubAllGlobals();
  localStorage.clear();
});
