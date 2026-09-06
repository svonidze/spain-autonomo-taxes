import type { ViewServices } from '../../vue/services.ts';
export interface TransactionDetail {
  transaction: {
    transaction_id: string;
    entry_type: string;
    lifecycle_status: string;
    transaction_date?: string;
    booking_date?: string;
    description?: string;
    amount_original_minor?: number | null;
    amount_minor?: number | null;
    original_currency?: string;
    currency: string;
    amount_eur_minor?: number | null;
  };
  period: { period_key: string; status?: string };
  document?: { document_id?: string; document_number?: string; issued_on?: string };
  counterparty?: { display_name?: string };
  workflow_follow_up?: { follow_up_pending?: boolean };
  tax_treatments?: TaxTreatment[];
}
export interface TaxTreatment {
  treatment_type: string;
  jurisdiction: string;
  notes?: string | null;
  tax_code?: string;
  deductible_irpf_minor?: number | null;
  deductible_vat_minor?: number | null;
  vat_investment_good?: boolean | null;
  include_modelo130?: boolean | null;
  include_modelo303?: boolean | null;
  include_modelo347?: boolean | null;
}
export interface ExpenseDetailContext {
  transactionId: string;
  returnUrl: string;
  services: ViewServices;
  resolved(data: TransactionDetail): { returnUrl: string; wrongTypeUrl: string };
  settled(): void;
}
export function transactionDetail(value: unknown, id: string): TransactionDetail {
  const data = value as TransactionDetail | null;
  if (
    !data?.transaction ||
    data.transaction.transaction_id !== id ||
    !/^\d{4}-Q[1-4]$/.test(data.period?.period_key)
  ) {
    throw new Error('review.invalidWorkItem');
  }
  return data;
}
