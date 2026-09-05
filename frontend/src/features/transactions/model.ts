import type {HelpContext} from '../../vue/help.ts';
import type {ViewServices} from '../../vue/services.ts';
export type Amount = number | string | null;
export interface TransactionRow {
  transaction_id: string; entry_type: string; lifecycle_status: string; document_status?: string;
  transaction_date: string; period_key: string; description?: string; counterparty_name?: string;
  document_id?: string; document_number?: string; document_issued_on?: string;
  amount_eur?: Amount; amount_original?: Amount; currency?: string; document_amount_eur?: Amount;
  deductible_irpf_eur?: Amount; deductible_vat_eur?: Amount;
  deductible_irpf_minor?: number | null; deductible_vat_minor?: number | null;
  ui_context: HelpContext; expense_kind?: 'purchase' | 'amortization'; is_future_dated?: boolean;
  asset_id?: string; asset_match_count?: number; asset_description?: string; asset_match_method?: string;
}
export type ExpenseKind = 'purchase' | 'amortization';
export interface ExpensePage {
  as_of: string; view_revision: string; rows: TransactionRow[]; has_more: boolean; next_offset: number;
  matching_counts: Record<ExpenseKind, number>; period_counts: Record<ExpenseKind, number>;
  summary: Record<ExpenseKind, {reviewed_total: {amount_eur: Amount; missing_amount_count: number}}>;
}
export interface TransactionListContext {
  kind: 'income' | 'expense'; period: string; query: string; copyTarget: string | null;
  services: ViewServices; queryChanged(query: string): void; settled(): void;
  openIntake(): void; copy(row: TransactionRow): void; mountChart(): void;
}
export const hasAmount = (value: unknown) => value !== null && value !== undefined && value !== '';
export const copyable = (row: TransactionRow, target: string | null) => !!target && row.entry_type === 'income' && !!row.transaction_id && !['duplicate','rejected','void'].includes(row.lifecycle_status) && !['duplicate','rejected','void'].includes(row.document_status || '');
export const listUrl = (kind: 'expense' | 'income', period: string, query = '') => `/${kind === 'expense' ? 'expenses' : 'income'}?${new URLSearchParams({period, ...(kind === 'expense' && query ? {q: query} : {})})}`;
