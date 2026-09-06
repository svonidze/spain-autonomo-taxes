const {spawnSync} = require('node:child_process');
const result = spawnSync(process.execPath, ['frontend/tests/chart-renderer.cjs'], {stdio: 'inherit'});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
