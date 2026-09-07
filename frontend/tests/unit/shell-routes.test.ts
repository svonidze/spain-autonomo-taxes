import { expect, test } from 'vitest';
import {
  canonicalId,
  parseRoute,
  routeUrl,
  safeReturnUrl,
  copyTarget,
} from '../../src/shell/routes.ts';
import { retentionPayload } from '../../src/features/settings/model.ts';
const periods = [
  { period_key: '2026-Q3', status: 'open' },
  { period_key: '2026-Q2', status: 'open' },
];
test('route paths and safe returns retain quarter and nested query contracts', () => {
  const id = '11111111-1111-4111-8111-111111111111';
  const back = '/expenses?period=2026-Q3&q=Two words';
  const url = routeUrl('expense-detail', '2026-Q2', { id, returnTo: back });
  expect(new URL(url, 'https://app.invalid').searchParams.get('returnTo')).toBe(back);
  expect(parseRoute(`/contacts/${id}`)).toEqual({ view: 'contact-detail', id });
  expect(parseRoute('/unknown')).toBeNull();
  expect(safeReturnUrl(back, '2026-Q2', periods)).toBe('/expenses?period=2026-Q3&q=Two+words');
  for (const value of [
    '//other.invalid/expenses',
    '/expenses?period=2026-Q3&period=2026-Q2',
    '/expenses?period=2026-Q3#other',
    '/settings',
    '/expenses?period=2030-Q1',
  ])
    expect(safeReturnUrl(value, '2026-Q2', periods)).toBe('/expenses?period=2026-Q2');
  expect(safeReturnUrl(`/contacts/${id}?period=2026-Q1`, '2026-Q2', periods)).toBe(
    `/contacts/${id}?period=2026-Q1`,
  );
});
test('copy target chooses an open current quarter before the existing fallback', () => {
  expect(copyTarget(periods, true, new Date(2026, 8, 1))).toBe('2026-Q3');
  expect(copyTarget(periods, false)).toBeNull();
  expect(copyTarget(periods, true, new Date(2027, 0, 1))).toBe('2026-Q3');
});
test('blank backup limits remain operator managed with explicit pruning consent', () => {
  expect(retentionPayload('', '3', 'synthetic-revision')).toEqual({
    expected_revision: 'synthetic-revision',
    daily_keep: null,
    monthly_keep: 3,
    confirm_local_pruning: true,
  });
});

test('detail ids are canonicalized the way the server does', () => {
  const canonical = 'abcdef01-2345-4678-8abc-def012345678';
  const compact = canonical.replaceAll('-', '').toUpperCase();
  expect(canonicalId(canonical)).toBe(canonical);
  expect(canonicalId(compact)).toBe(canonical);
  expect(parseRoute(`/expenses/${canonical.toUpperCase()}`)).toEqual({
    view: 'expense-detail',
    id: canonical,
  });
  expect(parseRoute(`/review/${compact}`)).toEqual({ view: 'review', id: canonical });
  expect(parseRoute(`/contacts/${compact}`)).toEqual({ view: 'contact-detail', id: canonical });
  expect(safeReturnUrl(`/contacts/${compact}?period=2026-Q1`, '2026-Q2', periods)).toBe(
    `/contacts/${canonical}?period=2026-Q1`,
  );
});

test('UUID normalization handles irregular hyphens without repairing invalid IDs', () => {
  const canonical = 'abcdef01-2345-4678-8abc-def012345678';
  const variants = [
    canonical,
    canonical.toUpperCase(),
    canonical.replaceAll('-', ''),
    'ABCDEF01-234546788ABCDEF012345678',
    'abcdef012345-4678-8abc-def012345678',
  ];
  for (const value of variants) {
    expect(canonicalId(value)).toBe(canonical);
    for (const path of ['contacts', 'expenses', 'review']) {
      expect(parseRoute(`/${path}/${value}`)?.id).toBe(canonical);
    }
    expect(safeReturnUrl(`/contacts/${value}?period=2026-Q1`, '2026-Q2', periods)).toBe(
      `/contacts/${canonical}?period=2026-Q1`,
    );
  }
  for (const invalid of [
    'abcdef01-2345-4678-8abc-def01234567',
    'abcdef01-2345-4678-8abc-def0123456789',
    'gbcdef01-2345-4678-8abc-def012345678',
  ]) {
    expect(canonicalId(invalid)).toBe(invalid);
  }
});

test('generated detail URLs canonicalize ids regardless of the caller', () => {
  const canonical = 'abcdef01-2345-4678-8abc-def012345678';
  expect(
    routeUrl('expense-detail', '2026-Q2', { id: canonical.replaceAll('-', '').toUpperCase() }),
  ).toBe(`/expenses/${canonical}?period=2026-Q2`);
  expect(routeUrl('review', '2026-Q2', { id: canonical.toUpperCase() })).toBe(
    `/review/${canonical}?period=2026-Q2`,
  );
});
