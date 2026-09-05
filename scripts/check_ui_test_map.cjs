const {existsSync, readFileSync, readdirSync} = require('node:fs');
const rows = JSON.parse(readFileSync('docs/plans/ui-test-map.json', 'utf8'));
const names = new Set();
for (const row of rows) {
  if (names.has(row.source)) throw new Error(`Duplicate test mapping: ${row.source}`);
  names.add(row.source);
  if (!row.behavior || !Number.isInteger(row.stage)) throw new Error(`Incomplete mapping: ${row.source}`);
  if (!existsSync(row.source) && (!row.replacements.length || row.replacements.some(path => !existsSync(path)))) {
    throw new Error(`Removed suite has no available replacement: ${row.source}`);
  }
}
for (const name of readdirSync('tests').filter(name => /^test_.*\.js$/.test(name))) {
  if (!names.has(`tests/${name}`)) throw new Error(`Unmapped JavaScript suite: ${name}`);
}
console.log(`${rows.length} suite dispositions verified`);
