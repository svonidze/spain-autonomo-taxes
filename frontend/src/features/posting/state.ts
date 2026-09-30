import { shallowReactive, shallowRef } from 'vue';
import type { UiMessage } from '../../core/i18n.ts';
import type { PostingResult } from './model.ts';
export interface StaleRefresh {
  period: string;
  /** Key+params descriptor or raw server text; rendered with messageText() so a locale change re-renders it. */
  error: UiMessage | string;
}
export const postingState = {
  busy: shallowRef(false),
  lastResult: shallowRef<{ period: string; result: PostingResult }>(),
  stale: shallowReactive(new Map<string, StaleRefresh>()),
};
export function markStale(period: string, error: UiMessage | string = '') {
  const marker = { period, error };
  postingState.stale.set(period, marker);
  return marker;
}
export function refreshToken(period: string) {
  return postingState.stale.get(period);
}
export function clearRefresh(period: string, marker: StaleRefresh | undefined) {
  if (postingState.stale.get(period) === marker) postingState.stale.delete(period);
}
