import {formatMessage, type Locale} from '../../core/i18n.ts';
import {ApiError} from '../../core/http.ts';
import type {ReviewDecision, ReviewPacket, ReviewState, WorkItem, FxChoice, FxSuggestion} from './guided-model.ts';
export const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object';
export function merge<T>(base: T, override: unknown): T {
  if (Array.isArray(override)) return clone(override) as T;
  if (!record(override)) return override as T;
  const result: Record<string, unknown> = Array.isArray(base) ? [] as unknown as Record<string,unknown> : {...(record(base) ? base : {})};
  for (const [key,value] of Object.entries(override)) {
    if (['__proto__','constructor','prototype'].includes(key)) continue;
    result[key] = record(value) && !Array.isArray(value) ? merge(record(base) ? base[key] || {} : {}, value) : clone(value);
  }
  return result as T;
}
export function setDecision(target: Record<string, unknown>, path: string, value: unknown) {
  const parts = path.split('.'); if (parts.some(key => ['__proto__','prototype','constructor'].includes(key))) throw new Error('Invalid field');
  let cursor = target;
  for (let index=0; index<parts.length-1; index++) {
    const key = parts[index]; if (cursor[key] == null) cursor[key] = /^\d+$/.test(parts[index+1]) ? [] : {};
    if (!record(cursor[key])) throw new Error('Invalid field parent'); cursor = cursor[key] as Record<string,unknown>;
  }
  cursor[parts.at(-1)!] = value;
}
export function getDecision(target: unknown, path: string): unknown {let result = target; for (const key of path.split('.')) result = record(result) ? result[key] : undefined; return result;}
export function evaluate(item: WorkItem | null | undefined) {
  const projection = item?.posting_context || item?.ui_context?.posting;
  if (!projection) return {category:'blocked',supported:false,canApply:false,availableOn:null,reasonKey:'review.unknownSupport'};
  const supported = item?.supported !== false, code = item?.ui_context?.state || projection.preview_bucket;
  return {category: !supported ? 'blocked' : code === 'deferred' ? 'later' : ['ready','blocked','needs_review'].includes(code || '') ? code! : 'blocked',supported,canApply:item?.review_allowed === true,availableOn:projection.posting_deferred_until || null,reasonKey:supported ? null : 'review.unsupported'};
}
export function answers(decision: ReviewDecision, state: ReviewState, fx: FxChoice | null): Record<string, boolean> {
  const text = (value: unknown) => String(value || '').trim(); const party = state.counterparty || {};
  return {business_purpose:!!text(decision.business_purpose),tax_code:!!decision.tax_treatment?.tax_code,deductible_irpf_minor:decision.tax_treatment?.deductible_irpf_minor != null,asset_decision:!!decision.asset_decision,vat_investment_good:typeof decision.tax_treatment?.vat_investment_good === 'boolean',counterparty_country:!!text(decision.counterparty_changes?.country_code) || (!!party.country_code && party.country_code !== 'ZZ'),fx_rate:!!fx,reason:!!text(decision.reason),document_valid:typeof decision.document_valid === 'boolean'};
}
export function autoResolve(packet: ReviewPacket, coverage: Record<string,string[]>, answered: Record<string,boolean>, locale: Locale) {
  packet.decision.issue_resolutions?.forEach((resolution,index) => {
    if (!resolution) return;
    const issue = resolution.issue_id ? packet.state.issues?.find(row=>row.validation_issue_id===resolution.issue_id) : packet.state.issues?.[index];
    const covering = issue && coverage[issue.issue_code];
    if (covering?.length && covering.every(id=>!!answered[id])) {resolution.action='resolve'; if (!resolution.reason) resolution.reason=formatMessage('review.issueAutoResolveHint',{labels:covering.length},locale);}
  });
}
export function legacyErrorTarget(message: string) {
  const text = message.toLowerCase();
  if (text.includes('business_purpose') || text.includes('business purpose')) return 'business_purpose';
  if (text.includes('tax_code') || text.includes('tax code')) return 'tax_code';
  if (text.includes('deductible_irpf')) return 'deductible_irpf_minor';
  if (text.includes('counterparty') || text.includes('country')) return 'counterparty_country';
  if (text.includes('document_valid') || text.includes('document valid')) return 'document_valid';
  if (text.includes('vat_investment_good')) return 'vat_investment_good';
  if (text.includes('asset')) return 'asset_decision';
  if (text.includes('fx') || text.includes('rate') || text.includes('eur')) return 'fx_rate';
  if (text.includes('issue')) return 'issues';
  if (text.includes('reason')) return 'reason';
  return 'general';
}
const questionIds = new Set(['business_purpose','tax_code','deductible_irpf_minor','counterparty_country','document_valid','vat_investment_good','asset_decision','fx_rate','issues','reason','general']);
export function errorTarget(value: unknown) {return value instanceof ApiError && value.field && questionIds.has(value.field) ? value.field : legacyErrorTarget(value instanceof Error ? value.message : String(value));}
export function needsFx(transaction: ReviewState['transaction']) {return String(transaction.original_currency || transaction.currency || 'EUR').toUpperCase() !== 'EUR' && !transaction.fx_rate_id;}
export function initialFx(suggestion?: FxSuggestion): FxChoice | null {return suggestion && ['exact','prior'].includes(suggestion.status) ? {mode:'ecb'} : null;}
export function confirmFx(choice: FxChoice | null, suggestion?: FxSuggestion) {
  if (!choice) return null;
  if (choice.mode==='ecb' && suggestion) return {rate_date:suggestion.rate_date,rate:suggestion.eur_per_unit,rate_source:'ecb',source_reference:null,raw_observation:null,raw_observation_hash:null,supersedes_rate_id:null};
  const rate=String(choice.rate||'').trim(), rateDate=String(choice.rateDate||'').trim(), reference=String(choice.sourceReference||'').trim();
  if (!rate || !rateDate || !reference) return null;
  return {rate_date:rateDate,rate,rate_source:'actual_settlement',source_reference:reference,raw_observation:JSON.stringify({kind:'documented_settlement',rate,rate_date:rateDate,source_reference:reference}),raw_observation_hash:null,supersedes_rate_id:null};
}
