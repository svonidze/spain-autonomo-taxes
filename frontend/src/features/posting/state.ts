import { shallowReactive, shallowRef } from 'vue';
import type { PostingResult } from './model.ts';
export interface StaleRefresh {
  period: string;
  error: string;
}
export const postingState = {
  busy: shallowRef(false),
  lastResult: shallowRef<{ period: string; result: PostingResult }>(),
  stale: shallowReactive(new Map<string, StaleRefresh>()),
};
export function markStale(period: string, error = '') {
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
