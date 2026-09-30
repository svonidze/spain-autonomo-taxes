import { nextTick } from 'vue';
import { test, expect, vi } from 'vitest';
import { mountView } from '../support/mount.ts';
import ReviewOverviewView from '../../src/features/posting/ReviewOverviewView.vue';
import { postingState } from '../../src/features/posting/state.ts';
import type { ReviewOverviewContext } from '../../src/features/posting/model.ts';
const ID = '11111111-1111-4111-8111-111111111111';
const ready = {
  transaction_id: ID,
  expected_row_version: 3,
  description: 'Synthetic frozen row',
  preview_bucket: 'ready',
  amount_eur: '12.00',
};
function setup() {
  postingState.busy.value = false;
  postingState.stale.clear();
  postingState.lastResult.value = undefined;
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.open = true;
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.open = false;
    },
  });
}
async function flush() {
  for (let count = 0; count < 8; count++) {
    await Promise.resolve();
    await nextTick();
  }
}
const preview = (period: string) => ({
  period,
  period_status: 'open',
  ready: [ready],
  deferred: [],
  blocked: [],
});
function context(
  period: string,
  request: ReviewOverviewContext['services']['request'],
): ReviewOverviewContext {
  return {
    period,
    tab: 'posting',
    services: { request, navigate: vi.fn() },
    selectTab: vi.fn(),
    settled: vi.fn(),
    refreshCalculation: vi.fn(async () => ({})),
    notify: vi.fn(),
  };
}
for (const same of [true, false])
  test(`frozen batch remains owned when context changes to ${same ? 'the same' : 'another'} period`, async () => {
    setup();
    let release!: (value: unknown) => void;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    const writes: unknown[] = [];
    const first = context('2026-Q3', async (url, options) => {
      if (url === '/api/review/post-ready') {
        writes.push(JSON.parse(String(options?.body)));
        return gate;
      }
      return url.includes('posting-preview') ? preview('2026-Q3') : [];
    });
    const root = document.createElement('div');
    document.body.append(root);
    const host = mountView(root, ReviewOverviewView, first);
    await flush();
    (root.querySelector('[data-posting-action="open-confirm"]') as HTMLButtonElement).click();
    await flush();
    const dialog = document.querySelector('#vue-posting-confirm-dialog') as HTMLDialogElement;
    dialog
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await flush();
    const latest = context(same ? '2026-Q3' : '2026-Q2', async (url) =>
      url.includes('posting-preview')
        ? { ...preview(same ? '2026-Q3' : '2026-Q2'), ready: [] }
        : [],
    );
    host.updateContext(latest);
    await flush();
    expect(postingState.busy.value).toBe(true);
    if (same) {
      expect(dialog.open).toBe(true);
      expect(dialog.textContent).toContain('Synthetic frozen row');
    } else expect(dialog.open).toBe(false);
    release({
      status: 'completed',
      summary: { posted_count: 1 },
      results: [{ transaction_id: ID, outcome: 'posted' }],
    });
    await flush();
    expect(writes).toEqual([
      { period: '2026-Q3', items: [{ transaction_id: ID, expected_row_version: 3 }] },
    ]);
    expect(first.refreshCalculation).not.toHaveBeenCalled();
    if (same) {
      expect(latest.refreshCalculation).toHaveBeenCalledWith('2026-Q3');
      expect(postingState.stale.size).toBe(0);
    } else {
      expect(latest.refreshCalculation).not.toHaveBeenCalled();
      expect(postingState.stale.has('2026-Q3')).toBe(true);
      expect(postingState.stale.has('2026-Q2')).toBe(false);
    }
    expect(postingState.lastResult.value?.period).toBe('2026-Q3');
    expect(postingState.busy.value).toBe(false);
    host.dispose();
    root.remove();
    postingState.stale.clear();
    postingState.lastResult.value = undefined;
    vi.unstubAllGlobals();
  });
