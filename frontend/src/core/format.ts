import {localeTag} from './i18n.ts';

export function escapeHtml(value: unknown): string {
  return String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;');
}
export function eur(value: unknown, locale: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  const number = Number(value);
  if (!Number.isFinite(number)) return '—';
  return new Intl.NumberFormat(localeTag(locale), {style: 'currency', currency: 'EUR', minimumFractionDigits: 2}).format(number);
}
export function formatDateText(value: unknown, locale: unknown): string {
  if (!value) return '—';
  const date = new Date(`${String(value)}T12:00:00`);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(localeTag(locale), {day: '2-digit', month: 'short', year: 'numeric'}).format(date);
}
export function formatDateHtml(value: unknown, locale: unknown): string {
  return escapeHtml(formatDateText(value, locale));
}
export function formatDateTime(value: unknown, locale: unknown): string {
  if (!value) return '—';
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return formatDateHtml(text, locale);
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return new Intl.DateTimeFormat(localeTag(locale), {day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'}).format(date);
}
