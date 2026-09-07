const {engines} = require('../package.json');
const {execFileSync} = require('node:child_process');
if (!process.env.npm_execpath) throw new Error('Run this check through npm run check:runtime');
const npm = execFileSync(process.execPath, [process.env.npm_execpath, '--version'], {encoding: 'utf8'}).trim();
if (process.versions.node !== engines.node || npm !== engines.npm) {
  throw new Error(`Use Node ${engines.node} and npm ${engines.npm}; found ${process.versions.node} / ${npm}`);
}
