import type { JsonOptions } from '../core/http.ts';
export interface ViewServices {
  request(url: string, options?: JsonOptions): Promise<unknown>;
  navigate(url: string): void;
}
export function followSpaLink(event: MouseEvent, navigate: (url: string) => void) {
  if (
    event.defaultPrevented ||
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey
  )
    return;
  const link = event.currentTarget as HTMLAnchorElement;
  if (link.target || link.hasAttribute('download')) return;
  if (link.origin !== window.location.origin) return;
  event.preventDefault();
  navigate(link.pathname + link.search + link.hash);
}
