import { nextTick } from 'vue';
import { expect, test, vi } from 'vitest';
import AeatDocumentsView from '../../src/features/aeat-documents/AeatDocumentsView.vue';
import type { AeatDocumentsContext } from '../../src/features/aeat-documents/model.ts';
import { mountView } from '../support/mount.ts';

async function flush() {
  for (let index = 0; index < 5; index++) {
    await Promise.resolve();
    await nextTick();
  }
}

test('AEAT registry filters rows and submits a sourced status transition', async () => {
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
  const requests: Array<{ url: string; options?: RequestInit }> = [];
  const rows = [
    {
      record_source: 'aeat_document' as const,
      record_id: 'record-036',
      aeat_case_id: '11111111-1111-4111-8111-111111111111',
      case_row_version: 1,
      title: 'Synthetic ROI',
      form_code: '036',
      procedure_kind: 'roi_registration',
      procedure_code: 'G322',
      document_kind: 'submission_receipt',
      status: 'submitted',
      occurred_at: '2032-04-03T10:20:30Z',
      submission_reference: '2032C3600000001A',
      source_available: true,
      content_url: '/api/document/11111111-1111-4111-8111-111111111111/content',
      status_history: [],
    },
    {
      record_source: 'filing_snapshot' as const,
      record_id: 'record-303',
      title: 'Modelo 303 · 2032-Q1',
      form_code: '303',
      procedure_kind: 'periodic_filing',
      document_kind: 'submission_receipt',
      status: 'submitted',
      occurred_at: '2032-04-01T09:00:00Z',
      source_available: true,
      content_url: '/api/aeat-filings/22222222-2222-4222-8222-222222222222/content',
    },
  ];
  const request = vi.fn(async (url: string, options?: RequestInit) => {
    requests.push({ url, options });
    if (url === '/api/aeat-documents') return { rows, total: rows.length };
    if (url.endsWith('/status')) return { current_status: 'approved' };
    throw new Error(`Unexpected request ${url}`);
  });
  const context: AeatDocumentsContext = {
    services: { request, navigate: vi.fn() },
    settled: vi.fn(),
    notify: vi.fn(),
  };
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, AeatDocumentsView, context);
  await flush();
  expect(root.textContent).toContain('Synthetic ROI');
  expect(root.textContent).toContain('Modelo 303');

  const search = root.querySelector('input[type="search"]') as HTMLInputElement;
  search.value = '036';
  search.dispatchEvent(new Event('input', { bubbles: true }));
  await nextTick();
  expect(root.textContent).toContain('Synthetic ROI');
  expect(root.textContent).not.toContain('Modelo 303 · 2032-Q1');

  (root.querySelector('.aeat-actions button') as HTMLButtonElement).click();
  await nextTick();
  const dialog = root.querySelector('dialog') as HTMLDialogElement;
  expect(dialog.open).toBe(true);
  const evidence = dialog.querySelector(
    'input[required]:not([type="datetime-local"])',
  ) as HTMLInputElement;
  evidence.value = 'synthetic-vies-check';
  evidence.dispatchEvent(new Event('input', { bubbles: true }));
  (dialog.querySelector('form') as HTMLFormElement).dispatchEvent(
    new Event('submit', { bubbles: true, cancelable: true }),
  );
  await flush();
  const statusRequest = requests.find(({ url }) => url.endsWith('/status'))!;
  expect(statusRequest.options?.method).toBe('POST');
  expect(JSON.parse(String(statusRequest.options?.body))).toMatchObject({
    evidence_reference: 'synthetic-vies-check',
    expected_row_version: 1,
  });
  host.dispose();
  root.remove();
});
