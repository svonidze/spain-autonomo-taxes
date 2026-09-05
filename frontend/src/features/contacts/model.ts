import type {HelpContext} from '../../vue/help.ts';
import type {ViewServices} from '../../vue/services.ts';
import type {UiMessage} from '../../core/i18n.ts';
export interface Counterparty {
  counterparty_id: string; display_name: string; row_version: number; ui_context: HelpContext;
  country_code?: string; tax_id?: string; vat_id?: string; legal_form?: string; email?: string; phone?: string;
  transaction_count?: number; last_transaction_on?: string; name_is_manual?: boolean;
}
export interface NameChange {old_name: string; new_name: string; changed_at: string; actor?: string; change_source: string;}
export interface ContactOperation {
  transaction_id: string; entry_type: string; transaction_date: string; period_key: string; description?: string;
  document_id?: string; document_number?: string; ui_context: HelpContext & {documents?: {document_id: string; available: boolean}[]};
  expense_kind?: string; deductible_irpf_eur?: number | null; amount_eur?: number | null;
  amount_original?: string | number | null; currency?: string; lifecycle_status: string; is_future_dated?: boolean;
}
export interface ContactPage {rows: ContactOperation[]; next_offset: number; matching_count: number; has_more: boolean;}
export interface ContactsContext {
  contactId: string | null; period: string; services: ViewServices;
  settled(): void; detailResolved(party: Counterparty): void;
  notify(message: UiMessage, error?: boolean): void;
  mountChart(): void; restorePosition(): void; rememberPosition(): void;
}
export const contactUrl = (id: string, period = '') => `/contacts/${encodeURIComponent(id)}${period ? `?period=${encodeURIComponent(period)}` : ''}`;
export const validName = (value: string) => value.trim().length > 0 && !/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/u.test(value);
