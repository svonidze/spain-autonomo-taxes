const {existsSync, readFileSync, readdirSync} = require('node:fs');
const {resolve} = require('node:path');

const root = resolve(__dirname, '..');
const fromRoot = path => resolve(root, path);
const rows = JSON.parse(readFileSync(fromRoot('docs/plans/ui-test-map.json'), 'utf8'));
const names = new Set();
for (const row of rows) {
  if (names.has(row.source)) throw new Error(`Duplicate test mapping: ${row.source}`);
  names.add(row.source);
  if (!row.behavior || !Number.isInteger(row.stage)) throw new Error(`Incomplete mapping: ${row.source}`);
  if (!existsSync(fromRoot(row.source)) && (!row.replacements.length || row.replacements.some(path => !existsSync(fromRoot(path))))) {
    throw new Error(`Removed suite has no available replacement: ${row.source}`);
  }
}
for (const name of readdirSync(fromRoot('tests')).filter(name => /^test_.*\.js$/.test(name))) {
  if (!names.has(`tests/${name}`)) throw new Error(`Unmapped JavaScript suite: ${name}`);
}
console.log(`${rows.length} suite dispositions verified`);

const retired = JSON.parse(readFileSync(fromRoot('docs/plans/ui-python-test-map.json'),'utf8'));
for(const row of retired){if(!row.test||!row.reason||!row.replacements.length||row.replacements.some(path=>!existsSync(fromRoot(path))))throw new Error(`Incomplete Python disposition: ${row.source}:${row.test}`);}
console.log(`${retired.length} Python source-test dispositions verified`);
