export interface FocusAnchor {
  id: string;
  decisionPath?: string;
  href: string | null;
  tag: string;
  className: string;
  ordinal: number;
  selection?: [number, number];
}

/** Capture one control address and selection, never a form's data values. */
export function captureFocus(host: HTMLElement | null): FocusAnchor | null {
  const candidate = host?.ownerDocument?.activeElement;
  if (!host || !candidate || !host.contains(candidate) || !('focus' in candidate)) return null;
  const active = candidate as HTMLElement;
  const peers = [...host.querySelectorAll<HTMLElement>('*')].filter(element => element.tagName === active.tagName && element.className === active.className);
  const anchor: FocusAnchor = {id: active.id, decisionPath: active.dataset.decisionPath, href: active.getAttribute('href'), tag: active.tagName, className: active.className, ordinal: peers.indexOf(active)};
  if (['INPUT', 'TEXTAREA'].includes(active.tagName)) {
    const field = active as HTMLInputElement | HTMLTextAreaElement;
    if (typeof field.selectionStart === 'number' && typeof field.selectionEnd === 'number') anchor.selection = [field.selectionStart, field.selectionEnd];
  }
  return anchor;
}

export function restoreFocus(host: HTMLElement | null, anchor: FocusAnchor | null): void {
  if (!host || !anchor) return;
  const document = host.ownerDocument;
  const active = document.activeElement;
  if (active && active !== document.body && active !== document.documentElement && active.isConnected) return;
  const elements = [...host.querySelectorAll<HTMLElement>('*')];
  const target = anchor.id ? elements.find(element => element.id === anchor.id)
    : anchor.decisionPath ? elements.find(element => element.dataset.decisionPath === anchor.decisionPath)
    : anchor.href ? elements.find(element => element.getAttribute('href') === anchor.href)
    : elements.filter(element => element.tagName === anchor.tag && element.className === anchor.className)[anchor.ordinal];
  if (!target) return;
  target.focus({preventScroll: true});
  if (anchor.selection && (target.tagName === 'TEXTAREA' || target.tagName === 'INPUT' && ['text', 'search', 'url', 'tel', 'password'].includes((target as HTMLInputElement).type))) (target as HTMLInputElement | HTMLTextAreaElement).setSelectionRange(...anchor.selection);
}
