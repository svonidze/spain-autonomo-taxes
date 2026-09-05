const {spawnSync} = require('node:child_process');
const result = spawnSync(process.execPath, ['tests/test_web_ui_charts.js'], {stdio: 'inherit', env: {...process.env, AUTONOMO_CHART_ESM: '1'}});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
