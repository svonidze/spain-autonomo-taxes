// @vitest-environment node
import {expect, test} from 'vitest';
import {mkdtempSync, mkdirSync, writeFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createIntl} from '@formatjs/intl';
import {readMessages, compileCatalogs} from '../../scripts/build_locales.mts';

test('catalog validation detects duplicates before JSON parsing loses them', () => {
  expect(() => readMessages('{"common.label":"A","common.label":"B"}', 'fixture')).toThrow('duplicate');
  expect(() => readMessages('{"common.label":""}', 'fixture')).toThrow('empty');
});

test('a third language adds only catalogs and registry metadata', () => {
  const directory = mkdtempSync(join(tmpdir(), 'autonomo-locales-'));
  const definitions = [
    {code: 'ru', intl: 'ru-RU', message: '{count, plural, one {# запись} few {# записи} many {# записей} other {# записи}}'},
    {code: 'en', intl: 'en-GB', message: '{count, plural, one {# record} other {# records}}'},
    {code: 'es', intl: 'es-ES', message: '{count, plural, one {# registro} many {# registros} other {# registros}}'},
  ];
  try {
    writeFileSync(join(directory, 'registry.json'), JSON.stringify(definitions.map(({code, intl}) => ({code, intl, name: code, direction: 'ltr'}))));
    for (const locale of definitions) {
      mkdirSync(join(directory, locale.code));
      writeFileSync(join(directory, locale.code, 'common.json'), JSON.stringify({'common.count': locale.message}));
    }
    const compiled = compileCatalogs(directory);
    const intl = createIntl({locale: 'es-ES', messages: compiled.ast.es});
    expect(intl.formatMessage({id: 'common.count'}, {count: 2})).toBe('2 registros');
    writeFileSync(join(directory, 'es', 'common.json'), JSON.stringify({'common.count': '{otherCount} registros'}));
    expect(() => compileCatalogs(directory)).toThrow('Argument names/types differ');
  } finally { rmSync(directory, {recursive: true, force: true}); }
});
