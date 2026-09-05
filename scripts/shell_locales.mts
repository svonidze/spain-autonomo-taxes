import {JSDOM} from 'jsdom';
import {defaultLocale, formatMessage, installLocaleControls, messageIds} from '../frontend/src/core/i18n.ts';

export function localizeShell(html: string): string {
  const dom = new JSDOM(html);
  const document = dom.window.document;
  function value(key: string): string {
    if (!messageIds.includes(key)) throw new Error(`Unknown shell message: ${key}`);
    return formatMessage(key, {}, defaultLocale);
  }
  for (const element of document.querySelectorAll<HTMLElement>('[data-i18n]')) {
    element.textContent = value(element.dataset.i18n!);
  }
  for (const [marker, attribute] of [['data-i18n-aria', 'aria-label'], ['data-i18n-title', 'title'], ['data-i18n-placeholder', 'placeholder']]) {
    for (const element of document.querySelectorAll(`[${marker}]`)) element.setAttribute(attribute, value(element.getAttribute(marker)!));
  }
  installLocaleControls(document);
  const result = dom.serialize();
  dom.window.close();
  return result;
}
