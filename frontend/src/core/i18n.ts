import { createIntl, createIntlCache, type IntlShape } from '@formatjs/intl';
import type { MessageFormatElement } from '@formatjs/icu-messageformat-parser';
import data from '../generated/catalogs.json' with { type: 'json' };
import type { Locale, MessageId, MessageValues } from '../generated/messages.ts';

export type { Locale, MessageId, MessageValues };
export interface LocaleSpec {
  code: string;
  name: string;
  intl: string;
  direction: 'ltr' | 'rtl';
  quarterNumbers?: string[];
}
interface CatalogData {
  registry: LocaleSpec[];
  ast: Record<string, Record<string, MessageFormatElement[]>>;
  arguments: Record<string, Record<string, 'string' | 'number' | 'date'>>;
  keys: string[];
}
const catalogs = data as CatalogData;
export const localeRegistry: readonly LocaleSpec[] = catalogs.registry;
export const defaultLocale: Locale = 'ru';
export const localeStorageKey = 'autonomo.locale';
export const messageIds: readonly string[] = catalogs.keys;
const cache = createIntlCache();
const formatters = new Map<string, IntlShape>();
const subscribers = new Set<(locale: Locale) => void>();
let selected: Locale = defaultLocale;

export function isLocale(value: unknown): value is Locale {
  return typeof value === 'string' && localeRegistry.some((locale) => locale.code === value);
}
export function normalizeLocale(value: unknown): Locale {
  return isLocale(value) ? value : defaultLocale;
}
export function localeTag(value: unknown): string {
  return localeRegistry.find((locale) => locale.code === normalizeLocale(value))!.intl;
}
export function quarterNumber(quarter: number, locale: unknown): string {
  return (
    localeRegistry.find((item) => item.code === normalizeLocale(locale))?.quarterNumbers?.[
      quarter - 1
    ] ?? String(quarter)
  );
}
export function getLocale(): Locale {
  return selected;
}
export function setLocale(value: unknown): Locale {
  const locale = normalizeLocale(value);
  if (selected !== locale) {
    selected = locale;
    for (const callback of subscribers) callback(locale);
  }
  return selected;
}
export function subscribeLocale(callback: (locale: Locale) => void): () => void {
  subscribers.add(callback);
  return () => subscribers.delete(callback);
}
export function loadLocale(storage: () => Pick<Storage, 'getItem'>): Locale {
  try {
    return normalizeLocale(storage().getItem(localeStorageKey));
  } catch {
    return defaultLocale;
  } // Storage can be disabled independently of the UI.
}
export function storeLocale(storage: () => Pick<Storage, 'setItem'>, value: unknown): void {
  try {
    storage().setItem(localeStorageKey, normalizeLocale(value));
  } catch {
    /* The selected in-memory locale remains usable. */
  }
}

function formatter(locale: string): IntlShape {
  let intl = formatters.get(locale);
  if (!intl) {
    const tag = localeTag(locale);
    intl = createIntl(
      {
        locale: tag,
        defaultLocale: tag,
        messages: catalogs.ast[locale],
        onError: (error) => {
          throw error;
        },
      },
      cache,
    );
    formatters.set(locale, intl);
  }
  return intl;
}

/** Dynamic-key entry point for legacy adapters and guarded server-code mappings. */
export function formatMessage(
  id: string,
  values: Record<string, unknown> = {},
  locale: unknown = selected,
): string {
  const requested = normalizeLocale(locale);
  const actual = Object.hasOwn(catalogs.ast[requested], id) ? requested : defaultLocale;
  if (!Object.hasOwn(catalogs.ast[actual], id)) return id;
  const normalized = Object.fromEntries(
    Object.entries(values).map(([key, value]) => [
      key,
      value instanceof Date ? value.getTime() : typeof value === 'number' ? value : String(value),
    ]),
  );
  return formatter(actual).formatMessage({ id }, normalized);
}

type Parameters<K extends MessageId> = keyof MessageValues[K] extends never
  ? [values?: Record<string, never>, locale?: Locale]
  : [values: MessageValues[K], locale?: Locale];
export function t<K extends MessageId>(id: K, ...[values, locale]: Parameters<K>): string {
  return formatMessage(id, values, locale);
}
export type UiMessage = {
  [K in MessageId]: { key: K } & (keyof MessageValues[K] extends never
    ? { params?: Record<string, never> }
    : { params: MessageValues[K] });
}[MessageId];
type MessageParameters<K extends MessageId> = keyof MessageValues[K] extends never
  ? [params?: Record<string, never>]
  : [params: MessageValues[K]];
export function message<K extends MessageId>(key: K, ...[params]: MessageParameters<K>): UiMessage {
  // The generic constructor ties the key to its generated argument signature.
  return { key, params } as UiMessage;
}
export function messageText(
  value: UiMessage | string | null | undefined,
  locale: unknown = selected,
): string {
  return value && typeof value === 'object'
    ? formatMessage(value.key, value.params, locale)
    : (value ?? '');
}
