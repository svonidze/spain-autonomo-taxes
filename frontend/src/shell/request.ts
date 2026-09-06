import {fetchJSON, type JsonOptions} from '../core/http.ts';
import {formatMessage} from '../core/i18n.ts';
export const request = (url: string, options: JsonOptions = {}) => fetchJSON(url, options, {
  fetch: (input, init) => window.fetch(input, init), origin: window.location.origin, t: formatMessage,
});
export async function refreshCalculation(period: string): Promise<void> {
  await request('/api/dashboard/refresh', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({period,as_of:new Date().toISOString().slice(0,10)})});
}
