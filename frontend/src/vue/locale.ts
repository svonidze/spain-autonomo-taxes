import { onScopeDispose, readonly, shallowRef } from 'vue';
import {
  getLocale,
  subscribeLocale,
  t as translate,
  type MessageId,
  type MessageValues,
} from '../core/i18n.ts';

type Args<K extends MessageId> = keyof MessageValues[K] extends never
  ? [values?: Record<string, never>]
  : [values: MessageValues[K]];
export function useLocale() {
  const locale = shallowRef(getLocale());
  onScopeDispose(
    subscribeLocale((value) => {
      locale.value = value;
    }),
  );
  function t<K extends MessageId>(id: K, ...args: Args<K>) {
    // The public signature preserves each generated key/parameter relationship.
    return (translate as (key: MessageId, values: unknown, locale: string) => string)(
      id,
      args[0],
      locale.value,
    );
  }
  return { locale: readonly(locale), t };
}
