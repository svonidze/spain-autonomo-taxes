import { expect, test, vi } from 'vitest';
import { router } from '../src/shell/router.ts';
import { connectHelp, shellHelp } from '../src/shell/help-history.ts';
import type { HelpNavigation } from '../src/help/registry.ts';
test('same-URL help cleanup preserves Router and contact custom state', async () => {
  let navigation!: HelpNavigation;
  vi.spyOn(shellHelp(), 'setHistoryAdapter').mockImplementation((value) => {
    navigation = value;
  });
  await router.replace({
    path: '/contacts/11111111-1111-4111-8111-111111111111',
    state: { contactGlobalPeriod: '2026-Q2' },
  });
  connectHelp(vi.fn());
  navigation.push('synthetic-help');
  await vi.waitFor(() => expect(history.state.accountingHelp).toBe('synthetic-help'));
  const position = history.state.position;
  expect(history.state.contactGlobalPeriod).toBe('2026-Q2');
  navigation.clear();
  await vi.waitFor(() => expect(history.state.accountingHelp).toBeNull());
  expect(history.state.contactGlobalPeriod).toBe('2026-Q2');
  expect(history.state.position).toBe(position);
  expect(router.currentRoute.value.path).toBe('/contacts/11111111-1111-4111-8111-111111111111');
});
