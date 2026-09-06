import { describe, expect, test, vi } from 'vitest';
import {
  t,
  formatMessage,
  loadLocale,
  storeLocale,
  setLocale,
  subscribeLocale,
} from '../src/core/i18n.ts';
import { eur, formatDateText, escapeHtml } from '../src/core/format.ts';
import { fetchJSON, ApiError, type HttpResponse } from '../src/core/http.ts';

describe('locale messages and private values', () => {
  test.each([
    [0, '0 операций'],
    [1, '1 операция'],
    [2, '2 операции'],
    [5, '5 операций'],
    [11, '11 операций'],
    [21, '21 операция'],
  ])('Russian count %s', (count, expected) => {
    expect(t('common.counts.operations', { count: Number(count) }, 'ru')).toBe(expected);
  });
  test('English grammar and named values', () => {
    expect(t('common.counts.operations', { count: 2 }, 'en')).toBe('2 transactions');
    expect(t('expense.sourceDate', { date: 'synthetic date' }, 'en')).toContain('synthetic date');
    expect(formatMessage('unknown.server.code', {}, 'en')).toBe('unknown.server.code');
  });
  test('disabled storage and subscriptions do not affect data', () => {
    const denied = () => {
      throw new Error('disabled');
    };
    expect(loadLocale(denied)).toBe('ru');
    expect(() => storeLocale(denied, 'en')).not.toThrow();
    setLocale('ru');
    const callback = vi.fn();
    const unsubscribe = subscribeLocale(callback);
    setLocale('en');
    setLocale('en');
    expect(callback).toHaveBeenCalledTimes(1);
    unsubscribe();
    setLocale('ru');
    expect(callback).toHaveBeenCalledTimes(1);
  });
  test('missing, zero and negative money remain distinct', () => {
    expect(eur(null, 'ru')).toBe('—');
    expect(eur('', 'en')).toBe('—');
    expect(eur('not a number', 'en')).toBe('—');
    expect(eur(0, 'en')).toContain('0.00');
    expect(eur(-0.01, 'en')).toContain('-');
    expect(eur('1234.56', 'ru')).not.toBe(eur('1234.56', 'en'));
    expect(formatDateText('not-a-date', 'ru')).toBe('not-a-date');
    expect(escapeHtml('<script>')).toBe('&lt;script&gt;');
  });
});

function response(status: number, contentType: string, body: string): HttpResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => contentType },
    text: async () => body,
  };
}
describe('HTTP boundary', () => {
  const context = (payload: HttpResponse) => ({
    fetch: async () => payload,
    origin: 'https://example.invalid',
    t: formatMessage,
  });
  test('preserves conflict metadata rather than normalizing it away', async () => {
    const request = fetchJSON(
      '/api/test',
      {},
      context(
        response(
          409,
          'application/json',
          JSON.stringify({
            error: 'Synthetic conflict',
            code: 'stale_counterparty',
            current: { row_version: 3 },
          }),
        ),
      ),
    );
    await expect(request).rejects.toMatchObject({
      status: 409,
      code: 'stale_counterparty',
      current: { row_version: 3 },
    });
    await expect(request).rejects.toBeInstanceOf(ApiError);
  });
  test.each([
    [200, 'text/html', '<html>wrong service</html>'],
    [200, 'application/json', '{broken'],
    [502, 'text/plain', 'unavailable'],
  ])('rejects invalid response %s %s', async (status, contentType, body) => {
    await expect(
      fetchJSON('/api/test', {}, context(response(status, contentType, body))),
    ).rejects.toBeInstanceOf(ApiError);
  });
  test('transport callback does not receive the context as its receiver', async () => {
    const transport = vi.fn(function (this: unknown) {
      expect(this).toBeUndefined();
      return Promise.resolve(response(200, 'application/json', '0'));
    });
    await expect(
      fetchJSON(
        '/api/test',
        {},
        { fetch: transport, origin: 'https://example.invalid', t: formatMessage },
      ),
    ).resolves.toBe(0);
  });
});

test('additive error messages retain legacy text when a server key is unknown', async () => {
  const { errorMessage } = await import('../src/core/error-message.ts');
  const error = new ApiError('Synthetic server explanation');
  error.messageCode = 'future.server.validation';
  expect(errorMessage(error, 'en')).toBe('Synthetic server explanation');
  error.messageCode = 'expense.sourceDate';
  expect(errorMessage(error, 'en')).toBe('Synthetic server explanation');
  error.messageCode = 'contacts.invalidName';
  expect(errorMessage(error, 'en')).toBe(formatMessage('contacts.invalidName', {}, 'en'));
});
