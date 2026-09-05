import {expect, test} from 'vitest';
import {readReviewDraft, writeReviewDraft, removeReviewDraft, reviewDraftKey} from '../src/core/drafts.ts';
import {captureFocus, restoreFocus} from '../src/core/focus.ts';
import {createRequestScope, type HttpResponse} from '../src/core/http.ts';

test('draft storage is scoped to a transaction and retains false, null and zero', () => {
  const memory = new Map<string, string>();
  const storage = () => ({getItem: (key: string) => memory.get(key) ?? null, setItem: (key: string, value: string) => {memory.set(key, value);}, removeItem: (key: string) => {memory.delete(key);}});
  const draft = {review_id: 'transaction:synthetic', transaction_id: 'synthetic', snapshot_hash: 'snapshot', decision: {approved: false, amount: 0, unknown: null}};
  writeReviewDraft(storage, draft);
  expect(readReviewDraft(storage, 'synthetic')).toEqual(draft);
  memory.set(reviewDraftKey('other'), JSON.stringify(draft));
  expect(readReviewDraft(storage, 'other')).toBeNull();
  memory.set(reviewDraftKey('synthetic'), '{broken');
  expect(readReviewDraft(storage, 'synthetic')).toBeNull();
  removeReviewDraft(storage, 'synthetic');
  expect(memory.has(reviewDraftKey('synthetic'))).toBe(false);
});

test('drafts cannot introduce prototype keys into a decision merge', () => {
  const raw = '{"review_id":"r","transaction_id":"x","snapshot_hash":"s","decision":{"__proto__":{"polluted":true}}}';
  expect(readReviewDraft(() => ({getItem: () => raw, setItem() {}, removeItem() {}}), 'x')).toBeNull();
});

test('focus restoration retains selection and never steals focus from another control', () => {
  document.body.innerHTML = '<main><textarea id="draft">synthetic text</textarea></main><button id="outside">outside</button>';
  const host = document.querySelector('main')!;
  const draft = document.querySelector<HTMLTextAreaElement>('#draft')!;
  draft.focus(); draft.setSelectionRange(2, 6);
  const anchor = captureFocus(host);
  host.innerHTML = '<textarea id="draft">synthetic text</textarea>';
  restoreFocus(host, anchor);
  expect(document.activeElement?.id).toBe('draft');
  expect(document.querySelector<HTMLTextAreaElement>('#draft')!.selectionStart).toBe(2);
  document.querySelector<HTMLButtonElement>('#outside')!.focus();
  restoreFocus(host, anchor);
  expect(document.activeElement?.id).toBe('outside');
});

test('request scope tracks writes until their response is consumed', async () => {
  let release!: (value: HttpResponse) => void;
  const scope = createRequestScope();
  const pending = scope.request('/api/test', {method: 'POST'}, {origin: 'https://example.invalid', t: key => key, fetch: () => new Promise(resolve => {release = resolve;})});
  expect(scope.writes).toBe(1);
  release({ok: true, status: 200, headers: {get: () => 'application/json'}, text: async () => '{}'});
  await pending;
  expect(scope.pending).toBe(0);
  expect(scope.writes).toBe(0);
});
