const {spawnSync} = require('node:child_process');
const path = require('node:path');
const result = spawnSync(process.execPath, [path.resolve(__dirname, '../tests/unit/chart-renderer.cjs')], {stdio: 'inherit'});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
