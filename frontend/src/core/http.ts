export interface HttpResponse {
  ok: boolean;
  status: number;
  headers: Pick<Headers, 'get'>;
  text(): Promise<string>;
}
export interface HttpContext {
  fetch(url: string, options: RequestInit): Promise<HttpResponse>;
  origin: string;
  t(id: string, variables?: Record<string, unknown>): string;
}
export type JsonOptions = RequestInit & {fallbackMessage?: string};
export class ApiError extends Error {
  status?: number;
  code?: string;
  current?: unknown;
  messageCode?: string;
  params?: Record<string, unknown>;
  field?: string;
}
export function parseJSONText(body: string): {ok: true; value: unknown} | {ok: false} {
  if (!body) return {ok: false};
  try { return {ok: true, value: JSON.parse(body) as unknown}; }
  catch { return {ok: false}; }
}
export function looksLikeHtmlResponse(contentType: string, body: string): boolean {
  return contentType === 'text/html' || contentType === 'application/xhtml+xml' || /^(?:<!doctype html\b|<html\b|<head\b|<body\b)/i.test(body);
}
export function looksLikeJsonResponse(contentType: string, body: string): boolean {
  return contentType === 'application/json' || contentType.endsWith('+json') || /^[\[{]/.test(body);
}
export async function fetchJSON(url: string, options: JsonOptions, context: HttpContext): Promise<unknown> {
  const {fallbackMessage, ...fetchOptions} = options;
  const transport = context.fetch;
  const response = await transport(url, fetchOptions);
  const responseUrl = new URL(url, context.origin);
  const contentType = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
  const body = (await response.text()).trim();
  const parsed = parseJSONText(body);
  if (parsed.ok) {
    if (!response.ok) {
      const payload = parsed.value && typeof parsed.value === 'object' ? parsed.value as Record<string, unknown> : {};
      const error = new ApiError(String(payload.error || fallbackMessage || `HTTP ${response.status}`));
      error.status = response.status;
      error.code = typeof payload.code === 'string' ? payload.code : undefined;
      error.current = payload.current;
      error.messageCode = typeof payload.message_code === 'string' ? payload.message_code : undefined;
      error.params = payload.params && typeof payload.params === 'object' ? payload.params as Record<string, unknown> : undefined;
      error.field = typeof payload.field === 'string' ? payload.field : undefined;
      throw error;
    }
    return parsed.value;
  }
  if (response.ok && looksLikeHtmlResponse(contentType, body)) throw new ApiError(context.t('errors.apiReturnedHtml', {origin: responseUrl.origin}));
  const key = looksLikeJsonResponse(contentType, body) ? 'errors.malformedJson' : 'errors.unexpectedNonJson';
  throw new ApiError(context.t(key, {url: responseUrl.href, status: response.status}));
}

export function createRequestScope(onSettled: () => void = () => {}) {
  let pending = 0;
  let writes = 0;
  return {
    get pending() { return pending; },
    get writes() { return writes; },
    async request(url: string, options: JsonOptions, context: HttpContext): Promise<unknown> {
      const write = !['GET', 'HEAD', 'OPTIONS'].includes((options.method || 'GET').toUpperCase());
      pending += 1;
      if (write) writes += 1;
      try { return await fetchJSON(url, options, context); }
      finally {
        pending -= 1;
        if (write) writes -= 1;
        queueMicrotask(onSettled);
      }
    },
  };
}
