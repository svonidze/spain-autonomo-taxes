export const views = [
  'dashboard',
  'income',
  'expenses',
  'review',
  'assets',
  'taxes',
  'contacts',
  'settings',
] as const;
export type ListView = (typeof views)[number];
export type View = ListView | 'contact-detail' | 'expense-detail';
export interface ScreenRoute {
  view: View;
  id?: string;
}
export interface Period {
  period_key: string;
  status?: string;
}
/** The server canonicalizes UUIDs (lowercase, hyphenated); compare and link in the same form. */
export function canonicalId(value: string): string {
  const id = value.toLowerCase();
  return /^[0-9a-f]{32}$/.test(id)
    ? `${id.slice(0, 8)}-${id.slice(8, 12)}-${id.slice(12, 16)}-${id.slice(16, 20)}-${id.slice(20)}`
    : id;
}
export function parseRoute(path: string): ScreenRoute | null {
  const detail = /^\/(contacts|expenses|review)\/([0-9a-fA-F-]{32,36})$/.exec(
    path.split('?')[0].split('#')[0],
  );
  if (detail)
    return {
      view:
        detail[1] === 'contacts'
          ? 'contact-detail'
          : detail[1] === 'expenses'
            ? 'expense-detail'
            : 'review',
      id: canonicalId(detail[2]),
    };
  const view = path.slice(1) as ListView;
  return views.includes(view) ? { view } : null;
}
export function routeUrl(
  view: View,
  period = '',
  options: { id?: string; q?: string; tab?: string; returnTo?: string } = {},
) {
  const root =
    view === 'contact-detail' ? 'contacts' : view === 'expense-detail' ? 'expenses' : view;
  const path = `/${root}${options.id ? `/${encodeURIComponent(options.id)}` : ''}`;
  const query = new URLSearchParams();
  if (period && view !== 'settings' && view !== 'contacts') query.set('period', period);
  if (view === 'expenses' && options.q) query.set('q', options.q);
  if (view === 'review' && !options.id && options.tab && options.tab !== 'queue')
    query.set('tab', options.tab);
  if (options.id && options.returnTo) query.set('returnTo', options.returnTo);
  return `${path}${query.size ? `?${query}` : ''}`;
}
export function knownPeriod(search: string, periods: Period[]) {
  const value = (new URLSearchParams(search).get('period') || '').trim().toUpperCase();
  return periods.some((row) => row.period_key === value) ? value : null;
}
export function safeReturnUrl(
  value: string | null,
  period: string,
  periods: Period[],
  fallback: ListView = 'expenses',
) {
  const defaultUrl = routeUrl(fallback, period);
  if (!value || !value.startsWith('/') || value.startsWith('//') || value.includes('\\'))
    return defaultUrl;
  try {
    const url = new URL(value, 'https://app.invalid');
    if (url.origin !== 'https://app.invalid' || url.hash) return defaultUrl;
    const contact = /^\/contacts\/([0-9a-fA-F-]{32,36})$/.exec(url.pathname);
    const allowed = contact
      ? ['period']
      : url.pathname === '/expenses'
        ? ['period', 'q']
        : ['/review', '/dashboard'].includes(url.pathname)
          ? ['period']
          : [];
    if (
      !allowed.length ||
      [...url.searchParams.keys()].some(
        (key) => !allowed.includes(key) || url.searchParams.getAll(key).length !== 1,
      )
    )
      return defaultUrl;
    if (contact) {
      const local = url.searchParams.get('period') || '';
      return !local || /^\d{4}-Q[1-4]$/.test(local)
        ? routeUrl('contact-detail', local, { id: canonicalId(contact[1]) })
        : defaultUrl;
    }
    const source = knownPeriod(url.search, periods);
    return source
      ? routeUrl(url.pathname.slice(1) as ListView, source, { q: url.searchParams.get('q') || '' })
      : defaultUrl;
  } catch {
    return defaultUrl;
  }
}
export function currentQuarter(today = new Date()) {
  return `${today.getFullYear()}-Q${Math.floor(today.getMonth() / 3) + 1}`;
}
export function copyTarget(periods: Period[], enabled: boolean, today = new Date()) {
  if (!enabled) return null;
  return (
    periods.find((row) => row.period_key === currentQuarter(today) && row.status === 'open')
      ?.period_key ||
    periods.find((row) => row.status === 'open')?.period_key ||
    null
  );
}
