import {readFileSync, readdirSync, mkdirSync, writeFileSync} from 'node:fs';
import {resolve, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseExpression} from '@babel/parser';
import {parse, TYPE, type MessageFormatElement} from '@formatjs/icu-messageformat-parser';

export interface LocaleSpec {
  code: string;
  name: string;
  intl: string;
  direction: 'ltr' | 'rtl';
  quarterNumbers?: string[];
}
export type ArgumentKind = 'number' | 'date' | 'string';
export type Arguments = Record<string, ArgumentKind>;

export function readMessages(text: string, filename: string): Record<string, string> {
  const value: unknown = JSON.parse(text);
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(`${filename}: expected an object`);
  const expression = parseExpression(text);
  if (expression.type !== 'ObjectExpression') throw new Error(`${filename}: expected JSON messages`);
  const seen = new Set<string>();
  for (const property of expression.properties) {
    if (property.type !== 'ObjectProperty' || property.key.type !== 'StringLiteral') throw new Error(`${filename}: invalid message property`);
    const id = property.key.value;
    if (seen.has(id)) throw new Error(`${filename}: duplicate ${id}`);
    seen.add(id);
  }
  for (const [id, message] of Object.entries(value)) {
    if (!/^[a-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$/.test(id)) throw new Error(`${filename}: invalid semantic ID ${id}`);
    if (typeof message !== 'string' || !message.trim()) throw new Error(`${filename}: empty or non-string ${id}`);
  }
  return value as Record<string, string>;
}

export function argumentsOf(elements: MessageFormatElement[], intl: string): Arguments {
  const arguments_: Arguments = {};
  function record(name: string, kind: ArgumentKind) {
    if (kind === 'string' && arguments_[name]) return;
    if (arguments_[name] === 'string') { arguments_[name] = kind; return; }
    if (arguments_[name] && arguments_[name] !== kind) throw new Error(`Inconsistent type for ${name}`);
    arguments_[name] = kind;
  }
  function visit(nodes: MessageFormatElement[]) {
    for (const element of nodes) {
      if (element.type === TYPE.argument) record(element.value, 'string');
      if (element.type === TYPE.number) record(element.value, 'number');
      if (element.type === TYPE.date || element.type === TYPE.time) record(element.value, 'date');
      if (element.type === TYPE.plural || element.type === TYPE.select) {
        record(element.value, element.type === TYPE.plural ? 'number' : 'string');
        if (!element.options.other) throw new Error(`Missing other form for ${element.value}`);
        if (element.type === TYPE.plural) {
          const categories = new Intl.PluralRules(intl, {type: element.pluralType}).resolvedOptions().pluralCategories;
          for (const category of categories) {
            if (!element.options[category]) throw new Error(`Missing ${category} plural for ${element.value} (${intl})`);
          }
        }
        for (const option of Object.values(element.options)) visit(option.value);
      }
      if (element.type === TYPE.tag) throw new Error('Translated HTML is not supported; use component composition');
    }
  }
  visit(elements);
  return Object.fromEntries(Object.entries(arguments_).sort());
}

export function compileCatalogs(directory: string) {
  const input: unknown = JSON.parse(readFileSync(join(directory, 'registry.json'), 'utf8'));
  if (!Array.isArray(input)) throw new Error('Locale registry must be an array');
  const registry: LocaleSpec[] = input.map(value => {
    if (!value || typeof value !== 'object' || Array.isArray(value)
        || typeof value.code !== 'string' || typeof value.name !== 'string' || !value.name.trim()
        || typeof value.intl !== 'string' || !['ltr', 'rtl'].includes(value.direction)) throw new Error('Invalid locale metadata');
    if (value.quarterNumbers !== undefined && (!Array.isArray(value.quarterNumbers) || value.quarterNumbers.length !== 4 || !value.quarterNumbers.every((item: unknown) => typeof item === 'string' && item.length > 0))) throw new Error('Quarter numerals must contain four labels');
    return value as LocaleSpec;
  });
  const codes = new Set<string>();
  const raw: Record<string, Record<string, string>> = {};
  const ast: Record<string, Record<string, MessageFormatElement[]>> = {};
  const argumentsByLocale: Record<string, Record<string, Arguments>> = {};
  for (const locale of registry) {
    if (!/^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/.test(locale.code) || codes.has(locale.code)) throw new Error('Invalid or duplicate locale code');
    if (!locale.name || !['ltr', 'rtl'].includes(locale.direction)) throw new Error(`Invalid locale metadata: ${locale.code}`);
    codes.add(locale.code);
    const canonical = Intl.getCanonicalLocales(locale.intl)[0];
    if (Intl.PluralRules.supportedLocalesOf([canonical]).length !== 1) throw new Error(`Unsupported Intl locale: ${locale.intl}`);
    const messages: Record<string, string> = {};
    for (const filename of readdirSync(join(directory, locale.code)).filter(name => name.endsWith('.json')).sort()) {
      const entries = readMessages(readFileSync(join(directory, locale.code, filename), 'utf8'), `${locale.code}/${filename}`);
      for (const [id, value] of Object.entries(entries)) {
        if (id in messages) throw new Error(`Duplicate across domains: ${locale.code}/${id}`);
        messages[id] = value;
      }
    }
    raw[locale.code] = messages;
    ast[locale.code] = {};
    argumentsByLocale[locale.code] = {};
    for (const [id, message] of Object.entries(messages)) {
      try {
        const elements = parse(message, {captureLocation: false});
        ast[locale.code][id] = elements;
        argumentsByLocale[locale.code][id] = argumentsOf(elements, locale.intl);
      } catch (error) { throw new Error(`${locale.code}/${id}: ${String(error)}`); }
    }
  }
  if (!raw.ru) throw new Error('Russian fallback catalog is required');
  const keys = Object.keys(raw.ru).sort();
  for (const locale of registry) {
    if (JSON.stringify(Object.keys(raw[locale.code]).sort()) !== JSON.stringify(keys)) throw new Error(`Message key coverage differs: ${locale.code}`);
    for (const key of keys) {
      if (JSON.stringify(argumentsByLocale[locale.code][key]) !== JSON.stringify(argumentsByLocale.ru[key])) throw new Error(`Argument names/types differ: ${locale.code}/${key}`);
    }
  }
  return {registry, raw, ast, arguments: argumentsByLocale.ru, keys};
}

function pseudolocalize(elements: MessageFormatElement[]): MessageFormatElement[] {
  const copy = structuredClone(elements);
  function visit(nodes: MessageFormatElement[]) {
    for (const element of nodes) {
      if (element.type === TYPE.literal && element.value.trim()) element.value = `⟦${element.value.replace(/[aeiou]/gi, value => value + value)}⟧`;
      if (element.type === TYPE.plural || element.type === TYPE.select) for (const option of Object.values(element.options)) visit(option.value);
    }
  }
  visit(copy);
  return copy;
}

export function generate(directory = resolve('frontend/src/locales'), destination = resolve('frontend/src/generated'), pseudo = false) {
  const result = compileCatalogs(directory);
  if (directory === resolve('frontend/src/locales')) {
    const references = JSON.parse(readFileSync(resolve('frontend/src/help/references.json'), 'utf8'));
    function check(value: unknown) {
      if (typeof value === 'string' && !result.keys.includes(value)) throw new Error(`Unknown help message reference: ${value}`);
      if (Array.isArray(value)) value.forEach(check);
      else if (value && typeof value === 'object') Object.values(value).forEach(check);
    }
    check(references);
  }
  if (pseudo) {
    result.registry.push({code: 'qps', name: 'Pseudo (test)', intl: 'en-GB', direction: 'ltr'});
    result.ast.qps = Object.fromEntries(Object.entries(result.ast.en).map(([key, value]) => [key, pseudolocalize(value)]));
  }
  mkdirSync(destination, {recursive: true});
  const {raw: _sourceMessages, ...runtime} = result;
  writeFileSync(join(destination, 'catalogs.json'), JSON.stringify({...runtime, testOnly: pseudo}) + '\n');
  const type = (kind: ArgumentKind) => kind === 'number' ? 'number' : kind === 'date' ? 'Date | number' : 'string | number';
  const declarations = result.keys.map(key => `  ${JSON.stringify(key)}: {${Object.entries(result.arguments[key]).map(([name, kind]) => `${JSON.stringify(name)}: ${type(kind)}`).join('; ')}};`).join('\n');
  writeFileSync(join(destination, 'messages.ts'), '// Generated from validated ICU catalogs. Do not edit.\nexport interface MessageValues {\n' + declarations + '\n}\nexport type MessageId = keyof MessageValues;\nexport type Locale = ' + result.registry.map(locale => JSON.stringify(locale.code)).join(' | ') + ';\n');
  console.log(`Validated ${result.keys.length} messages in ${result.registry.length} locales`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) generate(undefined, undefined, process.argv.includes('--pseudo'));
