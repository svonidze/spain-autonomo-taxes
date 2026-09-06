import { captureFocus, restoreFocus } from '../core/focus.ts';
import { getLocale, normalizeLocale } from '../core/i18n.ts';
import type { HelpContext } from '../vue/help.ts';
import { createPresentation, type AccountingContext, type Reason } from './presentation.ts';
export interface HelpNavigation {
  push(id: string): void;
  back(): void;
  clear(): void;
  navigate(url: string): void;
}
let registrySequence = 0;

/** Scoped records are owned by mounted StatusCells, never by HTML fragments. */
export function createAccountingHelp(doc = document, win = window) {
  const registryId = ++registrySequence;
  const records = new Map<string, AccountingContext>();
  let sequence = 0,
    opener: HTMLElement | null = null,
    scrollY = 0,
    panelUrl = '',
    pendingNavigation: string | null = null;
  let presentation = createPresentation(getLocale()),
    navigation: HelpNavigation | undefined,
    tipTrigger: HTMLElement | null = null,
    pinned = false;
  const listeners: (() => void)[] = [];
  const dialog = () => doc.getElementById('status-help-dialog') as HTMLDialogElement | null;
  const element = (id: string) => doc.getElementById(id)!;
  const activeId = () => win.history.state?.accountingHelp as string | undefined;
  function hideTip() {
    const tip = doc.getElementById('accounting-tooltip');
    if (tip) tip.hidden = true;
    tipTrigger = null;
    pinned = false;
  }
  function showTip(trigger: HTMLElement) {
    const tip = doc.getElementById('accounting-tooltip'),
      text = presentation.termText(trigger.dataset.helpTerm || '');
    if (!tip || !text) return;
    tipTrigger = trigger;
    tip.textContent = text;
    tip.hidden = false;
    (trigger.closest('dialog') || doc.body).appendChild(tip);
    const rect = trigger.getBoundingClientRect();
    tip.style.left = Math.max(8, Math.min(rect.left, win.innerWidth - 330)) + 'px';
    tip.style.top = Math.min(rect.bottom + 8, win.innerHeight - tip.offsetHeight - 8) + 'px';
  }
  function dismiss() {
    const node = dialog();
    if (!node?.open) return;
    node.close();
    if (opener?.isConnected) opener.focus({ preventScroll: true });
    win.scrollTo(0, scrollY);
    if (pendingNavigation) {
      const url = pendingNavigation;
      pendingNavigation = null;
      navigation?.navigate(url);
    }
  }
  function open(id: string, trigger: HTMLElement | null = null, push = true) {
    const context = records.get(id),
      node = dialog();
    if (!context || !node) return;
    hideTip();
    opener = trigger || opener;
    scrollY = win.scrollY;
    panelUrl = win.location.href;
    element('status-help-title').textContent = context.title || presentation.label(context);
    element('status-help-body').innerHTML = presentation.content(context);
    element('status-help-close').setAttribute('aria-label', presentation.word('close'));
    if (!node.open) node.showModal();
    element('status-help-close').focus();
    if (push) navigation?.push(id);
  }
  function close() {
    if (activeId() && dialog()?.open) navigation?.back();
    else dismiss();
  }
  function handlePopState() {
    const id = activeId();
    if (id && records.has(id) && win.location.href === panelUrl) {
      open(id, null, false);
      return true;
    }
    if (dialog()?.open) {
      dismiss();
      return win.location.href === panelUrl;
    }
    return false;
  }
  function createScope(context: HelpContext) {
    const id = `${registryId}-${++sequence}`;
    records.set(id, context as AccountingContext);
    let active = true;
    return {
      id,
      update(value: HelpContext) {
        if (active) records.set(id, value as AccountingContext);
      },
      dispose() {
        if (!active) return;
        active = false;
        records.delete(id);
        if (activeId() === id) {
          dismiss();
          navigation?.clear();
        }
      },
    };
  }
  function beforeRender() {
    dismiss();
    hideTip();
    if (activeId()) navigation?.clear();
    records.clear();
  }
  function setLocale(value: string) {
    presentation = createPresentation(normalizeLocale(value));
    const node = dialog(),
      context = records.get(activeId() || '');
    if (node?.open && context) {
      const focus = captureFocus(node),
        body = element('status-help-body'),
        scroll = body.scrollTop;
      element('status-help-title').textContent = context.title || presentation.label(context);
      body.innerHTML = presentation.content(context);
      body.scrollTop = scroll;
      restoreFocus(node, focus);
    }
    if (tipTrigger) showTip(tipTrigger);
  }
  function on<K extends keyof DocumentEventMap>(
    name: K,
    callback: (event: DocumentEventMap[K]) => void,
    capture = false,
  ) {
    doc.addEventListener(name, callback, capture);
    listeners.push(() => doc.removeEventListener(name, callback, capture));
  }
  const target = (event: Event) => (event.target instanceof Element ? event.target : null);
  on('click', async (event) => {
    const source = target(event);
    if (!source) return;
    const trigger = source.closest<HTMLElement>('[data-status-help]');
    if (trigger) {
      open(trigger.dataset.statusHelp!, trigger);
      return;
    }
    if (source.closest('#status-help-close')) {
      close();
      return;
    }
    const link = source.closest<HTMLAnchorElement>('[data-help-review]');
    if (link) {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
        return;
      event.preventDefault();
      pendingNavigation = link.getAttribute('href');
      close();
      return;
    }
    const copy = source.closest('[data-copy-question]');
    if (copy) {
      const article = copy.closest('article')!,
        feedback = article.querySelector('.copy-feedback')!;
      try {
        await win.navigator.clipboard.writeText(
          article.querySelector('.copy-question')!.textContent || '',
        );
        feedback.textContent = presentation.word('copied');
      } catch {
        feedback.textContent = presentation.word('copyError');
      }
      return;
    }
    const term = source.closest<HTMLElement>('[data-help-term]');
    if (term) {
      if (pinned && tipTrigger === term) hideTip();
      else {
        showTip(term);
        pinned = true;
      }
      return;
    }
    if (!source.closest('#accounting-tooltip')) hideTip();
  });
  on(
    'cancel',
    (event) => {
      if (target(event)?.id === 'status-help-dialog') {
        event.preventDefault();
        close();
      }
    },
    true,
  );
  on('focusin', (event) => {
    const trigger = target(event)?.closest<HTMLElement>('[data-help-term]');
    if (trigger) showTip(trigger);
  });
  on('focusout', () => {
    if (!pinned) hideTip();
  });
  on('mouseover', (event) => {
    const trigger = target(event)?.closest<HTMLElement>('[data-help-term]');
    if (trigger && !pinned) showTip(trigger);
  });
  on('mouseout', (event) => {
    const source = target(event),
      related = event.relatedTarget instanceof Element ? event.relatedTarget : null;
    if (
      !pinned &&
      source?.closest('[data-help-term],#accounting-tooltip') &&
      !related?.closest('[data-help-term],#accounting-tooltip')
    )
      hideTip();
  });
  on(
    'keydown',
    (event) => {
      if (event.key === 'Escape' && tipTrigger && !dialog()?.open) {
        event.preventDefault();
        event.stopPropagation();
        hideTip();
      }
    },
    true,
  );
  return {
    createScope,
    setLocale,
    beforeRender,
    handlePopState,
    setHistoryAdapter(value: HelpNavigation) {
      navigation = value;
    },
    label: (value: HelpContext) => presentation.label(value as AccountingContext),
    summary: (value: HelpContext) => presentation.summary(value as AccountingContext),
    tone: (value: HelpContext) => presentation.tone(value as AccountingContext),
    word: (key: string) => presentation.word(key),
    reasonInfo: (reason: Record<string, unknown>) => presentation.reasonInfo(reason as Reason),
    text: (key: string) => presentation.text(key),
    termLabel: (key: string) => presentation.termLabel(key),
    dispose() {
      beforeRender();
      listeners.forEach((remove) => remove());
    },
  };
}
