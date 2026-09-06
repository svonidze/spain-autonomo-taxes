import { ApiError } from './http.ts';
import { formatMessage, messageIds } from './i18n.ts';
/** Older/newer servers may send only diagnostic text or unknown additive IDs. */
export function errorMessage(value: unknown, locale: unknown): string {
  if (value instanceof ApiError && value.messageCode && messageIds.includes(value.messageCode)) {
    try {
      return formatMessage(value.messageCode, value.params, locale);
    } catch {
      return value.message;
    } // Additive metadata must not hide a server diagnostic.
  }
  const text = value instanceof Error ? value.message : String(value || '');
  try {
    return messageIds.includes(text) ? formatMessage(text, {}, locale) : text;
  } catch {
    return text;
  }
}
