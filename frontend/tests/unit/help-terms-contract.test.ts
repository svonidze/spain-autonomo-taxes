import { expect, test } from 'vitest';
import references from '../../src/help/references.json' with { type: 'json' };
import ru from '../../src/locales/ru/help.json' with { type: 'json' };
import en from '../../src/locales/en/help.json' with { type: 'json' };

const catalogs = { ru: ru as Record<string, string>, en: en as Record<string, string> };

test('every help term has non-empty RU and EN text', () => {
  for (const [term, id] of Object.entries(references.Terms)) {
    for (const [locale, catalog] of Object.entries(catalogs)) {
      expect(catalog[id], `${locale} is missing ${id} (term ${term})`).toBeTypeOf('string');
      expect(catalog[id].trim().length, `${locale}.${id} is empty`).toBeGreaterThan(0);
    }
  }
});
