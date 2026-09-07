import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { existsSync, lstatSync, mkdirSync, realpathSync, rmSync } from 'node:fs';
import { isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { localizeShell } from './scripts/shell_locales.mts';

const frontendRoot = fileURLToPath(new URL('.', import.meta.url));
const repositoryRoot = resolve(frontendRoot, '..');
const packageRoot = resolve(repositoryRoot, 'backend/src/autonomo_taxes');
const webUiRoot = resolve(packageRoot, 'web_ui');
const generatedUiRoot = resolve(webUiRoot, 'dist');

function isChild(parent: string, child: string) {
  const pathFromParent = relative(parent, child);
  return (
    pathFromParent !== '' &&
    pathFromParent !== '..' &&
    !pathFromParent.startsWith(`..${sep}`) &&
    !isAbsolute(pathFromParent)
  );
}

function clearGeneratedUiRoot() {
  const canonicalRepositoryRoot = realpathSync(repositoryRoot);
  const canonicalPackageRoot = realpathSync(packageRoot);
  if (!isChild(canonicalRepositoryRoot, canonicalPackageRoot)) {
    throw new Error(`Refusing UI output outside this repository: ${canonicalPackageRoot}`);
  }
  mkdirSync(webUiRoot, { recursive: true });
  if (lstatSync(webUiRoot).isSymbolicLink()) {
    throw new Error(`Refusing symlinked UI output parent: ${webUiRoot}`);
  }
  const canonicalWebUiRoot = realpathSync(webUiRoot);
  if (relative(canonicalPackageRoot, canonicalWebUiRoot) !== 'web_ui') {
    throw new Error(`Refusing unexpected UI output parent: ${canonicalWebUiRoot}`);
  }
  if (existsSync(generatedUiRoot)) {
    const outputStatus = lstatSync(generatedUiRoot);
    if (outputStatus.isSymbolicLink() || !outputStatus.isDirectory()) {
      throw new Error(`Refusing unexpected UI output: ${generatedUiRoot}`);
    }
    if (relative(canonicalWebUiRoot, realpathSync(generatedUiRoot)) !== 'dist') {
      throw new Error(`Refusing UI output outside its package: ${generatedUiRoot}`);
    }
  }
  if (!isChild(webUiRoot, generatedUiRoot)) {
    throw new Error(`Refusing to clear unexpected UI output: ${generatedUiRoot}`);
  }
  rmSync(generatedUiRoot, { recursive: true, force: true });
  mkdirSync(generatedUiRoot, { recursive: true });
}

export default defineConfig({
  plugins: [
    vue(),
    { name: 'catalog-shell', transformIndexHtml: { order: 'pre', handler: localizeShell } },
    { name: 'clear-generated-ui-output', buildStart: clearGeneratedUiRoot },
  ],
  root: frontendRoot,
  publicDir: false,
  envDir: false,
  envPrefix: [],
  build: {
    outDir: generatedUiRoot,
    assetsDir: 'ui-assets',
    emptyOutDir: false,
    target: 'es2022',
    sourcemap: false,
  },
});
