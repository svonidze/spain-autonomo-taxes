export interface ReviewDraft {
  review_id: string;
  transaction_id: string;
  snapshot_hash: string;
  decision: Record<string, unknown>;
}
type DraftStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
export const reviewDraftPrefix = 'autonomo.review-draft';
export function reviewDraftKey(transactionId: string): string { return `${reviewDraftPrefix}:${transactionId || 'unknown'}`; }

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function safeKeys(value: unknown): boolean {
  if (Array.isArray(value)) return value.every(safeKeys);
  return !isRecord(value) || Object.entries(value).every(([key, item]) => !['__proto__', 'prototype', 'constructor'].includes(key) && safeKeys(item));
}
export function readReviewDraft(storage: () => DraftStorage, transactionId: string): ReviewDraft | null {
  if (!transactionId) return null;
  try {
    const value: unknown = JSON.parse(storage().getItem(reviewDraftKey(transactionId)) || 'null');
    if (!isRecord(value) || value.transaction_id !== transactionId || typeof value.review_id !== 'string'
        || typeof value.snapshot_hash !== 'string' || !isRecord(value.decision) || !safeKeys(value.decision)) return null;
    return {...value, review_id: value.review_id, transaction_id: transactionId, snapshot_hash: value.snapshot_hash, decision: value.decision};
  } catch { return null; } // Corrupt or unavailable browser storage is optional.
}
export function writeReviewDraft(storage: () => DraftStorage, draft: ReviewDraft): void {
  if (!draft.transaction_id) return;
  try { storage().setItem(reviewDraftKey(draft.transaction_id), JSON.stringify(draft)); }
  catch { /* Editing remains available without persistent storage. */ }
}
export function removeReviewDraft(storage: () => DraftStorage, transactionId: string): void {
  if (!transactionId) return;
  try { storage().removeItem(reviewDraftKey(transactionId)); }
  catch { /* Browser storage may have been disabled after the draft was read. */ }
}
