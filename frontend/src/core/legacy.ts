import * as i18n from './i18n.ts';
import * as format from './format.ts';
import * as http from './http.ts';
import * as focus from './focus.ts';
import * as drafts from './drafts.ts';
import specification from './legacy-spec.json' with {type: 'json'};

type Tree = string | Tree[] | {[key: string]: Tree};
function resolveTree(tree: Tree, locale: string): unknown {
  if (typeof tree === 'string') return i18n.legacyTemplate(tree, locale);
  if (Array.isArray(tree)) return tree.map(child => resolveTree(child, locale));
  return Object.fromEntries(Object.entries(tree).map(([key, value]) => [key, resolveTree(value, locale)]));
}
const legacy: Record<string, unknown> = {};
for (const [name, tree] of Object.entries(specification.localeTables)) {
  legacy[name] = Object.fromEntries(i18n.localeRegistry.map(locale => [locale.code, resolveTree(tree, locale.code)]));
}
for (const [name, entries] of Object.entries(specification.keyTables)) {
  legacy[name] = Object.fromEntries(Object.entries(entries).map(([key, id]) => [key,
    Object.fromEntries(i18n.localeRegistry.map(locale => [locale.code, i18n.legacyTemplate(id, locale.code)])),
  ]));
}
export const legacyCore = {
  ...i18n, ...format, ...http, ...focus, ...drafts, legacy, refs: specification.refs,
  messages: Object.fromEntries(i18n.localeRegistry.map(locale => [locale.code,
    Object.fromEntries(i18n.messageIds.map(id => [id, i18n.legacyTemplate(id, locale.code)])),
  ])),
};
