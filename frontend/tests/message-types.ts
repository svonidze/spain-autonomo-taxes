import { t, message, type UiMessage } from '../src/core/i18n.ts';

// Compiled by typecheck, not executed: regressions make @ts-expect-error fail.
function messageTypes() {
  t('common.loading');
  t('expense.sourceDate', { date: 'synthetic' });
  // @ts-expect-error Unknown semantic ID.
  t('missing.message');
  // @ts-expect-error Named value is required.
  t('expense.sourceDate');
  // @ts-expect-error Incorrect parameter name.
  t('expense.sourceDate', { day: 'synthetic' });
  // @ts-expect-error Plural counts must be numeric.
  t('common.counts.operations', { count: 'two' });
  // @ts-expect-error Descriptors are locale independent.
  message('common.loading', {}, 'en');
  // @ts-expect-error Descriptors also require the matching named arguments.
  const invalid: UiMessage = { key: 'expense.sourceDate' };
  void invalid;
}
void messageTypes;
