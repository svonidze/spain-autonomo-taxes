const {spawnSync} = require('node:child_process');
const path = require('node:path');

function run(args, env = process.env) {
  const result = spawnSync(process.execPath, args, {stdio: 'inherit', env});
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Pseudolocale check failed (${result.status}): ${args[0]}`);
}
const vite = path.join(path.dirname(require.resolve('vite/package.json')), require('vite/package.json').bin.vite);
try {
  run(['scripts/build_locales.mts', '--pseudo']);
  run([vite, 'build']);
  run(['scripts/write_ui_manifest.cjs']);
  run(['node_modules/@playwright/test/cli.js', 'test', '--grep', 'pseudolocale'], {...process.env, AUTONOMO_PSEUDO: '1'});
} finally {
  // Test-only locale must never remain in an ordinary release artifact.
  run([process.env.npm_execpath, 'run', 'build']);
}
