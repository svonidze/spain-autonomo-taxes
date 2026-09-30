import { createApp, nextTick } from 'vue';
import { afterEach, expect, test, vi } from 'vitest';
import RetaCheckCard from '../../src/features/overview/RetaCheckCard.vue';
import TaxesView from '../../src/features/overview/TaxesView.vue';
import { mountView } from '../support/mount.ts';
import { setLocale } from '../../src/core/i18n.ts';
import { formatDateText } from '../../src/core/format.ts';
import type { OverviewContext, RetaCheck } from '../../src/features/overview/model.ts';
import en from '../../src/locales/en/reta.json' with { type: 'json' };
import ru from '../../src/locales/ru/reta.json' with { type: 'json' };

const known = (overrides: Partial<RetaCheck> = {}): RetaCheck => ({
  year: 2026,
  through: '2026-06-30',
  status: 'ok',
  reasons: [],
  income: { monthly_average_minor: 126398 },
  bracket: { table: 'general', tramo: 1, min_base_minor: 118954, max_base_minor: 170000 },
  boundary_sensitive: false,
  average_provisional_base_minor: 130000,
  estimated_additional_minor: 0,
  estimated_refund_minor: 0,
  additional_locked_in_minor: 0,
  next_base_change: { effective_on: '2026-09-01', request_by: '2026-08-31' },
  elections: [
    {
      effective_from: '2025-03-10',
      regime: 'tarifa_plana',
      monthly_base_minor: null,
      worker_kind: 'individual',
      source_reference: 'Synthetic TGSS resolution A',
    },
    {
      effective_from: '2026-03-10',
      regime: 'base',
      monthly_base_minor: 130000,
      worker_kind: 'individual',
      source_reference: 'Synthetic TGSS resolution B',
    },
  ],
  ...overrides,
});
const unmounts: (() => void)[] = [];
afterEach(() => {
  unmounts.splice(0).forEach((unmount) => unmount());
  setLocale('ru');
});
const settle = async () => {
  await new Promise((resolve) => setTimeout(resolve));
  await nextTick();
};
async function mount(result: RetaCheck | Error) {
  setLocale('en');
  const root = document.createElement('div'),
    urls: string[] = [];
  document.body.append(root);
  const app = createApp(RetaCheckCard, {
    year: '2026',
    request: async (url: string) => {
      urls.push(url);
      if (result instanceof Error) throw result;
      return result;
    },
  });
  app.mount(root);
  unmounts.push(() => {
    app.unmount();
    root.remove();
  });
  await settle();
  const text = (selector: string) => root.querySelector(selector)?.textContent?.trim();
  return { root, urls, text };
}
const inRussian = async () => {
  setLocale('ru');
  await nextTick();
};

test('ok shows the tramo, known zero estimates and read-only bases in EN and RU', async () => {
  const { root, urls, text } = await mount(known());
  expect(urls).toEqual(['/api/reta-check?year=2026']);
  expect(root.querySelector('.badge')!.className).toContain('status-positive');
  expect(text('.badge')).toBe(en['reta.status.ok']);
  expect(text('h2')).toBe('RETA bracket check 2026');
  expect(root.textContent).toContain(en['reta.disclaimer']);
  expect(text('[role="status"]')).toContain(`to ${formatDateText('2026-06-30', 'en')}.`);
  expect(text('dl')).toContain('General table, tramo 1');
  expect(text('dl')).toContain('€1,189.54 to €1,700.00');
  expect(text('dl')).toContain('€1,263.98');
  expect(text('[data-reta="additional"]')).toBe('€0.00');
  expect(text('[data-reta="refund"]')).toBe('€0.00');
  expect(text('[data-reta="locked"]')).toBe('€0.00');
  expect(text('dl')).toContain(
    `${formatDateText('2026-09-01', 'en')}, request by ${formatDateText('2026-08-31', 'en')}`,
  );
  expect(root.querySelector('.posting-warning')).toBeNull();
  const rows = [...root.querySelectorAll('tbody tr')].map((row) =>
    [...row.querySelectorAll('td')].map((cell) => cell.textContent),
  );
  expect(rows[0]!.slice(1)).toEqual([
    'Tarifa plana',
    '—',
    'Individual',
    'Synthetic TGSS resolution A',
  ]);
  expect(rows[1]![2]).toBe('€1,300.00');
  expect(root.textContent).toContain('autonomo-tax reta base add');
  expect(root.querySelector('input, button, form')).toBeNull();

  await inRussian();
  expect(text('.badge')).toBe(ru['reta.status.ok']);
  expect(text('h2')).toBe('Проверка tramo RETA за 2026');
  expect(text('dl')).toContain('Общая таблица, tramo 1');
  expect(text('[data-reta="additional"]')).toMatch(/^0,00\s€$/);
  expect(root.querySelector('tbody td:nth-child(2)')!.textContent).toBe('Tarifa plana');
  expect(root.querySelector('tbody td:nth-child(4)')!.textContent).toBe('Индивидуальный');
});

test('below keeps the additional payment, refund and locked-in amounts separate', async () => {
  const { root, text } = await mount(
    known({
      status: 'below_bracket',
      bracket: { table: 'general', tramo: 8, min_base_minor: 143791, max_base_minor: 400000 },
      boundary_sensitive: true,
      estimated_additional_minor: 92030,
      additional_locked_in_minor: 46015,
    }),
  );
  expect(root.querySelector('.badge')!.className).toContain('status-attention');
  expect(text('.badge')).toBe(en['reta.status.below_bracket']);
  expect(text('[role="status"]')).toContain(en['reta.statusDetail.below_bracket']);
  expect(text('[data-reta="additional"]')).toBe('€920.30');
  expect(text('[data-reta="refund"]')).toBe('€0.00');
  expect(text('[data-reta="locked"]')).toBe('€460.15');
  expect(text('.posting-warning')).toBe(en['reta.boundarySensitive']);
  await inRussian();
  expect(text('[data-reta="additional"]')).toMatch(/^920,30\s€$/);
  expect(text('[data-reta="locked"]')).toMatch(/^460,15\s€$/);
  expect(text('.posting-warning')).toBe(ru['reta.boundarySensitive']);
});

