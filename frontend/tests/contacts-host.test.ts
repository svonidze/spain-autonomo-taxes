import analytics from './fixtures/analytics.json' with { type: 'json' };
import { nextTick } from 'vue';
import { test, expect, vi } from 'vitest';
import ContactsView from '../src/features/contacts/ContactsView.vue';
import { mountView } from './support/mount.ts';
import type { ContactsContext } from '../src/features/contacts/model.ts';

test('host context replacement respects dirty and pending rename; saved names refresh the chart', async () => {
  vi.stubGlobal('AccountingHelp', {
    createScope: () => ({ id: 'owned', update() {}, dispose() {} }),
    label: () => 'Ready',
    summary: () => '',
    tone: () => 'positive',
    word: () => 'Details',
  });
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
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  let release!: (value: unknown) => void;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const id = '33333333-3333-4333-8333-333333333333';
  let name = 'Synthetic supplier';
  let chartReads = 0;
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  const request = vi.fn(async (url: string) => {
    if (url.startsWith('/api/analytics?')) {
      chartReads++;
      return analytics;
    }
    if (url.endsWith('/name-history')) return { changes: [] };
    if (url.endsWith('/rename')) {
      await gate;
      name = 'Synthetic corrected';
      return { display_name: name, row_version: 2, changed: true };
    }
    if (url === '/api/counterparties')
      return [
        {
          counterparty_id: id,
          display_name: name,
          row_version: 1,
          ui_context: { domain: 'counterparty', state: 'unknown' },
        },
      ];
    throw new Error('Unexpected request');
  });
  const context: ContactsContext = {
    contactId: null,
    period: '',
    services: { request, navigate: vi.fn() },
    settled: vi.fn(),
    detailResolved: vi.fn(),
    notify: vi.fn(),
    chartPeriod: '2026-Q2',
    restorePosition: vi.fn(),
    rememberPosition: vi.fn(),
  };
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, ContactsView, context);
  const flush = async () => {
    for (let index = 0; index < 5; index++) {
      await Promise.resolve();
      await nextTick();
    }
  };
  await flush();
  expect(chartReads).toBe(1);
  (root.querySelector('[data-counterparty-menu]') as HTMLButtonElement).click();
  await flush();
  (document.querySelector('#vue-counterparty-actions-menu button') as HTMLButtonElement).click();
  await flush();
  const input = document.querySelector('#vue-counterparty-name-input') as HTMLInputElement;
  input.value = 'Synthetic corrected';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  await nextTick();
  expect(host.updateContext({ ...context, contactId: id })).toBe(false);
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(input.value).toBe('Synthetic corrected');
  const form = document.querySelector('#vue-counterparty-name-dialog form')!;
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  await nextTick();
  expect(host.isBusy()).toBe(true);
  expect(host.updateContext({ ...context, contactId: id })).toBe(false);
  expect(confirm).toHaveBeenCalledTimes(1);
  release({});
  await flush();
  expect(chartReads).toBe(2);
  expect(root.textContent).toContain('Synthetic corrected');
  host.dispose();
  root.remove();
  vi.unstubAllGlobals();
});
