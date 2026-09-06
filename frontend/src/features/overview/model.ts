import type { ViewServices } from '../../vue/services.ts';
import type { UiMessage } from '../../core/i18n.ts';
import type { HelpContext } from '../../vue/help.ts';
import type { TransactionRow, Amount } from '../transactions/model.ts';
export interface OverviewContext {
  view: 'dashboard' | 'assets' | 'taxes';
  period: string;
  services: ViewServices;
  copyTarget: string | null;
  settled(): void;
  refreshCalculation(): Promise<void>;
  copy(row: TransactionRow): void;
  notify(message: UiMessage | string, error?: boolean): void;
}
export interface Obligation {
  obligation_code: string;
  determination: string;
  filing_status: string;
  statutory_due_on?: string;
  direct_debit_cutoff_on?: string;
}
export interface FormView {
  form_code: string;
  display_state: string;
  filed_on?: string | null;
  values: Record<string, Amount>;
  headline_value?: Amount;
  headline_detail?: Amount;
  preview_as_of?: string | null;
  extraction_status?: string | null;
}
export interface TaxSummary {
  settlement_status?: string;
  total_payable_minor?: number | null;
  total_confirmed_paid_minor?: number | null;
  outstanding_minor?: number | null;
  overpaid_minor?: number | null;
  calculated_as_of?: string;
  calculation_source?: string;
  requires_reconciliation?: boolean;
  forms?: Record<string, TaxFormSummary>;
}
export interface TaxFormSummary {
  settlement_status?: string;
  filing_status?: string;
  determination?: string;
  filed_on?: string;
  disposition?: string;
  carryforward_minor?: number | null;
  generated_credit_minor?: number | null;
  refund_requested_minor?: number | null;
  payable_minor?: number | null;
  statutory_due_on?: string;
  direct_debit_cutoff_on?: string;
}
export interface TaxesData {
  period: string;
  period_state?: { phase?: string };
  tax_summary?: TaxSummary;
  tax_forms?: Record<string, FormView>;
  obligations: Obligation[];
}
export interface ExpenseScope {
  amount_eur?: Amount;
  count?: number;
  missing_amount_count?: number;
}
export interface DashboardData {
  totals: {
    actual: { income_eur: Amount; income_transaction_count: number };
    forecast: { transaction_count?: number };
  };
  expense_summary: Record<
    'purchase' | 'amortization',
    { posted: ExpenseScope; approved: ExpenseScope; future_approved: ExpenseScope }
  >;
  obligations: Obligation[];
  tax_forms?: Record<string, FormView>;
  posting_preview_summary?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  readyCount?: number;
  ready_count?: number;
  recent_transactions: TransactionRow[];
  open_issues: { ui_context: HelpContext }[];
}
export interface AssetRow {
  asset_id: string;
  asset_code: string;
  description?: string;
  source_invoice_number?: string;
  placed_in_service_on?: string;
  cost_minor?: number | null;
  amortizable_base_minor?: number | null;
  business_use_percent?: Amount;
  annual_rate_percent?: Amount;
  ui_context: HelpContext & {
    amortization?: {
      count: number;
      rows: { include_in_books: boolean; entry_kind?: string }[];
      book_minor?: number | null;
      excluded_minor?: number | null;
      adjustment_minor?: number | null;
    };
  };
}
export interface ScheduleRow {
  amortization_entry_id: string;
  period_key: string;
  amount_minor: number;
  recognition_transaction_id?: string;
  recognition_on?: string;
  can_post: boolean;
  row_version: number;
}
export interface AssetScheduleData {
  native: boolean;
  rows: ScheduleRow[];
}
