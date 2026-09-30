import { test, expect } from 'vitest';
import { item, preview, result, postItems } from '../../src/features/posting/normalize.ts';
import {
  postingState,
  markStale,
  clearRefresh,
  refreshToken,
} from '../../src/features/posting/state.ts';
import reference from '../fixtures/posting-expected.json' with { type: 'json' };
test('posting aliases, zero amounts and partial identities retain reviewed normalization', () => {
  for (const row of reference.items) expect(item(row.raw)).toEqual(row.expected);
  for (const row of reference.previews) expect(preview(row.raw, '2026-Q3')).toEqual(row.expected);
  const ready = reference.items.map((row) => item(row.raw));
  for (const row of reference.results) expect(result(row.raw, ready)).toEqual(row.expected);
  expect(postItems(ready)).toEqual(reference.postItems);
});
test('an earlier calculation cannot clear a newer or different-period stale marker', () => {
  postingState.stale.clear();
  const first = markStale('2026-Q2'),
    second = markStale('2026-Q2', 'Synthetic later write');
  markStale('2026-Q3');
  clearRefresh('2026-Q2', first);
  expect(refreshToken('2026-Q2')).toBe(second);
  clearRefresh('2026-Q3', second);
  expect(refreshToken('2026-Q3')).toBeDefined();
  clearRefresh('2026-Q2', second);
  expect(refreshToken('2026-Q2')).toBeUndefined();
  postingState.stale.clear();
});

test('stale refresh markers keep a re-translatable descriptor', async () => {
  const { messageText } = await import('../../src/core/i18n.ts');
  postingState.stale.clear();
  const marker = markStale('2026-Q2', { key: 'review.postInterrupted' });
  expect(messageText(marker.error, 'ru')).not.toBe(messageText(marker.error, 'en'));
  expect(messageText(markStale('2026-Q3', 'Synthetic raw text').error, 'en')).toBe(
    'Synthetic raw text',
  );
  postingState.stale.clear();
});
