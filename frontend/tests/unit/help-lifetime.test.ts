import { expect, test, vi } from 'vitest';
import { createAccountingHelp } from '../../src/help/registry.ts';
import { createPresentation } from '../../src/help/presentation.ts';
for (const locale of ['ru', 'en'] as const)
  test(`help presents amounts, blockers and hostile text safely in ${locale}`, () => {
    const help = createPresentation(locale);
    expect(help.money(0)).not.toBe(help.word('missing'));
    expect(help.money(null)).toBe(help.word('missing'));
    expect(help.money('')).toBe(help.word('missing'));
    const context = {
      domain: 'asset',
      state: 'needs_review',
      reasons: [{ code: 'advance_documents_overlap' }, { code: 'iva_prior_deduction_unconfirmed' }],
      source_message: '<img src=x onerror="alert(1)">',
    };
    expect(help.label(context)).toBe(help.text('help.inline.awaitingAdvanceAndVatReview'));
    expect(help.tone(context)).toBe('attention');
    expect(help.tone({ domain: 'transaction', state: 'posted' })).toBe('positive');
    expect(help.content(context)).toContain('&lt;img');
    expect(help.content(context)).not.toContain('<img');
    expect(help.reasonInfo({ code: 'unknown' })).toHaveLength(3);
  });
test('scoped help survives history and locale changes, and releases disposed records', async () => {
  document.body.innerHTML =
    '<button id="opener">Open</button><dialog id="status-help-dialog"><h2 id="status-help-title"></h2><div id="status-help-body"></div><button id="status-help-close"></button></dialog><div id="accounting-tooltip" hidden></div>';
  const dialog = document.querySelector('dialog')!,
    button = document.querySelector<HTMLButtonElement>('#opener')!;
  dialog.showModal = () => {
    dialog.open = true;
  };
  dialog.close = () => {
    dialog.open = false;
  };
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  const helper = createAccountingHelp();
  let id: string | undefined;
  const state = (value?: string) => history.replaceState({ accountingHelp: value }, '');
  helper.setHistoryAdapter({
    push(value) {
      id = value;
      state(value);
    },
    back() {
      state();
      helper.handlePopState();
    },
    clear() {
      state();
    },
    navigate: vi.fn(),
  });
  const scope = helper.createScope({
    domain: 'asset',
    state: 'needs_review',
    reasons: [{ code: 'advance_documents_overlap' }],
  });
  button.dataset.statusHelp = scope.id;
  button.click();
  expect(dialog.open).toBe(true);
  const clipboard = vi.fn(async () => {});
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: clipboard },
  });
  helper.setLocale('en');
  document.querySelector<HTMLButtonElement>('[data-copy-question]')!.click();
  await Promise.resolve();
  expect(clipboard).toHaveBeenCalledTimes(1);
  expect(document.querySelector('.copy-feedback')!.textContent).toBe(helper.word('copied'));
  clipboard.mockRejectedValueOnce(new Error('Denied'));
  document.querySelector<HTMLButtonElement>('[data-copy-question]')!.click();
  await Promise.resolve();
  expect(document.querySelector('.copy-feedback')!.textContent).not.toBe(helper.word('copied'));
  state();
  helper.handlePopState();
  expect(dialog.open).toBe(false);
  expect(document.activeElement).toBe(button);
  state(id);
  helper.handlePopState();
  expect(dialog.open).toBe(true);
  const sibling = helper.createScope({ domain: 'transaction', state: 'ready' });
  scope.dispose();
  scope.dispose();
  expect(dialog.open).toBe(false);
  state(id);
  expect(helper.handlePopState()).toBe(false);
  sibling.update({ domain: 'transaction', state: 'posted' });
  button.dataset.statusHelp = sibling.id;
  button.click();
  expect(dialog.open).toBe(true);
  helper.dispose();
  expect(dialog.open).toBe(false);
  document.body.innerHTML = '';
  state();
});
