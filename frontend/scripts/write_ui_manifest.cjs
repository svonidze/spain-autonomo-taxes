const {createHash} = require('node:crypto');
const {readFileSync, writeFileSync, readdirSync, lstatSync, existsSync} = require('node:fs');
const {execFileSync} = require('node:child_process');
const path = require('node:path');
const frontendRoot = path.resolve(__dirname, '..');
const repositoryRoot = path.resolve(frontendRoot, '..');
const root = path.resolve(repositoryRoot, 'backend/src/autonomo_taxes/web_ui/dist');
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const files = {};
for (const name of ['index.html', ...readdirSync(path.join(root, 'ui-assets')).map(name => `ui-assets/${name}`)].sort()) {
  const target = path.join(root, name);
  if (!lstatSync(target).isFile() || !/^(index\.html|ui-assets\/[\w.-]+\.(js|css))$/.test(name)) throw new Error(`Unexpected UI resource: ${name}`);
  files[name] = digest(readFileSync(target));
}
const git = args => execFileSync('git', args, {cwd: repositoryRoot, encoding: 'utf8'}).trim();
const manifest = {
  contract: 1,
  test_only: existsSync(path.join(frontendRoot, 'src/generated/catalogs.json')) && JSON.parse(readFileSync(path.join(frontendRoot, 'src/generated/catalogs.json'), 'utf8')).testOnly === true,
  source_sha: git(['rev-parse', 'HEAD']),
  source_dirty: Boolean(git(['status', '--porcelain', '--untracked-files=no'])),
  node: process.versions.node,
  npm: require(path.join(frontendRoot, 'package.json')).engines.npm,
  lock_sha256: digest(readFileSync(path.join(frontendRoot, 'package-lock.json'))),
  files,
};
writeFileSync(path.join(root, 'build-manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log(`Recorded ${Object.keys(files).length} packaged UI resources`);
