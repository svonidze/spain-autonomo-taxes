import type { HelpContext } from '../../vue/help.ts';
import type { ViewServices } from '../../vue/services.ts';
import type { UiMessage } from '../../core/i18n.ts';
import type { TransactionRow, Amount } from '../transactions/model.ts';
export type ReviewTab = 'queue' | 'posting' | 'documents';
export interface PostingItem {
  reviewId: string;
  transactionId: string;
  expectedRowVersion: number | null;
  transactionDate: string;
  entryType: string;
  description: string;
  amountEur: string;
  rowStatus: string;
  previewBucket: string | null;
  postingDeferredUntil: string | null;
  structuredReasons: Record<string, unknown>[];
  documentId: string | null;
  message: string;
  reasons: string[];
  cleanupApplied: boolean;
  cleanupBlocked: boolean;
  readyToPost: boolean;
}
export interface PostingSummary {
  readyCount: number;
  deferredCount: number;
  blockedCount: number;
  approvedCount: number;
  readyTotalEur: string;
  cleanupCount: number;
  cleanupBlockedCount: number;
}
export interface PostingPreview {
  period: string;
  periodStatus: string;
  generatedAt: string | null;
  asOf: string | null;
  summary: PostingSummary;
  ready: PostingItem[];
  deferred: PostingItem[];
  blocked: PostingItem[];
}
export interface PostingResult {
  status: string;
  interrupted: boolean;
  summary: { postedCount: number };
  results: PostingItem[];
}
export interface ReviewRow extends TransactionRow {
  tax_code?: string;
}
export interface ReviewDocument {
  document_id: string;
  issued_on?: string;
  counterparty_name?: string;
  document_number?: string;
  document_type: string;
  lifecycle_status: string;
  ui_context: HelpContext;
  total_eur?: Amount;
  source_available: boolean;
}
export interface OverviewData {
  rows: ReviewRow[];
  issues: { ui_context: HelpContext }[];
  documents: ReviewDocument[];
  preview: PostingPreview;
}
export interface ReviewOverviewContext {
  period: string;
  tab: ReviewTab;
  services: ViewServices;
  settled(): void;
  selectTab(tab: ReviewTab): void;
  refreshCalculation(period: string): Promise<unknown>;
  notify(value: UiMessage | string, error?: boolean): void;
}
