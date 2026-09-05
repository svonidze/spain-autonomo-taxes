import type {HelpContext} from '../../vue/help.ts';
import type {ViewServices} from '../../vue/services.ts';
import type {UiMessage} from '../../core/i18n.ts';
import type {Fields} from './workflow-model.ts';
export interface ReviewDecision extends Record<string, unknown> {
  business_purpose?: string; reason?: string; document_valid?: boolean | null; asset_decision?: string; asset_id?: string | null; outcome?: string;
  tax_treatment: Fields; counterparty_changes: Fields; issue_resolutions?: IssueResolution[];
}
export interface IssueResolution {issue_id?: string; action?: string | null; reason?: string; [key: string]: unknown;}
export interface ReviewIssue {validation_issue_id?: string; issue_code: string; message?: string;}
export interface ReviewState {
  period: {period_key: string; status?: string};
  transaction: {transaction_id: string; entry_type: string; transaction_date?: string; original_currency?: string; currency?: string; fx_rate_id?: string | null; amount_original_minor?: number | null; amount_eur_minor?: number | null};
  document?: {document_id?: string; document_number?: string; currency?: string; issued_on?: string};
  counterparty?: Fields & {display_name?: string; country_code?: string};
  issues?: ReviewIssue[]; assets?: {asset_id: string}[];
}
export interface ReviewPacket {review_id: string; snapshot_hash: string; state: ReviewState; decision: ReviewDecision; allowed_values?: Record<string, string[]>; [key: string]: unknown;}
export interface FxSuggestion {status: string; currency?: string; transaction_date?: string; rate_date?: string; eur_per_unit?: string; note?: string; source_label?: string; rate_source?: string; amount_eur?: string;}
export interface FxChoice {mode: 'ecb' | 'settlement'; rate?: string; rateDate?: string; sourceReference?: string;}
export interface WorkItem {
  packet: ReviewPacket; supported?: boolean; review_allowed?: boolean; unavailable_reason?: string;
  posting_context?: {preview_bucket?: string; posting_deferred_until?: string};
  ui_context: HelpContext & {posting?: {preview_bucket?: string; posting_deferred_until?: string}};
  guidance?: {issue_coverage?: Record<string, string[]>; auto_filled?: {business_purpose?: string; deductible_irpf_minor?: number; asset_decision?: string}};
  fx_suggestion?: FxSuggestion; requirements?: (string | {code: string; supported?: boolean})[];
}
export interface GuidedContext {
  transactionId: string; returnUrl: string; knownPeriods: string[]; factsOnly?: boolean; services: ViewServices;
  resolved(period: ReviewState['period']): string; complete(outcome: 'approve' | 'reject'): void;
  posted(transactionId: string, period: string, replace?: boolean): void; settled(): void; notify(message: UiMessage | string, error?: boolean): void;
}
export interface ReviewDetailContext extends GuidedContext {period: string;}
