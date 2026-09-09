import type { UiMessage } from '../../core/i18n.ts';
import type { ViewServices } from '../../vue/services.ts';

export interface AeatDocumentRow {
  record_source: 'aeat_document' | 'filing_snapshot';
  record_id: string;
  aeat_case_id?: string | null;
  case_row_version?: number | null;
  title: string;
  form_code?: string | null;
  procedure_kind: string;
  procedure_code?: string | null;
  document_kind: string;
  status: string;
  occurred_at: string;
  requested_effective_on?: string | null;
  submission_reference?: string | null;
  justificante_number?: string | null;
  verification_code?: string | null;
  period_key?: string | null;
  content_url?: string | null;
  source_available: boolean;
  status_history?: Array<{
    status: string;
    occurred_at: string;
    evidence_reference?: string | null;
    notes?: string | null;
  }>;
}

export interface AeatDocumentsData {
  rows: AeatDocumentRow[];
  total: number;
}

export interface AeatDocumentsContext {
  services: ViewServices;
  settled(): void;
  notify(value: UiMessage | string, error?: boolean): void;
}
