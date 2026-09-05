const {readdirSync} = require('node:fs');
const {spawnSync} = require('node:child_process');
const suites = readdirSync('tests').filter(name => /^test_.*\.js$/.test(name)).sort();
let failed = false;
for (const suite of suites) {
  console.log(`Running ${suite}`);
  // This suite consumes a JSON draft produced by its existing Python fixture.
  const fixtureDriven = suite === 'test_expense_workflow_ui.js';
  const executable = fixtureDriven ? 'python' : process.execPath;
  const args = fixtureDriven
    ? ['-m', 'pytest', '-q', 'tests/test_expense_workflow_web.py::test_expense_ui_keeps_retry_identity_and_legacy_asset_route']
    : [`tests/${suite}`];
  const result = spawnSync(executable, args, {stdio: 'inherit'});
  if (result.error) console.error(result.error.message);
  if (result.status !== 0) failed = true;
}
console.log(`${suites.length} JavaScript suites; ${failed ? 'failures reported above' : 'all passed'}`);
process.exitCode = failed ? 1 : 0;