test('unknown lists translated reasons and never takes the success tone', async () => {
  const empty = {
    through: null,
    income: null,
    bracket: null,
    boundary_sensitive: null,
    average_provisional_base_minor: null,
    estimated_additional_minor: null,
    estimated_refund_minor: null,
    additional_locked_in_minor: null,
    next_base_change: null,
    elections: [],
  };
  const { root, text } = await mount(
    known({
      ...empty,
      status: 'unknown',
      reasons: ['profile_unavailable', 'table_unavailable', 'future_reason'],
    }),
  );
  const badge = root.querySelector('.badge')!;
  expect(badge.className).toContain('status-neutral');
  expect(badge.className).not.toContain('status-positive');
  expect(text('.badge')).toBe(en['reta.status.unknown']);
  const reasons = () =>
    [...root.querySelectorAll('.reta-check-reasons li')].map((li) => li.textContent);
  expect(reasons()).toEqual([
    en['reta.reasons.profile_unavailable'],
    en['reta.reasons.table_unavailable'],
    'Unrecognised reason: future_reason',
  ]);
  expect(text('[role="status"]')).toBe(en['reta.statusDetail.unknown']);
  expect(root.querySelector('dl')).toBeNull();
  expect(root.textContent).toContain(en['reta.basesEmpty']);
  await inRussian();
  expect(reasons()).toEqual([
    ru['reta.reasons.profile_unavailable'],
    ru['reta.reasons.table_unavailable'],
    'Неизвестная причина: future_reason',
  ]);

  const odd = await mount(known({ ...empty, status: 'maybe_ok' }));
  expect(odd.root.querySelector('.badge')!.className).toContain('status-neutral');
  expect(odd.text('.badge')).toBe(en['reta.status.unknown']);
});

test('null estimates read as a dash, distinct from a known zero', async () => {
  const { root, text } = await mount(
    known({
      status: 'unknown',
      reasons: ['base_missing'],
      average_provisional_base_minor: null,
      estimated_additional_minor: null,
      estimated_refund_minor: null,
      additional_locked_in_minor: null,
    }),
  );
  expect(root.querySelector('.badge')!.className).toContain('status-neutral');
  expect(text('dl')).toContain('General table, tramo 1');
  for (const key of ['additional', 'refund', 'locked'])
    expect(text(`[data-reta="${key}"]`)).toBe('—');
  expect(text('dl')).toContain('€1,263.98');
  await inRussian();
  for (const key of ['additional', 'refund', 'locked'])
    expect(text(`[data-reta="${key}"]`)).toBe('—');
});

test('a failed request is reported in the card and can be retried', async () => {
  const { root, urls, text } = await mount(new Error('Synthetic upgrade required'));
  expect(text('[role="alert"]')).toContain('Could not load data');
  expect(text('[role="alert"]')).toContain('Synthetic upgrade required');
  root.querySelector<HTMLButtonElement>('[role="alert"] button')!.click();
  await settle();
  expect(urls).toHaveLength(2);
});

test('a period change remounts the card: one fetch per mount, and a late reply is ignored', async () => {
  setLocale('en');
  // jsdom has no dialog methods; ChartHost closes its dialog on unmount.
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.open = false;
    },
  });
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  );
  let releaseOld!: (value: RetaCheck) => void;
  const reta: string[] = [];
  const request = vi.fn((url: string): Promise<unknown> => {
    if (url.startsWith('/api/taxes?'))
      return Promise.resolve({ period: decodeURIComponent(url.split('=')[1]!), obligations: [] });
    if (url.startsWith('/api/reta-check?')) {
      reta.push(url);
      return url.endsWith('2026')
        ? new Promise<RetaCheck>((resolve) => {
            releaseOld = resolve;
          })
        : Promise.resolve(
            known({ year: 2027, status: 'unknown', reasons: ['window_empty'], bracket: null }),
          );
    }
    return new Promise(() => {}); // analytics stays pending; the chart is not under test
  });
  const context = (period: string): OverviewContext => ({
    view: 'taxes',
    period,
    services: { request, navigate: vi.fn() },
    copyTarget: null,
    settled: vi.fn(),
    refreshCalculation: vi.fn(async () => {}),
    copy: vi.fn(),
    notify: vi.fn(),
  });
  const root = document.createElement('div');
  document.body.append(root);
  const host = mountView(root, TaxesView, context('2026-Q4'));
  unmounts.push(() => {
    host.dispose();
    root.remove();
    vi.unstubAllGlobals();
  });
  await settle();
  expect(reta).toEqual(['/api/reta-check?year=2026']);
  expect(host.updateContext(context('2027-Q1'))).toBe(true);
  await settle();
  await settle();
  expect(reta).toEqual(['/api/reta-check?year=2026', '/api/reta-check?year=2027']);
  const card = () => root.querySelector('section.reta-check')!;
  expect(card().querySelector('h2')!.textContent).toBe('RETA bracket check 2027');
  expect(card().textContent).toContain(en['reta.reasons.window_empty']);
  releaseOld(known());
  await settle();
  expect(card().querySelector('.badge')!.textContent).toBe(en['reta.status.unknown']);
  expect(card().querySelector('dl')).toBeNull();
  setLocale('ru');
  await nextTick();
  expect(reta).toHaveLength(2);
});
